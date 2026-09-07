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
from datetime import datetime, timedelta, timezone

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


def write_clean(cur, records: list[dict]) -> tuple[int, int]:
    """Upsert into the serving table.

    Returns (inserted, updated). `xmax = 0` on the returned row is the
    Postgres idiom for "this was an insert, not an update" -- it saves a second
    round trip to distinguish new records from revised ones, and that split is
    what the panel shows as new-vs-updated.
    """
    if not records:
        return (0, 0)

    inserted = 0
    updated = 0

    for record in records:
        row = None
        cur.execute(
            """
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
            """,
            record,
        )
        row = cur.fetchone()
        if row and row["was_insert"]:
            inserted += 1
        else:
            updated += 1

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

def run(trigger: str = "manual", days_back: int = 3, verbose: bool = True) -> dict:
    started = time.monotonic()
    now = datetime.now(timezone.utc)

    watermark_raw = db.get_state(WATERMARK_KEY)
    watermark = parse_ts(watermark_raw) if watermark_raw else socrata.default_watermark(days_back)

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

    touched_keys: list[str] = []
    max_updated = watermark

    try:
        for page in socrata.fetch_since(watermark):
            stats["pages_fetched"] += 1
            stats["rows_fetched"] += len(page)

            records = []
            failures_by_key: dict[str, list] = {}

            for raw_row in page:
                record = shape(raw_row)
                if not record["unique_key"]:
                    stats["rows_rejected"] += 1
                    continue

                updated_at = record["source_updated_at"]
                if updated_at and updated_at > max_updated:
                    max_updated = updated_at

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
                touched_keys.append(record["unique_key"])

            with db.connection() as conn:
                with conn.cursor() as cur:
                    write_raw(cur, page, run_id)
                    inserted, updated = write_clean(cur, records)
                    write_failures(cur, failures_by_key, run_id)
            stats["rows_inserted"] += inserted
            stats["rows_updated"] += updated

            if verbose:
                print(
                    f"  page {stats['pages_fetched']}: {len(page)} rows -> "
                    f"+{inserted} new, ~{updated} revised, "
                    f"{stats['rows_rejected']} rejected"
                )

        with db.connection() as conn:
            with conn.cursor() as cur:
                stats["duplicates_collapsed"] = run_dedup(
                    cur, touched_keys, settings.dedup_window_minutes
                )
                stats["raw_pruned"], stats["clean_pruned"] = retention.prune(cur)

        affected_days = aggregate.rollup(touched_keys)

        # Advance the watermark by one millisecond past the newest record seen,
        # so the strict `>` comparison in the next run does not re-fetch the
        # boundary row on every single run forever.
        if max_updated > watermark:
            new_watermark = max_updated + timedelta(milliseconds=1)
            db.set_state(WATERMARK_KEY, new_watermark.isoformat())
            stats["watermark_after"] = new_watermark

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
