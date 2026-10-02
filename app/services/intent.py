import json
from typing import Any

from groq import Groq

from app.core.config import settings
from app.models.understanding import QueryUnderstanding


client = Groq(
    api_key=settings.groq_api_key
)


SYSTEM_PROMPT = """
You are the Query Understanding Engine for PilgrimAI.

Your job is semantic understanding and structured extraction.

You do NOT:
- answer the user
- resolve canonical IDs
- invent entity IDs
- retrieve external facts
- check live availability
- calculate routes
- calculate travel times
- book anything

============================================================
CURRENT TURN VS SESSION CONTEXT
============================================================

The user message is ALWAYS the primary source for the CURRENT turn.

previous_session_state is CONTEXT ONLY.

IMPORTANT:

Do NOT copy previous subqueries, execution steps, plans,
hotels, restaurants, transport requests, or itinerary items
into the current turn unless the CURRENT user message explicitly
refers to them or depends on them.

Examples:

Previous:
"Find a hotel near Yadadri."

Current:
"Also find a restaurant near it."

Correct current interpretation:
- one restaurant search
- "it" refers to the relevant previous entity/context
- do NOT create a new hotel search

Previous:
"Find a hotel near Yadadri."

Current:
"How far is it from Hyderabad?"

Correct:
- one travel/distance query
- "it" refers to Yadadri
- do NOT repeat the hotel query

Previous:
"Find a hotel near Yadadri."

Current:
"Now find another hotel."

Correct:
- one accommodation query
- it may use Yadadri as inherited context
- do NOT copy the previous hotel query as another step

Only create subqueries representing work requested by the
CURRENT user message.

============================================================
SUPPORTED LANGUAGE
============================================================

Understand:
- English
- Telugu
- Hindi
- Tamil
- Kannada
- Malayalam
- Marathi
- Bengali
- Gujarati
- Punjabi
- Urdu
- Odia
- Assamese
- Hinglish
- transliterated Indian languages
- mixed-language messages

============================================================
ENTITY SEMANTICS
============================================================

Only extract something as an entity when the user is referring
to a specific identifiable thing.

A generic category/request is NOT a named entity.

Examples:

"hotel kavali"
-> DO NOT create an entity named "hotel".

"restaurant kavali"
-> DO NOT create an entity named "restaurant".

"find a hotel near Yadadri"
-> "hotel" is NOT an entity.
-> Yadadri IS an entity.

"Hotel Ashoka kavali"
-> "Hotel Ashoka" IS an accommodation entity.

"find restaurants near Flavor's Inn"
-> "restaurants" is NOT an entity.
-> "Flavor's Inn" IS a restaurant entity.

"visit Yadadri"
-> "Yadadri" IS a temple entity.

"Hyderabad nunchi"
-> "Hyderabad" IS a city/location entity.

Generic words such as:
- hotel
- hotels
- accommodation
- restaurant
- restaurants
- food
- temple
- temples
- cab
- taxi
- bus

must NOT automatically become named entities.

The domain/type of a requested resource should instead be
represented through:
- subquery.domain
- subquery.intent
- required_capabilities
- constraints
- preferences

============================================================
ENTITY TYPES
============================================================

Use:
- temple
- accommodation
- restaurant
- city
- district
- state
- landmark
- transport_point
- person
- organization
- unknown

Only use an entity type when there is a concrete entity
mention.

============================================================
ENTITY IDS
============================================================

Never invent canonical IDs.

Return the user's entity mention.

The deterministic application layer resolves it later.

============================================================
MULTI-QUERY
============================================================

Split ONLY the CURRENT user message into subqueries.

Example:

"Visit Yadadri tomorrow, get a hotel near the temple,
and find a restaurant near the hotel."

Q1:
domain = temple

Q2:
domain = accommodation
depends on Q1

Q3:
domain = restaurant
depends on Q2

Preserve dependencies.

Do NOT copy subqueries from previous_session_state.

============================================================
SESSION REFERENCES
============================================================

The current message may contain references such as:

- it
- there
- that temple
- the hotel
- the restaurant
- there itself
- near it
- from there
- that place

Resolve the semantic referent using previous_session_state.

The resolved referent should appear in the CURRENT
subquery/entity representation.

Example:

Previous session:
Yadadri -> temple

Current:
"Find a restaurant near it."

Correct:
subquery.domain = restaurant
subquery.intent = search
entity = Yadadri
location = Yadadri with role "near"

Do NOT reproduce the previous hotel subquery.

============================================================
CONTEXT PROPAGATION
============================================================

Relevant context may be inherited from the session.

Examples:

Previous:
Yadadri

Current:
"Find a hotel near it."

-> inherit Yadadri as the reference entity.

Previous:
Yadadri next Saturday

Current:
"Find a hotel there."

-> inherit Yadadri and the relevant date if it clearly
continues the same task.

Only inherit context that is relevant to the CURRENT request.

============================================================
CONSTRAINTS
============================================================

Hard constraints:
- dates
- times
- duration
- group size
- origin
- destination
- pickup
- dropoff
- proximity requirements
- explicit budget limits
- explicit transport requirements

Preferences:
- good
- quiet
- family friendly
- comfortable
- preferred transport

Do not convert preferences into hard constraints.

============================================================
PROXIMITY
============================================================

Explicit proximity language must be represented.

Examples:

"hotel near Yadadri"

-> Yadadri is an entity
-> location:
   mention = "Yadadri"
   role = "near"

"restaurant near the hotel"

-> the relevant hotel/reference entity or session context
   must be represented
-> location role = "near"

Do not silently drop "near".

============================================================
DATES
============================================================

Extract temporal expressions exactly as expressed.

Examples:
- tomorrow
- next Saturday
- next month
- morning
- 6 AM

Do NOT calculate calendar dates here.

Calendar/date normalization happens later during orchestration.

============================================================
LOCATIONS
============================================================

Identify:
- origin
- destination
- pickup
- dropoff
- city
- district
- other relevant location

============================================================
GROUP
============================================================

Extract explicitly stated:
- adults
- children
- seniors
- total count

============================================================
REQUIRED CAPABILITIES
============================================================

Populate required_capabilities for each subquery when
the requested operation clearly requires a capability.

Examples:

temple information/visit:
- temple_info

hotel search:
- hotel_search

restaurant search:
- restaurant_search

travel/route:
- route_search

transport:
- transport_search

distance:
- distance_calculation

Do not leave required_capabilities empty when the
subquery clearly requires one of these capabilities.

============================================================
CLARIFICATION
============================================================

Only request clarification when information genuinely required
to understand the CURRENT task is missing.

Do not ask clarification because a generic category such as
"hotel" or "restaurant" has no canonical entity.

That is a search requirement, not an unresolved entity.

============================================================
EMERGENCY
============================================================

Clearly urgent emergency requests must be classified as:

"emergency"

Do not convert them into ordinary pilgrimage planning.

============================================================
OUTPUT
============================================================

Return only valid JSON matching the supplied schema.
"""


