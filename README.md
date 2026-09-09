# NYC 311 — live data pipeline

A scheduled pipeline over the City of New York's 311 Service Requests feed. It
fetches records that changed upstream, validates and deduplicates them, stores
them in Postgres, and serves them through a REST API and a web front end.

**The point of the project is the data-quality layer, and it is on the page.**
Per-run ingestion counts, per-rule failure rates, duplicates collapsed, and
what changed since the previous run — above the data table, not behind a tab.

- **App:** https://nyc311-web.onrender.com
- **API docs:** `/docs` — auto-generated OpenAPI

---

## Why this is built the way it is

Three decisions carry most of the weight.

### 1. The watermark is on `:updated_at`, not `created_date`

311 records are **mutable**. A request created Monday gets its `closed_date`,
`status` and `resolution_description` filled in on Thursday.

Watermarking on `created_date` — the intuitive choice — ingests each record
exactly once in its incomplete state and never sees a single update. Worse, it
does so silently: every run reports success. The "closed_date precedes
created_date" rule then fires against rows that were never going to be wrong,
while the real corrections never arrive at all.

So the pipeline watermarks on Socrata's `:updated_at` system field and
**upserts on `unique_key`**. Records legitimately arrive several times as they
are revised. That is correct behaviour, and it is a different thing from the
duplicate-submission problem the dedup pass handles.

### 2. The validation layer flags; it does not throw data away

Three severities, and the middle one is where nearly everything lands:

| Severity | Meaning |
|---|---|
| `reject` | not trustworthy enough to serve — stored raw and counted, never reaches the serving table |
| `flag` | served, with the defect recorded and surfaced |
| `observe` | not a defect at all; something changed upstream and the count is what matters |

Only three things get rejected: a missing `unique_key`, a missing
`complaint_type`, and a missing `created_date`. A bad ZIP or a `(0, 0)`
coordinate flags — dropping a real complaint because a city agency typed an
address badly would make the app less useful *and* less honest than serving it
with the defect visible.

`complaint_type_unrecognized` is the `observe` case. When the city renames a
category, the correct behaviour is to keep the record, count the unrecognized
label, and show the count. Rejecting would mean the app silently stopped
reporting a whole category on the day it was renamed — exactly the failure a
data-quality layer exists to prevent.

### 3. Retention is a design input, not a cleanup task

The source dataset is ~40 million rows. The free Postgres tier is 0.5 GB. A
full backfill is not a long job — it is an impossible one.

| Table | Retention | Why |
|---|---|---|
| `raw_requests` | 7 days | untouched payloads of **flagged rows only**, so a rule change can be replayed against the records that were wrong (was a full 30-day mirror; see BREAKS.md 2026-09-07, 2026-09-08) |
| `clean_requests` | 60 days | the serving window |
| `validation_failures` | 7 days | a failure whose record is gone cannot be investigated |
| `daily_agg` | **permanent** | a few MB holds years; this is what makes the app worth opening in April |
| `ingest_runs` | 365 days | the evidence it has been alive for months |

---

## The validation rules

| Rule | Severity | What it catches |
|---|---|---|
| `unique_key_missing` | reject | no identity, cannot be upserted |
| `complaint_type_missing` | reject | no category |
| `created_date_missing` | reject | no position in time |
| `complaint_type_unrecognized` | observe | upstream renamed or split a category |
| `closed_before_created` | flag | closed before it was filed, beyond a 1-minute clock-skew tolerance |
| `created_date_future` | flag | filed in the future |
| `zip_missing` | flag | empty ZIP |
| `zip_malformed` | flag | `N/A`, `UNKNOWN`, four digits, a borough name in the ZIP box |
| `zip_outside_nyc` | flag | well-formed, just not a New York City ZIP |
| `coords_missing` | flag | both coordinates absent |
| `coords_partial` | flag | exactly one present |
| `coords_null_island` | flag | `(0, 0)` — a null sentinel, counted separately from a real error |
| `coords_swapped` | flag | latitude and longitude transposed |
| `coords_outside_nyc` | flag | outside the NYC bounding box |
| `borough_zip_conflict` | flag | `borough` disagrees with the ZIP's borough |
| `borough_geo_conflict` | flag | `borough` disagrees with the coordinates (needs the boundary cache) |

Every rule is a pure function — `(record) -> Failure | None`, no database, no
network, no clock. That constraint is what makes the test suite cheap: a rule
test is a dict in and an assertion out, with nothing to mock.

**One narrowing worth knowing about.** ZIP 10463 covers Marble Hill, which is
physically attached to the Bronx and politically part of Manhattan. Both
boroughs appear against it legitimately, so it is excluded from
`borough_zip_conflict` by name rather than producing a few hundred false
positives a month.

---

## Architecture

