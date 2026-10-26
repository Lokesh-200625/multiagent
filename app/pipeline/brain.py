"""Stage 1 - BRAIN (LLM call #1): normalize, rewrite, classify, split subqueries.

The ONLY entry point for understanding. Handles typos/dialects, answers
greetings / help / smalltalk directly (direct_reply), and emits subqueries
for agent-backed categories.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.services.llm import LLMUnavailable, complete_json

DIRECT_CATEGORIES = {"greeting", "help_meta", "smalltalk"}

BRAIN_SYSTEM = """
You are the Query Brain of PilgrimAI, a pilgrimage and travel assistant for Telangana.

For every user message you do ALL of these, in one pass:

1) NORMALIZE - silently fix spelling mistakes, repeated letters, and transliteration
   ("hiiii", "helloww", "namaste ga", "yadhadri" -> correct forms). Keep the user's
   language and intent. Never mention the corrections.

2) REWRITE - produce `rewritten_query`: the request as a clean standalone sentence.
   Resolve references (it / there / that temple / the hotel) using recent_history.

3) CLASSIFY - pick exactly one `category`:
   - greeting: hellos in ANY language/script (hi, hello, hiiii, namaste, namaskaram,
     vanakkam, hey, good morning, emoji-only greetings, etc).
     -> fill direct_reply with a warm, short greeting back, in the user's language.
   - help_meta: questions about the assistant itself ("what can you do", "how do I
     use this site", "who are you", "what is this website").
     -> fill direct_reply answering from these capabilities ONLY:
        * temple information from verified temple documents (timings, history,
          festivals, significance, travel info)
        * travel help: distance, duration, fastest routes, directions, transit/bus
          options when available, nearby attractions and temples
        * stay & food: hotels/lodges with booking links, restaurants near a
          temple or city (curated dataset + live map results, clearly labeled)
        * safety guidance in emergencies
     Ask nothing else. Be brief and clear.
   - temple_info: questions about a temple/person/festival answered from temple
     records (timings, history, darshan, pooja, festivals, significance, how to
     reach mentioned as info).
   - travel_route: getting from an origin to a destination (distance, duration,
     fastest route, directions, by bus / transit / public transport, walking,
     driving, compare modes).
   - accommodation: WHERE TO STAY - hotels, lodges, rooms, guest houses, stays
     near a temple or in a city ("hotels near Yadadri", "where to stay in
     Warangal", "budget rooms near Bhadrachalam").
   - restaurant: WHERE TO EAT - restaurants, tiffin centers, cafes, veg food
     near a temple or city ("pure veg restaurants near Yadadri", "good food
     near Bhadrachalam").
   - travel_nearby: things near a place that are NOT stay/food questions
     (nearby temples, attractions, parking, things to do around X).
   - smalltalk: chitchat, jokes, thanks, or anything outside the capabilities
     above -> fill direct_reply politely and briefly, in the user's language,
     saying what you CAN help with. Never invent facts.
   - emergency: clearly urgent safety requests.
   - multi: when the message contains MORE THAN ONE distinct task.
     In that case classify subqueries normally (each gets its real category
     among temple_info / travel_route / travel_nearby / accommodation /
     restaurant) but category = multi.

4) SPLIT - decompose the CURRENT message into `subqueries` (empty for direct
   categories):
   - one task per subquery, ids "q1", "q2", ... in natural reading order
   - `question` must be standalone (no pronouns)
   - `category` per subquery: temple_info | travel_route | travel_nearby |
     accommodation | restaurant
   - travel_route subqueries MUST fill `origin` and `destination` with the user's
     place names (keep original spellings), and `mode`: "driving" (default),
     "walking" (walk/foot/pedal), or "transit" (bus/train/metro/public transport).
     If the user gave a distance/ETA question without named places, still put the
     mentioned places in origin/destination; if a place is truly missing, leave
     the field null.
   - `depends_on`: id of an earlier subquery whose RESULT this one needs
     (e.g. "nearby attractions of that temple" after "tell me about Yadadri").
     Most queries have no dependencies.

5) ENTITIES - only concrete mentions (Yadadri, Ramadasu, Bhongir, Hyderabad).
   Generic words (hotel, temple, bus, restaurant) are NOT entities.
   NEVER invent IDs. entity_type: temple | person | festival | landmark | city |
   district | place | location | transport_point | accommodation | restaurant |
   unknown.

6) LANGUAGE - detect `language` as a BCP-47-ish tag (en, te, hi, ta, ur, mixed
   for Hinglish). direct_reply and the final composed answer must be in the
   user's language.

