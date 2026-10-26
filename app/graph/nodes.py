from __future__ import annotations

from typing import Any

from app.graph.state import GraphState

from app.travel.calculations import calculate_travel
from app.travel.projection import project_plan_step
from app.travel.resolver import (
    TravelResolverClarificationError,
    TravelResolverError,
    resolve_place,
)
from app.travel.providers.route_chain import route_with_fallbacks


def initialize_graph(
    state: GraphState,
) -> GraphState:
    plan = state.get("execution_plan", {})
    steps = plan.get("steps", [])

    existing_results = dict(
        state.get("step_results", {})
    )

    if not steps:
        return {
            **state,
            "current_step_id": None,
            "current_step": None,
            "dependency_results": {},
            "step_results": existing_results,
            "status": "COMPLETED",
        }

    return {
        **state,
        "current_step_id": None,
        "current_step": None,
        "dependency_results": {},
        "step_results": existing_results,
        "status": "RUNNING",
        "error": None,
        "clarification_required": False,
        "clarification_question": None,
    }


def supervisor(
    state: GraphState,
) -> GraphState:
    plan = state.get("execution_plan", {})
    steps = plan.get("steps", [])
    results = state.get("step_results", {})

    completed_ids = {
        step_id
        for step_id, result in results.items()
        if result.get("status") == "COMPLETED"
    }

    if not steps:
        return {
            **state,
            "current_step_id": None,
            "current_step": None,
            "status": "COMPLETED",
        }

    for step in steps:
        step_id = step.get("step_id")

        if step_id in completed_ids:
            continue

        if step.get("status") == "BLOCKED":
            continue

        dependencies = step.get("depends_on", [])

        if not all(
            dependency in completed_ids
            for dependency in dependencies
        ):
            continue

        dependency_results = {
            dependency: results[dependency]
            for dependency in dependencies
            if dependency in results
        }

        return {
            **state,
            "current_step_id": step_id,
            "current_step": step,
            "dependency_results": dependency_results,
            "status": "RUNNING",
            "clarification_required": False,
            "clarification_question": None,
        }

    remaining_steps = [
        step
        for step in steps
        if step.get("step_id") not in completed_ids
    ]

    if not remaining_steps:
        return {
            **state,
            "current_step_id": None,
            "current_step": None,
            "dependency_results": {},
            "status": "COMPLETED",
        }

    return {
        **state,
        "current_step_id": None,
        "current_step": None,
        "status": "BLOCKED",
        "clarification_required": True,
        "clarification_question": (
            "Execution is blocked because "
            "required dependencies have not completed."
        ),
    }


def route_step(
    state: GraphState,
) -> str:
    current_step = state.get("current_step")

    if not current_step:
        if state.get("status") == "COMPLETED":
            return "finish"

        if state.get("status") == "BLOCKED":
            return "blocked"

        return "finish"

    if current_step.get("status") == "BLOCKED":
        return "blocked"

    domain = current_step.get("domain", "").lower()

    if domain == "temple":
        return "temple"

    if domain in {"accommodation", "hotel"}:
        return "accommodation"

    if domain in {"restaurant", "food"}:
        return "restaurant"

    if domain in {"travel", "transport"}:
        return "travel"

    return "unknown"