```
GitHub Actions cron (*/15)
  → fetch pages where :updated_at > watermark      app/pipeline/socrata.py
  → shape into normalized records                  app/pipeline/shaping.py
  → validate (16 rules, pure functions)            app/pipeline/validate.py
  → upsert raw + clean tables                      app/pipeline/ingest.py
  → deduplicate against stored neighbours          app/pipeline/dedup.py
  → recompute affected days                        app/pipeline/aggregate.py
  → prune outside the retention windows            app/pipeline/retention.py
  → write the run log                              ingest_runs

Neon Postgres  ←  FastAPI on Render  ←  React + Vite static site
```

**Neon, not Render Postgres.** Render's free database is deleted 30 days after
creation. Neon's free plan has no expiry and scales to zero when idle, so an
unvisited app burns effectively no compute — which is what it takes for a link
shipped in September to still resolve the following spring.

---

## Running it locally

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

cp .env.example .env        # paste your Neon pooled connection string

python -m app.cli migrate   # apply the schema (idempotent)
python -m app.cli probe     # confirm the feed responds and eyeball its fields
python -m app.cli ingest    # one run

uvicorn app.main:app --reload      # http://127.0.0.1:8000/docs
```

```bash
cd web && npm install && npm run dev    # http://localhost:5173
```

### CLI

| Command | Does |
|---|---|
| `migrate` | apply `schema.sql`; safe to run repeatedly |
| `probe [--sample]` | inspect the live feed — trust this over the dataset docs |
| `ingest [--trigger schedule] [--days-back N]` | one pipeline run |
| `rollup --days 60` | rebuild aggregates after changing a rule |
| `status` | latest run and freshness |
| `tune-dedup --windows 15 60 1440` | compare dedup windows against stored data |
| `reset-watermark --days-back 3` | move the watermark back |

### Tests

```bash
pytest -v
```

78 test functions, 112 cases after parametrization. They need no database and
no network: shaping, validation, deduplication and the geographic checks are
pure, and the end-to-end tests run against a captured page of real-shaped
Socrata rows in `tests/fixtures/socrata_page.json`.

That fixture is a **regression asset**, not sample data — every defect class the
pipeline handles appears in it at least once, and one test fails if a row is
edited out, so the rule tests can never start passing vacuously.

---

## Deploying

**Neon** — create a project, copy the **pooled** connection string (the host
contains `-pooler`), make sure it ends with `?sslmode=require`.

**Render** — New → Blueprint, point it at this repo; `render.yaml` defines both
services. Then set, in the dashboard:

- `nyc311-api` → `DATABASE_URL` (Neon), `SOCRATA_APP_TOKEN` (optional),
  `CORS_ORIGINS` (the static site's origin, once it exists)
- `nyc311-web` → `VITE_API_BASE` (the API service's URL)

**GitHub** — repository secrets `DATABASE_URL` and `SOCRATA_APP_TOKEN`, then run
the **Ingest** workflow manually once to confirm it works before trusting the
cron.

### Three things that will bite later

1. **Render free web services sleep after 15 minutes** and take ~1 minute to
   wake. The front end renders an explicit "waking the server up" state with a
   running clock rather than a spinner — a visitor who sees a blank tab leaves,
   and the visitor is the entire point of this project.
2. **GitHub disables scheduled workflows after 60 days of repository
   inactivity**, silently. `ci.yml` carries a monthly `schedule` trigger to
   count as activity. Check `/api/runs` occasionally anyway.
3. **Neon storage is 0.5 GB.** `/api/storage` reports usage against that budget.
   If it climbs, shorten `RAW_RETENTION_DAYS` first — the JSONB payloads are
   most of it.

---

## Repository layout

```
app/
  config.py              environment-driven settings
  db.py                  psycopg 3 pool, migrations, pipeline_state
  schema.sql             five tables + the state store
  main.py                FastAPI app
  cli.py                 command-line entry point
  api/routes.py          endpoints, including /api/quality
  pipeline/
    socrata.py           paginated fetch, retry, backoff
    socrata_fields.py    :updated_at extraction (no I/O — hence its own module)
    normalize.py         ZIP, address, borough, complaint-type, timestamps
    geo.py               ZIP→borough, bounding box, optional polygons
    shaping.py           raw row → normalized record
    validate.py          the 16 rules
    dedup.py             duplicate-submission collapse
    aggregate.py         daily_agg rollup
    retention.py         pruning
    ingest.py            the run: orchestration and I/O
tests/                   78 tests, no database required
web/                     React + Vite front end
scripts/                 optional borough-boundary fetch
.github/workflows/       ci.yml (tests, docker, web build) · ingest.yml (cron)
BREAKS.md                the break log — keep it current
```

## Data source

[NYC Open Data — 311 Service Requests from 2010 to Present](https://data.cityofnewyork.us/Social-Services/311-Service-Requests-from-2010-to-Present/erm2-nwe9)
(`erm2-nwe9`), via the Socrata Open Data API. No authentication required; an app
token is free and raises the rate limit considerably.
