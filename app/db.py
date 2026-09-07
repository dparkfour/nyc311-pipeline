"""Database access.

psycopg 3 with a small connection pool. No ORM: the queries here are simple
enough that raw SQL is clearer than a mapping layer, and the aggregate rollup
is a single statement that would be worse expressed any other way.

Neon scales to zero after ~5 minutes idle, so the first connection after a
quiet period pays a wake-up cost. The pool is configured to tolerate that
rather than to fail fast.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import settings

_pool: ConnectionPool | None = None

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=settings.require_database_url(),
            min_size=0,          # Neon scales to zero; don't hold connections open
            max_size=5,          # Neon free tier is not generous with connections
            timeout=30,          # tolerate a cold Neon wake-up
            kwargs={"row_factory": dict_row},
            open=True,
        )
    return _pool


def close_pool() -> None:
    """Close the pool if one was opened.

    psycopg's pool runs worker threads that print
    `couldn't stop thread 'pool-1-worker-N' within 5.0 seconds` on interpreter
    shutdown when the pool is garbage-collected without being closed -- harmless
    but noisy, and noise in a pipeline's output is a real cost. The CLI calls
    this before it returns; the long-lived API process never does.
    """
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    """Yield a pooled connection. Commits on clean exit, rolls back on error."""
    pool = get_pool()
    with pool.connection() as conn:
        yield conn


@contextmanager
def cursor() -> Iterator[psycopg.Cursor]:
    with connection() as conn:
        with conn.cursor() as cur:
            yield cur


def query(sql: str, params: tuple | dict | None = None) -> list[dict[str, Any]]:
    with cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def query_one(sql: str, params: tuple | dict | None = None) -> dict[str, Any] | None:
    with cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


def execute(sql: str, params: tuple | dict | None = None) -> int:
    with cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def migrate() -> None:
    """Apply schema.sql. Idempotent -- every statement is IF NOT EXISTS, so
    this runs safely on every deploy."""
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
    print("migrate: schema applied")


# --- pipeline_state helpers -------------------------------------------------

def get_state(key: str) -> str | None:
    row = query_one("SELECT value FROM pipeline_state WHERE key = %s", (key,))
    return row["value"] if row else None


def set_state(key: str, value: str) -> None:
    execute(
        """
        INSERT INTO pipeline_state (key, value, updated_at)
        VALUES (%s, %s, now())
        ON CONFLICT (key) DO UPDATE
            SET value = EXCLUDED.value, updated_at = now()
        """,
        (key, value),
    )


def healthy() -> bool:
    try:
        query_one("SELECT 1 AS ok")
        return True
    except Exception:
        return False
