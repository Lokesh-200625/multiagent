
from contextlib import contextmanager
from datetime import date

import pytest

from app.travel.transit import journey_planner


SERVICE_DATE = date(2026, 10, 13)


class FakeConnection:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.sql = ""
        self.params = ()

    def execute(self, sql, params):
        self.sql = sql
        self.params = params
        return self

    def fetchall(self):
        return self.rows


def install_fake_connection(monkeypatch, rows=None):
    connection = FakeConnection(rows)

    @contextmanager
    def fake_get_connection():
        yield connection

    monkeypatch.setattr(
        journey_planner,
        "get_connection",
        fake_get_connection,
    )

    return connection


def make_journey_row(
    departure_seconds=21852,
    arrival_seconds=22564,
    first_trip="trip-1",
):
    legs = [
        {
            "trip_id": first_trip,
            "route_short_name": "GREEN",
            "board_stop_name": "Sultan Bazar",
            "alight_stop_name": "MGBS",
        },
        {
            "trip_id": "trip-2",
            "route_short_name": "RED",
            "board_stop_name": "MGBS",
            "alight_stop_name": "Malakpet",
        },
    ]

    return (
        "hmrl",
        "SUB1",
        "Sultan Bazar",
        "MKL1",
        "Malakpet",
        departure_seconds,
        arrival_seconds,
        legs,
    )


def test_transfer_query_ranks_best_connection_per_first_trip(monkeypatch):
    connection = install_fake_connection(monkeypatch)

    journey_planner.find_transfer_journeys(
        provider_id="hmrl",
        origin_stop_id="SUB",
        destination_stop_id="MKL",
        service_date=SERVICE_DATE,
        min_transfer_seconds=300,
        limit=10,
    )

    sql = connection.sql.upper()

    assert "ROW_NUMBER() OVER" in sql
    assert "PARTITION BY L1.TRIP_ID" in sql
    assert "WHERE CONNECTION_RANK = 1" in sql
    assert "ORDER BY DEPARTURE_SECONDS, ARRIVAL_SECONDS" in sql
    assert connection.params[-1] == 10


def test_transfer_results_are_mapped_correctly(monkeypatch):
    row = make_journey_row()
    install_fake_connection(monkeypatch, [row])

    journeys = journey_planner.find_transfer_journeys(
        provider_id="hmrl",
        origin_stop_id="SUB",
        destination_stop_id="MKL",
        service_date=SERVICE_DATE,
        limit=10,
    )

    assert len(journeys) == 1

    journey = journeys[0]

    assert journey["departure_time"] == "06:04:12"
    assert journey["arrival_time"] == "06:16:04"
    assert journey["travel_minutes"] == 11.9
    assert journey["legs"][0]["trip_id"] == "trip-1"
    assert journey["legs"][1]["trip_id"] == "trip-2"


def test_no_transfer_results_returns_empty_list(monkeypatch):
    install_fake_connection(monkeypatch, [])

    journeys = journey_planner.find_transfer_journeys(
        provider_id="hmrl",
        origin_stop_id="SUB",
        destination_stop_id="MKL",
        service_date=SERVICE_DATE,
    )

    assert journeys == []


def test_negative_transfer_buffer_is_rejected():
    with pytest.raises(ValueError, match="cannot be negative"):
        journey_planner.find_transfer_journeys(
            provider_id="hmrl",
            origin_stop_id="SUB",
            destination_stop_id="MKL",
            service_date=SERVICE_DATE,
            min_transfer_seconds=-1,
        )


def test_same_origin_and_destination_is_rejected():
    with pytest.raises(ValueError, match="must differ"):
        journey_planner.find_transfer_journeys(
            provider_id="hmrl",
            origin_stop_id="SUB",
            destination_stop_id="SUB",
            service_date=SERVICE_DATE,
        )


def test_direct_journey_maps_empty_result(monkeypatch):
    connection = install_fake_connection(monkeypatch)

    journeys = journey_planner.find_direct_journeys(
        provider_id="hmrl",
        origin_stop_id="SUB",
        destination_stop_id="MKL",
        service_date=SERVICE_DATE,
    )

    assert journeys == []
    assert "ORDER BY OST.DEPARTURE_SECONDS" in connection.sql.upper()
