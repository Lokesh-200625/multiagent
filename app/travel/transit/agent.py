
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.travel.transit import query_service
from app.travel.transit.journey_options import find_best_transfer_options
from app.travel.transit.journey_planner import find_direct_journeys
from app.travel.transit.providers import get_provider


def _normalise(value: Any) -> str:
    return str(value or "").strip().lower()


def _stop_id(stop: dict[str, Any]) -> str | None:
    return stop.get("stop_id") or stop.get("id")


def _stop_name(stop: dict[str, Any]) -> str:
    return str(stop.get("stop_name") or stop.get("name") or "")


def _transit_query_alias(query: str) -> str:
    normalized = re.sub(r"\s+", " ", query).strip()
    if re.fullmatch(
        r"(?:hyderabad\s+)?(?:mahatma gandhi bus station|mgbus|mgbs)",
        normalized,
        re.IGNORECASE,
    ):
        return "MGBS"
    return normalized


def _select_stop(query: str, stops: list[dict[str, Any]]) -> dict[str, Any]:
    wanted = _normalise(query)

    if not wanted:
        raise ValueError("A transit station name is required.")

    ranked = []

    for stop in stops:
        name = _normalise(_stop_name(stop))
        if not name:
            continue

        stop_identifiers = {
            _normalise(stop.get("stop_id")),
            _normalise(stop.get("stop_code")),
        }

        name_tokens = set(re.findall(r"[a-z0-9]+", name))
        wanted_tokens = set(re.findall(r"[a-z0-9]+", wanted))

        if name == wanted or wanted in stop_identifiers:
            score = 0
        elif wanted_tokens and wanted_tokens.issubset(name_tokens):
            score = 1
        else:
            continue

        ranked.append((score, len(name), stop))

    if not ranked:
        raise ValueError(
            f"No transit stop matched '{query}'. "
            "Try a nearby station or a more specific stop name."
        )

    ranked.sort(
        key=lambda item: (
            item[0],
            0 if item[2].get("location_type") == 1 else 1,
            0 if not item[2].get("parent_station") else 1,
            item[1],
        )
    )

    best_score = ranked[0][0]
    best_matches = [item[2] for item in ranked if item[0] == best_score]

    distinct_names = {_normalise(_stop_name(item)) for item in best_matches}
    if len(distinct_names) > 1:
        names = sorted(_stop_name(item) for item in best_matches[:5])
        raise ValueError(
            f"'{query}' matches multiple transit stops: "
            + ", ".join(names)
            + ". Please specify the station."
        )

    selected = ranked[0][2]
    parent = selected.get("parent_station")

    if parent:
        return {
            **selected,
            "stop_id": str(parent),
            "stop_name": _stop_name(selected),
        }

    stop_id = _stop_id(selected)
    if not stop_id:
        raise ValueError(f"Matched stop '{query}' has no stop ID.")

    return {**selected, "stop_id": str(stop_id)}


def _service_date(step: dict[str, Any]) -> date:
    for item in step.get("dates_times") or []:
        if not isinstance(item, dict):
            continue

        raw = str(item.get("raw") or "").strip()

        match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", raw)
        if match:
            try:
                return date.fromisoformat(match.group(1))
            except ValueError:
                pass

        relative = raw.lower()
        if "tomorrow" in relative:
            return datetime.now(ZoneInfo("Asia/Kolkata")).date() + timedelta(days=1)
        if "today" in relative:
            return datetime.now(ZoneInfo("Asia/Kolkata")).date()

    question = str(step.get("question") or "")
    match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", question)

    if match:
        try:
            return date.fromisoformat(match.group(1))
        except ValueError:
            pass

    lowered = question.lower()
    if "tomorrow" in lowered:
        return datetime.now(ZoneInfo("Asia/Kolkata")).date() + timedelta(days=1)

    return datetime.now(ZoneInfo("Asia/Kolkata")).date()


def _provider_and_mode(step: Any) -> tuple[str, str]:
    mode = _normalise(step.mode)
    capabilities = {_normalise(value) for value in step.capabilities}
    question = _normalise(step.question)

    hints = " ".join(
        [
            mode,
            question,
            " ".join(capabilities),
            str(step.preferences),
            str(step.constraints),
        ]
    )

    if any(word in hints for word in ("tgsrtc", "rtc bus", "bus", "bus stop")):
        return "tgsrtc", "bus"

    if any(word in hints for word in ("hmrl", "metro", "subway", "metro rail")):
        return "hmrl", "metro"

    if "train" in hints or "railway" in hints:
        raise ValueError(
            "Railway train schedules are not available in the current "
            "HMRL/TGSRTC feeds. Those feeds cover metro and bus services."
        )

    raise ValueError(
        "Please specify metro or bus for a public-transit journey."
    )


