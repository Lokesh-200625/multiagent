from __future__ import annotations

from typing import Any

from app.travel.schemas import TravelPlanStep


def _first_value(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value is not None:
            return value
    return None


def project_plan_step(
    step: dict[str, Any],
    dependency_results: dict[str, Any] | None = None,
) -> TravelPlanStep:
    """
    Convert the generic Query Planner step into a travel-specific view.

    The Query Planner remains domain-agnostic.
    """

    constraints = step.get("constraints") or {}
    preferences = step.get("preferences") or {}

    origin = _first_value(
        step,
        "origin",
        "from",
        "source",
        "start",
    )

    destination = _first_value(
        step,
        "destination",
        "to",
        "target",
        "end",
    )

    entities = step.get("entities") or []

    if destination is None and len(entities) >= 1:
        destination = entities[-1]

    if origin is None and len(entities) >= 2:
        origin = entities[0]

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