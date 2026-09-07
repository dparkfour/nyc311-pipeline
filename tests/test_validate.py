from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.pipeline import validate
from app.pipeline.validate import (
    check_borough_agreement,
    check_complaint_type_drift,
    check_coordinates,
    check_created_date_future,
    check_dates,
    check_zip,
)

UTC = timezone.utc


def record(**overrides) -> dict:
    """A clean, valid record. Each test breaks exactly one thing, so a failure
    names the rule that regressed rather than a whole scenario."""
    base = {
        "unique_key": "60000001",
        "created_date": datetime(2026, 9, 2, 9, 14, tzinfo=UTC),
        "closed_date": datetime(2026, 9, 3, 11, 2, tzinfo=UTC),
        "complaint_type_raw": "HEAT/HOT WATER",
        "complaint_type_norm": "HEAT/HOT WATER",
        "borough": "BROOKLYN",
        "incident_zip": "11215",
        "incident_zip_raw": "11215",
        "latitude": 40.6626,
        "longitude": -73.9885,
        "address_norm": "123 E 14 ST",
    }
    base.update(overrides)
    return base


class TestZip:
    def test_valid_nyc_zip_passes(self):
        assert check_zip(record()) is None

    def test_blank_zip_is_missing_not_malformed(self):
        failure = check_zip(record(incident_zip=None, incident_zip_raw=""))
        assert failure is not None and failure.rule == "zip_missing"

    def test_garbage_zip_is_malformed_and_keeps_the_offending_value(self):
        failure = check_zip(record(incident_zip=None, incident_zip_raw="N/A"))
        assert failure is not None
        assert failure.rule == "zip_malformed"
        assert failure.field_value == "N/A"

    def test_wellformed_non_nyc_zip_is_its_own_category(self):
        """07030 is Hoboken. It is a perfectly valid ZIP, which is why it needs
        a different rule from 'malformed'."""
        failure = check_zip(record(incident_zip="07030", incident_zip_raw="07030"))
        assert failure is not None and failure.rule == "zip_outside_nyc"

    def test_zip_plus_four_is_not_a_defect_after_normalization(self):
        assert check_zip(record(incident_zip="11215", incident_zip_raw="11215-3401")) is None

    def test_no_rule_rejects_a_record_over_its_zip(self):
        """A bad ZIP flags; it never drops a real complaint."""
        for raw in ("", "N/A", "07030"):
            failure = check_zip(record(incident_zip=None, incident_zip_raw=raw))
            if failure:
                assert failure.severity != validate.REJECT


class TestCoordinates:
    def test_valid_coordinates_pass(self):
        assert check_coordinates(record()) is None

    def test_null_island_is_distinguished_from_missing(self):
        """(0, 0) is a sentinel written by an upstream system, and the count of
        those is a different fact about the feed than the count of absences."""
        failure = check_coordinates(record(latitude=0.0, longitude=0.0))
        assert failure is not None and failure.rule == "coords_null_island"

    def test_both_absent(self):
        failure = check_coordinates(record(latitude=None, longitude=None))
        assert failure is not None and failure.rule == "coords_missing"

    def test_exactly_one_absent(self):
        failure = check_coordinates(record(longitude=None))
        assert failure is not None and failure.rule == "coords_partial"

    def test_transposed_pair_is_detected(self):
        """NYC longitude ~-74 is a valid latitude, so a swap produces a
        superficially plausible pair."""
        failure = check_coordinates(record(latitude=-73.9885, longitude=40.6626))
        assert failure is not None and failure.rule == "coords_swapped"

    def test_point_outside_nyc(self):
        failure = check_coordinates(record(latitude=34.05, longitude=-118.24))
        assert failure is not None and failure.rule == "coords_outside_nyc"


class TestDates:
    def test_normal_ordering_passes(self):
        assert check_dates(record()) is None

    def test_open_request_with_no_close_date_is_not_a_defect(self):
        assert check_dates(record(closed_date=None)) is None

    def test_closed_before_created(self):
        created = datetime(2026, 9, 2, 14, 5, tzinfo=UTC)
        failure = check_dates(record(created_date=created, closed_date=created - timedelta(hours=1)))
        assert failure is not None and failure.rule == "closed_before_created"

    def test_sub_minute_skew_is_tolerated(self):
        """Two city systems with slightly different clocks is not a data
        defect, and counting it as one buries the real violations."""
        created = datetime(2026, 9, 2, 14, 5, tzinfo=UTC)
        assert check_dates(record(created_date=created, closed_date=created - timedelta(seconds=20))) is None

    def test_missing_created_date_is_the_one_rejection(self):
        failure = check_dates(record(created_date=None))
        assert failure is not None
        assert failure.severity == validate.REJECT

    def test_future_created_date(self, now):
        failure = check_created_date_future(
            record(created_date=now + timedelta(days=2)), now=now
        )
        assert failure is not None and failure.rule == "created_date_future"

    def test_future_check_is_pure(self):
        """No `now` supplied means no opinion -- the rule never reads the wall
        clock itself, which is what keeps it testable."""
        assert check_created_date_future(record(), now=None) is None


