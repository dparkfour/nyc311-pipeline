import { useCallback, useEffect, useState } from 'react'
import { get, waitForServer } from './api'
import QualityPanel from './components/QualityPanel'
import RequestTable from './components/RequestTable'
import TrendChart from './components/TrendChart'
import WakingUp from './components/WakingUp'

const API_BASE = (import.meta.env.VITE_API_BASE || '').replace(/\/$/, '')

export default function App() {
  const [phase, setPhase] = useState('waking')   // waking | ready | error | empty
  const [waited, setWaited] = useState(0)
  const [error, setError] = useState(null)

  const [quality, setQuality] = useState(null)
  const [runs, setRuns] = useState(null)
  const [daily, setDaily] = useState([])
  const [types, setTypes] = useState([])

  const [rows, setRows] = useState([])
  const [borough, setBorough] = useState('')
  const [type, setType] = useState('')
  const [defectsOnly, setDefectsOnly] = useState(false)
  const [loadingRows, setLoadingRows] = useState(false)

  // --- boot ---------------------------------------------------------------
  useEffect(() => {
    let cancelled = false

    ;(async () => {
      const awake = await waitForServer({
        onAttempt: (_, seconds) => !cancelled && setWaited(seconds),
      })
      if (cancelled) return
      if (!awake) {
        setError('The server did not respond within 90 seconds.')
        setPhase('error')
        return
      }

      try {
        const [q, r, d, t] = await Promise.all([
          get('/quality'),
          get('/runs', { limit: 10 }),
          get('/daily', { days: 45, group_by: 'day' }),
          get('/complaint-types', { limit: 25 }),
        ])
        if (cancelled) return
        setQuality(q)
        setRuns(r)
        setDaily(d.results || [])
        setTypes(t.results || [])
        setPhase('ready')
      } catch (err) {
        if (cancelled) return
        // 503 from /quality means the schema is up but nothing has been
        // ingested yet — a first-deploy state, not a failure.
        if (err.status === 503) {
          setPhase('empty')
          setError(err.message)
        } else {
          setError(err.message)
          setPhase('error')
        }
      }
    })()

    return () => { cancelled = true }
  }, [])

  // --- filtered table -----------------------------------------------------
  const loadRows = useCallback(async () => {
    setLoadingRows(true)
    try {
      const data = await get('/requests', {
        borough: borough || undefined,
        complaint_type: type || undefined,
        defects_only: defectsOnly || undefined,
        limit: 100,
      })
      setRows(data.results || [])
    } catch {
      setRows([])
    } finally {
      setLoadingRows(false)
    }
  }, [borough, type, defectsOnly])

  useEffect(() => {
    if (phase === 'ready') loadRows()
  }, [phase, loadRows])

  // --- states -------------------------------------------------------------
  if (phase === 'waking') return <WakingUp seconds={waited} />

  if (phase === 'error') {
    return (
      <div className="state">
        <h2>Could not reach the API</h2>
        <p>{error}</p>
        <p style={{ color: 'var(--ink-muted)' }}>
          The run log at <code>/api/runs</code> is the first place to look —
          if the last successful run is hours old, the scheduled job stopped
          rather than the server.
        </p>
      </div>
    )
  }

  if (phase === 'empty') {
    return (
      <div className="state">
        <h2>No data ingested yet</h2>
        <p>{error}</p>
        <p style={{ color: 'var(--ink-muted)' }}>
          The database and API are up; the pipeline has not completed a run.
        </p>
      </div>
    )
  }

  return (
    <div className="shell">
      <header className="masthead">
        <h1>NYC 311 — live pipeline</h1>
        <p>
          A scheduled job pulls service requests from the City of New York's
          Socrata feed every fifteen minutes, validates and deduplicates them,
          and serves them here. The data-quality metrics are on the page rather
          than in a log, because they are the part worth showing.{' '}
          <a href={`${API_BASE}/docs`} target="_blank" rel="noreferrer">
            Browse the API →
          </a>
        </p>
      </header>

      <section className="section">
        <QualityPanel quality={quality} runs={runs} />
      </section>

      <section className="section">
        <div className="section-head">
          <h2>Daily volume</h2>
          <span className="section-note">
            from the permanent aggregate table · duplicates excluded
          </span>
        </div>
        <div className="card">
          <TrendChart data={daily} />
        </div>
      </section>

      <section className="section">
        <div className="section-head">
          <h2>Recent requests</h2>
          <span className="section-note">
            {loadingRows ? 'loading…' : `${rows.length} shown`}
          </span>
        </div>

        <div className="filters">
          <select value={borough} onChange={(e) => setBorough(e.target.value)} aria-label="Borough">
            <option value="">All boroughs</option>
            {['MANHATTAN', 'BRONX', 'BROOKLYN', 'QUEENS', 'STATEN ISLAND'].map((b) => (
              <option key={b} value={b}>{b}</option>
            ))}
          </select>

          <select value={type} onChange={(e) => setType(e.target.value)} aria-label="Complaint type">
            <option value="">All complaint types</option>
            {types.map((t) => (
              <option key={t.complaint_type} value={t.complaint_type}>
                {t.complaint_type} ({t.n})
              </option>
            ))}
          </select>

          <label className="toggle">
            <input
              type="checkbox"
              checked={defectsOnly}
              onChange={(e) => setDefectsOnly(e.target.checked)}
            />
            Only records with defects
          </label>
        </div>

        <div className="card">
          <RequestTable rows={rows} />
        </div>
      </section>

      <footer className="footer">
        Source: <a href="https://data.cityofnewyork.us/Social-Services/311-Service-Requests-from-2010-to-Present/erm2-nwe9" target="_blank" rel="noreferrer">NYC Open Data — 311 Service Requests</a>.
        Ingestion is watermarked on Socrata's <code>:updated_at</code> rather than
        <code> created_date</code>, because 311 records are revised after they are
        filed and a creation-time watermark never sees those revisions.
        Retention is bounded by a 0.5 GB storage budget: 30 days raw, 60 days
        clean, daily aggregates kept permanently.
      </footer>
    </div>
  )
}
