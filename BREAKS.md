# Break log

Every time something breaks, it gets four lines here, the same day.

This is the highest-return-per-minute file in the project and it is the one
that will get skipped. The projects that get people hired are the ones where
things broke and got fixed — a build that went smoothly gives you nothing to
say in an interview. Ten entries by October is the target.

**Format:**

```
## YYYY-MM-DD — one-line title
Symptom:
Cause:
Fix:
What I'd do differently:
```

Log the small ones too. "The deploy failed because I put the environment
variable in the wrong service" is a real answer to "tell me about a time
something went wrong," and it is the kind of thing nobody remembers a month
later.

---

## 2026-09-07 — First real ingest filled Neon (512 MB) after 82 minutes

**Symptom:** `python -m app.cli ingest --days-back 1` — the first run against
the real database — ran for 4914 s (82 min) and died with
`psycopg.errors.DiskFull: could not extend file because project size limit
(512 MB) has been exceeded`, raised from the per-row `cur.execute` in
`write_clean` (ingest.py:126). Run 5 recorded `status = partial`,
110,000 rows fetched over 22 pages, 28,239 inserted, 76,761 "updated". DB
ended at 489 MB: `raw_requests` 306 MB / 193k rows, `clean_requests` 168 MB /
193k rows. `daily_agg` empty — the run never reached the rollup step.

**Cause:** four things compounding.
1. `--days-back 1` is only honoured on a virgin DB (`socrata.default_watermark`).
   `pipeline_state` already held a watermark of 2026-09-05, so the run pulled
   ~2 days of the feed regardless of the flag.
2. The watermark is only persisted after dedup + rollup succeed
   (ingest.py:337). The two earlier runs I stopped by hand never got there, so
   every subsequent run restarts from the same 2026-09-05 point and re-fetches
   the same ~110k rows — hence pages of `+0 new, ~5000 revised`.
3. `write_raw` / `write_clean` commit once per page (ingest.py:311). Ctrl-C
   does not roll those back, so the ~165k rows from the two killed runs stayed
   in the tables and this run stacked another 28k on top.
4. `raw_requests` stores the entire Socrata JSON payload per record (~1.6 KB
   row): 193k rows = 306 MB, more than half the free-tier budget on the
   replay/audit table alone. Retention pruning (RAW_RETENTION_DAYS=30) never
   ran, and every row is < 30 days old anyway so it would have freed nothing.

Secondary, same run: the 82-minute duration is the row-at-a-time upsert loop in
`write_clean` (should batch like `write_raw` already does); `unrecognized_types`
was 10,543 of 28,239 inserted (37%) — the complaint-type recognizer is missing
a large share of real 311 types; and psycopg's pool logs
`couldn't stop thread 'pool-1-worker-N' within 5.0 seconds` on every CLI exit
because the pool is never closed explicitly.

Two latent bugs surfaced only once a run got past ingestion for the first time:
`aggregate.rollup` listed `computed_at` as an INSERT target without a matching
SELECT expression (`INSERT has more target columns than expressions`), and
`retention.estimate_sizes` — the query behind the quality panel's storage
widget — selects `relname` / `n_live_tup` unqualified across a join that
exposes both, so it raises `AmbiguousColumn`. Every earlier run died before
reaching either.

**Fix:**
- Truncated `raw_requests`, `clean_requests`, `validation_failures`; cleared
  the stuck watermark. DB 489 MB -> 8 MB.
- `write_clean` now does one pipelined `executemany(..., returning=True)` per
  page instead of a `cur.execute` per row: 5000 rows went from ~3 min to 7 s.
- The run processes each page end to end — write, dedup, roll up, *then*
  advance the watermark — so an interruption re-does one page instead of
  restarting the whole gap. `retention.prune` moved to the start of the run so
  a full database can recover on the next run rather than failing again.
- `RAW_RETENTION_DAYS` 30 -> 7 (config, .env, .env.example, schema, README).
  The payload column is ~1.6 KB/row; 7 days is ~90 MB, 30 was ~380 MB.
- `--reset` flag on `ingest` to override the stored watermark deliberately.
- `db.close_pool()`, called from the CLI, silences the thread-shutdown noise.
- Fixed the two latent bugs above.
- Re-ran clean: `ingest --reset --days-back 2` then `ingest`. Two successful
  runs, ~11k rows, 1636 duplicates collapsed, DB at 47 MB.

**Still open (design calls, not bugs):**
- `clean_pruned` is ~5000 on every run: the feed is watermarked on
  `:updated_at` but `clean_requests` retention is on `created_date`, so old
  cases getting a status update are fetched, stored, and then pruned next run.
  `daily_agg` (permanent) still captures the correction, so no data is lost —
  but it is wasted work. Decide whether the serving window should be
  `updated_at`-based.
- `unrecognized_types` ~20% of inserted rows. Expand the recognizer.

**What I'd do differently:** run the first ingest with a hard page cap
(`MAX_PAGES_PER_RUN=1`) instead of trusting `--days-back`, which turned out to
be a virgin-DB-only default. And never Ctrl-C an ingest that commits per page —
before this fix there was no clean resume, only a re-fetch from the last
committed watermark.

---

## 2026-09-04 — Seeded

Nothing has broken yet. This file exists before the first incident on purpose:
it does not get created after one.

Known things to watch for, from the design phase — when one of these happens,
replace this entry with a real one:

- Socrata rate-limits or changes pagination behaviour mid-run
- A `complaint_type` gets renamed upstream and `complaint_type_unrecognized`
  spikes on the quality panel
- The first Render deploy fails on a missing environment variable
- The dedup window turns out to be too aggressive and collapses distinct
  complaints — record the numbers at 15 min, 60 min, and 24 hr when tuning
- The GitHub scheduled workflow gets auto-disabled after 60 days of repo
  inactivity and ingestion silently stops
- Neon storage approaches 0.5 GB and retention pruning has to be tightened
