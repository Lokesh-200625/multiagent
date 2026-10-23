import logging
import math
import re
import time
import uuid

from fastapi import APIRouter, HTTPException

from app.graph.workflow import workflow

from app.models.query import ChatRequest

from app.models.state import TripState

from app.services.intent import understand_query

from app.services.query_planner import (

    build_execution_plan,

    validate_execution_plan,

)

from app.services.query_resolution import resolve_understanding

from app.services.safety import detect_emergency, emergency_response

from app.services.session import get_session, save_session

logger = logging.getLogger(__name__)

router = APIRouter()


def _persist_session(session_id: str, state: TripState) -> bool:
    started = time.perf_counter()
    logger.info(
        "chat_stage session_id=%s stage=session_persistence event=start",
        session_id,
    )
    try:
        return save_session(session_id, state.model_dump())
    except Exception:
        logger.exception(
            "chat_stage session_id=%s stage=session_persistence event=error elapsed_ms=%.2f",
            session_id,
            (time.perf_counter() - started) * 1000,
        )
        raise
    finally:
        logger.info(
            "chat_stage session_id=%s stage=session_persistence event=end elapsed_ms=%.2f",
            session_id,
            (time.perf_counter() - started) * 1000,
        )


_TRANSIT_REQUEST_RE = re.compile(

    r"\b(metro|hmrl|tgsrtc|rtc bus|bus route|bus stop|public transport)\b",

    re.IGNORECASE,

)

def _allow_gtfs_transit_steps(execution_plan: dict) -> dict:

    """Let the GTFS transit agent resolve station names for explicit transit requests."""

    steps = execution_plan.get("steps", [])

    if not isinstance(steps, list):

        return execution_plan

    transit_steps = []

    for step in steps:

        if not isinstance(step, dict):

            continue

        question = str(step.get("question") or step.get("query") or "")

        mode = str(step.get("mode") or "").strip().lower()

        capabilities = step.get("capabilities") or []

        if isinstance(capabilities, str):

            capabilities = [capabilities]

        capability_text = " ".join(str(item) for item in capabilities)

        is_transit = (

            mode in {

                "metro",

                "bus",

                "transit",

                "public_transport",

                "public transport",

                "hmrl",

                "tgsrtc",

            }

            or bool(_TRANSIT_REQUEST_RE.search(question))

            or bool(_TRANSIT_REQUEST_RE.search(capability_text))

        )

        if is_transit:

            step["domain"] = "travel"

            step["status"] = "READY"

            transit_steps.append(step)

    if not transit_steps:

        return execution_plan

    blocked_steps = [

        step

        for step in steps

        if isinstance(step, dict)

        and str(step.get("status", "")).upper() == "BLOCKED"

    ]

    # Preserve clarification safeguards for blocked steps in other domains.

    if all(step in transit_steps for step in blocked_steps):

        execution_plan["requires_clarification"] = False

        execution_plan["clarification_question"] = None

        execution_plan["status"] = "READY"

    return execution_plan

def merge_execution_plans(

    previous_plan: dict,

    current_plan: dict,

) -> dict:

    previous_steps = previous_plan.get("steps", [])

    current_steps = current_plan.get("steps", [])

    current_by_id = {

        step.get("step_id"): step

        for step in current_steps

        if step.get("step_id")

    }

    merged_steps = []

    for step in previous_steps:

        step_id = step.get("step_id")

        if not step_id:

            continue

        if step_id in current_by_id:

            continue

        merged_steps.append(step)

    merged_steps.extend(current_steps)

    return {

        "intent": current_plan.get(

            "intent",

            previous_plan.get("intent", "unknown"),

        ),

        "steps": merged_steps,

        "requires_clarification": current_plan.get(

            "requires_clarification",

            False,

        ),

        "clarification_question": current_plan.get(

            "clarification_question"

        ),

        "status": current_plan.get("status", "READY"),

    }

