from __future__ import annotations

import json
import logging
from typing import Any

from redis.exceptions import RedisError

from app.core.config import settings
from app.services.session import redis_client

logger = logging.getLogger(__name__)

TRAVEL_CACHE_PREFIX = "travel:"


def _normalize_part(value: Any) -> str:
    return str(value).strip().lower().replace(" ", "_")


def _cache_key(kind: str, *parts: Any) -> str:
    normalized = ":".join(_normalize_part(part) for part in parts)
    return f"{TRAVEL_CACHE_PREFIX}{kind}:{normalized}"


def _ttl_for_kind(kind: str) -> int:
    ttl_map = {
        "geocode": settings.ttl_geocode_s,
        "route": settings.ttl_route_s,
    }
    return ttl_map.get(kind, settings.ttl_route_s)


def get_fact(kind: str, *parts: Any) -> dict[str, Any] | None:
    key = _cache_key(kind, *parts)

    try:
        raw = redis_client.get(key)
    except RedisError as exc:
        logger.warning("Travel cache read failed: %s", exc)
        return None

    if not raw:
        return None

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Invalid travel cache value: %s", key)
        return None

    if not isinstance(payload, dict):
        logger.warning("Invalid travel cache payload: %s", key)
        return None

    if payload.get("negative") is True:
        return None

    return payload


def set_fact(
    kind: str,
    *parts: Any,
    payload: dict[str, Any],
    ttl: int | None = None,
) -> bool:
    if payload.get("status") == "FAILED":
        return set_negative(
            kind,
            *parts,
            error={
                "status": "FAILED",
                "reason": payload.get("metadata", {}).get("reason"),
            },
        )

    cache_ttl = ttl if ttl is not None else _ttl_for_kind(kind)
    key = _cache_key(kind, *parts)

    try:
        redis_client.set(
            key,
            json.dumps(payload, ensure_ascii=False),
            ex=cache_ttl,
        )
        return True
    except RedisError as exc:
        logger.warning("Travel cache write failed: %s", exc)
        return False


def set_negative(
    kind: str,
    *parts: Any,
    error: dict[str, Any],
) -> bool:
    key = _cache_key(kind, *parts)

    try:
        redis_client.set(
            key,
            json.dumps(
                {
                    "negative": True,
                    "error": error,
                },
                ensure_ascii=False,
            ),
            ex=settings.ttl_negative_s,
        )
        return True
    except RedisError as exc:
        logger.warning("Travel negative-cache write failed: %s", exc)
        return False


def quota_key(provider: str, date_key: str) -> str:
    return f"quota:{provider}:{date_key}"


def increment_quota(
    provider: str,
    date_key: str,
    ttl_seconds: int,
) -> int | None:
    key = quota_key(provider, date_key)

    try:
        count = redis_client.incr(key)

        if count == 1:
            redis_client.expire(key, ttl_seconds)

        return int(count)

    except RedisError as exc:
        logger.warning("Travel quota counter failed: %s", exc)
        return None