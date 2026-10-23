from app.travel.providers import alternatives
from app.travel.schemas import Coordinate


def _places():
    return (
        Coordinate(latitude=17.385, longitude=78.4867),
        Coordinate(latitude=17.5162, longitude=78.8867),
    )


def test_osrm_returns_multiple_routes(monkeypatch):
    origin, destination = _places()
    captured = {}

    def fake_request(url, **kwargs):
        captured["url"] = url
        return {
            "code": "Ok",
            "routes": [
                {"distance": 100000, "duration": 6000},
                {"distance": 110000, "duration": 6300},
                {"distance": 100000, "duration": 6000},
            ],
        }

    monkeypatch.setattr(alternatives, "_request_json", fake_request)

    result = alternatives.get_osrm_alternative_routes(
        origin,
        destination,
    )

    assert result["status"] == "COMPLETED"
    assert len(result["routes"]) == 2
    assert result["routes"][0]["distance_km"] == 100
    assert result["routes"][0]["duration_minutes"] == 100
    assert result["routes"][1]["distance_km"] == 110
    assert "alternatives=3" in captured["url"]


def test_osrm_no_routes_returns_unavailable(monkeypatch):
    origin, destination = _places()

    monkeypatch.setattr(
        alternatives,
        "_request_json",
        lambda *args, **kwargs: {"code": "Ok", "routes": []},
    )

    result = alternatives.get_osrm_alternative_routes(
        origin,
        destination,
    )

    assert result["status"] == "UNAVAILABLE"
    assert result["routes"] == []


def test_osrm_provider_error_is_reported(monkeypatch):
    origin, destination = _places()

    monkeypatch.setattr(
        alternatives,
        "_request_json",
        lambda *args, **kwargs: {"code": "NoRoute", "routes": []},
    )

    result = alternatives.get_osrm_alternative_routes(
        origin,
        destination,
    )

    assert result["status"] == "UNAVAILABLE"
    assert result["reason"] == "provider_returned_error"


def test_alternative_count_is_validated():
    origin, destination = _places()

    try:
        alternatives.get_osrm_alternative_routes(
            origin,
            destination,
            max_alternatives=0,
        )
    except ValueError as exc:
        assert "between 1 and 5" in str(exc)
    else:
        raise AssertionError("Expected invalid route count to fail")