def _make_groq_strict_schema(
    schema: dict[str, Any],
) -> dict[str, Any]:

    if not isinstance(schema, dict):
        return schema

    schema = dict(schema)

    if schema.get("type") == "object":

        properties = schema.get(
            "properties",
            {},
        )

        schema["additionalProperties"] = False

        schema["required"] = list(
            properties.keys()
        )

        schema["properties"] = {
            key: _make_groq_strict_schema(
                value
            )
            for key, value in properties.items()
        }

    if "items" in schema:
        schema["items"] = _make_groq_strict_schema(
            schema["items"]
        )

    if "$defs" in schema:
        schema["$defs"] = {
            key: _make_groq_strict_schema(value)
            for key, value in schema["$defs"].items()
        }

    if "anyOf" in schema:
        schema["anyOf"] = [
            _make_groq_strict_schema(item)
            for item in schema["anyOf"]
        ]

    if "oneOf" in schema:
        schema["oneOf"] = [
            _make_groq_strict_schema(item)
            for item in schema["oneOf"]
        ]

    if "allOf" in schema:
        schema["allOf"] = [
            _make_groq_strict_schema(item)
            for item in schema["allOf"]
        ]

    return schema


def _strict_schema() -> dict[str, Any]:

    schema = QueryUnderstanding.model_json_schema()

    return _make_groq_strict_schema(schema)


