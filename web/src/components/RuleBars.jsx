/**
 * Per-rule failure counts for the latest run.
 *
 * Horizontal bars because the categories are rule names — long text that would
 * be unreadable rotated under a vertical axis. One series, so no legend, and
 * every bar carries a direct label: the count is the point, and a reader
 * should never have to estimate it against an axis.
 *
 * Rules that fired zero times are shown, greyed. A rule that suddenly reports
 * nothing is as informative as one that spikes — it usually means an upstream
 * field stopped being populated at all.
 */

const SEVERITY_COPY = {
  reject: 'record dropped',
  flag: 'served, defect recorded',
  observe: 'counted, not a defect',
}

export default function RuleBars({ rules, total }) {
  if (!rules?.length) return <p className="section-note">No rules registered.</p>

  const max = Math.max(...rules.map((r) => r.failures), 1)
  const anyFired = rules.some((r) => r.failures > 0)

  return (
    <>
      {!anyFired && (
        <p className="section-note" style={{ marginTop: 0 }}>
          No rule fired on this run. With {(total ?? 0).toLocaleString()} records
          fetched that is worth a second look, not a celebration.
        </p>
      )}

      {rules.map((rule) => {
        const pct = (rule.failures / max) * 100
        return (
          <div className="rule-row" key={rule.rule}>
            <div className="rule-name" title={SEVERITY_COPY[rule.severity] || rule.severity}>
              {rule.rule}
            </div>
            <div className="rule-track">
              <div
                className={`rule-fill${rule.failures === 0 ? ' rule-fill-zero' : ''}`}
                style={{ width: `${Math.max(pct, rule.failures > 0 ? 1.5 : 0)}%` }}
              />
            </div>
            <div className="rule-value">
              {rule.failures.toLocaleString()}
              <span className="rule-severity"> {rule.rate_pct ? `· ${rule.rate_pct}%` : ''}</span>
            </div>
          </div>
        )
      })}

      <p className="section-note" style={{ marginTop: 14, lineHeight: 1.6 }}>
        <strong style={{ color: 'var(--ink-secondary)' }}>complaint_type_unrecognized</strong>{' '}
        is an observation, not a defect. When the city renames a category the
        record is kept and counted — dropping it would make the app silently stop
        reporting a whole category on the day the label changed.
      </p>
    </>
  )
}
