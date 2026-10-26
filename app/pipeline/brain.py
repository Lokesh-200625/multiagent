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
          options when available, nearby attractions, temples, hotels and food
        * safety guidance in emergencies
        Ask nothing else. Be brief and clear.
   - temple_info: questions about a temple/person/festival answered from temple
     records (timings, history, darshan, pooja, festivals, significance, how to
     reach mentioned as info).
   - travel_route: getting from an origin to a destination (distance, duration,
     fastest route, directions, by bus / transit / public transport, walking,
     driving, compare modes).
   - travel_nearby: things near a place (nearby temples, attractions, hotels,
     restaurants, parking, things to do around X).
   - smalltalk: chitchat, jokes, thanks, or anything outside the capabilities
     above -> fill direct_reply politely and briefly, in the user's language,
     saying what you CAN help with. Never invent facts.
   - emergency: clearly urgent safety requests.
   - multi: when the message contains MORE THAN ONE distinct task.
     In that case classify subqueries normally (each gets its real category
     among temple_info / travel_route / travel_nearby) but category = multi.

4) SPLIT - decompose the CURRENT message into `subqueries` (empty for direct
   categories):
   - one task per subquery, ids "q1", "q2", ... in natural reading order
   - `question` must be standalone (no pronouns)
   - `category` per subquery: temple_info | travel_route | travel_nearby
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
- recent_history is context only; never repeat previous tasks unless the user
  refers to them.
- Output ONLY the JSON object matching the schema.
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
    category: Literal["temple_info", "travel_route", "travel_nearby"]
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

    if result.category not in DIRECT_CATEGORIES and result.category != "emergency":
        if result.category != "multi" and len(cleaned) == 1:
            pass  # category already single
        if not cleaned:
            raise LLMUnavailable("brain produced no subqueries for a routed category")

    return result