def _get_completed_evidence(graph_result: dict) -> list[dict]:
    results = graph_result.get("step_results", {})

    if not isinstance(results, dict):
        return []

    evidence = []

    for result in results.values():
        if not isinstance(result, dict):
            continue

        if result.get("status") != "COMPLETED":
            continue

        # Collect evidence stored directly on the step result.
        result_evidence = result.get("evidence", [])
        if isinstance(result_evidence, list):
            evidence.extend(
                item for item in result_evidence
                if isinstance(item, dict)
            )

        # Road-routing evidence is nested inside travel_result.
        travel_result = result.get("travel_result", {})
        if isinstance(travel_result, dict):
            travel_evidence = travel_result.get("evidence", [])
            if isinstance(travel_evidence, list):
                evidence.extend(
                    item for item in travel_evidence
                    if isinstance(item, dict)
                )

        # Transit evidence may be nested in transit_result.
        transit_result = result.get("transit_result", {})
        if isinstance(transit_result, dict):
            transit_evidence = transit_result.get("evidence", [])
            if isinstance(transit_evidence, list):
                evidence.extend(
                    item for item in transit_evidence
                    if isinstance(item, dict)
                )

    return evidence


def _clean_text(text: str) -> str:

    text = str(text or "").strip()

    if not text:

        return ""

    text = re.sub(

        r"^(Introduction|Content|History|Summary|Overview):\s*",

        "",

        text,

        flags=re.IGNORECASE,

    )

    text = text.replace("\nHistory:", "").strip()

    text = re.sub(r"\s+", " ", text)

    return text.strip()

def _tokenize(text: str) -> set[str]:

    return {

        token

        for token in re.findall(

            r"[a-z0-9]+",

            str(text or "").lower(),

        )

        if len(token) > 2

    }

def _get_current_question(execution_plan: dict) -> str:

    steps = execution_plan.get("steps", [])

    if not isinstance(steps, list) or not steps:

        return ""

    current_step = steps[-1]

    if not isinstance(current_step, dict):

        return ""

    return str(

        current_step.get("question", "")

        or current_step.get("query", "")

        or ""

    )

def _has_any_term(text: str, terms: set[str]) -> bool:

    lowered = str(text or "").lower()

    return any(

        re.search(

            rf"\b{re.escape(term)}\b",

            lowered,

        )

        for term in terms

    )

def _evidence_relevance_score(

    evidence_item: dict,

    question: str,

) -> float:

    if not isinstance(evidence_item, dict):

        return float("-inf")

    question_text = str(question or "").lower()

    question_tokens = _tokenize(question_text)

    evidence_text = " ".join(

        str(evidence_item.get(field, ""))

        for field in (

            "text",

            "name",

            "content_type",

            "field_name",

            "section",

        )

    )

    evidence_tokens = _tokenize(evidence_text)

    if not question_tokens:

        return 0.0

    overlap = question_tokens & evidence_tokens

    score = float(min(len(overlap), 12) * 4)

    normalized_question = re.sub(

        r"\s+",

        " ",

        question_text,

    ).strip()

    normalized_evidence = re.sub(

        r"\s+",

        " ",

        evidence_text.lower(),

    ).strip()

    if (

        normalized_question

        and normalized_question in normalized_evidence

    ):

        score += 30

    timing_terms = {

        "timing",

        "timings",

        "time",

        "hours",

        "hour",

        "darshan",

        "opening",

        "opens",

        "close",

        "closes",

        "visiting",

        "visit",

    }

    if _has_any_term(

        question_text,

        timing_terms,

    ) and _has_any_term(

        evidence_text,

        timing_terms,

    ):

        score += 40

    festival_terms = {

        "festival",

        "festivals",

        "jathara",

        "jatara",

        "event",

        "events",

        "celebration",

        "celebrations",

    }

    if _has_any_term(

        question_text,

        festival_terms,

    ) and _has_any_term(

        evidence_text,

        festival_terms,

    ):

        score += 35

    access_terms = {

        "location",

        "located",

        "address",

        "reach",

        "reaching",

        "route",

        "directions",

        "how",

        "parking",

    }

    if _has_any_term(

        question_text,

        access_terms,

    ) and _has_any_term(

        evidence_text,

        access_terms,

    ):

        score += 30

    content_priority = {

        "overview": 5,

        "summary": 4,

        "religious_significance": 3,

        "spiritual_significance": 3,

        "history": 2,

        "sthalla_puranam": 2,

        "sthala_puranam": 2,

        "faq": 1,

        "audio": 0,

    }

    content_type = str(

        evidence_item.get("content_type", "")

    ).lower()

    score += content_priority.get(content_type, 0)

    return score

def _select_best_evidence(

    evidence: list[dict],

    question: str = "",

) -> dict | None:

    if not evidence:

        return None

    ranked = sorted(

        enumerate(evidence),

        key=lambda item: (

            _evidence_relevance_score(

                evidence_item=item[1],

                question=question,

            ),

            -item[0],

        ),

        reverse=True,

    )

    return ranked[0][1]

