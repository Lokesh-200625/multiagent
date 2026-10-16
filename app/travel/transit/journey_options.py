
from __future__ import annotations

from datetime import date
from typing import Any

from app.travel.transit.journey_planner import find_transfer_journeys


def find_best_transfer_options(
    provider_id: str,
    origin_stop_id: str,
    destination_stop_id: str,
    service_date: date,
    min_transfer_seconds: int = 300,
    after_seconds: int = 0,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Return distinct one-transfer journeys with the best connection per first trip."""

    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100.")

    candidates = find_transfer_journeys(
        provider_id=provider_id,
        origin_stop_id=origin_stop_id,
        destination_stop_id=destination_stop_id,
        service_date=service_date,
        min_transfer_seconds=min_transfer_seconds,
        after_seconds=after_seconds,
        limit=100,
    )

    best_by_first_trip: dict[str, dict[str, Any]] = {}

    for journey in candidates:
        legs = journey.get("legs") or []

        if len(legs) != 2:
            continue

        first_trip_id = str(legs[0].get("trip_id", ""))

        if not first_trip_id:
            continue

        previous = best_by_first_trip.get(first_trip_id)

        if (
            previous is None
            or journey["arrival_seconds"] < previous["arrival_seconds"]
        ):
            best_by_first_trip[first_trip_id] = journey

    options = sorted(
        best_by_first_trip.values(),
        key=lambda item: (
            item["departure_seconds"],
            item["arrival_seconds"],
        ),
    )

    return options[:limit]
