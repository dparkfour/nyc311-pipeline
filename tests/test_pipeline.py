"""End-to-end tests over a captured page of real-shaped Socrata rows.

These run the actual shaping and validation path the pipeline uses, without a
database and without touching the network. If the upstream feed changes shape,
re-capture the fixture with `python -m app.cli probe --sample` and these tests
say what broke.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.pipeline import dedup, validate
from app.pipeline.shaping import shape
from app.pipeline.socrata_fields import extract_updated_at

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)


def test_fixture_covers_every_defect_class(socrata_page):
    """The fixture is a regression asset, not sample data. If a row is edited
    out, this fails before the rule tests start passing vacuously."""
    records = [shape(row) for row in socrata_page]
    rules = {
        f.rule
        for record in records
        for f in validate.validate(record, now=NOW)
    }
    for expected in (
        "zip_malformed",
        "zip_outside_nyc",
        "coords_null_island",
        "coords_swapped",
        "closed_before_created",
        "borough_zip_conflict",
        "complaint_type_unrecognized",
        "unique_key_missing",
    ):
        assert expected in rules, f"fixture no longer exercises {expected}"


def test_shape_extracts_the_updated_at_watermark(socrata_page):
    """The single most important field in the project. If `:updated_at` stops
    being extracted the watermark freezes and ingestion silently stops."""
    for row in socrata_page:
        assert extract_updated_at(row) is not None


def test_watermark_advances_past_the_newest_row(socrata_page):
    stamps = [extract_updated_at(r) for r in socrata_page]
    assert max(stamps) > min(stamps), "fixture rows must not share one timestamp"


def test_zip_plus_four_row_is_not_flagged_for_its_zip(socrata_page):
    """Row 60000002 carries '11215-3401'. Normalization handles it, so it must
    not appear as a ZIP defect."""
    row = next(r for r in socrata_page if r.get("unique_key") == "60000002")
    record = shape(row)
    assert record["incident_zip"] == "11215"
    assert not any(
        f.rule.startswith("zip_") for f in validate.validate(record, now=NOW)
    )


def test_the_renamed_heating_category_is_canonicalized(socrata_page):
    """60000001 says 'HEAT/HOT WATER' and 60000002 says 'Heating'. They are the
    same category and must aggregate as one."""
    a = shape(next(r for r in socrata_page if r["unique_key"] == "60000001"))
    b = shape(next(r for r in socrata_page if r["unique_key"] == "60000002"))
    assert a["complaint_type_norm"] == b["complaint_type_norm"] == "HEAT/HOT WATER"


def test_the_same_doorway_spelled_two_ways_deduplicates(socrata_page):
    """60000001 is '123 EAST 14TH STREET' and 60000002 is '123 E 14 ST', 27
    minutes apart. Without address normalization this pair is invisible."""
    records = [shape(r) for r in socrata_page if r.get("unique_key") in ("60000001", "60000002")]
    links = dedup.collapse(records, window_minutes=60)
    assert len(links) == 1
    assert links[0].duplicate_of == "60000001"


def test_records_missing_a_unique_key_are_rejected(socrata_page):
    keyless = [shape(r) for r in socrata_page if not r.get("unique_key")]
    assert keyless, "fixture must contain a row with no unique_key"
    for record in keyless:
        assert validate.is_rejected(validate.validate(record, now=NOW))


def test_almost_everything_survives_validation(socrata_page):
    """The layer flags; it does not throw data away. A page of eight rows with
    six defect classes in it should still yield seven servable records."""
    records = [shape(r) for r in socrata_page]
    kept = [r for r in records if not validate.is_rejected(validate.validate(r, now=NOW))]
    assert len(kept) == len(records) - 1  # only the keyless row is dropped


def test_shaping_never_raises_on_a_malformed_row():
    """Socrata omits fields rather than nulling them, so a row can be missing
    almost anything. Shaping must degrade, not crash."""
    for row in ({}, {"unique_key": "1"}, {"unique_key": "2", "latitude": "N/A",
                                           "created_date": "not a date",
                                           "incident_zip": 11215}):
        record = shape(row)
        assert "unique_key" in record