def _extract_answer_sentence(text: str) -> str:

    text = _clean_text(text)

    if not text:

        return ""

    sentences = re.split(r"(?<=[.!?])\s+", text)

    sentences = [

        sentence.strip()

        for sentence in sentences

        if sentence.strip()

    ]

    if not sentences:

        return text

    return sentences[0]


def _build_grounded_reply(
    evidence: list[dict],
    question: str = "",
) -> str:
    if not evidence:
        return (
            "I couldn't find enough verified evidence "
            "to answer that safely."
        )

    broad_query = bool(
        re.search(
            r"\b(tell me about|overview|describe|information about)\b",
            question,
            re.IGNORECASE,
        )
    )

    candidates = [
        item for item in evidence
        if isinstance(item, dict) and _clean_text(item.get("text", ""))
    ]

    if broad_query:
        preferred_types = {
            "summary": 5,
            "overview": 4,
            "religious_significance": 3,
            "spiritual_significance": 3,
            "history": 2,
        }

        preferred = [
            item for item in candidates
            if str(item.get("content_type", "")).lower()
            in preferred_types
        ]

        if preferred:
            candidates = preferred
            candidates.sort(
                key=lambda item: (
                    preferred_types.get(
                        str(item.get("content_type", "")).lower(), 0
                    ),
                    _evidence_relevance_score(item, question),
                ),
                reverse=True,
            )
        else:
            candidates.sort(
                key=lambda item: _evidence_relevance_score(item, question),
                reverse=True,
            )

        text = _clean_text(candidates[0].get("text", ""))
        sentences = re.split(r"(?<=[.!?])\s+", text)
        answer = " ".join(
            sentence.strip()
            for sentence in sentences[:3]
            if sentence.strip()
        )
        return answer or (
            "I found evidence, but couldn't extract a usable answer."
        )

    best_evidence = _select_best_evidence(
        evidence=candidates,
        question=question,
    )

    if not best_evidence:
        return (
            "I couldn't find enough verified evidence "
            "to answer that safely."
        )

    answer = _extract_answer_sentence(
        _clean_text(best_evidence.get("text", ""))
    )

    return answer or (
        "I found evidence, but couldn't extract a usable answer."
    )


def _format_location(value, fallback: str) -> str:

    if isinstance(value, dict):

        return str(

            value.get("name")

            or value.get("stop_name")

            or value.get("canonical_name")

            or value.get("mention")

            or fallback

        )

    return str(value) if value else fallback

def _format_leg_time(value) -> str:

    if value is None:

        return ""

    if isinstance(value, str):

        return value

    # Transit planner values may be seconds after midnight.

    if isinstance(value, (int, float)):
        total_seconds = int(value)

        hours = total_seconds // 3600

        minutes = (total_seconds % 3600) // 60

        seconds = total_seconds % 60

        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    return str(value)

