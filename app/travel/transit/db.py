import os
import psycopg2
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager

DB_URL = os.getenv("DATABASE_URL")

@contextmanager
def get_connection():
    conn = psycopg2.connect(DB_URL, cursor_factory=RealDictCursor)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()