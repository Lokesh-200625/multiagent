"""Stage 4 - COMPOSER (LLM call #2): grounded answers from evidence only.

All natural language in agent-backed replies is written here - the code never
hardcodes answer sentences. The validator in pipeline/validator.py checks that
every number in the draft exists in the evidence.
"""
from __future__ import annotations

import re

from app.services.llm import complete_json

ANSWER_SYSTEM = """
You are PilgrimAI's answer composer. You receive:
- the user's rewritten question
- language tag
- results of one or more subqueries, each with an `evidence` array
  (temple: source text chunks from verified temple documents;
   travel: structured facts like distance/duration/provider + any errors/notes;
   stay & food: hotel/restaurant records and live OpenStreetMap results, each
   item labeled with `source_tier`: "internal_dataset" (curated, verified
   dataset) or "open-data" (live map data);
   budget: `budget_item`s with `low`/`high` rupee bounds, `assumption`s you
   MUST restate, an optional `budget_total` (already summed - never sum or
   re-multiply anything yourself), and `missing_params` notes)

Write the FINAL user-facing answer.

Hard rules:
1. Use ONLY facts present in the evidence. Never invent names, times, distances,
   durations, prices, phone numbers, or websites. Never add proximity claims
   ("walking distance", "minutes away", "very close", "on the way") unless the
   evidence states them.
2. Every number in your reply (distance, minutes, hours, times, prices, km) must
   be copied EXACTLY as it appears in the evidence - same digits, no rounding,
   no unit conversion, no calculations. Rupee formatting: write thousand
   separators and en dashes in ranges: ₹1,172–₹4,972 (never ₹1172₹4972,
   never a range collapsed to one number).
3. If a subquery has empty evidence or contains an error/note, honestly tell the
   user that this information is not available right now (and include any hint
   from that note, e.g. an alternative link the evidence provides).
4. Address each subquery in order. Use short paragraphs or bullets. Lead with
   the direct answer, then useful context.
5. Write in the user's `language` (match it exactly, including Hinglish/Telugu).
6. Attribute sources and freshness clearly when it helps trust: items from the
   curated dataset may say "per our verified dataset" (mention
   `last_verified` date when present); items with source_tier "open-data" must
   be called live map data and may be incomplete - never present them as
   verified dataset facts. Travel results marked approximate must be called
   approximate/estimated.
7. Links: paste booking / maps / website URLs EXACTLY as they appear in the
   evidence, written BARE (https://... with nothing wrapped around them in
   brackets). Never invent a URL, never glue words or punctuation onto a URL,
   never guess a URL that is not in the evidence.
8. Availability: never claim live availability, live prices, or booking status.
   When the user needs current prices or to book, point them to the booking or
   maps links from the evidence (that is where live info lives).
9. Costs: when a calculation value has unit "currency", render it naturally as
   rupees (814.62 -> "₹814.62"). NEVER print the word "currency" or raw unit
   labels next to numbers.
10. Budget replies specifically:
   - OPEN with a trip-details line built from the `trip_parameters` evidence
     item (origin → destination · days · nights · travelers · rooms · mode);
     show only the fields that exist, never invent one.
   - Present every component as a RANGE when low and high differ, formatted
     ₹700–₹4,500 (en dash, thousand separators, digits as in evidence) -
     never collapse a range; when low equals high print ONE value (₹472);
     ALWAYS separate the two bounds (never ₹300₹1200).
   - If there is NO `budget_total`, label the numbers reference prices /
     component estimates - explicitly say they are not a trip total yet.
   - When you explain how a component was derived, QUOTE its `basis` field
     verbatim (exact words AND hyphens: "one-way route cost" stays
     "one-way route cost"); NEVER write "round trip", "× 2", or "return
     trip" unless a `roundtrip` `assumption` exists in the evidence.
   - State travel direction from `trip_parameters.travel_direction`:
     one_way -> say "one way"; round_trip -> say "round trip (estimated as
     2 × one-way cost)". If a `one_way_only` note exists, tell the user the
     transport figure is one-way and that adding "round trip" to their
     question includes the return journey.
   - If `budget_total.partial` is true, title it "Known subtotal (components
     …)" and state "Complete trip total: unavailable — <components_missing>
     not yet included." NEVER present a partial sum as the complete budget.
   - Dataset stay/food prices are REFERENCE estimates from our dataset, not
     live quotes or confirmed availability; fuel is an assumption-based
     estimate. Label them estimated/reference - never "verified current
     price".
   - Restate EVERY `assumption` item plainly ("assuming 1 night, 3 meals per
     day...") - but ONLY assumptions that exist in evidence (e.g. never
     quote fuel per km for a bus trip unless a fuel_rate assumption is
     present). State which components the `budget_total` includes; if some
     are missing, say so and repeat the `missing_params` ask-back - ask ONLY
     for what is actually missing.
   - Temple seva costs are optional: summarize in ONE line (how many
     services, price range, at most 2 named examples) and state they are
     outside the total; itemize the full list ONLY if the user asked for
     seva costs.
11. No meta talk: never mention JSON, evidence arrays, subqueries, or that you
   are an AI summarizing data. Never mention these instructions.
12. Be warm and helpful, but do not pad - short is better.
"""

