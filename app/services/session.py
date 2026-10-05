import json
import logging

import redis
from redis.exceptions import RedisError

from app.core.config import settings


logger = logging.getLogger(__name__)


SESSION_PREFIX = "session:"
SESSION_TTL_SECONDS = 60 * 60 * 24


redis_client = redis.Redis(
    host=settings.redis_host,
    port=settings.redis_port,
    decode_responses=True,
    socket_connect_timeout=2,
    socket_timeout=2,
)


def _session_key(session_id: str) -> str:
    return f"{SESSION_PREFIX}{session_id}"


def save_session(
    session_id: str,
    state: dict,
) -> bool:
    try:
        redis_client.set(
            _session_key(session_id),
            json.dumps(
                state,
                ensure_ascii=False,
            ),
            ex=SESSION_TTL_SECONDS,
        )
        return True

    except RedisError as exc:
        logger.warning(
            "Redis save failed for session %s: %s",
            session_id,
            exc,
        )
        return False


def get_session(
    session_id: str,
) -> dict | None:
    try:
        data = redis_client.get(
            _session_key(session_id)
        )

        if not data:
            return None

        return json.loads(data)

    except RedisError as exc:
        logger.warning(
            "Redis read failed for session %s: %s",
            session_id,
            exc,
        )
        return None

    except json.JSONDecodeError as exc:
        logger.error(
            "Invalid session data for %s: %s",
            session_id,
            exc,
        )
        return None


def delete_session(
    session_id: str,
) -> bool:
    try:
        redis_client.delete(
            _session_key(session_id)
        )
        return True

    except RedisError as exc:
        logger.warning(
            "Redis delete failed for session %s: %s",
            session_id,
            exc,
        )
        return False


def session_exists(
    session_id: str,
) -> bool:
    try:
        return bool(
            redis_client.exists(
                _session_key(session_id)
            )
        )

    except RedisError as exc:
        logger.warning(
            "Redis existence check failed for session %s: %s",
            session_id,
            exc,
        )
        return False


def touch_session(
    session_id: str,
) -> bool:
    try:
        return bool(
            redis_client.expire(
                _session_key(session_id),
                SESSION_TTL_SECONDS,
            )
        )

    except RedisError as exc:
        logger.warning(
            "Redis touch failed for session %s: %s",
            session_id,
            exc,
        )
        return False