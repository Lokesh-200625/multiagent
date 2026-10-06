from __future__ import annotations

from datetime import datetime, timezone

from app.travel.calculations import (
    calculate_distance_cost,
    calculate_eta,
    calculate_travel,
)
from app.travel.schemas import TravelFact


def _facts(
    *,
    distance: float = 100.0,
    duration: float = 120.0,
    approximate: bool = False,
) -> list[TravelFact]:
    fetched_at = datetime.now(timezone.utc)

    return [
        TravelFact(
            key="distance",
            value=distance,
            unit="km",
            provider="osrm",
            source_tier="open-data",
            fetched_at=fetched_at,
            approximate=approximate,
        ),
        TravelFact(
            key="duration",
            value=duration,
            unit="minutes",
            provider="osrm",
            source_tier="open-data",
            fetched_at=fetched_at,
            approximate=approximate,
        ),
    ]


def test_eta_from_provider_duration():
    result = calculate_eta(_facts())

    assert result.status == "COMPLETED"
    assert result.key == "eta"
    assert result.value == 120.0
    assert result.unit == "minutes"
    assert result.estimated is False


def test_eta_with_buffer():
    result = calculate_eta(
        _facts(duration=120.0),
        buffer_minutes=30.0,
    )

    assert result.status == "COMPLETED"
    assert result.value == 150.0
    assert result.estimated is True
    assert result.configurable is True
    assert result.inputs["buffer_minutes"] == 30.0


def test_distance_cost():
    result = calculate_distance_cost(
        _facts(distance=100.0),
        cost_per_km=12.5,
    )

    assert result.status == "COMPLETED"
    assert result.key == "travel_cost"
    assert result.value == 1250.0
    assert result.unit == "currency"
    assert result.estimated is True
    assert result.configurable is True


def test_missing_cost_is_unavailable():
    result = calculate_distance_cost(_facts())

    assert result.status == "UNAVAILABLE"
    assert result.value is None
    assert result.metadata["reason"] == "cost_per_km_not_supplied"


def test_invalid_inputs_are_unavailable():
    eta = calculate_eta(
        _facts(duration=-10.0),
    )

    cost = calculate_distance_cost(
        _facts(distance=-50.0),
        cost_per_km=10.0,
    )

    assert eta.status == "UNAVAILABLE"
    assert eta.metadata["reason"] == "negative_duration"

    assert cost.status == "UNAVAILABLE"
    assert cost.metadata["reason"] == "negative_distance"


def test_approximate_provider_fact_propagates_estimated():
    results = calculate_travel(
        _facts(
            distance=80.0,
            duration=90.0,
            approximate=True,
        ),
        buffer_minutes=15.0,
        cost_per_km=10.0,
    )

    eta = results[0]
    cost = results[1]

    assert eta.status == "COMPLETED"
    assert eta.value == 105.0
    assert eta.estimated is True

    assert cost.status == "COMPLETED"
    assert cost.value == 800.0
    assert cost.estimated is True