def is_transit_request(step: Any) -> bool:
    mode = _normalise(step.mode)
    capabilities = {_normalise(value) for value in step.capabilities}
    question = _normalise(step.question)

    transit_modes = {
        "metro",
        "bus",
        "transit",
        "public_transport",
        "public transport",
        "tgsrtc",
        "hmrl",
    }

    if mode in transit_modes:
        return True

    if capabilities.intersection(
        {"transit", "public_transport", "public-transport"}
    ):
        return True

    return bool(
        re.search(
            r"\b(metro|tgsrtc|rtc bus|bus route|bus stop|public transport)\b",
            question,
        )
    )


def plan_transit_journey(
    step: Any,
    *,
    limit: int = 10,
) -> dict[str, Any]:
    if not 1 <= limit <= 20:
        raise ValueError("limit must be between 1 and 20.")

    provider_id, mode = _provider_and_mode(step)
    provider = get_provider(provider_id)

    origin_query = str(step.origin or "").strip()
    destination_query = str(step.destination or "").strip()

    if not origin_query or not destination_query:
        raise ValueError("Both origin and destination are required.")

    if _normalise(origin_query) == _normalise(destination_query):
        raise ValueError("Origin and destination must differ.")

    origin_stops = query_service.find_stops(
        provider_id=provider_id,
        query=_transit_query_alias(origin_query),
        limit=10,
    )

    destination_stops = query_service.find_stops(
        provider_id=provider_id,
        query=_transit_query_alias(destination_query),
        limit=10,
    )

    origin = _select_stop(
        _transit_query_alias(origin_query),
        origin_stops,
    )
    destination = _select_stop(
        _transit_query_alias(destination_query),
        destination_stops,
    )

    service_date = _service_date(
        step.model_dump() if hasattr(step, "model_dump") else step
    )

    direct = find_direct_journeys(
        provider_id=provider_id,
        origin_stop_id=origin["stop_id"],
        destination_stop_id=destination["stop_id"],
        service_date=service_date,
        limit=limit,
    )

    transfers = find_best_transfer_options(
        provider_id=provider_id,
        origin_stop_id=origin["stop_id"],
        destination_stop_id=destination["stop_id"],
        service_date=service_date,
        limit=limit,
    )

    unique: dict[tuple[Any, ...], dict[str, Any]] = {}

    for journey in direct + transfers:
        legs = journey.get("legs") or []
        trip_ids = tuple(str(leg.get("trip_id", "")) for leg in legs)
        key = (
            journey.get("departure_seconds"),
            journey.get("arrival_seconds"),
            trip_ids,
        )
        unique[key] = journey

    journeys = sorted(
        unique.values(),
        key=lambda item: (
            item.get("departure_seconds", 0),
            item.get("arrival_seconds", 0),
        ),
    )[:limit]

    return {
        "provider_id": provider_id,
        "mode": mode,
        "schedule_type": "scheduled_gtfs",
        "service_date": service_date.isoformat(),
        "origin": {
            "stop_id": origin["stop_id"],
            "stop_name": _stop_name(origin),
        },
        "destination": {
            "stop_id": destination["stop_id"],
            "stop_name": _stop_name(destination),
        },
        "journey_count": len(journeys),
        "journey_status": (
            "AVAILABLE" if journeys else "NO_SCHEDULED_JOURNEY"
        ),
        "journeys": journeys,
        "attribution": provider.attribution,
        "fare": {
            "status": "UNAVAILABLE",
            "message": (
                "Fare data is unavailable for the selected journey; "
                "missing fare data does not mean free travel."
            ),
        },
        "limitations": [
            "Results are scheduled GTFS services, not live tracking.",
            "Coverage depends on the currently active provider feed.",
            "Only direct and one-transfer journeys are searched.",
        ],
        "message": (
            "Scheduled services from the GTFS feed; "
            "these are not live vehicle arrival predictions."
        ),
    }
