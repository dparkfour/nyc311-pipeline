"""The ingestion run: fetch, validate, deduplicate, aggregate, prune, log.

The run log is written first and updated last, so a run that dies halfway
leaves a row with status 'running' and a stale `started_at`. That is the
signal the quality panel surfaces -- a pipeline that fails loudly is fine, a
pipeline that fails silently is the actual risk, and the run log is what makes
the difference.
"""

from __future__ import annotations

import time
import traceback
from datetime import date, datetime, timedelta, timezone

from psycopg.types.json import Jsonb

from app import db
from app.config import settings
from app.pipeline import aggregate, dedup, retention, socrata, validate
from app.pipeline.normalize import parse_ts
from app.pipeline.shaping import shape, to_clean_row
from app.pipeline.socrata_fields import extract_updated_at

WATERMARK_KEY = "socrata_updated_at_watermark"


# ---------------------------------------------------------------------------
# Run bookkeeping
# ---------------------------------------------------------------------------

def start_run(trigger: str, watermark_before: datetime | None) -> int:
    row = db.query_one(
        """
        INSERT INTO ingest_runs (trigger, watermark_before, status)
        VALUES (%s, %s, 'running')
        RETURNING id
        """,
        (trigger, watermark_before),
    )
    return row["id"]


def finish_run(run_id: int, status: str, stats: dict, error: str | None = None) -> None:
    db.execute(
        """
        UPDATE ingest_runs SET
            finished_at = now(),
            status = %(status)s,
            watermark_after = %(watermark_after)s,
            pages_fetched = %(pages_fetched)s,
            rows_fetched = %(rows_fetched)s,
            rows_inserted = %(rows_inserted)s,
            rows_updated = %(rows_updated)s,
            rows_rejected = %(rows_rejected)s,
            rows_flagged = %(rows_flagged)s,
            duplicates_collapsed = %(duplicates_collapsed)s,
            unrecognized_types = %(unrecognized_types)s,
            raw_pruned = %(raw_pruned)s,
            clean_pruned = %(clean_pruned)s,
            duration_seconds = %(duration_seconds)s,
            error = %(error)s
        WHERE id = %(run_id)s
        """,
        {**stats, "status": status, "error": error, "run_id": run_id},
    )


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------

def write_raw(cur, rows: list[dict], run_id: int) -> None:
    """Store the untouched payload. `seen_count` increments on re-fetch, which
    is how often a record has been revised upstream -- a free, and genuinely
    interesting, statistic."""
    params = []
    for row in rows:
        key = row.get("unique_key")
        if not key:
            continue
        params.append(
            (
                str(key),
                Jsonb(row),
                extract_updated_at(row),
                parse_ts(row.get("created_date")),
                run_id,
            )
        )
    if not params:
        return

    cur.executemany(
        """
        INSERT INTO raw_requests
            (unique_key, payload, source_updated_at, created_date, ingest_run_id)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (unique_key) DO UPDATE SET
            payload = EXCLUDED.payload,
            source_updated_at = EXCLUDED.source_updated_at,
            ingested_at = now(),
            ingest_run_id = EXCLUDED.ingest_run_id,
            seen_count = raw_requests.seen_count + 1
        """,
        params,
    )


_CLEAN_UPSERT = """
    INSERT INTO clean_requests (
        unique_key, created_date, closed_date, agency, agency_name,
        complaint_type, complaint_type_norm, descriptor, status, borough,
        incident_zip, incident_address, address_norm, latitude, longitude,
        resolution_description, source_updated_at, defect_count,
        zip_borough_conflict, unrecognized_type, updated_at
    ) VALUES (
        %(unique_key)s, %(created_date)s, %(closed_date)s, %(agency)s,
        %(agency_name)s, %(complaint_type)s, %(complaint_type_norm)s,
        %(descriptor)s, %(status)s, %(borough)s, %(incident_zip)s,
        %(incident_address)s, %(address_norm)s, %(latitude)s, %(longitude)s,
        %(resolution_description)s, %(source_updated_at)s, %(defect_count)s,
        %(zip_borough_conflict)s, %(unrecognized_type)s, now()
    )
    ON CONFLICT (unique_key) DO UPDATE SET
        created_date = EXCLUDED.created_date,
        closed_date = EXCLUDED.closed_date,
        agency = EXCLUDED.agency,
        agency_name = EXCLUDED.agency_name,
        complaint_type = EXCLUDED.complaint_type,
        complaint_type_norm = EXCLUDED.complaint_type_norm,
        descriptor = EXCLUDED.descriptor,
        status = EXCLUDED.status,
        borough = EXCLUDED.borough,
        incident_zip = EXCLUDED.incident_zip,
        incident_address = EXCLUDED.incident_address,
        address_norm = EXCLUDED.address_norm,
        latitude = EXCLUDED.latitude,
        longitude = EXCLUDED.longitude,
        resolution_description = EXCLUDED.resolution_description,
        source_updated_at = EXCLUDED.source_updated_at,
        defect_count = EXCLUDED.defect_count,
        zip_borough_conflict = EXCLUDED.zip_borough_conflict,
        unrecognized_type = EXCLUDED.unrecognized_type,
        updated_at = now()
    RETURNING (xmax = 0) AS was_insert
"""


