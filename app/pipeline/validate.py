"""Validation rules.

Every rule is a pure function with the signature

    (record: dict) -> Failure | None

taking an already-normalized record. No database, no network, no clock. That
constraint is the whole reason the test suite is cheap: a rule test is a dict
in and an assertion out, with nothing to mock.

Severity is a three-way decision, and the middle option is the one that
matters:

    reject   the record is not trustworthy enough to serve -- it is written to
             raw_requests and counted, but never reaches clean_requests
    flag     the record is served, with the defect recorded and surfaced
    observe  not a defect at all; something changed upstream and we want the
             count, not a judgement

Almost everything is `flag`. Rejecting real complaints because a city agency
typed a ZIP badly would make the app less useful and less honest than serving
them with the defect visible.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Callable, Optional

from app.pipeline import geo

REJECT = "reject"
FLAG = "flag"
OBSERVE = "observe"


@dataclass(frozen=True)
class Failure:
    rule: str
    severity: str
    detail: str
    field_value: str | None = None


# ---------------------------------------------------------------------------
# Rule 1 -- ZIP validity
# ---------------------------------------------------------------------------

def check_zip(record: dict) -> Optional[Failure]:
    """`incident_zip` contains blanks, ZIP+4, non-NYC ZIPs and outright garbage.

    Normalization has already reduced ZIP+4 to five digits, so a None here
    means the raw value was empty or unparseable, and those are different
    problems worth different labels.
    """
    raw = record.get("incident_zip_raw")
    zip5 = record.get("incident_zip")

    if zip5 is None:
        if raw is None or str(raw).strip() == "":
            return Failure("zip_missing", FLAG, "incident_zip is empty")
        return Failure(
            "zip_malformed",
            FLAG,
            "incident_zip is not a parseable five-digit ZIP",
            str(raw)[:64],
        )

    if not geo.is_nyc_zip(zip5):
        return Failure(
            "zip_outside_nyc",
            FLAG,
            "ZIP is well-formed but not allocated to New York City",
            zip5,
        )

    return None


# ---------------------------------------------------------------------------
# Rule 2 -- coordinate sanity
# ---------------------------------------------------------------------------

def check_coordinates(record: dict) -> Optional[Failure]:
    """Missing coordinates, (0, 0), transposed pairs, and points outside NYC.

    (0, 0) gets its own rule rather than being folded into "outside NYC"
    because it is a sentinel, not an error: some upstream system writes zeros
    where it means null, and the count of those is a different fact about the
    feed than the count of genuinely wrong coordinates.
    """
    lat = record.get("latitude")
    lon = record.get("longitude")

    if lat is None and lon is None:
        return Failure("coords_missing", FLAG, "latitude and longitude are both absent")

    if lat is None or lon is None:
        return Failure(
            "coords_partial",
            FLAG,
            "exactly one of latitude/longitude is present",
            f"lat={lat} lon={lon}",
        )

    if geo.is_null_island(lat, lon):
        return Failure(
            "coords_null_island",
            FLAG,
            "coordinates are (0, 0) -- a null sentinel, not a location",
            "0,0",
        )

    if geo.looks_swapped(lat, lon):
        return Failure(
            "coords_swapped",
            FLAG,
            "latitude and longitude appear transposed",
            f"{lat},{lon}",
        )

    if not geo.in_nyc_bbox(lat, lon):
        return Failure(
            "coords_outside_nyc",
            FLAG,
            "coordinates fall outside the New York City bounding box",
            f"{lat},{lon}",
        )

    return None


# ---------------------------------------------------------------------------
# Rule 3 -- date ordering
# ---------------------------------------------------------------------------

# A closed_date a few seconds before created_date is clock skew between two
# city systems, not a data defect. Anything beyond this is real.
CLOCK_SKEW_TOLERANCE = timedelta(minutes=1)


def check_dates(record: dict) -> Optional[Failure]:
    """`closed_date` occasionally precedes `created_date`.

    Note the watermark interaction: because this pipeline watermarks on
    `:updated_at` rather than `created_date`, a record is re-ingested when it
    is closed and this rule runs against the complete row. Watermarking on
    creation time instead would fire this rule against half-populated records
    and never see the correction.
    """
    created = record.get("created_date")
    closed = record.get("closed_date")

    if created is None:
        return Failure("created_date_missing", REJECT, "created_date is absent")

    if closed is None:
        return None  # an open request; not a defect

    if closed < created - CLOCK_SKEW_TOLERANCE:
        delta = created - closed
        return Failure(
            "closed_before_created",
            FLAG,
            f"closed_date precedes created_date by {delta}",
            closed.isoformat(),
        )

    return None


def check_created_date_future(record: dict, now=None) -> Optional[Failure]:
    """A created_date in the future is a data-entry defect, not a forecast.

    `now` is a parameter rather than a call to datetime.now() inside the
    function so this rule stays pure and its test does not depend on the wall
    clock.
    """
    created = record.get("created_date")
    if created is None or now is None:
        return None
    if created > now + timedelta(hours=1):
        return Failure(
            "created_date_future",
            FLAG,
            "created_date is in the future",
            created.isoformat(),
        )
    return None


# ---------------------------------------------------------------------------
# Rule 4 -- borough vs. geography
# ---------------------------------------------------------------------------

def check_borough_agreement(record: dict) -> Optional[Failure]:
    """`borough` sometimes disagrees with the ZIP and with the coordinates.

    Two comparisons, reported separately, because they fail for different
    reasons: a ZIP mismatch is usually a typo in the address, while a
    coordinate mismatch is usually a bad geocode.

    ZIPs that straddle a borough line -- Marble Hill's 10463 above all -- are
    excluded by geo.zip_borough_agrees returning None. That exclusion was added
    after the rule fired on several hundred legitimate rows; see BREAKS.md.
    """
    borough = record.get("borough")
    zip5 = record.get("incident_zip")
    lat = record.get("latitude")
    lon = record.get("longitude")

    agrees = geo.zip_borough_agrees(zip5, borough)
    if agrees is False:
        return Failure(
            "borough_zip_conflict",
            FLAG,
            f"borough is {borough} but ZIP {zip5} is allocated to "
            f"{geo.borough_for_zip(zip5)}",
            f"{borough}/{zip5}",
        )

    if borough and geo.in_nyc_bbox(lat, lon):
        by_polygon = geo.point_in_borough(lat, lon)
        # None means the boundary cache is absent -- not checked, not a failure.
        if by_polygon is not None and by_polygon != borough:
            return Failure(
                "borough_geo_conflict",
                FLAG,
                f"borough is {borough} but the coordinates fall in {by_polygon}",
                f"{borough}/{lat},{lon}",
            )

    return None


# ---------------------------------------------------------------------------
# Rule 5 -- complaint_type drift
# ---------------------------------------------------------------------------

def check_complaint_type_drift(record: dict) -> Optional[Failure]:
    """Categories get renamed and split upstream.

    The correct behaviour when that happens is NOT to drop the record. It is a
    real complaint; the only thing that changed is a label. So this is
    severity `observe`: the record passes, the unrecognized category is
    counted, and the count is shown on the quality panel where a rising number
    is the signal that the alias table needs a new entry.

    Rejecting here would mean the app silently stopped reporting a whole
    category on the day the city renamed it -- which is exactly the failure
    mode a data-quality layer exists to prevent.
    """
    from app.pipeline.normalize import is_recognized_complaint_type

    raw = record.get("complaint_type_raw")

    if raw is None or str(raw).strip() == "":
        return Failure("complaint_type_missing", REJECT, "complaint_type is absent")

    if not is_recognized_complaint_type(raw):
        return Failure(
            "complaint_type_unrecognized",
            OBSERVE,
            "complaint type is not in the known-category table; record kept",
            str(raw)[:120],
        )

    return None


# ---------------------------------------------------------------------------
# Rule 6 -- identity
# ---------------------------------------------------------------------------

def check_unique_key(record: dict) -> Optional[Failure]:
    key = record.get("unique_key")
    if key is None or str(key).strip() == "":
        return Failure("unique_key_missing", REJECT, "unique_key is absent")
    return None


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

RULES: list[Callable[[dict], Optional[Failure]]] = [
    check_unique_key,
    check_complaint_type_drift,
    check_dates,
    check_zip,
    check_coordinates,
    check_borough_agreement,
]

# Every rule name a record can fail. The quality panel reports a rate for each
# of these, including the ones at zero -- a rule that suddenly reports nothing
# is as informative as one that spikes.
ALL_RULE_NAMES = [
    "unique_key_missing",
    "complaint_type_missing",
    "complaint_type_unrecognized",
    "created_date_missing",
    "created_date_future",
    "closed_before_created",
    "zip_missing",
    "zip_malformed",
    "zip_outside_nyc",
    "coords_missing",
    "coords_partial",
    "coords_null_island",
    "coords_swapped",
    "coords_outside_nyc",
    "borough_zip_conflict",
    "borough_geo_conflict",
]


def validate(record: dict, now=None) -> list[Failure]:
    """Run every rule against one normalized record.

    Returns all failures, not the first -- a record with a bad ZIP *and* bad
    coordinates should count against both rules, or the panel's per-rule rates
    are quietly wrong.
    """
    failures: list[Failure] = []
    for rule in RULES:
        failure = rule(record)
        if failure is not None:
            failures.append(failure)

    future = check_created_date_future(record, now=now)
    if future is not None:
        failures.append(future)

    return failures


def is_rejected(failures: list[Failure]) -> bool:
    return any(f.severity == REJECT for f in failures)


def defect_count(failures: list[Failure]) -> int:
    """Observations are not defects, so they do not count here."""
    return sum(1 for f in failures if f.severity in (REJECT, FLAG))
