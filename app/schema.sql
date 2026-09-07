-- NYC 311 pipeline schema.
--
-- Applied idempotently by `python -m app.cli migrate`. Every statement is
-- IF NOT EXISTS so the migration is safe to run on every deploy, which is
-- how Render's start command invokes it.
--
-- Five tables, and the reason each exists:
--   raw_requests        untouched Socrata payload, rolling 7 days
--   clean_requests      validated + deduplicated, rolling 60 days, served by the API
--   validation_failures one row per rule violation, drives the quality panel
--   daily_agg           permanent rollup; the only table that is never pruned
--   ingest_runs         one row per pipeline run; how we know the scheduler is alive


-- ---------------------------------------------------------------------------
-- raw_requests: exactly what the API returned, plus ingest bookkeeping.
-- Kept so a recent validation-rule change can be replayed against real
-- payloads without re-fetching, and so "the upstream schema changed" is
-- provable. 7-day window: the payload column is large and 30 days of it
-- overran the 0.5 GB budget (BREAKS.md 2026-09-07).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw_requests (
    unique_key      TEXT PRIMARY KEY,
    payload         JSONB       NOT NULL,
    source_updated_at TIMESTAMPTZ,          -- Socrata's :updated_at
    created_date    TIMESTAMPTZ,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    ingest_run_id   BIGINT,
    seen_count      INTEGER     NOT NULL DEFAULT 1   -- times this key was re-fetched
);

CREATE INDEX IF NOT EXISTS idx_raw_ingested_at ON raw_requests (ingested_at);
CREATE INDEX IF NOT EXISTS idx_raw_source_updated ON raw_requests (source_updated_at);


-- ---------------------------------------------------------------------------
-- clean_requests: the serving table. One row per real complaint.
-- `is_duplicate_of` is set rather than the row being deleted, so the quality
-- panel can report "duplicates collapsed" with an auditable trail.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS clean_requests (
    unique_key          TEXT PRIMARY KEY,
    created_date        TIMESTAMPTZ,
    closed_date         TIMESTAMPTZ,
    agency              TEXT,
    agency_name         TEXT,
    complaint_type      TEXT,
    complaint_type_norm TEXT,          -- canonicalized; upstream renames categories
    descriptor          TEXT,
    status              TEXT,
    borough             TEXT,
    incident_zip        TEXT,
    incident_address    TEXT,
    address_norm        TEXT,          -- normalized, used as the dedup key
    latitude            DOUBLE PRECISION,
    longitude           DOUBLE PRECISION,
    resolution_description TEXT,
    source_updated_at   TIMESTAMPTZ,

    -- quality columns
    defect_count        INTEGER     NOT NULL DEFAULT 0,
    is_duplicate_of     TEXT,          -- unique_key of the surviving record
    zip_borough_conflict BOOLEAN    NOT NULL DEFAULT FALSE,
    unrecognized_type   BOOLEAN     NOT NULL DEFAULT FALSE,

    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_clean_created ON clean_requests (created_date DESC);
CREATE INDEX IF NOT EXISTS idx_clean_borough ON clean_requests (borough);
CREATE INDEX IF NOT EXISTS idx_clean_type ON clean_requests (complaint_type_norm);
CREATE INDEX IF NOT EXISTS idx_clean_dupe ON clean_requests (is_duplicate_of);
-- Supports the dedup lookup: same type + same address, ordered by time.
CREATE INDEX IF NOT EXISTS idx_clean_dedup_key
    ON clean_requests (complaint_type_norm, address_norm, created_date);


-- ---------------------------------------------------------------------------
-- validation_failures: one row per (record, rule) violation.
-- Not a column on clean_requests, because a record can fail several rules and
-- the panel reports per-rule rates.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS validation_failures (
    id             BIGSERIAL PRIMARY KEY,
    unique_key     TEXT        NOT NULL,
    rule           TEXT        NOT NULL,
    severity       TEXT        NOT NULL,   -- 'reject' | 'flag' | 'observe'
    detail         TEXT,
    field_value    TEXT,
    ingest_run_id  BIGINT,
    detected_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_vf_run ON validation_failures (ingest_run_id);
CREATE INDEX IF NOT EXISTS idx_vf_rule ON validation_failures (rule);
CREATE INDEX IF NOT EXISTS idx_vf_detected ON validation_failures (detected_at);


-- ---------------------------------------------------------------------------
-- daily_agg: permanent. Never pruned. A few MB holds years.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS daily_agg (
    day                 DATE   NOT NULL,
    borough             TEXT   NOT NULL,
    complaint_type_norm TEXT   NOT NULL,
    request_count       INTEGER NOT NULL,
    closed_count        INTEGER NOT NULL,
    median_hours_to_close DOUBLE PRECISION,
    defect_count        INTEGER NOT NULL DEFAULT 0,
    duplicate_count     INTEGER NOT NULL DEFAULT 0,
    computed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (day, borough, complaint_type_norm)
);

CREATE INDEX IF NOT EXISTS idx_agg_day ON daily_agg (day DESC);


-- ---------------------------------------------------------------------------
-- ingest_runs: the run log. This is what proves the scheduler is alive, and
-- it is the first thing to check when the app looks stale.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ingest_runs (
    id                  BIGSERIAL PRIMARY KEY,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at         TIMESTAMPTZ,
    status              TEXT        NOT NULL DEFAULT 'running',  -- running|success|failed|partial
    trigger             TEXT        NOT NULL DEFAULT 'manual',   -- manual|schedule|backfill

    watermark_before    TIMESTAMPTZ,
    watermark_after     TIMESTAMPTZ,

    pages_fetched       INTEGER NOT NULL DEFAULT 0,
    rows_fetched        INTEGER NOT NULL DEFAULT 0,
    rows_inserted       INTEGER NOT NULL DEFAULT 0,
    rows_updated        INTEGER NOT NULL DEFAULT 0,
    rows_rejected       INTEGER NOT NULL DEFAULT 0,
    rows_flagged        INTEGER NOT NULL DEFAULT 0,
    duplicates_collapsed INTEGER NOT NULL DEFAULT 0,
    unrecognized_types  INTEGER NOT NULL DEFAULT 0,

    raw_pruned          INTEGER NOT NULL DEFAULT 0,
    clean_pruned        INTEGER NOT NULL DEFAULT 0,

    duration_seconds    DOUBLE PRECISION,
    error               TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_started ON ingest_runs (started_at DESC);


-- ---------------------------------------------------------------------------
-- pipeline_state: single-row key/value store. Holds the watermark.
-- The watermark is on Socrata's :updated_at, NOT created_date -- 311 records
-- are mutable and a created_date watermark silently never sees updates.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pipeline_state (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