def temple_agent(
    state: GraphState,
) -> GraphState:
    from app.services.temple_vector_store import similarity_search

    current_step = state.get("current_step")
    current_step_id = state.get("current_step_id")

    if not current_step or not current_step_id:
        return {
            **state,
            "status": "ERROR",
            "error": (
                "No current temple step was selected "
                "by the supervisor."
            ),
        }

    query = _extract_step_query(current_step)

    if not query:
        return {
            **state,
            "status": "ERROR",
            "error": (
                "Temple step does not contain "
                "a searchable question."
            ),
        }

    expected_temple_ids = _extract_expected_temple_ids(
        current_step
    )

    try:
        documents = similarity_search(
            query,
            k=5,
            temple_ids=(
                sorted(expected_temple_ids)
                if expected_temple_ids
                else None
            ),
        )
    except Exception as exc:
        return {
            **state,
            "status": "ERROR",
            "error": (
                "Temple retrieval failed: "
                f"{type(exc).__name__}: {exc}"
            ),
        }

    retrieved_evidence = [
        _document_to_evidence(document)
        for document in documents
    ]

    validated_evidence = _validate_evidence(
        retrieved_evidence,
        expected_temple_ids,
    )

    results = dict(
        state.get("step_results", {})
    )

    results[current_step_id] = {
        "agent": "temple",
        "status": "COMPLETED",
        "stub": False,
        "query": query,
        "expected_temple_ids": sorted(
            expected_temple_ids
        ),
        "retrieved_evidence_count": len(
            retrieved_evidence
        ),
        "validated_evidence_count": len(
            validated_evidence
        ),
        "evidence": validated_evidence,
        "rejected_evidence": [
            evidence
            for evidence in retrieved_evidence
            if evidence not in validated_evidence
        ],
        "step": current_step,
        "dependency_results": dict(
            state.get("dependency_results", {})
        ),
    }

    return {
        **state,
        "step_results": results,
        "status": "RUNNING",
        "error": None,
    }


def accommodation_agent(
    state: GraphState,
) -> GraphState:
    return _complete_stub_step(
        state,
        "accommodation",
    )


def restaurant_agent(
    state: GraphState,
) -> GraphState:
    return _complete_stub_step(
        state,
        "restaurant",
    )


