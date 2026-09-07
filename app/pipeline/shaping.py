"""Turning a raw Socrata row into the normalized record the rules expect.

Deliberately its own module rather than a function inside ingest.py: shaping
has no database dependency, and keeping it here means the whole
fetch -> shape -> validate -> deduplicate path can be tested against a captured
fixture without Postgres, a connection string, or a single mock.

That boundary -- pure transformation here, I/O in ingest.py -- is what makes
the test suite fast enough to run on every push.
"""

from __future__ import annotations

from app.pipeline.normalize import (
    clean_text,
    normalize_address,
    normalize_borough,
    normalize_complaint_type,
    normalize_zip,
    parse_float,
    parse_ts,
)
from app.pipeline.socrata_fields import extract_updated_at


def shape(row: dict) -> dict:
    """One raw Socrata row -> one normalized record.

    Both the raw and the normalized value are kept for every field that has a
    rule attached (`*_raw`), because "this ZIP was empty" and "this ZIP was the
    string 'N/A'" are different defects and the quality panel reports them
    separately.

    Never raises. Socrata omits fields rather than nulling them, so a row can
    be missing almost anything, and a shaping function that throws would take
    down an entire 5,000-row page over one bad record.
    """
    zip_raw = row.get("incident_zip")
    ctype_raw = row.get("complaint_type")
    address_raw = row.get("incident_address") or row.get("street_name")
    key = row.get("unique_key")

    return {
        "unique_key": str(key) if key not in (None, "") else None,
        "created_date": parse_ts(row.get("created_date")),
        "closed_date": parse_ts(row.get("closed_date")),
        "agency": clean_text(row.get("agency"), 32),
        "agency_name": clean_text(row.get("agency_name"), 256),
        "complaint_type_raw": ctype_raw,
        "complaint_type": clean_text(ctype_raw, 256),
        "complaint_type_norm": normalize_complaint_type(ctype_raw),
        "descriptor": clean_text(row.get("descriptor"), 512),
        "status": clean_text(row.get("status"), 64),
        "borough": normalize_borough(row.get("borough")),
        "borough_raw": row.get("borough"),
        "incident_zip_raw": zip_raw,
        "incident_zip": normalize_zip(zip_raw),
        "incident_address": clean_text(address_raw, 256),
        "address_norm": normalize_address(address_raw),
        "latitude": parse_float(row.get("latitude")),
        "longitude": parse_float(row.get("longitude")),
        "resolution_description": clean_text(row.get("resolution_description"), 2000),
        "source_updated_at": extract_updated_at(row),
    }


def to_clean_row(record: dict, failures: list) -> dict:
    """Project a validated record onto the columns of `clean_requests`."""
    from app.pipeline import validate

    return {
        "unique_key": record["unique_key"],
        "created_date": record["created_date"],
        "closed_date": record["closed_date"],
        "agency": record["agency"],
        "agency_name": record["agency_name"],
        "complaint_type": record["complaint_type"],
        "complaint_type_norm": record["complaint_type_norm"],
        "descriptor": record["descriptor"],
        "status": record["status"],
        "borough": record["borough"],
        "incident_zip": record["incident_zip"],
        "incident_address": record["incident_address"],
        "address_norm": record["address_norm"],
        "latitude": record["latitude"],
        "longitude": record["longitude"],
        "resolution_description": record["resolution_description"],
        "source_updated_at": record["source_updated_at"],
        "defect_count": validate.defect_count(failures),
        "zip_borough_conflict": any(
            f.rule in ("borough_zip_conflict", "borough_geo_conflict") for f in failures
        ),
        "unrecognized_type": any(
            f.rule == "complaint_type_unrecognized" for f in failures
        ),
    }
