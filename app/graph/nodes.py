from __future__ import annotations

import logging
import re
import time
from typing import Any

from app.graph.state import GraphState

from app.travel.calculations import (
    calculate_fuel_cost,
    calculate_travel,
)
from app.travel.projection import project_plan_step
from app.travel.resolver import (
    TravelResolverClarificationError,
    TravelResolverError,
    resolve_place,
)
from app.travel.providers.alternatives import (
    build_toll_status,
    get_osrm_alternative_routes,
)
from app.travel.providers.route_chain import route_with_fallbacks
from app.travel.options import normalize_option, rank_options
from app.travel.transit.agent import is_transit_request, plan_transit_journey

logger = logging.getLogger(__name__)


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
    logger.info(
        "graph_stage session_id=%s stage=supervisor status=%s current_step_id=%s completed_steps=%d",
        state.get("session_id"),
        state.get("status"),
        state.get("current_step_id"),
        sum(
            result.get("status") == "COMPLETED"
            for result in state.get("step_results", {}).values()
            if isinstance(result, dict)
        ),
    )

    # Stop immediately when an agent reports an error.
    # Otherwise, the supervisor may select the same step repeatedly.
    if state.get("status") == "ERROR":
        return {
            **state,
            "current_step_id": None,
            "current_step": None,
            "dependency_results": {},
            "clarification_required": False,
            "clarification_question": None,
            "status": "ERROR",
        }

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
            state.get("clarification_question")
            or (
                "Execution is blocked because "
                "required dependencies have not completed."
            )
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
    # Importing temple_vector_store constructs the BGE embedding model.
    # Log before the import so a slow model initialization is observable.
    import_started = time.perf_counter()
    logger.info(
        "graph_stage session_id=%s stage=temple_module_import event=start",
        state.get("session_id"),
    )
    try:
        from app.services.temple_vector_store import similarity_search
    except Exception:
        logger.exception(
            "graph_stage session_id=%s stage=temple_module_import event=error elapsed_ms=%.2f",
            state.get("session_id"),
            (time.perf_counter() - import_started) * 1000,
        )
        raise
    finally:
        logger.info(
            "graph_stage session_id=%s stage=temple_module_import event=end elapsed_ms=%.2f",
            state.get("session_id"),
            (time.perf_counter() - import_started) * 1000,
        )

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

    retrieval_started = time.perf_counter()
    logger.info(
        "graph_stage session_id=%s stage=temple_retrieval event=start step_id=%s",
        state.get("session_id"),
        current_step_id,
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
        logger.exception(
            "graph_stage session_id=%s stage=temple_retrieval event=error step_id=%s elapsed_ms=%.2f",
            state.get("session_id"),
            current_step_id,
            (time.perf_counter() - retrieval_started) * 1000,
        )
        return {
            **state,
            "status": "ERROR",
            "error": (
                "Temple retrieval failed: "
                f"{type(exc).__name__}: {exc}"
            ),
        }
    finally:
        logger.info(
            "graph_stage session_id=%s stage=temple_retrieval event=end step_id=%s elapsed_ms=%.2f",
            state.get("session_id"),
            current_step_id,
            (time.perf_counter() - retrieval_started) * 1000,
        )

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


def _request_text(step: Any) -> str:
            values = [
                step.question,
                step.mode,
                " ".join(str(value) for value in step.capabilities),
                str(step.preferences),
                str(step.constraints),
            ]
            return " ".join(values).lower()


def _is_multimodal_request(step: Any) -> bool:
            text = _request_text(step)
            explicit_single_mode = (
                any(term in text for term in ("bus only", "only bus"))
                or bool(re.search(r"\bbus\s+route(?:\s|$)", text))
                or any(term in text for term in ("car only", "only car"))
                or any(term in text for term in ("bike only", "only bike"))
            )
            comparison_terms = (
                "compare",
                "comparison",
                "all travel options",
                "best way",
                "cheapest",
                "fastest",
                "balanced",
                "bus, car",
                "car and bike",
                "public transport with",
            )
            return not explicit_single_mode and any(
                term in text for term in comparison_terms
            )


def _ranking_preference(step: Any) -> str | None:
            text = _request_text(step)
            for preference in ("cheapest", "fastest", "balanced"):
                if preference in text:
                    return preference
            return None


def _road_option(
            step: Any,
            mode: str,
            origin: Any,
            destination: Any,
) -> dict[str, Any]:
            try:
                result = route_with_fallbacks(
                    origin.coordinates,
                    destination.coordinates,
                    mode=mode,
                )
            except Exception as exc:
                return normalize_option(
                    mode=mode,
                    duration_minutes=None,
                    provider=None,
                    available=False,
                    reason=f"routing failed: {type(exc).__name__}",
                )

            facts = result.facts
            distance = next(
                (fact.value for fact in facts if fact.key == "distance"),
                None,
            )
            duration = next(
                (fact.value for fact in facts if fact.key == "duration"),
                None,
            )
            mileage = _extract_numeric_option(
                step.constraints,
                step.preferences,
                "mileage_km_per_litre",
            )
            fuel_price = _extract_numeric_option(
                step.constraints,
                step.preferences,
                "fuel_price_per_litre",
            )
            fuel = calculate_fuel_cost(
                facts,
                mileage_km_per_litre=mileage,
                fuel_price_per_litre=fuel_price,
                vehicle_type=mode,
            )
            option = normalize_option(
                mode=mode,
                duration_minutes=duration,
                estimated_cost_inr=(
                    fuel.value if fuel.status == "COMPLETED" else None
                ),
                provider=result.metadata.get("selected_provider"),
                available=result.status in {"COMPLETED", "PARTIAL"},
                reason=(
                    None
                    if result.status in {"COMPLETED", "PARTIAL"}
                    else "no valid road route was returned"
                ),
                distance_km=distance,
                estimated=True,
                scheduled=False,
                toll_status=result.metadata.get(
                    "toll_status",
                    {
                        "status": "UNAVAILABLE",
                        "message": (
                            "Toll charges are unavailable from the current "
                            "routing provider. This does not mean the route is toll-free."
                        ),
                    },
                ),
                fuel={
                    "status": fuel.status,
                    "cost_inr": fuel.value,
                    "inputs": fuel.inputs,
                    "message": (
                        None
                        if fuel.status == "COMPLETED"
                        else "Mileage and fuel price are required for a fuel estimate."
                    ),
                },
                profile_disclosure=(
                    "Standard driving profile; not motorcycle-specific."
                    if mode == "bike"
                    else None
                ),
                alternatives=result.metadata.get("alternative_routes", []),
            )
            return option


def _complete_multimodal_travel(
            state: GraphState,
            current_step: dict[str, Any],
            current_step_id: str,
            travel_step: Any,
            dependency_results: dict[str, Any],
) -> GraphState:
            results = dict(state.get("step_results", {}))
            try:
                origin = resolve_place(travel_step.origin)
                destination = resolve_place(travel_step.destination)
            except TravelResolverClarificationError as exc:
                return _travel_clarification_result(
                    state=state,
                    current_step=current_step,
                    current_step_id=current_step_id,
                    travel_step=travel_step,
                    dependency_results=dependency_results,
                    error=exc,
                )
            except Exception as exc:
                return {
                    **state,
                    "status": "ERROR",
                    "error": f"Travel place resolution failed: {exc}",
                }

            options = [
                _road_option(travel_step, "car", origin, destination),
                _road_option(travel_step, "bike", origin, destination),
            ]

            bus_step = travel_step.model_copy(
                update={
                    "mode": "bus",
                    "capabilities": ["transit"],
                    "question": f"{travel_step.question} TGSRTC bus",
                }
            )
            try:
                transit = plan_transit_journey(bus_step)
                journeys = transit.get("journeys", [])
                journey = journeys[0] if journeys else None
                bus_option = normalize_option(
                    mode="bus",
                    duration_minutes=(
                        journey.get("travel_minutes") if journey else None
                    ),
                    provider="tgsrtc",
                    available=bool(journey),
                    reason=(
                        None
                        if journey
                        else (
                            "No scheduled journey in the active feed. "
                            "Try https://www.redbus.in/ for broader bus search."
                        )
                    ),
                    scheduled=True,
                    attribution=transit.get("attribution"),
                    fare=transit.get("fare"),
                    service_date=transit.get("service_date"),
                    journeys=transit.get("journeys", []),
                )
            except Exception as exc:
                transit = {
                    "status": "UNAVAILABLE",
                    "attribution": "Contains data provided by TGSRTC",
                }
                bus_option = normalize_option(
                    mode="bus",
                    duration_minutes=None,
                    provider="tgsrtc",
                    available=False,
                    reason=f"scheduled bus planning failed: {type(exc).__name__}",
                    scheduled=True,
                    attribution=transit["attribution"],
                )
            options.insert(0, bus_option)

            preference = _ranking_preference(travel_step)
            ranking = (
                rank_options(options, preference)
                if preference
                else None
            )
            comparison = {
                "origin": origin.display_name,
                "destination": destination.display_name,
                "options": options,
                "ranking": ranking,
                "message": (
                    "Road times are provider estimates; bus times are scheduled GTFS "
                    "journeys. Fuel estimates exclude tolls and other costs."
                ),
            }
            results[current_step_id] = {
                "agent": "travel",
                "status": "COMPLETED",
                "stub": False,
                "step": current_step,
                "travel_step": travel_step.model_dump(mode="json"),
                "multimodal_result": comparison,
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


def _travel_clarification_result(
    *,
    state: GraphState,
    current_step: dict[str, Any],
    current_step_id: str,
    travel_step: Any,
    dependency_results: dict[str, Any],
    error: TravelResolverClarificationError,
) -> GraphState:
    results = dict(state.get("step_results", {}))
    execution_plan = dict(state.get("execution_plan", {}))
    plan_steps = []
    for step in execution_plan.get("steps", []):
        updated_step = dict(step)
        if updated_step.get("step_id") == current_step_id:
            updated_step["status"] = "BLOCKED"
        plan_steps.append(updated_step)
    execution_plan["steps"] = plan_steps
    execution_plan["status"] = "BLOCKED"
    execution_plan["requires_clarification"] = True
    execution_plan["clarification_question"] = (
        f"Which place do you mean by '{error.query}'?"
    )
    results[current_step_id] = {
        "agent": "travel",
        "status": "NEEDS_CLARIFICATION",
        "stub": False,
        "step": current_step,
        "travel_step": travel_step.model_dump(mode="json"),
        "clarification_question": (
            f"Which place do you mean by '{error.query}'?"
        ),
        "candidates": [
            candidate.model_dump(mode="json")
            for candidate in error.candidates
        ],
        "dependency_results": dependency_results,
    }
    blocked_step = dict(current_step)
    blocked_step["status"] = "BLOCKED"
    return {
        **state,
        "execution_plan": execution_plan,
        "current_step": blocked_step,
        "step_results": results,
        "status": "BLOCKED",
        "error": None,
        "clarification_required": True,
        "clarification_question": (
            f"Which place do you mean by '{error.query}'?"
        ),
    }


def travel_agent(
    state: GraphState,
) -> GraphState:
    current_step = state.get("current_step")
    current_step_id = state.get("current_step_id")
    if not current_step or not current_step_id:
        return {
            **state,
            "status": "ERROR",
            "error": "No current travel step was selected by the supervisor.",
        }

    dependency_results = dict(state.get("dependency_results", {}))
    try:
        travel_step = project_plan_step(
            current_step,
            dependency_results=dependency_results,
        )
    except Exception as exc:
        return {
            **state,
            "status": "ERROR",
            "error": f"Travel step projection failed: {type(exc).__name__}: {exc}",
        }

    if travel_step.origin is None or travel_step.destination is None:
        return {
            **state,
            "status": "ERROR",
            "error": "Travel step requires both an origin and destination.",
        }

    if _is_multimodal_request(travel_step):
        return _complete_multimodal_travel(
            state,
            current_step,
            current_step_id,
            travel_step,
            dependency_results,
        )

    if is_transit_request(travel_step):
        try:
            transit_result = plan_transit_journey(travel_step)
        except Exception as exc:
            return {
                **state,
                "status": "ERROR",
                "error": f"Transit journey planning failed: {type(exc).__name__}: {exc}",
            }
        result = {
            "agent": "travel",
            "status": "COMPLETED",
            "stub": False,
            "step": current_step,
            "travel_step": travel_step.model_dump(mode="json"),
            "transit_result": transit_result,
            "dependency_results": dependency_results,
        }
    else:
        try:
            origin = resolve_place(travel_step.origin)
            destination = resolve_place(travel_step.destination)
            route_result = route_with_fallbacks(
                origin.coordinates,
                destination.coordinates,
                mode=travel_step.mode,
            )
        except TravelResolverClarificationError as exc:
            return _travel_clarification_result(
                state=state,
                current_step=current_step,
                current_step_id=current_step_id,
                travel_step=travel_step,
                dependency_results=dependency_results,
                error=exc,
            )
        except Exception as exc:
            return {
                **state,
                "status": "ERROR",
                "error": f"Travel routing failed: {type(exc).__name__}: {exc}",
            }

        if route_result.status in {"COMPLETED", "PARTIAL"}:
            route_result.calculations = calculate_travel(
                route_result.facts,
                cost_per_km=_extract_numeric_option(
                    travel_step.constraints,
                    travel_step.preferences,
                    "cost_per_km",
                ),
            )
            if travel_step.mode.lower() in {
                "driving",
                "car",
                "bike",
                "motorcycle",
            }:
                route_result.calculations.append(
                    calculate_fuel_cost(
                        route_result.facts,
                        mileage_km_per_litre=_extract_numeric_option(
                            travel_step.constraints,
                            travel_step.preferences,
                            "mileage_km_per_litre",
                        ),
                        fuel_price_per_litre=_extract_numeric_option(
                            travel_step.constraints,
                            travel_step.preferences,
                            "fuel_price_per_litre",
                        ),
                        vehicle_type=travel_step.mode,
                    )
                )
            route_result.metadata = {
                **route_result.metadata,
                "profile_disclosure": (
                    "Bike routes use the provider's standard driving profile; "
                    "they are not motorcycle-specific."
                    if travel_step.mode.lower() in {"bike", "motorcycle"}
                    else None
                ),
                "toll_status": build_toll_status(
                    provider=route_result.metadata.get(
                        "selected_provider", "unknown"
                    )
                ),
            }
        result = {
            "agent": "travel",
            "status": "COMPLETED",
            "stub": False,
            "step": current_step,
            "travel_step": travel_step.model_dump(mode="json"),
            "origin": origin.model_dump(mode="json"),
            "destination": destination.model_dump(mode="json"),
            "travel_result": route_result.model_dump(mode="json"),
            "dependency_results": dependency_results,
        }

    results = dict(state.get("step_results", {}))
    results[current_step_id] = result
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
        "status": "COMPLETED",
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
        "status": "RUNNING",
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

    # Preserve errors that were returned without a step_results entry.
    if state.get("status") == "ERROR":
        return {
            **state,
            "current_step_id": None,
            "current_step": None,
            "dependency_results": {},
            "status": "ERROR",
        }

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