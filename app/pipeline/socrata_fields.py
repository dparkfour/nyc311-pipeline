"""Reading Socrata's system fields off a row.

Split out of socrata.py so that shaping and the tests that cover it do not
have to import httpx. Nothing here does any I/O.
"""

from __future__ import annotations

from datetime import datetime, timezone


def extract_updated_at(row: dict) -> datetime | None:
    """Pull `:updated_at` out of a row.

    This is the single most important field in the project: it is the
    watermark, and if it stops being extracted the pipeline silently stops
    ingesting while every run still reports success.

    Socrata returns it as Unix epoch seconds under the literal key
    ':updated_at'. Some dataset versions expose it as 'updated_at' without the
    colon, and both are checked -- an upstream rename of a system field is
    exactly the kind of change that stalls a watermark without any error.
    """
    for key in (":updated_at", "updated_at", ":updated_at_meta"):
        if key not in row or row[key] is None:
            continue
        value = row[key]
        try:
            if isinstance(value, (int, float)) or str(value).strip().isdigit():
                return datetime.fromtimestamp(float(value), tz=timezone.utc)
            text = str(value).strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(text)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except (ValueError, OSError, OverflowError, TypeError):
            continue
    return None
