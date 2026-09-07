"""Download NYC borough boundaries and cache them for the polygon check.

Optional. Without this cache, `geo.point_in_borough` returns None and the
borough-agreement rule falls back to the ZIP comparison alone. That fallback is
deliberate: the ingest path must not depend on a second API being reachable.

    python scripts/fetch_borough_boundaries.py

Writes data/borough_boundaries.json, which is gitignored — regenerate it rather
than committing a few megabytes of geometry.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

# NYC Open Data, Borough Boundaries (Water Areas Included).
URL = "https://data.cityofnewyork.us/api/geospatial/tqmj-j8zm?method=export&format=GeoJSON"

OUT = Path(__file__).resolve().parent.parent / "data" / "borough_boundaries.json"

NAME_MAP = {
    "manhattan": "MANHATTAN",
    "bronx": "BRONX",
    "brooklyn": "BROOKLYN",
    "queens": "QUEENS",
    "staten island": "STATEN ISLAND",
}


def main() -> int:
    print(f"fetching {URL}")
    try:
        response = httpx.get(URL, timeout=120.0, follow_redirects=True)
        response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        print(f"failed: {exc}", file=sys.stderr)
        print("The pipeline runs fine without this file.", file=sys.stderr)
        return 1

    geojson = response.json()
    out: dict[str, list] = {}

    for feature in geojson.get("features", []):
        props = feature.get("properties", {})
        raw_name = str(props.get("boro_name") or props.get("BoroName") or "").strip().lower()
        borough = NAME_MAP.get(raw_name)
        if not borough:
            continue

        geometry = feature.get("geometry", {})
        gtype = geometry.get("type")
        coords = geometry.get("coordinates", [])

        polygons = coords if gtype == "MultiPolygon" else [coords] if gtype == "Polygon" else []
        out.setdefault(borough, []).extend(polygons)

    if len(out) != 5:
        print(f"expected 5 boroughs, got {sorted(out)}", file=sys.stderr)
        return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out), encoding="utf-8")

    size_mb = OUT.stat().st_size / 1024 / 1024
    print(f"wrote {OUT} ({size_mb:.1f} MB)")
    for borough, polygons in sorted(out.items()):
        print(f"  {borough:<14} {len(polygons)} polygon(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
