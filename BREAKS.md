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
