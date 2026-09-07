"""Geographic checks. Pure functions.

Two independent signals for "is this record's location internally consistent":

  1. ZIP -> borough, via NYC's ZIP allocation, which is exact and enumerable.
  2. lat/long -> inside the NYC bounding box, which is coarse but catches the
     defects that actually occur: (0, 0), swapped coordinates, and points that
     landed somewhere else entirely.

A per-borough point-in-polygon test would be stricter than (2), but it needs
real boundary geometry. `scripts/fetch_borough_boundaries.py` downloads it and
`point_in_borough` uses it when the cache is present; without the cache the
coordinate check degrades to the bounding box rather than failing. That
fallback is deliberate -- the pipeline must not depend on a second API being
reachable at ingest time.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

BOUNDARY_CACHE = Path(__file__).resolve().parent.parent.parent / "data" / "borough_boundaries.json"

# NYC's overall bounding box, generous by a hair on every side.
NYC_BBOX = {
    "min_lat": 40.477,
    "max_lat": 40.918,
    "min_lon": -74.259,
    "max_lon": -73.700,
}

# ZIP ranges by borough. Ranges rather than a 200-entry list, because the
# allocation genuinely is contiguous by borough -- that is how ZIPs were
# assigned here.
_ZIP_RANGES: list[tuple[int, int, str]] = [
    (10001, 10286, "MANHATTAN"),
    (10292, 10292, "MANHATTAN"),
    (10301, 10314, "STATEN ISLAND"),
    (10451, 10475, "BRONX"),
    (11004, 11005, "QUEENS"),
    (11101, 11120, "QUEENS"),
    (11201, 11256, "BROOKLYN"),
    (11351, 11436, "QUEENS"),
    (11691, 11697, "QUEENS"),
]

# ZIPs that legitimately straddle a borough line. 10463 is the famous one:
# Marble Hill is physically attached to the Bronx and politically part of
# Manhattan, and both boroughs appear against that ZIP in the feed. Flagging
# those rows as conflicts would produce a few hundred false positives a month,
# so they are excluded from the borough-agreement rule by name.
AMBIGUOUS_ZIPS: dict[str, set[str]] = {
    "10463": {"BRONX", "MANHATTAN"},   # Marble Hill / Kingsbridge
    "11370": {"QUEENS"},               # East Elmhurst; kept for documentation
}


def borough_for_zip(zip5: str | None) -> str | None:
    """Return the borough a five-digit NYC ZIP belongs to, or None if the ZIP
    is outside New York City (a Nassau, Westchester or out-of-state ZIP)."""
    if not zip5 or len(zip5) != 5 or not zip5.isdigit():
        return None
    if zip5 in AMBIGUOUS_ZIPS:
        # Ambiguous ZIPs have no single answer; callers handle them via
        # zip_borough_agrees below.
        return None
    n = int(zip5)
    for low, high, borough in _ZIP_RANGES:
        if low <= n <= high:
            return borough
    return None


def is_nyc_zip(zip5: str | None) -> bool:
    if not zip5:
        return False
    if zip5 in AMBIGUOUS_ZIPS:
        return True
    return borough_for_zip(zip5) is not None


def zip_borough_agrees(zip5: str | None, borough: str | None) -> bool | None:
    """Do the ZIP and the borough field describe the same place?

    Returns True/False, or None when the question cannot be asked -- either
    field missing, a non-NYC ZIP, or a ZIP that straddles a borough line.
    None means "no opinion", and the validation layer does not count it as
    either a pass or a failure.
    """
    if not zip5 or not borough:
        return None
    if zip5 in AMBIGUOUS_ZIPS:
        return borough in AMBIGUOUS_ZIPS[zip5]
    expected = borough_for_zip(zip5)
    if expected is None:
        return None
    return expected == borough


def in_nyc_bbox(lat: float | None, lon: float | None) -> bool:
    if lat is None or lon is None:
        return False
    return (
        NYC_BBOX["min_lat"] <= lat <= NYC_BBOX["max_lat"]
        and NYC_BBOX["min_lon"] <= lon <= NYC_BBOX["max_lon"]
    )


def is_null_island(lat: float | None, lon: float | None) -> bool:
    """(0, 0) off the coast of Africa. Present in the 311 feed as a stand-in
    for 'no coordinate', which is different from the field being absent and
    worth counting separately."""
    if lat is None or lon is None:
        return False
    return abs(lat) < 1e-6 and abs(lon) < 1e-6


def looks_swapped(lat: float | None, lon: float | None) -> bool:
    """Latitude and longitude transposed: NYC longitude is around -74, which
    is a valid latitude, while NYC latitude ~40.7 is a valid longitude."""
    if lat is None or lon is None:
        return False
    if in_nyc_bbox(lat, lon):
        return False
    return in_nyc_bbox(lon, lat)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# --- optional polygon refinement -------------------------------------------

_boundaries: dict[str, list] | None = None
_boundaries_loaded = False


def _load_boundaries() -> dict[str, list] | None:
    global _boundaries, _boundaries_loaded
    if _boundaries_loaded:
        return _boundaries
    _boundaries_loaded = True
    if BOUNDARY_CACHE.exists():
        try:
            _boundaries = json.loads(BOUNDARY_CACHE.read_text(encoding="utf-8"))
        except Exception:
            _boundaries = None
    return _boundaries


def _point_in_ring(lat: float, lon: float, ring: list) -> bool:
    """Ray casting. `ring` is a list of [lon, lat] pairs, GeoJSON order."""
    inside = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat):
            x_cross = (xj - xi) * (lat - yi) / (yj - yi) + xi
            if lon < x_cross:
                inside = not inside
        j = i
    return inside


def point_in_borough(lat: float | None, lon: float | None) -> str | None:
    """Borough containing a point, using cached boundary polygons.

    Returns None when the cache is absent -- callers must treat None as "not
    checked", not as "not in any borough".
    """
    if lat is None or lon is None:
        return None
    data = _load_boundaries()
    if not data:
        return None
    for borough, polygons in data.items():
        for polygon in polygons:
            if polygon and _point_in_ring(lat, lon, polygon[0]):
                # holes ignored: NYC borough polygons have none that matter here
                return borough
    return None
