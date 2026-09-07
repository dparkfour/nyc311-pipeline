"""Retention pruning.

Written in week one rather than discovered in week four. Neon's free plan is
0.5 GB and this feed produces on the order of 8,000-10,000 records a day, so
an unpruned `raw_requests` with its JSONB payload fills the budget in a couple
of months and writes start failing -- at which point the app looks abandoned
and the cause is invisible from the outside.

Retention is therefore a design input, not a cleanup task:

    raw_requests    30 days   payload kept for replaying rule changes
    clean_requests  60 days   the serving window
    daily_agg       forever   never pruned, and the reason the app ages well

`validation_failures` is pruned alongside the raw table, since a failure whose
record is gone cannot be investigated anyway.
"""

from __future__ import annotations

from app.config import settings


def prune(cur) -> tuple[int, int]:
    """Delete rows outside the retention windows. Returns (raw, clean) counts.

    Takes a cursor rather than opening its own connection so it runs inside
    the ingest run's transaction -- a run that fails after pruning should not
    have pruned.
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
            relname AS table_name,
            pg_size_pretty(pg_total_relation_size(c.oid)) AS size,
            pg_total_relation_size(c.oid) AS size_bytes,
            n_live_tup AS approx_rows
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
        WHERE n.nspname = 'public' AND c.relkind = 'r'
        ORDER BY pg_total_relation_size(c.oid) DESC
        """
    )
