
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


def _json_compatible(value: T) -> T:
    """Return the same JSON-compatible types on cache misses and hits."""
    return json.loads(json.dumps(value, default=_json_default))


def _cache_key(
    operation: str,
    arguments: dict[str, Any],
    feed_version: str,
) -> str:
    encoded = json.dumps(
        arguments,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    return f"transit:query:v3:{feed_version}:{operation}:{digest}"


def _get_current_feed_version(provider_id: str) -> str:
    feed = repository.get_active_feed(provider_id.strip().lower())

    if not feed:
        return "no-active-feed"

    content_hash = feed.get("content_sha256")
    if content_hash:
        return str(content_hash)

    feed_id = feed.get("id")
    if feed_id:
        return str(feed_id)

    raise RuntimeError(
        "Active transit feed has neither a content hash nor an ID."
    )


def _cached(
    operation: str,
    arguments: dict[str, Any],
    loader: Callable[[], T],
) -> T:
    provider_id = str(arguments["provider_id"]).strip().lower()
    feed_version = _get_current_feed_version(provider_id)
    key = _cache_key(operation, arguments, feed_version)

    try:
        raw = redis_client.get(key)
        if raw:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            return json.loads(raw)
    except Exception:
        logger.warning(
            "Transit cache read failed; using database",
            exc_info=True,
        )

    result = _json_compatible(loader())

    try:
        ttl = max(1, int(settings.transit_cache_ttl_s))
        redis_client.set(
            key,
            json.dumps(result, default=_json_default),
            ex=ttl,
        )
    except Exception:
        logger.warning(
            "Transit cache write failed",
            exc_info=True,
        )

    return result


def active_feed(provider_id: str):
    result = repository.get_active_feed(provider_id.strip().lower())
    return _json_compatible(result)


def find_stops(provider_id: str, query: str, limit: int = 10):
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
):
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
):
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
    direction_id: int | None = None,
):
    arguments = {
        "provider_id": provider_id.strip().lower(),
        "route_id": route_id.strip(),
        "limit": limit,
        "direction_id": direction_id,
    }
    return _cached(
        "route-stops",
        arguments,
        lambda: repository.get_route_stops(
            provider_id,
            route_id,
            limit,
            direction_id=direction_id,
        ),
    )


def stop_departures(
    provider_id: str,
    stop_id: str,
    after_seconds: int = 0,
    limit: int = 20,
):
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