def _build_transit_reply(graph_result: dict) -> str | None:

    """Format actual scheduled transit results from graph state."""

    step_results = graph_result.get("step_results", {})

    if not isinstance(step_results, dict):

        return None

    transit_result = None

    for result in reversed(list(step_results.values())):

        if not isinstance(result, dict):

            continue

        candidate = result.get("transit_result")

        if isinstance(candidate, dict):

            transit_result = candidate

            break

    if transit_result is None:

        return None

    origin_name = _format_location(

        transit_result.get("origin"),

        "your origin",

    )

    destination_name = _format_location(

        transit_result.get("destination"),

        "your destination",

    )

    service_date = transit_result.get(

        "service_date",

        "not specified",

    )

    mode = transit_result.get("mode", "transit")

    journeys = transit_result.get("journeys", [])

    if not isinstance(journeys, list) or not journeys:
        return (

            f"No scheduled {mode} journeys were found from "

            f"{origin_name} to {destination_name} for {service_date}. "

            "Try another date or departure time. "

            "This result reflects the available schedule data, "
            "not live service availability. "
            "The active feed may not cover this journey; try "
            "https://www.redbus.in/ for broader bus-search options."
        )

    lines = [

        f"Scheduled journeys: {origin_name} → {destination_name}",

        f"Date: {service_date}",

        f"Transport: {mode}",

        "",

    ]

    displayed_count = 0

    for journey in journeys:

        if not isinstance(journey, dict):

            continue

        displayed_count += 1

        if displayed_count > 5:

            break

        departure = _format_leg_time(

            journey.get("departure_time")

        ) or "unknown"

        arrival = _format_leg_time(

            journey.get("arrival_time")

        ) or "unknown"

        duration = journey.get(

            "travel_minutes",

            journey.get("duration_minutes"),

        )

        lines.append(

            f"{displayed_count}. Depart: {departure} | "

            f"Arrive: {arrival}"

        )

        if duration is not None:

            try:

                lines.append(

                    f"   Total journey time: "

                    f"{float(duration):.1f} minutes"

                )

            except (TypeError, ValueError):

                lines.append(

                    f"   Total journey time: {duration} minutes"

                )

        legs = journey.get("legs", [])

        if isinstance(legs, list):

            for leg_index, leg in enumerate(legs, start=1):

                if not isinstance(leg, dict):

                    continue

                route = (

                    leg.get("route_short_name")

                    or leg.get("route_name")

                    or leg.get("route_id")

                    or leg.get("route")

                    or "Transit service"

                )

                from_stop = (

                    leg.get("origin_stop_name")

                    or leg.get("from_stop_name")

                    or leg.get("origin")

                    or leg.get("from_stop")

                    or "Starting stop"

                )

                to_stop = (

                    leg.get("destination_stop_name")

                    or leg.get("to_stop_name")

                    or leg.get("destination")

                    or leg.get("to_stop")

                    or "Ending stop"

                )

                leg_departure = _format_leg_time(

                    leg.get("departure_time")

                )

                leg_arrival = _format_leg_time(

                    leg.get("arrival_time")

                )

                # Support GTFS-style field names used by the

                # current journey planner.

                if not leg_departure:

                    leg_departure = _format_leg_time(

                        leg.get("departure_seconds")

                    )

                if not leg_arrival:

                    leg_arrival = _format_leg_time(

                        leg.get("arrival_seconds")

                    )

                headsign = leg.get("headsign")

                detail = (

                    f"   Leg {leg_index}: {route} | "

                    f"{from_stop} → {to_stop}"

                )

                if leg_departure or leg_arrival:

                    detail += (

                        f" | {leg_departure or '?'}"

                        f"–{leg_arrival or '?'}"

                    )

                if headsign:

                    detail += f" | Direction: {headsign}"

                lines.append(detail)

        if displayed_count < min(len(journeys), 5):

            lines.append("")

    limitations = transit_result.get("limitations")

    if limitations:

        lines.extend(["", f"Note: {limitations}"])

    else:

        lines.extend([

            "",

            "Note: These are scheduled journeys. "

            "Live delays and cancellations may not be reflected.",
        ])

    attribution = transit_result.get("attribution")
    if attribution:
        lines.extend(["", f"Source: {attribution}."])

    fare = transit_result.get("fare")
    if isinstance(fare, dict) and fare.get("status") != "AVAILABLE":
        lines.extend([
            "",
            str(
                fare.get(
                    "message",
                    "Fare data is unavailable; this does not mean free travel.",
                )
            ),
        ])

    return "\n".join(lines)


