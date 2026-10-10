
from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime
from typing import Any, Callable, TypeVar

from app.core.config import settings
from app.services.session import redis_client
from app.travel.transit import repository

logger = logging.getLogger(__name__)
T = TypeVar("T")


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _cache_key(operation: str, arguments: dict[str, Any]) -> str:
    encoded = json.dumps(
        arguments,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return f"transit:query:v2:{operation}:{digest}"


def _cached(
    operation: str,
    arguments: dict[str, Any],
    loader: Callable[[], T],
) -> T:
    key = _cache_key(operation, arguments)

    try:
        raw = redis_client.get(key)
        if raw:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            return json.loads(raw)
    except Exception:
        logger.warning("Transit cache read failed; using database", exc_info=True)

    result = loader()

    try:
        ttl = max(1, int(settings.transit_cache_ttl_s))
        redis_client.set(
            key,
            json.dumps(result, default=_json_default),
            ex=ttl,
        )
    except Exception:
        logger.warning("Transit cache write failed", exc_info=True)

    return result


def active_feed(provider_id: str) -> dict[str, Any] | None:
    arguments = {"provider_id": provider_id.strip().lower()}
    return _cached(
        "active-feed",
        arguments,
        lambda: repository.get_active_feed(provider_id),
    )


def find_stops(
    provider_id: str,
    query: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    arguments = {
        "provider_id": provider_id.strip().lower(),
        "query": query.strip(),
        "limit": limit,
    }
    return _cached(
        "find-stops",
        arguments,
        lambda: repository.search_stops(provider_id, query, limit),
    )


def find_nearby_stops(
    provider_id: str,
    latitude: float,
    longitude: float,
    radius_m: int = 1000,
    limit: int = 10,
) -> list[dict[str, Any]]:
    arguments = {
        "provider_id": provider_id.strip().lower(),
        "latitude": latitude,
        "longitude": longitude,
        "radius_m": radius_m,
        "limit": limit,
    }
    return _cached(
        "nearby-stops",
        arguments,
        lambda: repository.nearby_stops(
            provider_id, latitude, longitude, radius_m, limit
        ),
    )


def find_routes(
    provider_id: str,
    query: str = "",
    limit: int = 10,
) -> list[dict[str, Any]]:
    arguments = {
        "provider_id": provider_id.strip().lower(),
        "query": query.strip(),
        "limit": limit,
    }
    return _cached(
        "find-routes",
        arguments,
        lambda: repository.search_routes(provider_id, query, limit),
    )


def route_stops(
    provider_id: str,
    route_id: str,
    limit: int = 500,
) -> list[dict[str, Any]]:
    arguments = {
        "provider_id": provider_id.strip().lower(),
        "route_id": route_id.strip(),
        "limit": limit,
    }
    return _cached(
        "route-stops",
        arguments,
        lambda: repository.get_route_stops(provider_id, route_id, limit),
    )


def stop_departures(
    provider_id: str,
    stop_id: str,
    after_seconds: int = 0,
    limit: int = 20,
) -> list[dict[str, Any]]:
    arguments = {
        "provider_id": provider_id.strip().lower(),
        "stop_id": stop_id.strip(),
        "after_seconds": after_seconds,
        "limit": limit,
    }
    return _cached(
        "stop-departures",
        arguments,
        lambda: repository.get_stop_departures(
            provider_id, stop_id, after_seconds, limit
        ),
    )
