from __future__ import annotations

import logging
import uuid

import pytest

from app.core.config import settings
from app.services.session import redis_client
from app.travel.cache import _cache_key
from app.travel.providers import route_chain
from app.travel.providers import routing
from app.travel.resolver import (
    TravelResolverClarificationError,
    resolve_place,
)
from app.travel.schemas import Coordinate, PlaceCandidate


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


def test_ors_disabled_falls_back_to_osrm(monkeypatch):
    origin, destination = _coordinates()
    calls: list[str] = []

    def fake_ors(*args, **kwargs):
        calls.append("ors")
        raise AssertionError("ORS must not be called")

    def fake_osrm(*args, **kwargs):
        calls.append("osrm")
        return [
            routing.TravelFact(
                key="distance",
                value=100.0,
                unit="km",
                provider="osrm",
                source_tier="open-data",
                fetched_at=routing._timestamp(),
                confidence=0.8,
            )
        ]

    monkeypatch.setattr(route_chain, "route_ors", fake_ors)
    monkeypatch.setattr(route_chain, "route_osrm", fake_osrm)

    result = route_chain.route_with_fallbacks(
        origin,
        destination,
        use_ors=False,
        use_osrm=True,
        allow_haversine=False,
    )

    assert result.status == "COMPLETED"
    assert result.facts[0].provider == "osrm"
    assert calls == ["osrm"]


def test_ors_quota_exhausted_falls_back_to_osrm(monkeypatch):
    origin, destination = _coordinates()
    calls: list[str] = []

    monkeypatch.setattr(
        routing,
        "try_consume_ors_quota",
        lambda: False,
    )

    def fake_ors(*args, **kwargs):
        calls.append("ors")
        return routing.route_ors(*args, **kwargs)

    def fake_osrm(*args, **kwargs):
        calls.append("osrm")
        return [
            routing.TravelFact(
                key="distance",
                value=101.0,
                unit="km",
                provider="osrm",
                source_tier="open-data",
                fetched_at=routing._timestamp(),
                confidence=0.8,
            )
        ]

    monkeypatch.setattr(route_chain, "route_ors", fake_ors)
    monkeypatch.setattr(route_chain, "route_osrm", fake_osrm)

    result = route_chain.route_with_fallbacks(
        origin,
        destination,
        use_ors=True,
        use_osrm=True,
        allow_haversine=False,
    )

    assert result.status == "COMPLETED"
    assert result.facts[0].provider == "osrm"
    assert calls == ["ors", "osrm"]


def test_all_providers_disabled_returns_failed():
    origin, destination = _coordinates()

    result = route_chain.route_with_fallbacks(
        origin,
        destination,
        use_ors=False,
        use_osrm=False,
        allow_haversine=False,
    )

    assert result.status == "FAILED"
    assert result.facts == []
    assert result.metadata["selected_provider"] is None


def test_ambiguous_geocode_returns_clarification(monkeypatch):
    candidates = [
        PlaceCandidate(
            display_name="Springfield A",
            latitude=17.1,
            longitude=78.1,
            source="nominatim",
            importance=0.50,
        ),
        PlaceCandidate(
            display_name="Springfield B",
            latitude=17.2,
            longitude=78.2,
            source="nominatim",
            importance=0.48,
        ),
    ]

    monkeypatch.setattr(
        "app.travel.resolver._resolve_named_entity",
        lambda query: None,
    )
    monkeypatch.setattr(
        "app.travel.resolver._resolve_from_geography_registry",
        lambda query: None,
    )
    monkeypatch.setattr(
        "app.travel.resolver.nominatim_search",
        lambda query, limit=5: candidates,
    )

    with pytest.raises(TravelResolverClarificationError) as exc_info:
        resolve_place("Springfield")

    error = exc_info.value

    assert error.query == "Springfield"
    assert len(error.candidates) == 2
    assert error.candidates[0].display_name == "Springfield A"


def test_cache_hit_skips_provider(monkeypatch):
    origin, destination = _coordinates()
    calls = 0

    def fake_osrm(*args, **kwargs):
        nonlocal calls
        calls += 1

        return [
            routing.TravelFact(
                key="distance",
                value=120.0,
                unit="km",
                provider="osrm",
                source_tier="open-data",
                fetched_at=routing._timestamp(),
                confidence=0.8,
            )
        ]

    monkeypatch.setattr(
        route_chain,
        "route_ors",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("ORS disabled for cache test")
        ),
    )
    monkeypatch.setattr(route_chain, "route_osrm", fake_osrm)

    first = route_chain.route_with_fallbacks(
        origin,
        destination,
        use_ors=False,
        use_osrm=True,
        allow_haversine=False,
    )

    second = route_chain.route_with_fallbacks(
        origin,
        destination,
        use_ors=False,
        use_osrm=True,
        allow_haversine=False,
    )

    assert first.status == "COMPLETED"
    assert second.status == "COMPLETED"
    assert second.metadata["cache_hit"] is True
    assert calls == 1


def test_negative_cache_ttl_and_latency_log(
    monkeypatch,
    caplog,
):
    origin, destination = _coordinates()

    monkeypatch.setattr(
        route_chain,
        "route_ors",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("forced ORS failure")
        ),
    )
    monkeypatch.setattr(
        route_chain,
        "route_osrm",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("forced OSRM failure")
        ),
    )

    result = route_chain.route_with_fallbacks(
        origin,
        destination,
        use_ors=True,
        use_osrm=True,
        allow_haversine=False,
    )

    assert result.status == "FAILED"

    key = _cache_key(
        "route",
        f"{origin.latitude:.6f},{origin.longitude:.6f}",
        f"{destination.latitude:.6f},{destination.longitude:.6f}",
        "driving",
    )

    ttl = redis_client.ttl(key)

    assert 0 < ttl <= settings.ttl_negative_s

    def fake_request_json(*args, **kwargs):
        return {
            "code": "Ok",
            "routes": [
                {
                    "distance": 1000.0,
                    "duration": 600.0,
                }
            ],
        }

    monkeypatch.setattr(
        routing,
        "_request_json",
        fake_request_json,
    )

    with caplog.at_level(logging.INFO):
        facts = routing.route_osrm(
            origin,
            destination,
        )

    assert facts
    assert facts[0].provider == "osrm"

    latency_lines = [
        record.message
        for record in caplog.records
        if "provider=osrm" in record.message
        and "kind=route" in record.message
        and "ms=" in record.message
        and "status=success" in record.message
    ]

    assert latency_lines