def _build_road_route_reply(graph_result: dict) -> str | None:
    """Build a user-facing reply from a road-route result."""
    results = graph_result.get("step_results", {})
    if not isinstance(results, dict):
        return None

    for result in results.values():
        if not isinstance(result, dict):
            continue

        travel_result = result.get("travel_result")
        if not isinstance(travel_result, dict):
            continue

        route_status = travel_result.get("status")
        if route_status not in {"COMPLETED", "PARTIAL"}:
            continue

        facts = travel_result.get("facts", [])
        if not isinstance(facts, list):
            continue

        distance = next(
            (
                fact for fact in facts
                if isinstance(fact, dict)
                and fact.get("key") == "distance"
            ),
            None,
        )
        duration = next(
            (
                fact for fact in facts
                if isinstance(fact, dict)
                and fact.get("key") == "duration"
            ),
            None,
        )

        if distance is None:
            continue

        try:
            distance_value = float(distance.get("value"))
        except (TypeError, ValueError):
            continue

        distance_unit = distance.get("unit") or "km"
        lines = [
            f"Driving route distance: {distance_value:g} {distance_unit}."
        ]

        if duration is not None:
            try:
                minutes = float(duration.get("value"))
                if minutes >= 0:
                    if minutes >= 60:
                        hours = int(minutes // 60)
                        remaining = round(minutes % 60)
                        if remaining == 60:
                            hours += 1
                            remaining = 0

                        duration_text = f"{hours} hr"
                        if remaining:
                            duration_text += f" {remaining} min"
                    else:
                        duration_text = f"{round(minutes)} min"

                    lines.append(
                        f"Estimated driving time: {duration_text}."
                    )
            except (TypeError, ValueError):
                pass

        metadata = travel_result.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}

        toll_status = metadata.get("toll_status", {})
        if isinstance(toll_status, dict):
            toll_message = toll_status.get("message")
            if toll_message:
                lines.append(str(toll_message))

        calculations = travel_result.get("calculations", [])
        if isinstance(calculations, list):
            for calculation in calculations:
                if not isinstance(calculation, dict):
                    continue
                if calculation.get("key") != "fuel_cost":
                    continue
                if calculation.get("status") == "COMPLETED":
                    inputs = calculation.get("inputs", {})
                    lines.append(
                        "Estimated fuel cost: "
                        f"INR {float(calculation['value']):.2f} "
                        f"(based on {inputs.get('mileage_km_per_litre')} "
                        "km/litre and "
                        f"INR {inputs.get('fuel_price_per_litre')}/litre; "
                        "fuel only)."
                    )
                else:
                    lines.append(
                        "Fuel cost unavailable: provide vehicle mileage "
                        "and fuel price per litre for an estimate."
                    )

        provider = metadata.get("selected_provider")
        if provider:
            lines.append(f"Routing provider: {str(provider).upper()}.")

        profile_disclosure = metadata.get("profile_disclosure")
        if profile_disclosure:
            lines.append(f"Note: {profile_disclosure}")

        is_approximate = (
            route_status == "PARTIAL"
            or metadata.get("approximate")
            or metadata.get("distance_type") == "straight_line"
            or distance.get("approximate")
        )
        alternatives = metadata.get("alternative_routes", [])
        if isinstance(alternatives, list):
            valid_alternatives = []

            for route in alternatives:
                if not isinstance(route, dict):
                    continue

                try:
                    route_km = float(route["distance_km"])
                    route_minutes = float(route["duration_minutes"])
                except (KeyError, TypeError, ValueError):
                    continue

                if (
                    route_km < 0
                    or route_minutes < 0
                    or not math.isfinite(route_km)
                    or not math.isfinite(route_minutes)
                ):
                    continue

                valid_alternatives.append(
                    (route, route_km, route_minutes)
                )

            if valid_alternatives:
                lines.append("")
                lines.append("Available alternative driving routes:")

                for route, route_km, route_minutes in valid_alternatives:
                    route_number = route.get("route_number", "?")
                    lines.append(
                        f"Route {route_number}: approximately "
                        f"{route_km:g} km, "
                        f"{round(route_minutes)} min "
                        f"(OSRM estimate)."
                    )

                lines.append(
                    "Alternative routes are provider-dependent; "
                    "availability does not guarantee the fastest or cheapest route."
                )

        if is_approximate:
            lines.append(
                "Note: This is an approximate straight-line distance, "
                "not a verified driving route."
            )

        errors = travel_result.get("errors", [])
        if isinstance(errors, list) and errors:
            failed_providers = sorted({
                str(error.get("provider"))
                for error in errors
                if isinstance(error, dict) and error.get("provider")
            })
            if failed_providers:
                lines.append(
                    "Note: Routing fallback was used after "
                    + ", ".join(failed_providers)
                    + " failed."
                )

        return "\n".join(lines)

    return None


