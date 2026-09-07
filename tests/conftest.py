from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def socrata_page() -> list[dict]:
    """A captured page of real-shaped Socrata rows.

    Every defect the validation layer is built for is represented here at least
    once, which is what makes this a regression fixture rather than sample
    data: 60000004 has a 'N/A' ZIP and (0, 0) coordinates, 60000003 closes
    before it is created, 60000005 has a Brooklyn ZIP on a Bronx record,
    60000006 is in Hoboken, 60000007 has transposed coordinates and 60000008
    (the last row) is missing its unique_key entirely.
    """
    return json.loads((FIXTURES / "socrata_page.json").read_text(encoding="utf-8"))


@pytest.fixture
def now() -> datetime:
    return datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)


def _has_db() -> bool:
    return bool(os.environ.get("DATABASE_URL"))


needs_db = pytest.mark.skipif(
    not _has_db(), reason="DATABASE_URL not set; integration tests skipped"
)