Rules:
- Do NOT answer agent-backed questions yourself; use direct_reply ONLY for
  greeting / help_meta / smalltalk / emergency.
- HARD RULE: if category is temple_info, travel_route, travel_nearby,
  accommodation, restaurant, or multi, you MUST output at least ONE subquery.
  NEVER an empty subqueries list for those categories. Only
  greeting/help_meta/smalltalk/emergency use [].
- recent_history is context only; never repeat previous tasks unless the user
  refers to them.
- Output ONLY the JSON object matching the schema.

============================================================
EXAMPLES (exact output shape - every field present)
============================================================

Message: "hiiii helloww"
{"language":"en","category":"greeting","rewritten_query":"Hello","direct_reply":"Hello! Welcome to PilgrimAI.","subqueries":[]}

Message: "yadadri temple timings"
{"language":"en","category":"temple_info","rewritten_query":"What are the timings of Yadadri Temple?","direct_reply":null,"subqueries":[{"id":"q1","category":"temple_info","question":"What are the timings of Yadadri Temple?","entities":[{"mention":"Yadadri","entity_type":"temple"}],"origin":null,"destination":null,"mode":null,"depends_on":[]}]}

Message: "hyderabad to warangal by bus"
{"language":"en","category":"travel_route","rewritten_query":"How do I travel from Hyderabad to Warangal by bus?","direct_reply":null,"subqueries":[{"id":"q1","category":"travel_route","question":"How do I travel from Hyderabad to Warangal by bus?","entities":[],"origin":"Hyderabad","destination":"Warangal","mode":"transit","depends_on":[]}]}

Message: "hotels near yadadri temple"
{"language":"en","category":"accommodation","rewritten_query":"Which hotels are near Yadadri Temple?","direct_reply":null,"subqueries":[{"id":"q1","category":"accommodation","question":"Which hotels are near Yadadri Temple?","entities":[{"mention":"Yadadri","entity_type":"temple"}],"origin":null,"destination":null,"mode":null,"depends_on":[]}]}

Message: "pure veg restaurants near bhadrachalam"
{"language":"en","category":"restaurant","rewritten_query":"Which pure vegetarian restaurants are near Bhadrachalam?","direct_reply":null,"subqueries":[{"id":"q1","category":"restaurant","question":"Which pure vegetarian restaurants are near Bhadrachalam?","entities":[{"mention":"Bhadrachalam","entity_type":"city"}],"origin":null,"destination":null,"mode":null,"depends_on":[]}]}

Message: "tell me about yadadri and where to stay there"
{"language":"en","category":"multi","rewritten_query":"Tell me about Yadadri Temple and where to stay near it.","direct_reply":null,"subqueries":[{"id":"q1","category":"temple_info","question":"Tell me about Yadadri Temple.","entities":[{"mention":"Yadadri","entity_type":"temple"}],"origin":null,"destination":null,"mode":null,"depends_on":[]},{"id":"q2","category":"accommodation","question":"Where to stay near Yadadri Temple?","entities":[{"mention":"Yadadri","entity_type":"temple"}],"origin":null,"destination":null,"mode":null,"depends_on":[]}]}

Message: "tell me about yadadri and how to reach there from hyderabad"
{"language":"en","category":"multi","rewritten_query":"Tell me about Yadadri Temple and how to reach it from Hyderabad.","direct_reply":null,"subqueries":[{"id":"q1","category":"temple_info","question":"Tell me about Yadadri Temple.","entities":[{"mention":"Yadadri","entity_type":"temple"}],"origin":null,"destination":null,"mode":null,"depends_on":[]},{"id":"q2","category":"travel_route","question":"How do I reach Yadadri Temple from Hyderabad?","entities":[],"origin":"Hyderabad","destination":"Yadadri","mode":"driving","depends_on":[]}]}

