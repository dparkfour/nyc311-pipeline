# Deploy checklist

Work top to bottom. Each step has a check that proves it worked — do not move
on until the check passes, because a failure two steps later is much harder to
attribute.

---

## 1. Neon (5 min)

1. [neon.tech](https://neon.tech) → sign up with GitHub
2. New project, name `nyc311`, region **AWS us-east-2 (Ohio)** — the same
   region as the Render service, so the round trip is a few milliseconds rather
   than a few dozen
3. Copy the **pooled** connection string. The host contains `-pooler`. Ensure it
   ends with `?sslmode=require`

**Do not create a PostgreSQL database on Render.** The free one is deleted 30
days after creation. This is the single decision that determines whether the
link still works in the spring.

**Check:**

```bash
cp .env.example .env          # paste DATABASE_URL
python -m app.cli migrate
```

Expect `migrate: schema applied`.

---

## 2. Socrata app token (2 min, optional but do it)

[data.cityofnewyork.us developer settings](https://data.cityofnewyork.us/profile/edit/developer_settings)
→ create an app token. Free, instant, no approval.

Without one you share a throttling budget with every other anonymous caller
hitting that domain, and 429s stop being exceptional.

**Check:**

```bash
python -m app.cli probe
```

Expect `"has_updated_at": true` and a field list. Compare that list against
what `SELECT_FIELDS` in `app/pipeline/socrata.py` asks for — the documented
schema and the returned schema have drifted before, and this is the moment to
find out.

---

## 3. First ingest (5 min)

```bash
python -m app.cli ingest --days-back 1
python -m app.cli status
```

Expect a `success` status and a non-zero `rows_inserted`.

If `rows_fetched` is large and `rows_inserted` is 0, the watermark is ahead of
the data — `python -m app.cli reset-watermark --days-back 1`.

**Capture a real fixture now, while you are here:**

```bash
python -m app.cli probe --sample
```

Paste a few real rows into `tests/fixtures/socrata_page.json`, keeping the
deliberately broken rows that are already in it. The committed fixture is
hand-built to cover every defect class; real rows make it a better regression
asset.

---

## 4. GitHub (5 min)

1. Private repo, push
2. Settings → Secrets and variables → Actions → `DATABASE_URL`,
   `SOCRATA_APP_TOKEN`
3. Actions tab → **Ingest** → Run workflow

**Check:** the manual run is green, and `python -m app.cli status` shows a run
with `trigger = schedule`.

Do this before trusting the cron. A scheduled job that fails at 3am is
invisible; a manual one that fails is on screen.

---

## 5. Render (15 min)

1. [render.com](https://render.com) → sign up with GitHub
2. New → **Blueprint** → select the repo. `render.yaml` creates both services
3. On `nyc311-api`, set `DATABASE_URL` (Neon) and `SOCRATA_APP_TOKEN`
4. Wait for the API to deploy, note its URL
5. On `nyc311-web`, set `VITE_API_BASE` to that API URL — **no trailing slash**
6. Redeploy the static site so the build picks up the variable

**Checks, in order:**

```bash
curl https://<api>.onrender.com/api/health      # {"status":"ok","database":true}
curl https://<api>.onrender.com/api/quality     # the panel's data
```

Then open `https://<api>.onrender.com/docs` and click through a route. Then
open the static site.

### If the front end shows data but the API calls fail from the browser

CORS. Set `CORS_ORIGINS` on the API service to the static site's exact origin
(scheme + host, no path, no trailing slash) and redeploy. Tighten it from `*`
once both URLs exist — that is the point at which it stops being a placeholder.

### If the first load hangs

That is the cold start, and the UI says so with a running clock. If it exceeds
90 seconds, check the Render logs — most likely `DATABASE_URL` is unset on the
service and the app is retrying the pool.

---

## 6. Confirm it is alive (next day)

- `/api/runs` shows several `success` runs 15 minutes apart
- `minutes_since_last_success` is small and `stale` is `false`
- The quality panel shows non-zero rule counts
- `/api/storage` shows `used_pct` in single digits

Then fill in the live URL at the top of `README.md`, and set a monthly calendar
reminder to push something — GitHub disables scheduled workflows after 60 days
of repository inactivity, and this is the failure most likely to make the
project look abandoned at the worst possible moment.