def _build_multimodal_reply(graph_result: dict) -> str | None:
    results = graph_result.get("step_results", {})
    if not isinstance(results, dict):
        return None

    comparison = None
    for result in reversed(list(results.values())):
        if isinstance(result, dict):
            candidate = result.get("multimodal_result")
            if isinstance(candidate, dict):
                comparison = candidate
                break

    if comparison is None:
        return None

    lines = [
        (
            f"Travel options: {comparison.get('origin', 'origin')} "
            f"→ {comparison.get('destination', 'destination')}"
        ),
        "",
    ]

    for option in comparison.get("options", []):
        if not isinstance(option, dict):
            continue
        mode = str(option.get("mode", "travel")).title()
        if option.get("available") is not True:
            lines.append(
                f"{mode}: unavailable"
                + (
                    f" ({option['reason']})"
                    if option.get("reason")
                    else "."
                )
            )
            continue

        duration = option.get("duration_minutes")
        detail = f"{float(duration):.1f} minutes" if duration is not None else "duration unavailable"
        provider = option.get("provider") or "provider unavailable"
        lines.append(f"{mode}: {detail} ({provider}).")

        if option.get("distance_km") is not None:
            lines.append(
                f"  Distance: approximately {float(option['distance_km']):g} km."
            )

        if option.get("scheduled"):
            journeys = option.get("journeys") or []
            if journeys:
                journey = journeys[0]
                lines.append(
                    f"  Scheduled departure: {journey.get('departure_time', 'unknown')}; "
                    f"arrival: {journey.get('arrival_time', 'unknown')}."
                )
            lines.append("  Scheduled GTFS data; not live tracking.")
            if option.get("attribution"):
                lines.append(f"  Source: {option['attribution']}.")
            fare = option.get("fare")
            if isinstance(fare, dict) and fare.get("status") != "AVAILABLE":
                lines.append(
                    "  Fare unavailable; this does not mean free travel."
                )
        else:
            fuel = option.get("fuel")
            if isinstance(fuel, dict) and fuel.get("status") == "COMPLETED":
                inputs = fuel.get("inputs", {})
                lines.append(
                    f"  Fuel estimate: INR {float(fuel['cost_inr']):.2f} "
                    f"({inputs.get('mileage_km_per_litre')} km/litre, "
                    f"INR {inputs.get('fuel_price_per_litre')}/litre)."
                )
            else:
                lines.append(
                    "  Fuel estimate unavailable: mileage and fuel price "
                    "assumptions were not supplied."
                )
            lines.append(
                "  Toll charges are unavailable from the current routing "
                "provider. This does not mean the route is toll-free."
            )
            if option.get("profile_disclosure"):
                lines.append(f"  Note: {option['profile_disclosure']}")

    ranking = comparison.get("ranking")
    if isinstance(ranking, dict):
        lines.append("")
        preference = str(ranking.get("preference", "")).title()
        selected = ranking.get("selected")
        if ranking.get("status") == "COMPLETED" and selected:
            lines.append(
                f"{preference} option based on available evidence: "
                f"{str(selected.get('mode', 'travel')).title()}."
            )
        else:
            lines.append(
                f"{preference} comparison unavailable: "
                f"{ranking.get('reason', 'insufficient comparable data')}."
            )

    message = comparison.get("message")
    if message:
        lines.extend(["", f"Note: {message}"])

    return "\n".join(lines)


def _build_reply(

    execution_plan: dict,

    graph_result: dict,

) -> str:

    if execution_plan.get("requires_clarification", False):

        return (

            execution_plan.get("clarification_question")

            or "I need a little more information."

        )

    status = graph_result.get(

        "status",

        execution_plan.get("status", "READY"),

    )

    if status == "FAILED":

        return "I couldn't complete the request safely."

    if status == "ERROR":

        error = graph_result.get("error")

        if error:

            return f"I couldn't complete the request: {error}"

        return (

            "I couldn't complete the request "

            "because an agent encountered an error."

        )

    if status == "LLM_UNAVAILABLE":

        return (

            "The query understanding service is "

            "temporarily unavailable. Please try again."

        )

    multimodal_reply = _build_multimodal_reply(graph_result)
    if multimodal_reply is not None:
        return multimodal_reply

    transit_reply = _build_transit_reply(graph_result)

    if transit_reply is not None:

        return transit_reply

    road_route_reply = _build_road_route_reply(graph_result)
    if road_route_reply is not None:
        return road_route_reply

    evidence = _get_completed_evidence(graph_result)

    if evidence:

        question = _get_current_question(execution_plan)

        return _build_grounded_reply(

            evidence=evidence,

            question=question,

        )

    if status == "BLOCKED":

        return (

            "I couldn't execute the request because a required "

            "step is blocked. Please clarify the location or "

            "requirement and try again."

        )

    return (

        "I couldn't find enough verified evidence "

        "to answer that safely."

    )

@router.post("/chat")

