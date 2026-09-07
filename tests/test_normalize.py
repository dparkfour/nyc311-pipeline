from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.pipeline.normalize import (
    normalize_address,
    normalize_borough,
    normalize_complaint_type,
    normalize_zip,
    parse_float,
    parse_ts,
)


class TestNormalizeZip:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("11215", "11215"),
            ("11215-3401", "11215"),          # ZIP+4
            ("11215 - 3401", "11215"),        # ZIP+4 with spacing
            ("  10011  ", "10011"),
            ("07030", "07030"),               # valid ZIP, just not in NYC
            ("", None),
            (None, None),
            ("N/A", None),
            ("UNKNOWN", None),
            ("1121", None),                   # four digits
            ("112155", None),                 # six digits
            ("00000", None),                  # sentinel, not an address
            ("BROOKLYN", None),               # borough typed into the ZIP box
        ],
    )
    def test_shapes_present_in_the_feed(self, raw, expected):
        assert normalize_zip(raw) == expected


class TestNormalizeAddress:
    def test_spelled_out_and_abbreviated_forms_collapse(self):
        """The reason deduplication finds anything at all. The 311 feed
        contains both spellings for the same doorway on the same day."""
        assert normalize_address("123 East 14th Street") == normalize_address("123 E 14 ST")

    def test_punctuation_and_case_are_irrelevant(self):
        assert normalize_address("55 W. 21st St.") == normalize_address("55 west 21 street")

    def test_distinct_addresses_stay_distinct(self):
        """Guards the over-aggressive failure mode: a normalizer that collapses
        these would merge unrelated complaints."""
        assert normalize_address("123 E 14 ST") != normalize_address("125 E 14 ST")
        assert normalize_address("123 E 14 ST") != normalize_address("123 W 14 ST")

    def test_empty_input(self):
        assert normalize_address(None) is None
        assert normalize_address("   ") is None


class TestNormalizeComplaintType:
    def test_known_drift_is_canonicalized(self):
        """'Heating' was renamed to 'HEAT/HOT WATER' upstream. Without the
        alias table a chart shows one line ending and an identical one
        starting on the same day."""
        assert normalize_complaint_type("Heating") == "HEAT/HOT WATER"
        assert normalize_complaint_type("HEAT/HOT WATER") == "HEAT/HOT WATER"

    def test_punctuation_variants_of_noise(self):
        assert normalize_complaint_type("Noise - Residential") == "NOISE - RESIDENTIAL"
        assert normalize_complaint_type("noise residential") == "NOISE - RESIDENTIAL"

    def test_unknown_category_passes_through_rather_than_vanishing(self):
        """A renamed category must not silently disappear from the app."""
        result = normalize_complaint_type("Some Brand New Category")
        assert result == "SOME BRAND NEW CATEGORY"

    def test_empty(self):
        assert normalize_complaint_type("") is None
        assert normalize_complaint_type(None) is None


class TestNormalizeBorough:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("BROOKLYN", "BROOKLYN"),
            ("Brooklyn", "BROOKLYN"),
            ("New York", "MANHATTAN"),
            ("Richmond", "STATEN ISLAND"),
            ("Unspecified", None),      # a missing value, not a sixth borough
            ("", None),
            (None, None),
            ("Nassau", None),
        ],
    )
    def test_aliases(self, raw, expected):
        assert normalize_borough(raw) == expected


class TestParseTimestamp:
    def test_socrata_floating_timestamp(self):
        dt = parse_ts("2026-09-02T09:14:00.000")
        assert dt == datetime(2026, 9, 2, 9, 14, tzinfo=timezone.utc)

    def test_epoch_seconds_as_used_by_updated_at(self):
        dt = parse_ts(1788549731)
        assert dt is not None and dt.tzinfo is not None

    def test_garbage_returns_none_rather_than_raising(self):
        assert parse_ts("not a date") is None
        assert parse_ts("") is None
        assert parse_ts(None) is None


def test_parse_float_tolerates_the_feeds_string_numbers():
    assert parse_float("40.6626") == pytest.approx(40.6626)
    assert parse_float("") is None
    assert parse_float("N/A") is None
    assert parse_float(None) is None
