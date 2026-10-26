from __future__ import annotations

import uuid

import pytest

from app.core.config import settings
from app.services.session import redis_client
from app.travel.cache import _cache_key, get_fact, set_fact, set_negative
from app.travel.providers import route_chain, routing
from app.travel.schemas import Coordinate


def _coordinates() -> tuple[Coordinate, Coordinate]:
    token = uuid.uuid4().int % 100000
    origin = Coordinate(
        latitude=17.0 + (token / 1_000_000),
        longitude=78.0 + (token / 1_000_000),
    )
    destination = Coordinate(
        latitude=17.5 + (token / 1_000_000),
        longitude=78.5 + (token / 1_000_000),
    )
    return origin, destination


def _require_redis() -> None:
    try:
        redis_client.ping()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"redis unavailable: {exc}")


def test_provider_order_google_then_osrm_then_haversine(monkeypatch):
    """With a Google key configured, google runs first; its failure falls to
    OSRM; OSRM failure falls to Haversine (PARTIAL)."""
    origin, destination = _coordinates()
    calls: list[str] = []

    monkeypatch.setattr(settings, "google_maps_api_key", "test-key")
    monkeypatch.setattr(settings, "osrm_enabled", True)

    def fake_google(*args, **kwargs):
        calls.append("google")
        raise RuntimeError("google down")

    def fake_osrm(*args, **kwargs):
        calls.append("osrm")
        raise RuntimeError("osrm down")

    monkeypatch.setattr(
        "app.travel.providers.google_routes.route_google", fake_google
    )
    monkeypatch.setattr(route_chain, "route_osrm", fake_osrm)
    monkeypatch.setattr(
        route_chain.settings, "google_maps_api_key", "test-key"
    )

    # route_chain imports route_google lazily; patch the module it imports from
    import app.travel.providers.google_routes as google_module

    monkeypatch.setattr(google_module, "route_google", fake_google)

    result = route_chain.route_with_fallbacks(
        origin, destination, mode="driving"
    )

    assert result.status == "PARTIAL"
    assert result.metadata["selected_provider"] == "haversine"
    assert calls[0] == "google"
    assert "osrm" in calls


def test_haversine_when_everything_disabled(monkeypatch):
    origin, destination = _coordinates()

    monkeypatch.setattr(settings, "google_maps_api_key", None)
    monkeypatch.setattr(settings, "osrm_enabled", False)

    result = route_chain.route_with_fallbacks(
        origin, destination, mode="driving"
    )

    assert result.status == "PARTIAL"
    assert result.metadata["selected_provider"] == "haversine"
    assert result.metadata["approximate"] is True


def test_haversine_math():
    origin = Coordinate(latitude=17.385, longitude=78.4867)
    destination = Coordinate(latitude=17.385, longitude=79.4867)

    facts = routing.route_haversine(origin, destination)
    distance = next(f for f in facts if f.key == "distance")

    # 1 degree longitude at ~17.3N ≈ 106 km
    assert 95.0 < distance.value < 115.0
    assert distance.approximate is True


def test_travel_cache_roundtrip_and_negative():
    _require_redis()

    kind = "route"
    parts = ("test", str(uuid.uuid4().int % 10000))
    key = _cache_key(kind, *parts)

    assert get_fact(kind, *parts) is None

    set_fact(kind, *parts, payload={"status": "COMPLETED", "value": 42})
    assert get_fact(kind, *parts) == {"status": "COMPLETED", "value": 42}

    set_negative(kind, *parts, error={"status": "FAILED"})
    # negative entries read as a miss
    assert get_fact(kind, *parts) is None

    redis_client.delete(key)


def test_negative_cache_ttl_is_short():
    _require_redis()

    kind = "route"
    parts = ("neg", str(uuid.uuid4().int % 10000))
    key = _cache_key(kind, *parts)

    set_negative(kind, *parts, error={"status": "FAILED"})
    ttl = redis_client.ttl(key)

    assert 0 < ttl <= settings.ttl_negative_s
    redis_client.delete(key)