def travel_agent(
    state: GraphState,
) -> GraphState:
    current_step = state.get("current_step")
    current_step_id = state.get("current_step_id")

    if not current_step or not current_step_id:
        return {
            **state,
            "status": "ERROR",
            "error": (
                "No current travel step was selected "
                "by the supervisor."
            ),
        }

    dependency_results = dict(
        state.get("dependency_results", {})
    )

    try:
        travel_step = project_plan_step(
            current_step,
            dependency_results=dependency_results,
        )
    except Exception as exc:
        return {
            **state,
            "status": "ERROR",
            "error": (
                "Travel step projection failed: "
                f"{type(exc).__name__}: {exc}"
            ),
        }

    if travel_step.origin is None:
        return {
            **state,
            "status": "ERROR",
            "error": (
                "Travel step does not contain "
                "an origin."
            ),
        }

    if travel_step.destination is None:
        return {
            **state,
            "status": "ERROR",
            "error": (
                "Travel step does not contain "
                "a destination."
            ),
        }

    try:
        origin = resolve_place(
            travel_step.origin
        )
    except TravelResolverClarificationError as exc:
        results = dict(
            state.get("step_results", {})
        )

        results[current_step_id] = {
            "agent": "travel",
            "status": "NEEDS_CLARIFICATION",
            "stub": False,
            "step": current_step,
            "travel_step": travel_step.model_dump(
                mode="json"
            ),
            "clarification_question": (
                f"Which place do you mean by "
                f"'{exc.query}'?"
            ),
            "candidates": [
                candidate.model_dump(mode="json")
                for candidate in exc.candidates
            ],
            "dependency_results": dependency_results,
        }

        blocked_step = dict(current_step)
        blocked_step["status"] = "BLOCKED"

        return {
            **state,
            "current_step": blocked_step,
            "step_results": results,
            "status": "BLOCKED",
            "clarification_required": True,
            "clarification_question": (
                f"Which place do you mean by "
                f"'{exc.query}'?"
            ),
            "error": None,
        }
    except TravelResolverError as exc:
        return {
            **state,
            "status": "ERROR",
            "error": (
                f"Travel place resolution failed: {exc}"
            ),
        }
    except Exception as exc:
        return {
            **state,
            "status": "ERROR",
            "error": (
                "Travel place resolution failed: "
                f"{type(exc).__name__}: {exc}"
            ),
        }

    try:
        destination = resolve_place(
            travel_step.destination
        )
    except TravelResolverClarificationError as exc:
        results = dict(
            state.get("step_results", {})
        )

        results[current_step_id] = {
            "agent": "travel",
            "status": "NEEDS_CLARIFICATION",
            "stub": False,
            "step": current_step,
            "travel_step": travel_step.model_dump(
                mode="json"
            ),
            "clarification_question": (
                f"Which place do you mean by "
                f"'{exc.query}'?"
            ),
            "candidates": [
                candidate.model_dump(mode="json")
                for candidate in exc.candidates
            ],
            "dependency_results": dependency_results,
        }

        blocked_step = dict(current_step)
        blocked_step["status"] = "BLOCKED"

        return {
            **state,
            "current_step": blocked_step,
            "step_results": results,
            "status": "BLOCKED",
            "clarification_required": True,
            "clarification_question": (
                f"Which place do you mean by "
                f"'{exc.query}'?"
            ),
            "error": None,
        }
    except TravelResolverError as exc:
        return {
            **state,
            "status": "ERROR",
            "error": (
                f"Travel place resolution failed: {exc}"
            ),
        }
    except Exception as exc:
        return {
            **state,
            "status": "ERROR",
            "error": (
                "Travel place resolution failed: "
                f"{type(exc).__name__}: {exc}"
            ),
        }

    try:
        route_result = route_with_fallbacks(
            origin.coordinates,
            destination.coordinates,
            mode=travel_step.mode,
        )
    except Exception as exc:
        return {
            **state,
            "status": "ERROR",
            "error": (
                "Travel routing failed: "
                f"{type(exc).__name__}: {exc}"
            ),
        }

    calculations = []

    if route_result.status in {
        "COMPLETED",
        "PARTIAL",
    }:
        buffer_minutes = _extract_numeric_option(
            travel_step.constraints,
            travel_step.preferences,
            "buffer_minutes",
        )

        cost_per_km = _extract_numeric_option(
            travel_step.constraints,
            travel_step.preferences,
            "cost_per_km",
        )

        calculations = calculate_travel(
            route_result.facts,
            buffer_minutes=(
                buffer_minutes
                if buffer_minutes is not None
                else 0.0
            ),
            cost_per_km=cost_per_km,
        )

        route_result.calculations = calculations

    results = dict(
        state.get("step_results", {})
    )

    results[current_step_id] = {
        "agent": "travel",
        "status": "COMPLETED",
        "stub": False,
        "step": current_step,
        "travel_step": travel_step.model_dump(
            mode="json"
        ),
        "origin": origin.model_dump(
            mode="json"
        ),
        "destination": destination.model_dump(
            mode="json"
        ),
        "travel_result": route_result.model_dump(
            mode="json"
        ),
        "dependency_results": dependency_results,
    }

    return {
        **state,
        "step_results": results,
        "status": "RUNNING",
        "error": None,
        "clarification_required": False,
        "clarification_question": None,
    }


def unknown_agent(
    state: GraphState,
) -> GraphState:
    return {
        **state,
        "status": "ERROR",
        "error": (
            "No agent available for the "
            "current execution-plan domain."
        ),
    }


def _complete_stub_step(
    state: GraphState,
    agent: str,
) -> GraphState:
    current_step = state.get("current_step")
    current_step_id = state.get("current_step_id")

    if not current_step or not current_step_id:
        return {
            **state,
            "status": "ERROR",
            "error": (
                "No current step was selected "
                "by the supervisor."
            ),
        }

    results = dict(
        state.get("step_results", {})
    )

    dependency_results = dict(
        state.get("dependency_results", {})
    )

    results[current_step_id] = {
        "agent": agent,
        "status": "STUB",
        "stub": True,
        "message": (
            f"{agent} agent executed "
            f"step {current_step_id} "
            f"as a stub."
        ),
        "step": current_step,
        "dependency_results": dependency_results,
    }

    return {
        **state,
        "step_results": results,
        "status": "STUB",
    }


