from __future__ import annotations

from typing import Any

from app.travel.schemas import TravelCalculation, TravelFact


def _find_fact(
    facts: list[TravelFact],
    key: str,
) -> TravelFact | None:
    for fact in facts:
        if fact.key == key:
            return fact
    return None


def calculate_eta(
    facts: list[TravelFact],
    *,
    buffer_minutes: float = 0.0,
) -> TravelCalculation:
    duration_fact = _find_fact(facts, "duration")

    if duration_fact is None:
        return TravelCalculation(
            key="eta",
            status="UNAVAILABLE",
            method="provider_duration",
            metadata={"reason": "duration_fact_missing"},
        )

    try:
        duration = float(duration_fact.value)
    except (TypeError, ValueError):
        return TravelCalculation(
            key="eta",
            status="UNAVAILABLE",
            method="provider_duration",
            metadata={"reason": "invalid_duration"},
        )

    if duration < 0:
        return TravelCalculation(
            key="eta",
            status="UNAVAILABLE",
            method="provider_duration",
            metadata={"reason": "negative_duration"},
        )

    if buffer_minutes < 0:
        return TravelCalculation(
            key="eta",
            status="UNAVAILABLE",
            method="provider_duration",
            metadata={"reason": "negative_buffer"},
        )

    total_minutes = duration + buffer_minutes

    return TravelCalculation(
        key="eta",
        value=total_minutes,
        unit="minutes",
        status="COMPLETED",
        method="provider_duration_plus_buffer",
        estimated=duration_fact.approximate or buffer_minutes > 0,
        configurable=buffer_minutes > 0,
        inputs={
            "provider_duration_minutes": duration,
            "buffer_minutes": buffer_minutes,
        },
        metadata={
            "provider": duration_fact.provider,
            "source_tier": duration_fact.source_tier,
            "approximate_provider_fact": duration_fact.approximate,
        },
    )


def calculate_distance_cost(
    facts: list[TravelFact],
    *,
    cost_per_km: float | None = None,
) -> TravelCalculation:
    if cost_per_km is None:
        return TravelCalculation(
            key="travel_cost",
            status="UNAVAILABLE",
            method="distance_times_cost_per_km",
            metadata={"reason": "cost_per_km_not_supplied"},
        )

    if cost_per_km < 0:
        return TravelCalculation(
            key="travel_cost",
            status="UNAVAILABLE",
            method="distance_times_cost_per_km",
            metadata={"reason": "negative_cost_per_km"},
        )

    distance_fact = _find_fact(facts, "distance")

    if distance_fact is None:
        return TravelCalculation(
            key="travel_cost",
            status="UNAVAILABLE",
            method="distance_times_cost_per_km",
            metadata={"reason": "distance_fact_missing"},
        )

    try:
        distance_km = float(distance_fact.value)
    except (TypeError, ValueError):
        return TravelCalculation(
            key="travel_cost",
            status="UNAVAILABLE",
            method="distance_times_cost_per_km",
            metadata={"reason": "invalid_distance"},
        )

    if distance_km < 0:
        return TravelCalculation(
            key="travel_cost",
            status="UNAVAILABLE",
            method="distance_times_cost_per_km",
            metadata={"reason": "negative_distance"},
        )

    total_cost = distance_km * cost_per_km

    return TravelCalculation(
        key="travel_cost",
        value=total_cost,
        unit="currency",
        status="COMPLETED",
        method="distance_times_cost_per_km",
        estimated=True,
        configurable=True,
        inputs={
            "distance_km": distance_km,
            "cost_per_km": cost_per_km,
        },
        metadata={
            "provider": distance_fact.provider,
            "source_tier": distance_fact.source_tier,
            "currency": "UNSPECIFIED",
        },
    )


def calculate_travel(
    facts: list[TravelFact],
    *,
    buffer_minutes: float = 0.0,
    cost_per_km: float | None = None,
) -> list[TravelCalculation]:
    return [
        calculate_eta(
            facts,
            buffer_minutes=buffer_minutes,
        ),
        calculate_distance_cost(
            facts,
            cost_per_km=cost_per_km,
        ),
    ]