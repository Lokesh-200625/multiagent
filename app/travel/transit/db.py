from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg import Connection

from app.core.config import settings


def get_database_url() -> str:
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not configured.")

    return settings.database_url


@contextmanager
def get_connection() -> Iterator[Connection]:
    connection = psycopg.connect(
        get_database_url(),
        autocommit=False,
    )

    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()