def _extract_numeric_option(
    constraints: dict[str, Any],
    preferences: dict[str, Any],
    key: str,
) -> float | None:
    for source in (constraints, preferences):
        value = source.get(key)

        if value is None:
            continue

        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            return None

        if numeric_value < 0:
            return None

        return numeric_value

    return None


def _extract_step_query(
    step: dict[str, Any],
) -> str | None:
    candidates = [
        step.get("question"),
        step.get("query"),
        step.get("user_query"),
        step.get("text"),
        step.get("description"),
        step.get("task"),
    ]

    for candidate in candidates:
        if isinstance(candidate, str):
            value = candidate.strip()

            if value:
                return value

    return None


def _extract_expected_temple_ids(
    step: dict[str, Any],
) -> set[str]:
    temple_ids: set[str] = set()

    direct_keys = {
        "temple_id",
        "entity_id",
    }

    list_keys = {
        "temple_ids",
        "entity_ids",
    }

    for key in direct_keys:
        value = step.get(key)

        if isinstance(value, str) and value.strip():
            temple_ids.add(value.strip())

    for key in list_keys:
        value = step.get(key)

        if isinstance(value, list):
            for item in value:
                if (
                    isinstance(item, str)
                    and item.strip()
                ):
                    temple_ids.add(
                        item.strip()
                    )

    entities = step.get("entities")

    if isinstance(entities, list):
        for entity in entities:
            if not isinstance(entity, dict):
                continue

            entity_id = entity.get(
                "entity_id",
                entity.get("canonical_id"),
            )

            if (
                isinstance(entity_id, str)
                and entity_id.strip()
                and (
                    str(
                        entity.get(
                            "entity_type",
                            entity.get(
                                "type",
                                "",
                            ),
                        )
                    ).lower().strip()
                    in {
                        "temple",
                        "person",
                        "festival",
                        "event",
                        "landmark",
                        "place",
                    }
                )
            ):
                temple_ids.add(
                    entity_id.strip()
                )

    return temple_ids


def _validate_evidence(
    evidence: list[dict[str, Any]],
    expected_temple_ids: set[str],
) -> list[dict[str, Any]]:
    if not expected_temple_ids:
        return evidence

    return [
        item
        for item in evidence
        if item.get("temple_id")
        in expected_temple_ids
    ]


def _document_to_evidence(
    document: Any,
) -> dict[str, Any]:
    metadata = dict(document.metadata)

    return {
        "text": document.page_content,
        "temple_id": metadata.get("temple_id"),
        "name": metadata.get("name"),
        "content_type": metadata.get("content_type"),
        "field_name": metadata.get("field_name"),
        "section": metadata.get("section"),
        "chunk_index": metadata.get("chunk_index"),
        "source_tier": metadata.get("source_tier"),
        "fetched_at": metadata.get("fetched_at"),
        "valid_to": metadata.get("valid_to"),
        "last_verified": metadata.get("last_verified"),
    }


def blocked_node(
    state: GraphState,
) -> GraphState:
    return {
        **state,
        "status": "BLOCKED",
    }


def finish_node(
    state: GraphState,
) -> GraphState:
    results = state.get("step_results", {})

    has_errors = any(
        result.get("status") == "ERROR"
        for result in results.values()
    )

    if has_errors:
        return {
            **state,
            "current_step_id": None,
            "current_step": None,
            "dependency_results": {},
            "status": "ERROR",
        }

    has_stubs = any(
        result.get("status") == "STUB"
        or result.get("stub") is True
        for result in results.values()
    )

    if has_stubs:
        return {
            **state,
            "current_step_id": None,
            "current_step": None,
            "dependency_results": {},
            "status": "STUB",
        }

    return {
        **state,
        "current_step_id": None,
        "current_step": None,
        "dependency_results": {},
        "status": "COMPLETED",
    }