def write_clean(cur, records: list[dict]) -> tuple[int, int]:
    """Upsert a page into the serving table in one pipelined round trip.

    Returns (inserted, updated). `executemany(..., returning=True)` sends the
    whole batch down one pipeline and hands back one result set per row;
    `xmax = 0` on each is the Postgres idiom for "this was an insert, not an
    update", which is the new-vs-revised split the panel shows.

    The previous version issued a separate `cur.execute` per record. Against a
    remote database with a day-wide watermark gap that was the pipeline's
    dominant cost -- 82 minutes for 110k rows. See BREAKS.md 2026-09-07.
    """
    if not records:
        return (0, 0)

    cur.executemany(_CLEAN_UPSERT, records, returning=True)

    inserted = 0
    updated = 0
    while True:
        row = cur.fetchone()
        if row is not None:
            if row["was_insert"]:
                inserted += 1
            else:
                updated += 1
        if not cur.nextset():
            break

    return (inserted, updated)


def write_failures(cur, failures_by_key: dict, run_id: int) -> None:
    params = []
    for key, failures in failures_by_key.items():
        for failure in failures:
            params.append(
                (key, failure.rule, failure.severity, failure.detail,
                 failure.field_value, run_id)
            )
    if not params:
        return
    cur.executemany(
        """
        INSERT INTO validation_failures
            (unique_key, rule, severity, detail, field_value, ingest_run_id)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        params,
    )


# ---------------------------------------------------------------------------
# Deduplication pass
# ---------------------------------------------------------------------------

def run_dedup(cur, touched_keys: list[str], window_minutes: int) -> int:
    """Deduplicate the records this run touched, against their neighbours.

    The neighbour query is the subtle part: a new record's duplicate may
    already be stored from a previous run, so comparing the batch only against
    itself misses most real duplicates. This pulls every stored record sharing
    a candidate key within twice the window, and runs the pure collapse
    function over the union.
    """
    if not touched_keys:
        return 0

    cur.execute(
        """
        WITH touched AS (
            SELECT complaint_type_norm, address_norm, created_date
            FROM clean_requests
            WHERE unique_key = ANY(%(keys)s)
              AND complaint_type_norm IS NOT NULL
              AND address_norm IS NOT NULL
        )
        SELECT DISTINCT c.unique_key, c.complaint_type_norm,
               c.address_norm, c.created_date
        FROM clean_requests c
        JOIN touched t
          ON c.complaint_type_norm = t.complaint_type_norm
         AND c.address_norm = t.address_norm
         AND c.created_date BETWEEN t.created_date - %(window)s
                                AND t.created_date + %(window)s
        """,
        {"keys": touched_keys, "window": timedelta(minutes=window_minutes * 2)},
    )
    candidates = cur.fetchall()

    links = dedup.collapse(candidates, window_minutes)
    if not links:
        return 0

    cur.executemany(
        "UPDATE clean_requests SET is_duplicate_of = %s WHERE unique_key = %s",
        [(link.duplicate_of, link.unique_key) for link in links],
    )
    return len(links)


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

def run(
    trigger: str = "manual",
    days_back: int = 3,
    verbose: bool = True,
    reset_watermark: bool = False,
) -> dict:
    started = time.monotonic()
    now = datetime.now(timezone.utc)

    watermark_raw = db.get_state(WATERMARK_KEY)
    if reset_watermark or not watermark_raw:
        watermark = socrata.default_watermark(days_back)
        if watermark_raw and verbose:
            print(f"run: --reset given, watermark moved back to {watermark.isoformat()}")
    else:
        watermark = parse_ts(watermark_raw)

    run_id = start_run(trigger, watermark)

    stats = {
        "watermark_after": watermark,
        "pages_fetched": 0,
        "rows_fetched": 0,
        "rows_inserted": 0,
        "rows_updated": 0,
        "rows_rejected": 0,
        "rows_flagged": 0,
        "duplicates_collapsed": 0,
        "unrecognized_types": 0,
        "raw_pruned": 0,
        "clean_pruned": 0,
        "duration_seconds": 0.0,
    }

    if verbose:
        print(f"run {run_id}: watermark {watermark.isoformat()}")

    affected_days: set[date] = set()
    max_updated = watermark

    try:
        # Prune before fetching, not after. A database that filled its budget on
        # a previous run can only recover if the old rows go before the new ones
        # arrive -- pruning at the end of the run is too late to help. Deletes
        # are all outside the retention window, so committing them independently
        # of this run's outcome is safe.
        with db.connection() as conn:
            with conn.cursor() as cur:
                stats["raw_pruned"], stats["clean_pruned"] = retention.prune(cur)

        for page in socrata.fetch_since(watermark):
            stats["pages_fetched"] += 1
            stats["rows_fetched"] += len(page)

            records = []
            failures_by_key: dict[str, list] = {}
            page_keys: list[str] = []
            page_max_updated = max_updated

            for raw_row in page:
                record = shape(raw_row)
                if not record["unique_key"]:
                    stats["rows_rejected"] += 1
                    continue

                updated_at = record["source_updated_at"]
                if updated_at and updated_at > page_max_updated:
                    page_max_updated = updated_at

                failures = validate.validate(record, now=now)
                if failures:
                    failures_by_key[record["unique_key"]] = failures

                if validate.is_rejected(failures):
                    stats["rows_rejected"] += 1
                    continue

                if failures:
                    stats["rows_flagged"] += 1
                if any(f.rule == "complaint_type_unrecognized" for f in failures):
                    stats["unrecognized_types"] += 1

                records.append(to_clean_row(record, failures))
                page_keys.append(record["unique_key"])

            # One transaction per page: raw payloads, clean rows, failures and
            # this page's dedup either all land or none do.
            with db.connection() as conn:
                with conn.cursor() as cur:
                    write_raw(cur, page, run_id)
                    inserted, updated = write_clean(cur, records)
                    write_failures(cur, failures_by_key, run_id)
                    stats["duplicates_collapsed"] += run_dedup(
                        cur, page_keys, settings.dedup_window_minutes
                    )
            stats["rows_inserted"] += inserted
            stats["rows_updated"] += updated

            # Roll up the days this page touched, THEN advance the watermark. In
            # that order an interruption re-does a page rather than skipping its
            # aggregates: the run is resumable at page granularity, which is
            # what stops a killed or timed-out run from restarting at zero and
            # re-fetching the whole gap. See BREAKS.md 2026-09-07.
            affected_days.update(aggregate.rollup(page_keys))

            if page_max_updated > max_updated:
                max_updated = page_max_updated
                new_watermark = max_updated + timedelta(milliseconds=1)
                db.set_state(WATERMARK_KEY, new_watermark.isoformat())
                stats["watermark_after"] = new_watermark

            if verbose:
                print(
                    f"  page {stats['pages_fetched']}: {len(page)} rows -> "
                    f"+{inserted} new, ~{updated} revised, "
                    f"{stats['rows_rejected']} rejected, "
                    f"watermark -> {max_updated.isoformat()}"
                )

        stats["duration_seconds"] = round(time.monotonic() - started, 2)
        finish_run(run_id, "success", stats)

        if verbose:
            print(
                f"run {run_id}: success in {stats['duration_seconds']}s -- "
                f"{stats['rows_fetched']} fetched, "
                f"{stats['duplicates_collapsed']} duplicates collapsed, "
                f"{len(affected_days)} days re-aggregated"
            )

        return {"run_id": run_id, "status": "success", **stats}

    except Exception as exc:
        stats["duration_seconds"] = round(time.monotonic() - started, 2)
        error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[:2000]}"
        # 'partial' rather than 'failed' when rows already landed: the data is
        # in the database and the watermark simply did not advance, so the next
        # run picks up where this one stopped.
        status = "partial" if stats["rows_inserted"] or stats["rows_updated"] else "failed"
        try:
            finish_run(run_id, status, stats, error=error)
        except Exception:
            pass
        if verbose:
            print(f"run {run_id}: {status} -- {exc}")
        raise
