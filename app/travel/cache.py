from __future__ import annotations

import json
import logging
from typing import Any

from redis.exceptions import RedisError

from app.core.config import settings
from app.services.session import redis_client

logger = logging.getLogger(__name__)

TRAVEL_CACHE_PREFIX = "travel:"
NEGATIVE_TTL_SECONDS = 30


TTL_SECONDS = {
    "geocode": 60 * 60 * 24 * 30,
    "route": 60 * 60 * 24,
    "matrix": 60 * 60 * 24,
    "weather": 60 * 60 * 6,
    "poi": 60 * 60 * 24 * 7,
}


def _cache_key(
    operation: str,
    provider: str,
    identifier: str,
) -> str:
    return (
        f"{TRAVEL_CACHE_PREFIX}"
        f"{operation}:"
        f"{provider}:"
        f"{identifier}"
    )


def get_cached_fact(
    operation: str,
    provider: str,
    identifier: str,
) -> dict[str, Any] | None:
    key = _cache_key(operation, provider, identifier)

    try:
        value = redis_client.get(key)
    except RedisError as exc:
        logger.warning("Travel cache read failed: %s", exc)
        return None

    if not value:
        return None

    try:
        return json.loads(value)
    except json.JSONDecodeError:
        logger.warning("Invalid travel cache value: %s", key)
        return None


def set_cached_fact(
    operation: str,
    provider: str,
    identifier: str,
    fact: dict[str, Any],
) -> bool:
    ttl = TTL_SECONDS.get(operation, 60 * 60)

    key = _cache_key(operation, provider, identifier)

    try:
        redis_client.set(
            key,
            json.dumps(fact, ensure_ascii=False),
            ex=ttl,
        )
        return True
    except RedisError as exc:
        logger.warning("Travel cache write failed: %s", exc)
        return False


def set_negative_cache(
    operation: str,
    provider: str,
    identifier: str,
    error: dict[str, Any],
) -> bool:
    """
    Negative caching is intentionally short-lived.
    Provider failures must never occupy the normal cache TTL.
    """

    key = _cache_key(operation, provider, identifier)

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
            ex=NEGATIVE_TTL_SECONDS,
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