def _merge_unique(
    existing: list,
    incoming: list,
) -> list:

    result = list(existing)

    for item in incoming:
        if item not in result:
            result.append(item)

    return result


def _remove_generic_entities(
    result: QueryUnderstanding,
) -> QueryUnderstanding:

    generic_terms = {
        "hotel",
        "hotels",
        "accommodation",
        "accommodations",
        "restaurant",
        "restaurants",
        "food",
        "temple",
        "temples",
        "cab",
        "cabs",
        "taxi",
        "taxis",
        "bus",
        "buses",
    }

    def is_generic(entity) -> bool:
        return (
            entity.mention.strip().lower()
            in generic_terms
        )

    result.entities = [
        entity
        for entity in result.entities
        if not is_generic(entity)
    ]

    for subquery in result.subqueries:
        subquery.entities = [
            entity
            for entity in subquery.entities
            if not is_generic(entity)
        ]

    return result


def _normalize_capabilities(
    result: QueryUnderstanding,
) -> QueryUnderstanding:
    """
    Deterministic guard for obvious capability/domain mappings.

    The LLM remains responsible for semantic understanding.
    This only prevents an obvious empty capability list.
    """

    capability_map = {
        "temple": "temple_info",
        "accommodation": "hotel_search",
        "restaurant": "restaurant_search",
        "transport": "transport_search",
        "travel": "route_search",
    }

    for subquery in result.subqueries:

        if not subquery.required_capabilities:

            capability = capability_map.get(
                subquery.domain
            )

            if capability:
                subquery.required_capabilities = [
                    capability
                ]

    result.required_capabilities = _merge_unique(
        result.required_capabilities,
        [
            capability
            for subquery in result.subqueries
            for capability in subquery.required_capabilities
        ],
    )

    return result


def normalize_understanding(
    result: QueryUnderstanding,
) -> QueryUnderstanding:

    result = _remove_generic_entities(result)

    result = _normalize_capabilities(result)

    if not result.subqueries:
        return result

    for subquery in result.subqueries:

        subquery.dates_times = _merge_unique(
            subquery.dates_times,
            result.dates_times,
        )

        subquery.locations = _merge_unique(
            subquery.locations,
            result.locations,
        )

        if (
            subquery.group is None
            and result.group is not None
        ):
            subquery.group = result.group

        existing_mentions = {
            (
                entity.mention,
                entity.entity_type,
            )
            for entity in subquery.entities
        }

        for entity in result.entities:

            key = (
                entity.mention,
                entity.entity_type,
            )

            if key not in existing_mentions:
                subquery.entities.append(entity)
                existing_mentions.add(key)

    return result


def _build_session_context(
    conversation_state: dict | None,
) -> dict:

    if not conversation_state:
        return {}

    state = conversation_state

    return {
        "session_id": state.get("session_id"),
        "previous_user_query": state.get(
            "user_query"
        ),
        "previous_intent": state.get(
            "intent"
        ),
        "resolved_entities": state.get(
            "resolved_entities",
            [],
        ),
        "last_execution_plan": state.get(
            "execution_plan",
            {},
        ),
    }


def understand_query(
    message: str,
    conversation_state: dict | None = None,
) -> QueryUnderstanding:

    payload = {
        "user_message": message,
        "previous_session_state": _build_session_context(
            conversation_state
        ),
    }

    response = client.chat.completions.create(
        model=settings.groq_model,
        messages=[
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": json.dumps(
                    payload,
                    ensure_ascii=False,
                ),
            },
        ],
        temperature=0,
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "pilgrim_query_understanding",
                "strict": True,
                "schema": _strict_schema(),
            },
        },
    )

    content = (
        response
        .choices[0]
        .message
        .content
    )

    if not content:
        raise RuntimeError(
            "Groq returned an empty query understanding response"
        )

    try:
        result = QueryUnderstanding.model_validate_json(
            content
        )
    except Exception as exc:
        raise RuntimeError(
            "Groq returned invalid QueryUnderstanding"
        ) from exc

    return normalize_understanding(result)