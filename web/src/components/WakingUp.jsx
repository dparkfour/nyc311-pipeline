/**
 * Render's free tier spins a web service down after 15 minutes of no traffic
 * and takes about a minute to wake. A spinner for sixty seconds reads as
 * broken and the visitor closes the tab — and the visitor is the entire point
 * of this project.
 *
 * So: say what is happening and why, and show the clock running. This is not
 * an apology. Someone who explains their infrastructure's first ten seconds is
 * making a better impression than someone whose page just sits there.
 */
export default function WakingUp({ seconds }) {
  const pct = Math.min(100, (seconds / 60) * 100)

  return (
    <div className="state">
      <h2>Waking the server up</h2>
      <p>
        This runs on free-tier infrastructure that sleeps after 15 minutes
        without traffic. The first request wears the cold start — usually under
        a minute.
      </p>
      <p style={{ color: 'var(--ink-muted)', fontVariantNumeric: 'tabular-nums' }}>
        {seconds}s elapsed
      </p>
      <div className="progress">
        <div className="progress-bar" style={{ width: `${pct}%` }} />
      </div>
    </div>
  )
}
