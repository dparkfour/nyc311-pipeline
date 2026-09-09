"""Daily rollup into `daily_agg` -- the only permanent table.

`clean_requests` holds 60 days and `raw_requests` keeps 7 days of flagged-row
payloads only, because Neon's free tier is 0.5 GB and the source dataset is
roughly 40 million rows. `daily_agg` is
what makes the app still worth opening in April: one row per day x borough x
complaint type is a few megabytes for a year, and it is the trend data no
screener can tell was cheap to keep.

Recomputed rather than incremented. A record's `closed_date` and `status`
change after the fact, so its contribution to a day's median-time-to-close is
not known when it first arrives. Only the days a run actually touched are
recomputed, so this stays proportional to new data rather than to history.
"""

from __future__ import annotations

from datetime import date

from app import db


def rollup(touched_keys: list[str] | None = None, days: int | None = None) -> list[date]:
    """Recompute `daily_agg` for affected days. Returns the days rewritten.

    Pass `touched_keys` for the incremental path an ingest run uses, or `days`
    to force a rebuild of the last N days after changing a rule.
    """
    if days is not None:
        rows = db.query(
            """
            SELECT DISTINCT created_date::date AS day
            FROM clean_requests
            WHERE created_date >= (CURRENT_DATE - %s::int)
            """,
            (days,),
        )
    elif touched_keys:
        rows = db.query(
            """
            SELECT DISTINCT created_date::date AS day
            FROM clean_requests
            WHERE unique_key = ANY(%s) AND created_date IS NOT NULL
            """,
            (touched_keys,),
        )
    else:
        return []

    affected = [r["day"] for r in rows if r["day"] is not None]
    if not affected:
        return []

    db.execute(
        """
        INSERT INTO daily_agg (
            day, borough, complaint_type_norm, request_count, closed_count,
            median_hours_to_close, defect_count, duplicate_count, computed_at
        )
        SELECT
            created_date::date                              AS day,
            COALESCE(borough, 'UNSPECIFIED')                AS borough,
            COALESCE(complaint_type_norm, 'UNKNOWN')        AS complaint_type_norm,
            -- duplicates are excluded from the headline count: the aggregate
            -- should say how many real problems were reported, not how many
            -- times people called about them
            COUNT(*) FILTER (WHERE is_duplicate_of IS NULL) AS request_count,
            COUNT(*) FILTER (WHERE is_duplicate_of IS NULL
                               AND closed_date IS NOT NULL) AS closed_count,
            PERCENTILE_CONT(0.5) WITHIN GROUP (
                ORDER BY EXTRACT(EPOCH FROM (closed_date - created_date)) / 3600.0
            ) FILTER (WHERE is_duplicate_of IS NULL
                        AND closed_date IS NOT NULL
                        AND closed_date >= created_date)    AS median_hours_to_close,
            COUNT(*) FILTER (WHERE defect_count > 0
                               AND is_duplicate_of IS NULL) AS defect_count,
            COUNT(*) FILTER (WHERE is_duplicate_of IS NOT NULL) AS duplicate_count,
            now()                                          AS computed_at
        FROM clean_requests
        WHERE created_date::date = ANY(%s)
        GROUP BY 1, 2, 3
        ON CONFLICT (day, borough, complaint_type_norm) DO UPDATE SET
            request_count = EXCLUDED.request_count,
            closed_count = EXCLUDED.closed_count,
            median_hours_to_close = EXCLUDED.median_hours_to_close,
            defect_count = EXCLUDED.defect_count,
            duplicate_count = EXCLUDED.duplicate_count,
            computed_at = now()
        """,
        (affected,),
    )

    return affected
