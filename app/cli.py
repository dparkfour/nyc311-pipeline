"""Command-line entry point.

    python -m app.cli migrate            apply the schema (idempotent)
    python -m app.cli probe              inspect the live Socrata feed
    python -m app.cli ingest             one pipeline run
    python -m app.cli ingest --trigger schedule --days-back 1
    python -m app.cli rollup --days 60   rebuild aggregates after a rule change
    python -m app.cli status             latest run + freshness
    python -m app.cli tune-dedup         compare dedup windows on stored data
    python -m app.cli reset-watermark --days-back 3
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import timezone


def _json(obj) -> str:
    def default(o):
        if hasattr(o, "isoformat"):
            return o.isoformat()
        return str(o)
    return json.dumps(obj, indent=2, default=default)


def cmd_migrate(args) -> int:
    from app import db
    db.migrate()
    return 0


def cmd_probe(args) -> int:
    from app.pipeline import socrata
    result = socrata.probe()
    sample = result.pop("sample", None)
    print(_json(result))
    if sample and args.sample:
        print("\n--- one row -------------------------------------------------")
        print(_json(sample))
    return 0


def cmd_ingest(args) -> int:
    from app.pipeline import ingest
    try:
        ingest.run(
            trigger=args.trigger,
            days_back=args.days_back,
            reset_watermark=args.reset,
        )
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"ingest failed: {exc}", file=sys.stderr)
        return 1


def cmd_rollup(args) -> int:
    from app.pipeline import aggregate
    days = aggregate.rollup(days=args.days)
    print(f"rebuilt {len(days)} days of aggregates")
    return 0


def cmd_status(args) -> int:
    from app import db
    run = db.query_one(
        "SELECT * FROM ingest_runs ORDER BY started_at DESC LIMIT 1"
    )
    if not run:
        print("no runs yet")
        return 1
    freshness = db.query_one(
        "SELECT EXTRACT(EPOCH FROM (now() - MAX(finished_at)))/60 AS minutes "
        "FROM ingest_runs WHERE status = 'success'"
    )
    print(_json({"latest_run": run, "minutes_since_last_success": freshness["minutes"]}))
    return 0


def cmd_tune_dedup(args) -> int:
    """Run the dedup rule at several windows over stored data and print what
    each one collapses. This is how the 60-minute default was chosen; the
    numbers belong in docs/dedup-tuning.md, not in a guess."""
    from app import db
    from app.pipeline import dedup

    rows = db.query(
        """
        SELECT unique_key, complaint_type_norm, address_norm, created_date
        FROM clean_requests
        WHERE complaint_type_norm IS NOT NULL AND address_norm IS NOT NULL
          AND created_date >= now() - make_interval(days => %s)
        """,
        (args.days,),
    )
    print(f"{len(rows)} candidate records over the last {args.days} days\n")
    print(f"{'window':>10} {'collapsed':>10} {'pct':>7} {'largest cluster':>16}")
    print("-" * 48)
    for window in args.windows:
        links = dedup.collapse(list(rows), window)
        sizes = dedup.cluster_sizes(links)
        largest = max(sizes.values()) + 1 if sizes else 0
        pct = 100.0 * len(links) / len(rows) if rows else 0
        print(f"{window:>8}m {len(links):>10} {pct:>6.2f}% {largest:>16}")
    print(
        "\nA window is too wide when the largest cluster stops looking like "
        "'neighbours reporting one problem' and starts looking like 'every "
        "complaint at this address this week'."
    )
    return 0


def cmd_reset_watermark(args) -> int:
    from app import db
    from app.pipeline import socrata
    from app.pipeline.ingest import WATERMARK_KEY
    new = socrata.default_watermark(args.days_back)
    db.set_state(WATERMARK_KEY, new.astimezone(timezone.utc).isoformat())
    print(f"watermark set to {new.isoformat()}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="app.cli", description="NYC 311 pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", help="apply schema.sql").set_defaults(func=cmd_migrate)

    p = sub.add_parser("probe", help="inspect the live Socrata feed")
    p.add_argument("--sample", action="store_true", help="also print one full row")
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("ingest", help="run the pipeline once")
    p.add_argument("--trigger", default="manual", choices=["manual", "schedule", "backfill"])
    p.add_argument("--days-back", type=int, default=3,
                   help="starting watermark when none is stored yet (or with --reset)")
    p.add_argument("--reset", action="store_true",
                   help="ignore the stored watermark and start --days-back days ago")
    p.set_defaults(func=cmd_ingest)

    p = sub.add_parser("rollup", help="rebuild daily aggregates")
    p.add_argument("--days", type=int, default=30)
    p.set_defaults(func=cmd_rollup)

    sub.add_parser("status", help="latest run and freshness").set_defaults(func=cmd_status)

    p = sub.add_parser("tune-dedup", help="compare dedup windows on stored data")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--windows", type=int, nargs="+", default=[5, 15, 60, 240, 1440])
    p.set_defaults(func=cmd_tune_dedup)

    p = sub.add_parser("reset-watermark", help="move the watermark back")
    p.add_argument("--days-back", type=int, default=3)
    p.set_defaults(func=cmd_reset_watermark)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    finally:
        from app import db
        db.close_pool()


if __name__ == "__main__":
    raise SystemExit(main())