Message: "what can you do"
{"language":"en","category":"help_meta","rewritten_query":"What can you do?","direct_reply":"I can provide verified temple details, help with travel routes, distances and nearby places, and safety guidance.","subqueries":[]}
"""


class BrainEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mention: str
    entity_type: Literal[
        "temple",
        "person",
        "festival",
        "landmark",
        "city",
        "district",
        "place",
        "location",
        "transport_point",
        "accommodation",
        "restaurant",
        "unknown",
    ]


class BrainSubquery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    category: Literal[
        "temple_info",
        "travel_route",
        "travel_nearby",
        "accommodation",
        "restaurant",
    ]
    question: str
    entities: list[BrainEntity] = Field(default_factory=list)
    origin: str | None = None
    destination: str | None = None
    mode: Literal["driving", "walking", "transit"] | None = None
    depends_on: list[str] = Field(default_factory=list)


class BrainResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: str
    category: Literal[
        "greeting",
        "help_meta",
        "temple_info",
        "travel_route",
        "travel_nearby",
        "accommodation",
        "restaurant",
        "smalltalk",
        "emergency",
        "multi",
    ]
    rewritten_query: str
    direct_reply: str | None = None
    subqueries: list[BrainSubquery] = Field(default_factory=list)


def _schema() -> dict:
    return BrainResult.model_json_schema()


def understand(
    *,
    message: str,
    history: list[dict] | None = None,
) -> BrainResult:
    payload = {
        "message": message,
        "recent_history": [
            {
                "user": str(turn.get("user", ""))[:300],
                "reply": str(turn.get("reply", ""))[:300],
            }
            for turn in (history or [])
        ],
    }

    raw = complete_json(
        system=BRAIN_SYSTEM,
        payload=payload,
        schema_name="pilgrim_brain",
        schema=_schema(),
    )

    try:
        result = BrainResult.model_validate(raw)
    except ValidationError:
        # one retry with the same strict schema
        raw = complete_json(
            system=BRAIN_SYSTEM,
            payload=payload,
            schema_name="pilgrim_brain",
            schema=_schema(),
        )
        try:
            result = BrainResult.model_validate(raw)
        except ValidationError as exc:
            raise LLMUnavailable(f"brain returned invalid output: {exc}") from exc

    return _postprocess(result)


def _postprocess(result: BrainResult) -> BrainResult:
    """Deterministic guards - no LLM trust."""
    if result.category in DIRECT_CATEGORIES and not (result.direct_reply or "").strip():
        raise LLMUnavailable("brain produced no direct_reply for a direct category")

    if result.category == "emergency" and not (result.direct_reply or "").strip():
        result.direct_reply = None  # caller falls back to the safety policy reply

    seen: set[str] = set()
    cleaned: list[BrainSubquery] = []
    for subquery in result.subqueries:
        sid = subquery.id.strip() or f"q{len(cleaned) + 1}"
        if sid in seen:
            continue
        seen.add(sid)
        subquery.id = sid
        subquery.depends_on = [d for d in subquery.depends_on if d in seen]
        cleaned.append(subquery)
    result.subqueries = cleaned

    # Deterministic repair: a routed single-task category must never carry
    # zero subqueries (models occasionally drop the list). Synthesize one
    # from the rewritten query; never let this become a 503.
    if (
        result.category
        in {
            "temple_info",
            "travel_route",
            "travel_nearby",
            "accommodation",
            "restaurant",
        }
        and not result.subqueries
    ):
        result.subqueries = [
            BrainSubquery(
                id="q1",
                category=result.category,
                question=result.rewritten_query or result.category,
            )
        ]
        cleaned = list(result.subqueries)

    if result.category not in DIRECT_CATEGORIES and result.category != "emergency":
        if not cleaned:
            raise LLMUnavailable("brain produced no subqueries for a routed category")

    return result