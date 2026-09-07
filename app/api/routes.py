"""API routes.

`/api/quality` is the point of the whole project. Everything else is the data
it is a quality report about.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException, Query

from app import db
from app.config import settings
from app.pipeline import retention
from app.pipeline.validate import ALL_RULE_NAMES

router = APIRouter()


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@router.get("/health", tags=["meta"], summary="Liveness and database reachability")
def health():
    """Cheap enough for the front end to poll while the free-tier instance is
    waking up."""
    ok = db.healthy()
    return {"status": "ok" if ok else "degraded", "database": ok}


# ---------------------------------------------------------------------------
# The quality panel
# ---------------------------------------------------------------------------

@router.get("/quality", tags=["quality"], summary="Data-quality report for the latest run")
def quality():
    """Everything the quality panel renders, in one request.

    Per-rule failure counts include rules that fired zero times. A rule that
    suddenly reports nothing is as informative as one that spikes -- usually it
    means an upstream field stopped being populated at all.
    """
    latest = db.query_one(
        "SELECT * FROM ingest_runs WHERE status <> 'running' ORDER BY started_at DESC LIMIT 1"
    )
    if not latest:
        raise HTTPException(
            status_code=503,
            detail="No completed ingest run yet. Run `python -m app.cli ingest`.",
        )

    previous = db.query_one(
        """
        SELECT * FROM ingest_runs
        WHERE status <> 'running' AND id < %s
        ORDER BY started_at DESC LIMIT 1
        """,
        (latest["id"],),
    )

    rule_rows = db.query(
        """
        SELECT rule, severity, COUNT(*) AS failures
        FROM validation_failures
        WHERE ingest_run_id = %s
        GROUP BY rule, severity
        """,
        (latest["id"],),
    )
    counted = {r["rule"]: r for r in rule_rows}

    fetched = latest["rows_fetched"] or 0
    rules = [
        {
            "rule": name,
            "failures": counted.get(name, {}).get("failures", 0),
            "severity": counted.get(name, {}).get("severity", "flag"),
            "rate_pct": round(
                100.0 * counted.get(name, {}).get("failures", 0) / fetched, 3
            ) if fetched else 0.0,
        }
        for name in ALL_RULE_NAMES
    ]
    rules.sort(key=lambda r: r["failures"], reverse=True)

    # Rolling 24h totals, which read better than a single run on a page a
    # stranger loads at an arbitrary moment.
    day = db.query_one(
        """
        SELECT
            COALESCE(SUM(rows_fetched), 0)          AS rows_fetched,
            COALESCE(SUM(rows_inserted), 0)         AS rows_inserted,
            COALESCE(SUM(rows_updated), 0)          AS rows_updated,
            COALESCE(SUM(rows_rejected), 0)         AS rows_rejected,
            COALESCE(SUM(rows_flagged), 0)          AS rows_flagged,
            COALESCE(SUM(duplicates_collapsed), 0)  AS duplicates_collapsed,
            COUNT(*)                                AS runs
        FROM ingest_runs
        WHERE started_at > now() - interval '24 hours' AND status <> 'running'
        """
    )

    totals = db.query_one(
        """
        SELECT
            (SELECT COUNT(*) FROM clean_requests)                              AS clean_rows,
            (SELECT COUNT(*) FROM clean_requests WHERE is_duplicate_of IS NOT NULL) AS duplicate_rows,
            (SELECT COUNT(*) FROM clean_requests WHERE defect_count > 0)       AS defective_rows,
            (SELECT COUNT(*) FROM raw_requests)                                AS raw_rows,
            (SELECT COUNT(*) FROM daily_agg)                                   AS agg_rows,
            (SELECT MIN(day) FROM daily_agg)                                   AS agg_since
        """
    )

    def delta(field: str):
        if not previous:
            return None
        return (latest[field] or 0) - (previous[field] or 0)

    return {
        "latest_run": {
            "id": latest["id"],
            "status": latest["status"],
            "trigger": latest["trigger"],
            "started_at": latest["started_at"],
            "finished_at": latest["finished_at"],
            "duration_seconds": latest["duration_seconds"],
            "watermark_before": latest["watermark_before"],
            "watermark_after": latest["watermark_after"],
            "rows_fetched": latest["rows_fetched"],
            "rows_inserted": latest["rows_inserted"],
            "rows_updated": latest["rows_updated"],
            "rows_rejected": latest["rows_rejected"],
            "rows_flagged": latest["rows_flagged"],
            "duplicates_collapsed": latest["duplicates_collapsed"],
            "unrecognized_types": latest["unrecognized_types"],
            "error": latest["error"],
        },
        "change_since_previous_run": {
            "rows_fetched": delta("rows_fetched"),
            "rows_flagged": delta("rows_flagged"),
            "rows_rejected": delta("rows_rejected"),
            "duplicates_collapsed": delta("duplicates_collapsed"),
        },
        "last_24h": day,
        "rules": rules,
        "totals": totals,
        "dedup_window_minutes": settings.dedup_window_minutes,
        "retention": {
            "raw_days": settings.raw_retention_days,
            "clean_days": settings.clean_retention_days,
            "daily_agg": "permanent",
        },
    }


@router.get("/runs", tags=["quality"], summary="Recent pipeline runs")
def runs(limit: int = Query(30, ge=1, le=200)):
    """The run log. Proof the scheduler is alive, and the first place to look
    when the data on the page stops moving."""
    rows = db.query(
        "SELECT * FROM ingest_runs ORDER BY started_at DESC LIMIT %s", (limit,)
    )
    freshness = db.query_one(
        """
        SELECT EXTRACT(EPOCH FROM (now() - MAX(finished_at))) / 60.0 AS minutes_since_last_success
        FROM ingest_runs WHERE status = 'success'
        """
    )
    minutes = freshness["minutes_since_last_success"] if freshness else None
    return {
        "runs": rows,
        "minutes_since_last_success": round(minutes, 1) if minutes is not None else None,
        # GitHub disables scheduled workflows after 60 days of repo inactivity,
        # so a stale pipeline usually means the cron was silently turned off
        # rather than that the code broke.
        "stale": bool(minutes is not None and minutes > 180),
    }


@router.get("/storage", tags=["quality"], summary="Per-table storage against the 0.5 GB budget")
def storage():
    tables = retention.estimate_sizes(db)
    total = sum(t["size_bytes"] or 0 for t in tables)
    budget = 512 * 1024 * 1024
    return {
        "tables": tables,
        "total_bytes": total,
        "budget_bytes": budget,
        "used_pct": round(100.0 * total / budget, 2),
    }


# ---------------------------------------------------------------------------
# The data
# ---------------------------------------------------------------------------

@router.get("/requests", tags=["data"], summary="Service requests from the clean table")
def requests(
    borough: Optional[str] = Query(None, description="MANHATTAN, BRONX, BROOKLYN, QUEENS, STATEN ISLAND"),
    complaint_type: Optional[str] = Query(None, description="Canonicalized complaint type"),
    status: Optional[str] = Query(None),
    defects_only: bool = Query(False, description="Only records that failed at least one rule"),
    include_duplicates: bool = Query(False, description="Include records collapsed as duplicates"),
    since: Optional[date] = Query(None),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    where = []
    params: dict = {"limit": limit, "offset": offset}

    if not include_duplicates:
        where.append("is_duplicate_of IS NULL")
    if borough:
        where.append("borough = %(borough)s")
        params["borough"] = borough.upper()
    if complaint_type:
        where.append("complaint_type_norm = %(complaint_type)s")
        params["complaint_type"] = complaint_type.upper()
    if status:
        where.append("status ILIKE %(status)s")
        params["status"] = status
    if defects_only:
        where.append("defect_count > 0")
    if since:
        where.append("created_date >= %(since)s")
        params["since"] = since

    clause = ("WHERE " + " AND ".join(where)) if where else ""

    rows = db.query(
        f"""
        SELECT unique_key, created_date, closed_date, agency, complaint_type,
               complaint_type_norm, descriptor, status, borough, incident_zip,
               incident_address, latitude, longitude, defect_count,
               is_duplicate_of, zip_borough_conflict, unrecognized_type
        FROM clean_requests
        {clause}
        ORDER BY created_date DESC NULLS LAST
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        params,
    )
    total = db.query_one(
        f"SELECT COUNT(*) AS n FROM clean_requests {clause}", params
    )
    return {"total": total["n"], "limit": limit, "offset": offset, "results": rows}


