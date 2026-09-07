"""Deduplication.

A separate pass from validation, and a genuinely different problem from the
upsert in the ingest layer. Those three things get conflated constantly:

  upsert       the same complaint arriving again because Socrata updated it.
               Correct behaviour: overwrite. Not a duplicate.
  duplicate    several *different* 311 submissions describing one real-world
               problem -- four neighbours reporting the same broken hydrant
               within ten minutes. Correct behaviour: keep one, link the rest.
  defect       a field with a wrong or missing value. Unrelated to both.

Duplicates are marked, never deleted. `is_duplicate_of` points at the
surviving record, so "duplicates collapsed" on the quality panel is an
auditable number rather than a claim about rows that no longer exist.

Pure functions: the caller supplies both the new batch and any already-stored
records that could be neighbours, so nothing here touches the database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class DuplicateLink:
    unique_key: str          # the record being marked as a duplicate
    duplicate_of: str        # the surviving record it collapses into
    minutes_apart: float


def dedup_key(record: dict) -> tuple[str, str] | None:
    """The grouping key: same complaint type at the same normalized address.

    Returns None when the record cannot participate -- no address, or no
    complaint type. A record with no address is NOT grouped with every other
    addressless record; that would collapse thousands of unrelated complaints,
    which is the over-aggressive failure mode this design is built to avoid.
    """
    ctype = record.get("complaint_type_norm")
    address = record.get("address_norm")
    if not ctype or not address:
        return None
    return (ctype, address)


def collapse(records: list[dict], window_minutes: int) -> list[DuplicateLink]:
    """Find duplicate submissions within a batch.

    `records` should include both the newly-fetched rows and any stored rows
    that share a candidate key and fall near the window, so a complaint filed
    at 23:58 and its duplicate at 00:03 the next day are still matched.

    The surviving record in each cluster is the earliest by `created_date`,
    with `unique_key` breaking ties so the result is deterministic -- the same
    input always produces the same survivor, which matters because this runs
    every fifteen minutes against overlapping windows.

    Chaining is deliberately allowed: A at 10:00, B at 10:45 and C at 11:20
    with a 60-minute window all collapse into A, even though A and C are 80
    minutes apart. Four neighbours reporting one hydrant do not arrive on a
    schedule, and requiring every member to be within the window of the first
    one splits a real cluster in two.
    """
    window = timedelta(minutes=window_minutes)
    groups: dict[tuple[str, str], list[dict]] = {}

    for record in records:
        key = dedup_key(record)
        if key is None:
            continue
        if record.get("created_date") is None or not record.get("unique_key"):
            continue
        groups.setdefault(key, []).append(record)

    links: list[DuplicateLink] = []

    for group in groups.values():
        if len(group) < 2:
            continue

        group.sort(key=lambda r: (r["created_date"], str(r["unique_key"])))

        survivor = group[0]
        previous = group[0]

        for record in group[1:]:
            if record["created_date"] - previous["created_date"] <= window:
                delta = record["created_date"] - survivor["created_date"]
                links.append(
                    DuplicateLink(
                        unique_key=str(record["unique_key"]),
                        duplicate_of=str(survivor["unique_key"]),
                        minutes_apart=round(delta.total_seconds() / 60.0, 2),
                    )
                )
                previous = record
            else:
                # Gap wider than the window: this record starts a new cluster.
                survivor = record
                previous = record

    return links


def cluster_sizes(links: list[DuplicateLink]) -> dict[str, int]:
    """How many records collapsed into each survivor. Used when tuning the
    window -- a survivor absorbing forty records is the signal that the window
    is too wide."""
    sizes: dict[str, int] = {}
    for link in links:
        sizes[link.duplicate_of] = sizes.get(link.duplicate_of, 0) + 1
    return sizes
