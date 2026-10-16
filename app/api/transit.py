
from __future__ import annotations

import logging
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.travel.transit import query_service
from app.travel.transit.journey_options import find_best_transfer_options
from app.travel.transit.journey_planner import find_direct_journeys

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/transit", tags=["Transit"])

SUPPORTED_PROVIDERS = {"hmrl", "tgsrtc"}


def _validate_provider(provider_id: str) -> str:
    provider = provider_id.strip().lower()

    if provider not in SUPPORTED_PROVIDERS:
        raise HTTPException(
            status_code=400,
            detail="provider_id must be 'hmrl' or 'tgsrtc'.",
        )

    try:
        feed = query_service.active_feed(provider)
    except Exception as exc:
        logger.exception(
            "Failed to access active transit feed for provider=%s",
            provider,
        )
        raise HTTPException(
            status_code=503,
            detail="Unable to access transit data. Please try again.",
        ) from exc

    if not feed:
        raise HTTPException(
            status_code=404,
            detail=f"No active transit feed found for '{provider}'.",
        )

    return provider


def _resolve_stop(provider_id: str, term: str) -> dict[str, Any]:
    term = term.strip()

    if not term:
        raise HTTPException(
            status_code=422,
            detail="Origin and destination must not be empty.",
        )

    try:
        matches = query_service.find_stops(
            provider_id=provider_id,
            query=term,
            limit=20,
        )
    except Exception as exc:
        logger.exception(
            "Failed to search transit stops: provider=%s query=%r",
            provider_id,
            term,
        )
        raise HTTPException(
            status_code=503,
            detail="Unable to search transit stops. Please try again.",
        ) from exc

    if not matches:
        raise HTTPException(
            status_code=404,
            detail=f"No transit stop found matching '{term}'.",
        )

    exact = [
        stop
        for stop in matches
        if str(stop.get("stop_name") or "").casefold() == term.casefold()
        or str(stop.get("stop_id") or "").casefold() == term.casefold()
        or str(stop.get("stop_code") or "").casefold() == term.casefold()
    ]

    if exact:
        # Prefer the parent station when a station and its platforms
        # have the same name. The planner expands parent IDs to platforms.
        exact.sort(
            key=lambda stop: (
                0 if stop.get("location_type") == 1 else 1,
                0 if not stop.get("parent_station") else 1,
                str(stop.get("stop_id") or ""),
            )
        )
        return exact[0]

    if len(matches) == 1:
        return matches[0]

    raise HTTPException(
        status_code=422,
        detail={
            "message": (
                f"'{term}' matches multiple stops. "
                "Use a more specific station name or stop ID."
            ),
            "candidates": [
                {
                    "stop_id": stop.get("stop_id"),
                    "stop_name": stop.get("stop_name"),
                    "stop_code": stop.get("stop_code"),
                    "parent_station": stop.get("parent_station"),
                }
                for stop in matches[:10]
            ],
        },
    )


@router.get("/stops")
def search_transit_stops(
    provider_id: str = Query(default="hmrl", min_length=1),
    q: str = Query(min_length=1, max_length=100),
    limit: int = Query(default=10, ge=1, le=100),
):
    """Search transit stops by station name, code, or stop ID."""
    provider = _validate_provider(provider_id)

    try:
        stops = query_service.find_stops(
            provider_id=provider,
            query=q,
            limit=limit,
        )
    except Exception as exc:
        logger.exception(
            "Transit stop search failed: provider=%s query=%r",
            provider,
            q,
        )
        raise HTTPException(
            status_code=503,
            detail="Unable to search transit stops. Please try again.",
        ) from exc

    return {
        "provider_id": provider,
        "query": q,
        "count": len(stops),
        "stops": stops,
    }


@router.get("/journeys")
def search_transit_journeys(
    origin: str = Query(min_length=1, max_length=100),
    destination: str = Query(min_length=1, max_length=100),
    service_date: date = Query(...),
    provider_id: str = Query(default="hmrl", min_length=1),
    after_seconds: int = Query(default=0, ge=0, le=172800),
    min_transfer_minutes: int = Query(default=5, ge=0, le=60),
    limit: int = Query(default=10, ge=1, le=20),
):
    """
    Find direct and one-transfer journeys between transit stations.

    Times are scheduled GTFS service-day times, not live predictions.
    """
    provider = _validate_provider(provider_id)

    if origin.strip().casefold() == destination.strip().casefold():
        raise HTTPException(
            status_code=400,
            detail="Origin and destination must differ.",
        )

    origin_stop = _resolve_stop(provider, origin)
    destination_stop = _resolve_stop(provider, destination)

    origin_id = str(origin_stop["stop_id"])
    destination_id = str(destination_stop["stop_id"])

    if origin_id == destination_id:
        raise HTTPException(
            status_code=400,
            detail="Origin and destination resolve to the same stop.",
        )

    try:
        direct = find_direct_journeys(
            provider_id=provider,
            origin_stop_id=origin_id,
            destination_stop_id=destination_id,
            service_date=service_date,
            after_seconds=after_seconds,
            limit=limit,
        )

        transfers = find_best_transfer_options(
            provider_id=provider,
            origin_stop_id=origin_id,
            destination_stop_id=destination_id,
            service_date=service_date,
            min_transfer_seconds=min_transfer_minutes * 60,
            after_seconds=after_seconds,
            limit=limit,
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        logger.exception(
            (
                "Journey planning failed: provider=%s origin=%s "
                "destination=%s service_date=%s after_seconds=%s "
                "min_transfer_minutes=%s limit=%s"
            ),
            provider,
            origin_id,
            destination_id,
            service_date,
            after_seconds,
            min_transfer_minutes,
            limit,
        )
        raise HTTPException(
            status_code=503,
            detail=(
                "Journey planning failed. Check transit database "
                "connectivity and try again."
            ),
        ) from exc

    journeys = []

    for journey in direct:
        journeys.append({
            **journey,
            "journey_type": "direct",
        })

    for journey in transfers:
        journeys.append({
            **journey,
            "journey_type": "one_transfer",
        })

    journeys.sort(
        key=lambda journey: (
            journey["departure_seconds"],
            journey["arrival_seconds"],
        )
    )

    return {
        "provider_id": provider,
        "service_date": service_date.isoformat(),
        "origin": {
            "stop_id": origin_id,
            "stop_name": origin_stop.get("stop_name"),
        },
        "destination": {
            "stop_id": destination_id,
            "stop_name": destination_stop.get("stop_name"),
        },
        "min_transfer_minutes": min_transfer_minutes,
        "count": len(journeys),
        "journeys": journeys,
        "limitations": [
            "Results are based on scheduled GTFS timetables.",
            "Live delays and cancellations are not included.",
            "Calendar exceptions and physical transfer walking "
            "times are not yet accounted for.",
            "Only direct and one-transfer journeys are searched.",
        ],
    }