@router.get("/requests/{unique_key}", tags=["data"], summary="One request, with its rule failures")
def request_detail(unique_key: str):
    row = db.query_one("SELECT * FROM clean_requests WHERE unique_key = %s", (unique_key,))
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    failures = db.query(
        "SELECT rule, severity, detail, field_value, detected_at "
        "FROM validation_failures WHERE unique_key = %s ORDER BY detected_at DESC",
        (unique_key,),
    )
    duplicates = db.query(
        "SELECT unique_key, created_date FROM clean_requests WHERE is_duplicate_of = %s",
        (unique_key,),
    )
    return {"request": row, "validation_failures": failures, "collapsed_duplicates": duplicates}


@router.get("/daily", tags=["data"], summary="Daily aggregates (the permanent table)")
def daily(
    days: int = Query(30, ge=1, le=730),
    borough: Optional[str] = Query(None),
    complaint_type: Optional[str] = Query(None),
    group_by: Literal["day", "borough", "complaint_type"] = Query("day"),
):
    where = ["day >= CURRENT_DATE - %(days)s::int"]
    params: dict = {"days": days}
    if borough:
        where.append("borough = %(borough)s")
        params["borough"] = borough.upper()
    if complaint_type:
        where.append("complaint_type_norm = %(complaint_type)s")
        params["complaint_type"] = complaint_type.upper()
    clause = "WHERE " + " AND ".join(where)

    dimension = {
        "day": "day",
        "borough": "borough",
        "complaint_type": "complaint_type_norm",
    }[group_by]

    rows = db.query(
        f"""
        SELECT {dimension} AS key,
               SUM(request_count)  AS request_count,
               SUM(closed_count)   AS closed_count,
               SUM(defect_count)   AS defect_count,
               SUM(duplicate_count) AS duplicate_count,
               ROUND(AVG(median_hours_to_close)::numeric, 1) AS avg_median_hours_to_close
        FROM daily_agg
        {clause}
        GROUP BY 1
        ORDER BY 1
        """,
        params,
    )
    return {"group_by": group_by, "days": days, "results": rows}


@router.get("/complaint-types", tags=["data"], summary="Distinct complaint types with counts")
def complaint_types(limit: int = Query(40, ge=1, le=200)):
    rows = db.query(
        """
        SELECT complaint_type_norm AS complaint_type,
               COUNT(*) AS n,
               BOOL_OR(unrecognized_type) AS has_unrecognized
        FROM clean_requests
        WHERE complaint_type_norm IS NOT NULL AND is_duplicate_of IS NULL
        GROUP BY 1 ORDER BY 2 DESC LIMIT %s
        """,
        (limit,),
    )
    return {"results": rows}


@router.get("/boroughs", tags=["data"], summary="Per-borough counts and defect rates")
def boroughs():
    rows = db.query(
        """
        SELECT COALESCE(borough, 'UNSPECIFIED') AS borough,
               COUNT(*) AS n,
               COUNT(*) FILTER (WHERE defect_count > 0) AS with_defects,
               ROUND(100.0 * COUNT(*) FILTER (WHERE defect_count > 0)
                     / NULLIF(COUNT(*), 0), 2) AS defect_rate_pct
        FROM clean_requests
        WHERE is_duplicate_of IS NULL
        GROUP BY 1 ORDER BY 2 DESC
        """
    )
    return {"results": rows}
