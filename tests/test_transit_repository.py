
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.travel.transit import repository


def test_nearby_stops_deduplicates_parent_and_platforms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Verify the query ranks a parent station ahead of its platforms
    and returns only the highest-ranked stop per station group.
    """
    connection = MagicMock()
    cursor = MagicMock()

    connection.__enter__.return_value = connection
    connection.execute.return_value = cursor
    cursor.description = [
        MagicMock(name="stop_id"),
    ]
    cursor.description[0].name = "stop_id"
    cursor.fetchall.return_value = [("PRG",)]

    monkeypatch.setattr(
        repository,
        "get_connection",
        lambda: connection,
    )

    results = repository.nearby_stops(
        provider_id="hmrl",
        latitude=17.4156,
        longitude=78.4750,
        radius_m=300,
        limit=50,
    )

    sql = connection.execute.call_args.args[0]

    assert "PARTITION BY station_group_id" in sql
    assert "ROW_NUMBER() OVER" in sql
    assert "WHERE station_rank = 1" in sql
    assert results == [{"stop_id": "PRG"}]


def test_nearby_stops_rejects_invalid_coordinates() -> None:
    with pytest.raises(ValueError, match="latitude"):
        repository.nearby_stops(
            "hmrl",
            latitude=100,
            longitude=78.4,
        )

    with pytest.raises(ValueError, match="longitude"):
        repository.nearby_stops(
            "hmrl",
            latitude=17.4,
            longitude=200,
        )