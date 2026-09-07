"""Retention pruning.

Written in week one rather than discovered in week four. Neon's free plan is
0.5 GB and this feed produces on the order of 8,000-10,000 records a day, so
an unpruned `raw_requests` with its JSONB payload fills the budget in a couple
of months and writes start failing -- at which point the app looks abandoned
and the cause is invisible from the outside.

Retention is therefore a design input, not a cleanup task:

    raw_requests     7 days   payload kept for replaying recent rule changes
    clean_requests  60 days   the serving window
    daily_agg       forever   never pruned, and the reason the app ages well

`raw_requests` was 30 days until the first real ingest filled the 0.5 GB budget
in a single run (BREAKS.md 2026-09-07) -- the payload column is ~1.6 KB a row
and 30 days of it is more than half the plan on its own.

`validation_failures` is pruned alongside the raw table, since a failure whose
record is gone cannot be investigated anyway.
"""

from __future__ import annotations

from app.config import settings


def prune(cur) -> tuple[int, int]:
    """Delete rows outside the retention windows. Returns (raw, clean) counts.

    Takes a cursor rather than opening its own connection so the caller
    controls the transaction. The ingest run calls this first, before
    fetching, and commits it on its own: every row deleted here is already
    outside its window, so the delete is correct regardless of whether the
    rest of the run succeeds -- and doing it up front is what lets a database
    that hit its size limit recover on the next run instead of failing again.
    """
    cur.execute(
        "DELETE FROM raw_requests WHERE ingested_at < now() - make_interval(days => %s)",
        (settings.raw_retention_days,),
    )
    raw_pruned = cur.rowcount

    cur.execute(
        """
        DELETE FROM validation_failures
        WHERE detected_at < now() - make_interval(days => %s)
        """,
        (settings.raw_retention_days,),
    )

    cur.execute(
        """
        DELETE FROM clean_requests
        WHERE created_date < now() - make_interval(days => %s)
        """,
        (settings.clean_retention_days,),
    )
    clean_pruned = cur.rowcount

    # Run history is small and is the evidence the pipeline has been alive for
    # months, so it is kept far longer than the data itself.
    cur.execute("DELETE FROM ingest_runs WHERE started_at < now() - interval '365 days'")

    return (raw_pruned, clean_pruned)


def estimate_sizes(db_module) -> list[dict]:
    """Per-table size, so the storage budget is observable rather than assumed.
    Surfaced on the quality panel."""
    return db_module.query(
        """
        SELECT
            c.relname AS table_name,
            pg_size_pretty(pg_total_relation_size(c.oid)) AS size,
            pg_total_relation_size(c.oid) AS size_bytes,
            s.n_live_tup AS approx_rows
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
        WHERE n.nspname = 'public' AND c.relkind = 'r'
        ORDER BY pg_total_relation_size(c.oid) DESC
        """
    )
