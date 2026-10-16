
from __future__ import annotations

from typing import Any

from app.travel.schemas import TravelPlanStep


def _items_to_dict(value: Any) -> dict[str, Any]:
    """Normalize a dictionary or a list of named plan items."""
    if isinstance(value, dict):
        return value

    result: dict[str, Any] = {}

    if isinstance(value, list):
        for item in value:
            if not isinstance(item, dict):
                continue

            name = item.get("name") or item.get("key")
            if name is not None:
                result[str(name)] = item.get("value")

    return result


def _first_value(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value is not None:
            return value
    return None


def _location_value(
    locations: Any,
    *roles: str,
) -> str | None:
    if not isinstance(locations, list):
        return None

    normalized_roles = {role.lower() for role in roles}

    for location in locations:
        if not isinstance(location, dict):
            continue

        role = str(location.get("role", "")).lower()
        mention = location.get("mention")

        if role in normalized_roles and mention:
            return str(mention)

    return None


def _entity_value(entity: Any) -> Any:
    if isinstance(entity, dict):
        return (
            entity.get("canonical_name")
            or entity.get("mention")
            or entity.get("canonical_id")
        )

    return entity


def project_plan_step(
    step: dict[str, Any],
    dependency_results: dict[str, Any] | None = None,
) -> TravelPlanStep:
    constraints = _items_to_dict(step.get("constraints"))
    preferences = _items_to_dict(step.get("preferences"))

    locations = step.get("locations") or []

    origin = _first_value(
        step, "origin", "from", "source", "start"
    ) or _location_value(
        locations, "origin", "from", "source", "start"
    )

    destination = _first_value(
        step, "destination", "to", "target", "end"
    ) or _location_value(
        locations, "destination", "to", "target", "end"
    )

    entities = step.get("entities") or []
    entity_values = [_entity_value(entity) for entity in entities]

    if destination is None and len(entity_values) >= 1:
        destination = entity_values[-1]

    if origin is None and len(entity_values) >= 2:
        origin = entity_values[0]

    mode = (
        step.get("mode")
        or preferences.get("transport_mode")
        or preferences.get("mode")
        or constraints.get("transport_mode")
        or "driving"
    )

    return TravelPlanStep(
        step_id=str(step.get("step_id", "travel")),
        question=str(
            step.get("question")
            or step.get("query")
            or step.get("user_query")
            or step.get("text")
            or ""
        ),
        intent=str(step.get("intent") or "travel"),
        capabilities=list(step.get("required_capabilities") or []),
        origin=origin,
        destination=destination,
        mode=str(mode),
        constraints=constraints,
        preferences=preferences,
        dependency_results=dependency_results or {},
    )
