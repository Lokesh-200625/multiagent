from datetime import datetime, timezone

from app.travel.calculations import calculate_fuel_cost
from app.travel.schemas import TravelFact


def make_facts(distance=100.0):
    return [
        TravelFact(
            key="distance",
            value=distance,
            unit="km",
            provider="osrm",
            source_tier="open-data",
            fetched_at=datetime.now(timezone.utc),
        )
    ]


def test_car_fuel_cost():
    result = calculate_fuel_cost(
        make_facts(100),
        mileage_km_per_litre=15,
        fuel_price_per_litre=105,
        vehicle_type="car",
    )

    assert result.status == "COMPLETED"
    assert result.value == 700.0
    assert result.unit == "INR"
    assert result.inputs["estimated_litres"] == 6.667
    assert result.estimated is True


def test_bike_fuel_cost():
    result = calculate_fuel_cost(
        make_facts(100),
        mileage_km_per_litre=40,
        fuel_price_per_litre=105,
        vehicle_type="bike",
    )

    assert result.status == "COMPLETED"
    assert result.value == 262.5


def test_zero_distance_costs_zero():
    result = calculate_fuel_cost(
        make_facts(0),
        mileage_km_per_litre=15,
        fuel_price_per_litre=105,
    )

    assert result.status == "COMPLETED"
    assert result.value == 0.0


def test_missing_distance_is_unavailable():
    result = calculate_fuel_cost(
        [],
        mileage_km_per_litre=15,
        fuel_price_per_litre=105,
    )

    assert result.status == "UNAVAILABLE"
    assert result.metadata["reason"] == "distance_fact_missing"


def test_invalid_mileage_is_unavailable():
    result = calculate_fuel_cost(
        make_facts(),
        mileage_km_per_litre=0,
        fuel_price_per_litre=105,
    )

    assert result.status == "UNAVAILABLE"
    assert result.metadata["reason"] == "mileage_must_be_positive"


def test_negative_fuel_price_is_unavailable():
    result = calculate_fuel_cost(
        make_facts(),
        mileage_km_per_litre=15,
        fuel_price_per_litre=-1,
    )

    assert result.status == "UNAVAILABLE"
    assert result.metadata["reason"] == "fuel_price_must_not_be_negative"