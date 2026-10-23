from __future__ import annotations

import re

from app.models.understanding import (
    QueryEntity,
    QueryLocation,
    QueryPreference,
    QueryUnderstanding,
    SubQuery,
)


_TRAVEL_HINT = re.compile(
    r"\b(travel|go|route|drive|ride|bus|metro|"
    r"public transport|compare|cheapest|fastest|balanced)\b",
    re.IGNORECASE,
)
_PLACE_PATTERN = re.compile(
    r"\bfrom\s+(?P<origin>.+?)\s+to\s+(?P<destination>.+?)"
    r"(?=\s+(?:by|via|using|with|for)\b|[?.!,]|$)",
    re.IGNORECASE,
)
_BETWEEN_PATTERN = re.compile(
    r"\bbetween\s+(?P<origin>.+?)\s+and\s+(?P<destination>.+?)"
    r"(?=\s+(?:by|via|using|with|for)\b|[?.!,]|$)",
    re.IGNORECASE,
)


def _mode(message: str) -> str:
    lowered = message.lower()
    if re.search(r"\bmetro(?: rail)?\b|\bhmrl\b", lowered):
        return "metro"
    if re.search(r"\b(?:tgsrtc|rtc bus|bus)\b", lowered):
        return "bus"
    if re.search(r"\b(?:bike|bicycle|motorcycle)\b", lowered):
        return "bike"
    if re.search(r"\b(?:car|drive|driving)\b", lowered):
        return "car"
    return "driving"


def _preference(message: str) -> str | None:
    lowered = message.lower()
    for value in ("cheapest", "fastest", "balanced"):
        if re.search(rf"\b{value}\b", lowered):
            return value
    return None


def _extract_places(message: str) -> tuple[str, str] | None:
    for pattern in (_PLACE_PATTERN, _BETWEEN_PATTERN):
        match = pattern.search(message)
        if match:
            origin = match.group("origin").strip(" \t\n.,?!")
            destination = match.group("destination").strip(" \t\n.,?!")
            if origin and destination and origin.lower() != destination.lower():
                return origin, destination
    return None


def deterministic_travel_understanding(
    message: str,
) -> QueryUnderstanding | None:
    """Parse only explicit two-place travel requests without guessing."""
    if not _TRAVEL_HINT.search(message):
        return None

    places = _extract_places(message)
    if places is None:
        return None

    origin, destination = places
    mode = _mode(message)
    preference = _preference(message)
    capabilities = ["route_search"]
    if mode in {"bus", "metro"}:
        capabilities.append("transit")

    preferences = []
    if preference:
        preferences.append(
            QueryPreference(name="ranking", value=preference)
        )
    preferences.append(
        QueryPreference(name="transport_mode", value=mode)
    )

    entities = [
        QueryEntity(
            mention=origin,
            entity_type="location",
            role="origin",
        ),
        QueryEntity(
            mention=destination,
            entity_type="location",
            role="destination",
        ),
    ]
    locations = [
        QueryLocation(mention=origin, role="origin"),
        QueryLocation(mention=destination, role="destination"),
    ]
    subquery = SubQuery(
        query_id="travel-1",
        domain="travel",
        intent="route_search",
        question=message,
        entities=entities,
        preferences=preferences,
        locations=locations,
        required_capabilities=capabilities,
    )
    return QueryUnderstanding(
        intent="travel",
        confidence=1.0,
        language="en",
        entities=entities,
        preferences=preferences,
        locations=locations,
        required_capabilities=capabilities,
        subqueries=[subquery],
    )
