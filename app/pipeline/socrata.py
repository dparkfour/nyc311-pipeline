"""Socrata API client for the NYC 311 dataset.

The important decision in this file is the watermark field.

311 records are MUTABLE. A request created on Monday gets its `closed_date`,
`status` and `resolution_description` filled in on Thursday. Watermarking on
`created_date` -- which is the obvious choice and the wrong one -- ingests each
record exactly once, in its incomplete state, and never sees a single update.
The "closed_date precedes created_date" rule then fires against rows that were
never going to be wrong, while the real corrections never arrive.

So: watermark on the Socrata system field `:updated_at`, and upsert on
`unique_key`. Records legitimately arrive several times as they change. That is
correct behaviour, and it is a different concept from the duplicate-submission
problem in dedup.py.

Pagination is `$order=:updated_at ASC` plus `$offset`. Ordering by the same
field the watermark filters on is what keeps paging stable while the dataset
is being written to underneath us.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Iterator

import httpx

from app.config import settings
from app.pipeline.normalize import to_socrata_ts
from app.pipeline.socrata_fields import extract_updated_at  # re-exported for callers

# Fields pulled from the API. `:*` yields the Socrata system fields, which is
# where `:updated_at` lives. Selecting explicitly rather than taking every
# column keeps each page small -- the full row is ~40 fields, most unused.
SELECT_FIELDS = (
    ":*,"
    "unique_key,created_date,closed_date,agency,agency_name,complaint_type,"
    "descriptor,status,borough,incident_zip,incident_address,street_name,"
    "latitude,longitude,resolution_description,resolution_action_updated_date"
)

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class SocrataError(RuntimeError):
    pass


def _headers() -> dict[str, str]:
    headers = {"Accept": "application/json", "User-Agent": "nyc311-pipeline/1.0"}
    if settings.socrata_app_token:
        headers["X-App-Token"] = settings.socrata_app_token
    return headers


def _get_with_retry(
    client: httpx.Client,
    params: dict,
    max_attempts: int = 5,
) -> list[dict]:
    """One page, with exponential backoff on throttling and transient errors.

    Socrata throttles unauthenticated callers aggressively and shares that
    budget across every anonymous client hitting the domain, so 429 is a
    routine response rather than an exceptional one. It is retried, not
    treated as a failure. `Retry-After` is honoured when present.
    """
    delay = 2.0
    last_error = None

    for attempt in range(1, max_attempts + 1):
        try:
            response = client.get(settings.socrata_url, params=params, timeout=60.0)
        except httpx.RequestError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if attempt == max_attempts:
                raise SocrataError(f"network error after {attempt} attempts: {last_error}")
            time.sleep(delay)
            delay *= 2
            continue

        if response.status_code == 200:
            return response.json()

        if response.status_code in RETRYABLE_STATUS and attempt < max_attempts:
            retry_after = response.headers.get("Retry-After")
            wait = float(retry_after) if retry_after and retry_after.isdigit() else delay
            print(
                f"  socrata: HTTP {response.status_code}, retrying in {wait:.0f}s "
                f"(attempt {attempt}/{max_attempts})"
            )
            time.sleep(wait)
            delay *= 2
            continue

        raise SocrataError(
            f"HTTP {response.status_code} from Socrata: {response.text[:300]}"
        )

    raise SocrataError(f"exhausted retries: {last_error}")


def fetch_since(
    watermark: datetime,
    page_size: int | None = None,
    max_pages: int | None = None,
) -> Iterator[list[dict]]:
    """Yield pages of records updated after `watermark`, oldest first.

    A generator rather than a list: a wide watermark gap can span hundreds of
    thousands of rows, and holding them all in memory on a 512 MB Render
    instance is how the process gets OOM-killed.
    """
    page_size = page_size or settings.page_size
    max_pages = max_pages or settings.max_pages_per_run

    where = f":updated_at > '{to_socrata_ts(watermark)}'"
    offset = 0

    with httpx.Client(headers=_headers(), follow_redirects=True) as client:
        for page_num in range(max_pages):
            params = {
                "$select": SELECT_FIELDS,
                "$where": where,
                "$order": ":updated_at ASC",
                "$limit": page_size,
                "$offset": offset,
            }
            rows = _get_with_retry(client, params)
            if not rows:
                return

            yield rows

            if len(rows) < page_size:
                return  # short page means we caught up

            offset += page_size

        print(
            f"  socrata: stopped at the {max_pages}-page cap; the next run "
            f"continues from the new watermark"
        )


def default_watermark(days_back: int = 3) -> datetime:
    """Starting watermark for a database that has never been ingested into.

    Three days, not the full dataset. The 311 feed is ~40 million rows and
    Neon's free tier is 0.5 GB -- a full backfill is not a long job, it is an
    impossible one, and retention is a design input here rather than an
    afterthought.
    """
    return datetime.now(timezone.utc) - timedelta(days=days_back)


def probe() -> dict:
    """Fetch a handful of rows and report what the feed actually looks like.

    Used by `python -m app.cli probe` on day zero and any time the pipeline
    starts behaving oddly. Trust this over the dataset documentation -- the
    documented field list and the returned field list have drifted before.
    """
    with httpx.Client(headers=_headers(), follow_redirects=True) as client:
        rows = _get_with_retry(
            client, {"$select": SELECT_FIELDS, "$order": ":updated_at DESC", "$limit": 5}
        )
    fields = sorted({k for row in rows for k in row})
    return {
        "rows_returned": len(rows),
        "fields_present": fields,
        "has_updated_at": any(extract_updated_at(r) for r in rows),
        "newest_updated_at": max(
            (extract_updated_at(r) for r in rows if extract_updated_at(r)),
            default=None,
        ),
        "sample": rows[0] if rows else None,
    }