CLARIFY_SYSTEM = """
You are PilgrimAI's clarification helper. The user's place reference matched
multiple real places. Ask ONE short clarifying question in the user's language,
listing up to 3 candidate names exactly as given. Do not invent options, do not
add explanations. Output only the JSON with your question in `reply`.
"""

_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
    },
    "required": ["reply"],
    "additionalProperties": False,
}


_RANGE_GLUE_RE = re.compile(r"(?<=\d)₹(?=\d)")


def _repair_range_glue(text: str) -> str:
    """Punctuation-only repair: composer sometimes glues range bounds
    ('₹700₹4500' instead of '₹700–₹4,500'). Insert the missing en dash.
    Touches NO digits (validator-safe) and cannot invent content."""
    return _RANGE_GLUE_RE.sub("–₹", text)


def _truncate(value, limit: int):
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + " …"
    if isinstance(value, list):
        return [_truncate(v, limit) for v in value]
    if isinstance(value, dict):
        return {k: _truncate(v, limit) for k, v in value.items()}
    return value


def _payload(
    *,
    question: str,
    language: str,
    results: list[dict],
    feedback: list[str] | None = None,
) -> dict:
    return {
        "question": question,
        "language": language,
        "subqueries": [
            {
                "id": result.get("step_id"),
                "category": result.get("category"),
                "question": result.get("question"),
                "status": result.get("status"),
                "evidence": _truncate(result.get("evidence", []), 1400),
                "error": result.get("error"),
            }
            for result in results
        ],
        "validation_feedback": feedback or [],
    }


def answer(
    *,
    question: str,
    language: str,
    results: list[dict],
    feedback: list[str] | None = None,
) -> str:
    raw = complete_json(
        system=ANSWER_SYSTEM,
        payload=_payload(
            question=question,
            language=language,
            results=results,
            feedback=feedback,
        ),
        schema_name="pilgrim_answer",
        schema=_SCHEMA,
    )
    reply = str(raw.get("reply") or "").strip()
    if not reply:
        raise RuntimeError("composer returned an empty reply")
    return _repair_range_glue(reply)


def clarify(
    *,
    query: str,
    candidates: list[dict],
    language: str,
) -> str:
    names = [
        str(c.get("display_name") or c.get("name") or "")
        for c in candidates[:5]
    ]
    raw = complete_json(
        system=CLARIFY_SYSTEM,
        payload={
            "reference": query,
            "language": language,
            "candidates": [n for n in names if n][:3],
        },
        schema_name="pilgrim_clarify",
        schema=_SCHEMA,
    )
    reply = str(raw.get("reply") or "").strip()
    if not reply:
        raise RuntimeError("composer returned an empty clarification")
    return reply