def chat(request: ChatRequest):
    request_started = time.perf_counter()

    if not request.message.strip():

        raise HTTPException(

            status_code=400,

            detail="Message cannot be empty.",

        )

    session_id = request.session_id or str(uuid.uuid4())

    session_started = time.perf_counter()
    logger.info(
        "chat_stage session_id=%s stage=session_retrieval event=start",
        session_id,
    )
    existing_state = get_session(session_id)
    logger.info(
        "chat_stage session_id=%s stage=session_retrieval event=end elapsed_ms=%.2f found=%s",
        session_id,
        (time.perf_counter() - session_started) * 1000,
        bool(existing_state),
    )

    if existing_state:

        state = TripState(**existing_state)

    else:

        state = TripState(session_id=session_id)

    state.user_query = request.message

    # SAFETY GATE

    if detect_emergency(request.message):

        response = emergency_response()

        state.intent = "emergency"

        state.intent_confidence = 1.0

        state.validation = {

            "status": "EMERGENCY",

            "llm_called": False,

        }

        _persist_session(session_id, state)

        return {

            "session_id": session_id,

            "intent": "emergency",

            "reply": response["reply"],

            "clarification": None,

            "status": "EMERGENCY",

        }

    # SESSION CONTEXT

    # GREETING HANDLER
    normalized_message = re.sub(
        r"[,.!?]+",
        " ",
        request.message.strip().lower(),
    )
    normalized_message = " ".join(normalized_message.split())

    greeting_phrases = {
        "hi",
        "hello",
        "hey",
        "hi there",
        "hello there",
        "hey there",
        "how are you",
        "hi how are you",
        "hello how are you",
        "hey how are you",
        "good morning",
        "good afternoon",
        "good evening",
    }

    if normalized_message in greeting_phrases:
        state.intent = "greeting"
        state.intent_confidence = 1.0
        state.validation = {
            "status": "COMPLETED",
            "llm_called": False,
        }

        _persist_session(session_id, state)

        return {
            "session_id": session_id,
            "intent": "greeting",
            "reply": (
                "Hi! I'm doing well, thanks for asking. "
                "How can I help you plan your pilgrimage?"
            ),
            "clarification": None,
            "status": "COMPLETED",
            "evidence": [],
        }


    previous_state = state.model_dump()

    previous_execution_plan = state.execution_plan or {}

    previous_step_results = previous_execution_plan.get(

        "step_results",

        {},

    )

    # QUERY UNDERSTANDING
    stage_started = time.perf_counter()
    logger.info(
        "chat_stage session_id=%s stage=query_understanding event=start",
        session_id,
    )
    try:
        understanding = understand_query(

            message=request.message,

            conversation_state={

                **previous_state,

                "previous_execution_plan": previous_execution_plan,

                "previous_step_results": previous_step_results,

            },
        )
    except RuntimeError as exc:
        logger.exception(
            "chat_stage session_id=%s stage=query_understanding event=error elapsed_ms=%.2f",
            session_id,
            (time.perf_counter() - stage_started) * 1000,
        )

        if str(exc).startswith("LLM_UNAVAILABLE:"):

            state.validation = {

                "status": "LLM_UNAVAILABLE",

                "llm_called": True,

                "error": str(exc),

            }

            _persist_session(session_id, state)

            raise HTTPException(

                status_code=503,

                detail=(

                    "Query understanding service "

                    "is temporarily unavailable."

                ),

            ) from exc

        raise
    finally:
        logger.info(
            "chat_stage session_id=%s stage=query_understanding event=end elapsed_ms=%.2f",
            session_id,
            (time.perf_counter() - stage_started) * 1000,
        )

    state.intent = understanding.intent

    state.intent_confidence = understanding.confidence

    state.understanding = understanding.model_dump()

    state.entities = {

        entity.mention: entity.model_dump()

        for entity in understanding.entities

    }

    state.constraints = {

        constraint.name: constraint.model_dump()

        for constraint in understanding.constraints

    }

    state.preferences = {

        preference.name: preference.model_dump()

        for preference in understanding.preferences

    }

    state.dates_times = [

        item.model_dump()

        for item in understanding.dates_times

    ]

    state.locations = [

        item.model_dump()

        for item in understanding.locations

    ]

    state.group = (

        understanding.group.model_dump()

        if understanding.group

        else None

    )

    state.required_capabilities = (

        understanding.required_capabilities

    )

    # ENTITY RESOLUTION
    stage_started = time.perf_counter()
    logger.info(
        "chat_stage session_id=%s stage=entity_resolution event=start",
        session_id,
    )
    try:
        resolved_query = resolve_understanding(
            understanding=understanding,
            session_context={
                "previous_steps": previous_execution_plan.get(
                    "steps",
                    [],
                ),
            },
        )
    except Exception:
        logger.exception(
            "chat_stage session_id=%s stage=entity_resolution event=error elapsed_ms=%.2f",
            session_id,
            (time.perf_counter() - stage_started) * 1000,
        )
        raise
    finally:
        logger.info(
            "chat_stage session_id=%s stage=entity_resolution event=end elapsed_ms=%.2f",
            session_id,
            (time.perf_counter() - stage_started) * 1000,
        )

    state.resolved_query = resolved_query.model_dump()

    state.resolved_entities = [

        entity

        for entity in resolved_query.entities

        if entity.status == "RESOLVED"

    ]

    state.unresolved_entities = resolved_query.unresolved_mentions
    logger.info(
        "chat_timing session_id=%s stage=entity_resolution elapsed_ms=%.2f",
        session_id,
        (time.perf_counter() - stage_started) * 1000,
    )

    # EXECUTION PLAN
    stage_started = time.perf_counter()
    logger.info(
        "chat_stage session_id=%s stage=execution_planning event=start",
        session_id,
    )
    try:
        current_execution_plan = build_execution_plan(resolved_query)

        current_plan_dict = current_execution_plan.model_dump()

        if previous_execution_plan.get("steps"):

            execution_plan_dict = merge_execution_plans(

                previous_plan=previous_execution_plan,

                current_plan=current_plan_dict,

            )

        else:

            execution_plan_dict = current_plan_dict

        # Run normal validation first. Then allow explicit transit steps to

        # resolve their station names through GTFS instead of the general registry.

        execution_plan_dict = validate_execution_plan(

            execution_plan_dict

        )

        execution_plan_dict = _allow_gtfs_transit_steps(

            execution_plan_dict

        )

        execution_plan_dict["step_results"] = previous_step_results
    except Exception:
        logger.exception(
            "chat_stage session_id=%s stage=execution_planning event=error elapsed_ms=%.2f",
            session_id,
            (time.perf_counter() - stage_started) * 1000,
        )
        raise
    finally:
        logger.info(
            "chat_stage session_id=%s stage=execution_planning event=end elapsed_ms=%.2f",
            session_id,
            (time.perf_counter() - stage_started) * 1000,
        )

    state.execution_plan = execution_plan_dict
    logger.info(
        "chat_timing session_id=%s stage=execution_planning elapsed_ms=%.2f steps=%d requires_clarification=%s",
        session_id,
        (time.perf_counter() - stage_started) * 1000,
        len(execution_plan_dict.get("steps", [])),
        execution_plan_dict.get("requires_clarification", False),
    )

    # LANGGRAPH

    graph_input = {

        "session_id": session_id,

        "execution_plan": execution_plan_dict,

        "current_step_id": None,

        "step_results": previous_step_results,

        "status": "PENDING",

        "error": None,

        "clarification_required": execution_plan_dict.get(

            "requires_clarification",

            False,

        ),

        "clarification_question": execution_plan_dict.get(

            "clarification_question"

        ),

    }

    stage_started = time.perf_counter()
    logger.info(
        "chat_stage session_id=%s stage=graph_execution event=start",
        session_id,
    )
    try:
        graph_result = workflow.invoke(graph_input)
    except Exception:
        logger.exception(
            "chat_stage session_id=%s stage=graph_execution event=error elapsed_ms=%.2f",
            session_id,
            (time.perf_counter() - stage_started) * 1000,
        )
        raise
    finally:
        logger.info(
            "chat_stage session_id=%s stage=graph_execution event=end elapsed_ms=%.2f",
            session_id,
            (time.perf_counter() - stage_started) * 1000,
        )

    # PERSIST FULL INTERNAL STATE

    state.execution_plan = graph_result.get(

        "execution_plan",

        execution_plan_dict,

    )

    state.execution_plan["step_results"] = graph_result.get(

        "step_results",

        previous_step_results,

    )

    state.last_agent = None

    state.validation = {

        "graph_status": graph_result.get("status"),

        "graph_error": graph_result.get("error"),

    }

    _persist_session(session_id, state)

    # PUBLIC API RESPONSE

    status = graph_result.get(

        "status",

        execution_plan_dict.get("status", "READY"),

    )

    evidence = _get_completed_evidence(graph_result)
    logger.info(
        "chat_timing session_id=%s stage=request_total_before_response elapsed_ms=%.2f intent=%s status=%s evidence_count=%d",
        session_id,
        (time.perf_counter() - request_started) * 1000,
        state.intent,
        status,
        len(evidence),
    )
    return {

        "session_id": session_id,

        "intent": state.intent,

        "reply": _build_reply(

            execution_plan=execution_plan_dict,

            graph_result=graph_result,

        ),

        "clarification": (

            execution_plan_dict.get("clarification_question")

            if execution_plan_dict.get(

                "requires_clarification",

                False,

            )

            else None

        ),

        "status": status,

        "evidence": evidence,

    }
