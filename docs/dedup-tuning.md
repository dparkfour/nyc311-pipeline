# Tuning the deduplication window

**Status: not yet tuned.** The default is 60 minutes, which is a starting
guess, not a finding. Fill this in once the pipeline has a week of real data.

A rule you picked and never questioned is worth very little in an interview. A
rule you tuned, with the numbers from three settings and a reason for the one
you kept, is a twenty-minute answer.

## How to gather the numbers

```bash
python -m app.cli tune-dedup --days 7 --windows 5 15 60 240 1440
```

It reports, for each window: how many records collapse, what percentage of the
candidate set that is, and the **largest cluster** — which is the number that
actually matters.

## What to record

| Window | Collapsed | % of candidates | Largest cluster | Spot-check verdict |
|---|---|---|---|---|
| 5 min | | | | |
| 15 min | | | | |
| 60 min | | | | |
| 4 hr | | | | |
| 24 hr | | | | |

## How to decide

The percentage alone will not tell you. **Look at the largest clusters by
hand** — `/api/requests/{unique_key}` returns a record with everything that
collapsed into it.

A window is too wide when the biggest cluster stops looking like *"four
neighbours reported one broken hydrant"* and starts looking like *"every noise
complaint at this address this week."* Two genuinely separate parties on
Friday and Saturday at the same address is the case that breaks a 24-hour
window, and it is the one worth checking specifically.

Note also what the window does **not** catch: two people reporting the same
pothole from opposite ends of the block have different addresses and will not
collapse. Widening the time window does not fix that — it is an address
problem, not a time problem, and the honest answer is that this pipeline
does not attempt spatial clustering.

## Write down

1. The window chosen and the number that decided it
2. One cluster that was correctly collapsed, with its records
3. One pair that was correctly **not** collapsed at the chosen window but would
   have been at the next one up
4. Anything the rule cannot catch, and why widening the window would not help

Then set `DEDUP_WINDOW_MINUTES` and add a dated entry to `BREAKS.md` if the
tuning revealed anything surprising.
