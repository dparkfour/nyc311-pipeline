import RuleBars from './RuleBars'

/**
 * The point of the project.
 *
 * Every student's project shows the data. This shows what was wrong with the
 * data and what the pipeline did about it — which is the part that is
 * defensible in an interview, so it goes above the table rather than behind a
 * tab.
 */

function num(n) {
  return (n ?? 0).toLocaleString()
}

function ago(iso) {
  if (!iso) return 'never'
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes} min ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} hr ago`
  return `${Math.round(hours / 24)} d ago`
}

function Delta({ value, invert = false }) {
  if (value === null || value === undefined || value === 0) {
    return <span className="delta delta-down">no change</span>
  }
  const better = invert ? value < 0 : value > 0
  return (
    <span className={better ? 'delta delta-up' : 'delta delta-down'}>
      {value > 0 ? '+' : ''}{num(value)}
    </span>
  )
}

function Tile({ label, value, sub }) {
  return (
    <div className="tile">
      <div className="tile-label">{label}</div>
      <div className="tile-value">{value}</div>
      {sub && <div className="tile-sub">{sub}</div>}
    </div>
  )
}

export default function QualityPanel({ quality, runs }) {
  const run = quality.latest_run
  const day = quality.last_24h || {}
  const delta = quality.change_since_previous_run || {}
  const totals = quality.totals || {}

  const fetched = day.rows_fetched || 0
  const flagRate = fetched ? (100 * (day.rows_flagged || 0)) / fetched : 0
  const dupRate = fetched ? (100 * (day.duplicates_collapsed || 0)) / fetched : 0

  // Freshness is the real health signal. GitHub disables scheduled workflows
  // after 60 days of repository inactivity, so a pipeline that has gone quiet
  // usually means the cron was silently switched off rather than that anything
  // threw an error.
  const stale = runs?.stale
  const failed = run.status === 'failed' || run.status === 'partial'
  const health = failed ? 'critical' : stale ? 'warning' : 'good'
  const healthText = failed
    ? `last run ${run.status}`
    : stale
      ? 'no successful run in 3+ hours'
      : 'ingesting on schedule'

  return (
    <>
      <div className="section-head">
        <h2>Data quality — last 24 hours</h2>
        <span className="pill">
          <span className={`dot dot-${health}`} aria-hidden="true" />
          {healthText}
        </span>
      </div>

      <div className="tiles">
        <Tile
          label="Records ingested"
          value={num(fetched)}
          sub={<>across {num(day.runs)} runs · <Delta value={delta.rows_fetched} /> vs. previous run</>}
        />
        <Tile
          label="Flagged with defects"
          value={num(day.rows_flagged)}
          sub={`${flagRate.toFixed(1)}% of ingested — served, with the defect recorded`}
        />
        <Tile
          label="Duplicates collapsed"
          value={num(day.duplicates_collapsed)}
          sub={`${dupRate.toFixed(1)}% · ${quality.dedup_window_minutes}-minute window, same type and address`}
        />
        <Tile
          label="Rejected"
          value={num(day.rows_rejected)}
          sub="missing a unique key, complaint type, or creation date"
        />
      </div>

      <div className="section">
        <div className="section-head">
          <h2>Per-rule failures — run #{run.id}</h2>
          <span className="section-note">
            {ago(run.finished_at)} · {run.duration_seconds ?? '—'}s ·{' '}
            {num(run.rows_inserted)} new, {num(run.rows_updated)} revised
          </span>
        </div>
        <div className="card">
          <RuleBars rules={quality.rules} total={run.rows_fetched} />
        </div>
      </div>

      <div className="section">
        <div className="section-head">
          <h2>Store</h2>
          <span className="section-note">
            raw {quality.retention?.raw_days}d · clean {quality.retention?.clean_days}d ·
            daily aggregates permanent
          </span>
        </div>
        <div className="tiles">
          <Tile label="Servable records" value={num(totals.clean_rows)} sub="after deduplication" />
          <Tile label="With a defect" value={num(totals.defective_rows)} sub="flagged, not discarded" />
          <Tile label="Marked duplicate" value={num(totals.duplicate_rows)} sub="linked to a survivor, never deleted" />
          <Tile
            label="Days aggregated"
            value={num(totals.agg_rows)}
            sub={totals.agg_since ? `since ${String(totals.agg_since).slice(0, 10)}` : 'permanent table'}
          />
        </div>
      </div>
    </>
  )
}
