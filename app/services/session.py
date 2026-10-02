import json

import redis


redis_client = redis.Redis(
    host="localhost",
    port=6379,
    decode_responses=True,
)


SESSION_PREFIX = "session:"
SESSION_TTL_SECONDS = 60 * 60 * 24


def _session_key(session_id: str) -> str:
    return f"{SESSION_PREFIX}{session_id}"


def save_session(
    session_id: str,
    state: dict,
) -> None:

    redis_client.set(
        _session_key(session_id),
        json.dumps(
            state,
            ensure_ascii=False,
        ),
        ex=SESSION_TTL_SECONDS,
    )


def get_session(
    session_id: str,
) -> dict | None:

    data = redis_client.get(
        _session_key(session_id)
    )

    if not data:
        return None

    return json.loads(data)


def delete_session(
    session_id: str,
) -> None:

    redis_client.delete(
        _session_key(session_id)
    )


def session_exists(
    session_id: str,
) -> bool:

    return bool(
        redis_client.exists(
            _session_key(session_id)
        )
    )


def touch_session(
    session_id: str,
) -> None:

    redis_client.expire(
        _session_key(session_id),
        SESSION_TTL_SECONDS,
    )