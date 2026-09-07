from __future__ import annotations

import pytest

from app.pipeline import geo


@pytest.mark.parametrize(
    "zip5,borough",
    [
        ("10011", "MANHATTAN"),
        ("10282", "MANHATTAN"),
        ("10314", "STATEN ISLAND"),
        ("10457", "BRONX"),
        ("11215", "BROOKLYN"),
        ("11249", "BROOKLYN"),
        ("11375", "QUEENS"),
        ("11101", "QUEENS"),
        ("11697", "QUEENS"),   # Breezy Point
        ("11004", "QUEENS"),   # Glen Oaks
    ],
)
def test_zip_maps_to_the_right_borough(zip5, borough):
    assert geo.borough_for_zip(zip5) == borough


@pytest.mark.parametrize("zip5", ["07030", "11501", "10601", "90210", "", "abcde", None])
def test_non_nyc_zips_have_no_borough(zip5):
    assert geo.borough_for_zip(zip5) is None


def test_marble_hill_is_ambiguous_rather_than_wrong():
    """10463 covers Marble Hill (Manhattan) and Kingsbridge (Bronx). Both
    boroughs appear against it in the feed and neither is an error."""
    assert geo.zip_borough_agrees("10463", "BRONX") is True
    assert geo.zip_borough_agrees("10463", "MANHATTAN") is True
    assert geo.zip_borough_agrees("10463", "BROOKLYN") is False


def test_agreement_returns_none_when_the_question_cannot_be_asked():
    """None is 'no opinion' -- it must count as neither a pass nor a failure."""
    assert geo.zip_borough_agrees(None, "BROOKLYN") is None
    assert geo.zip_borough_agrees("11215", None) is None
    assert geo.zip_borough_agrees("07030", "BROOKLYN") is None


def test_agreement_detects_a_real_mismatch():
    assert geo.zip_borough_agrees("11215", "BRONX") is False
    assert geo.zip_borough_agrees("11215", "BROOKLYN") is True


class TestCoordinateChecks:
    def test_points_inside_and_outside_nyc(self):
        assert geo.in_nyc_bbox(40.7419, -73.9930) is True     # Manhattan
        assert geo.in_nyc_bbox(40.5795, -74.1502) is True     # Staten Island
        assert geo.in_nyc_bbox(34.0522, -118.2437) is False   # Los Angeles
        assert geo.in_nyc_bbox(None, -73.99) is False

    def test_null_island(self):
        assert geo.is_null_island(0.0, 0.0) is True
        assert geo.is_null_island(40.74, -73.99) is False
        assert geo.is_null_island(None, None) is False

    def test_swap_detection_is_not_triggered_by_valid_points(self):
        assert geo.looks_swapped(-73.9930, 40.7419) is True
        assert geo.looks_swapped(40.7419, -73.9930) is False
        assert geo.looks_swapped(34.05, -118.24) is False

    def test_polygon_check_returns_none_without_the_boundary_cache(self):
        """None must mean 'not checked', never 'not in any borough' -- the
        pipeline cannot depend on a second API being reachable at ingest time."""
        if geo.BOUNDARY_CACHE.exists():
            pytest.skip("boundary cache present")
        assert geo.point_in_borough(40.7419, -73.9930) is None


def test_haversine_matches_a_known_distance():
    """Times Square to Grand Central, about 1 km."""
    km = geo.haversine_km(40.7580, -73.9855, 40.7527, -73.9772)
    assert 0.6 < km < 1.2