class TestBoroughAgreement:
    def test_agreeing_record_passes(self):
        assert check_borough_agreement(record()) is None

    def test_brooklyn_zip_on_a_bronx_record_conflicts(self):
        failure = check_borough_agreement(
            record(borough="BRONX", incident_zip="11215", latitude=40.856, longitude=-73.901)
        )
        assert failure is not None and failure.rule == "borough_zip_conflict"

    def test_marble_hill_is_not_a_conflict(self):
        """10463 legitimately spans the Bronx and Manhattan. Flagging it would
        produce a few hundred false positives a month -- the rule was narrowed
        after exactly that happened."""
        for borough in ("BRONX", "MANHATTAN"):
            assert check_borough_agreement(
                record(borough=borough, incident_zip="10463",
                       latitude=40.8790, longitude=-73.9060)
            ) is None

    def test_missing_borough_is_not_a_conflict(self):
        assert check_borough_agreement(record(borough=None)) is None

    def test_non_nyc_zip_produces_no_borough_opinion(self):
        """Already reported by the ZIP rule; reporting it twice would
        double-count one defect across two rule rates."""
        assert check_borough_agreement(record(incident_zip="07030")) is None


class TestComplaintTypeDrift:
    def test_known_category_passes(self):
        assert check_complaint_type_drift(record()) is None

    def test_unknown_category_is_observed_not_rejected(self):
        """The whole point of rule 5. A city agency renaming a category must
        not make the app stop reporting it."""
        failure = check_complaint_type_drift(
            record(complaint_type_raw="Vacant Lot Vegetation Overgrowth Nuisance")
        )
        assert failure is not None
        assert failure.rule == "complaint_type_unrecognized"
        assert failure.severity == validate.OBSERVE
        assert not validate.is_rejected([failure])

    def test_absent_complaint_type_is_rejected(self):
        failure = check_complaint_type_drift(record(complaint_type_raw=None))
        assert failure is not None and failure.severity == validate.REJECT


class TestValidateAggregate:
    def test_clean_record_produces_no_failures(self):
        assert validate.validate(record()) == []

    def test_all_failures_are_returned_not_just_the_first(self):
        """A record with several defects must count against every rule it
        breaks, or the per-rule rates on the panel are quietly wrong."""
        failures = validate.validate(
            record(incident_zip=None, incident_zip_raw="N/A", latitude=0.0, longitude=0.0)
        )
        rules = {f.rule for f in failures}
        assert "zip_malformed" in rules
        assert "coords_null_island" in rules

    def test_observations_do_not_inflate_the_defect_count(self):
        failures = validate.validate(
            record(complaint_type_raw="Some Brand New Category")
        )
        assert all(f.severity == validate.OBSERVE for f in failures)
        assert validate.defect_count(failures) == 0

    def test_every_producible_rule_name_is_registered(self):
        """Guards the panel: a rule whose name is missing from ALL_RULE_NAMES
        would fire, be stored, and never be displayed."""
        scenarios = [
            record(incident_zip=None, incident_zip_raw=""),
            record(incident_zip=None, incident_zip_raw="N/A"),
            record(incident_zip="07030", incident_zip_raw="07030"),
            record(latitude=None, longitude=None),
            record(longitude=None),
            record(latitude=0.0, longitude=0.0),
            record(latitude=-73.9885, longitude=40.6626),
            record(latitude=34.05, longitude=-118.24),
            record(created_date=None),
            record(complaint_type_raw=None),
            record(complaint_type_raw="Brand New Thing"),
            record(unique_key=None),
            record(borough="BRONX", incident_zip="11215"),
        ]
        produced = {
            f.rule for scenario in scenarios for f in validate.validate(scenario)
        }
        assert produced, "no rules fired -- the scenarios are wrong"
        assert produced <= set(validate.ALL_RULE_NAMES), (
            f"unregistered rule names: {produced - set(validate.ALL_RULE_NAMES)}"
        )
