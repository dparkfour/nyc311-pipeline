import { useMemo, useState } from 'react'

/**
 * Daily request volume — one series, so no legend: the section title names it.
 * Hand-rolled SVG rather than a charting library, because the whole chart is
 * about eighty lines and a dependency would be larger than the thing it draws.
 *
 * Interaction: a crosshair and a tooltip, which a line chart in HTML should
 * have by default. The hit targets are full-height columns, much wider than
 * the 8px markers, so pointing at the line is not a precision exercise.
 */
export default function TrendChart({ data, height = 190 }) {
  const [hover, setHover] = useState(null)

  const W = 900
  const H = height
  const pad = { top: 14, right: 16, bottom: 26, left: 46 }

  const points = useMemo(
    () => data.filter((d) => d.key).map((d) => ({
      label: String(d.key),
      value: Number(d.request_count) || 0,
    })),
    [data],
  )

  if (points.length < 2) {
    return (
      <p className="section-note" style={{ padding: '28px 0' }}>
        Not enough days aggregated yet — the trend appears once the pipeline has
        run across more than one day.
      </p>
    )
  }

  const max = Math.max(...points.map((p) => p.value), 1)
  const niceMax = Math.ceil(max / 100) * 100 || 100
  const innerW = W - pad.left - pad.right
  const innerH = H - pad.top - pad.bottom

  const x = (i) => pad.left + (i / (points.length - 1)) * innerW
  const y = (v) => pad.top + innerH - (v / niceMax) * innerH

  const line = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${x(i)} ${y(p.value)}`).join(' ')
  const area = `${line} L ${x(points.length - 1)} ${pad.top + innerH} L ${x(0)} ${pad.top + innerH} Z`

  const ticks = [0, 0.5, 1].map((f) => Math.round(niceMax * f))
  // At most six date labels, so they never collide however many days are shown.
  const labelEvery = Math.max(1, Math.ceil(points.length / 6))

  return (
    <div style={{ position: 'relative' }}>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        style={{ width: '100%', height: 'auto', display: 'block' }}
        role="img"
        aria-label={`Daily service request volume across ${points.length} days`}
        onMouseLeave={() => setHover(null)}
      >
        {ticks.map((t) => (
          <g key={t}>
            <line
              x1={pad.left} x2={W - pad.right} y1={y(t)} y2={y(t)}
              stroke="var(--grid)" strokeWidth="1"
            />
            <text
              x={pad.left - 9} y={y(t) + 4} textAnchor="end"
              fill="var(--ink-muted)" fontSize="11"
            >
              {t.toLocaleString()}
            </text>
          </g>
        ))}

        <path d={area} fill="var(--series-1-soft)" opacity="0.42" />
        <path
          d={line}
          fill="none"
          stroke="var(--series-1)"
          strokeWidth="2"
          strokeLinejoin="round"
          strokeLinecap="round"
        />

        {hover !== null && (
          <>
            <line
              x1={x(hover)} x2={x(hover)} y1={pad.top} y2={pad.top + innerH}
              stroke="var(--axis)" strokeWidth="1" strokeDasharray="3 3"
            />
            {/* 2px surface ring so the marker reads on top of the line */}
            <circle
              cx={x(hover)} cy={y(points[hover].value)} r="5"
              fill="var(--series-1)" stroke="var(--surface)" strokeWidth="2"
            />
          </>
        )}

        {points.map((p, i) =>
          i % labelEvery === 0 ? (
            <text
              key={p.label}
              x={x(i)} y={H - 7}
              textAnchor="middle" fill="var(--ink-muted)" fontSize="11"
            >
              {p.label.slice(5)}
            </text>
          ) : null,
        )}

        {/* Full-height hit columns: a much bigger target than the marks. */}
        {points.map((p, i) => (
          <rect
            key={`hit-${p.label}`}
            x={x(i) - innerW / points.length / 2}
            y={pad.top}
            width={innerW / points.length}
            height={innerH}
            fill="transparent"
            onMouseEnter={() => setHover(i)}
          />
        ))}
      </svg>

      {hover !== null && (
        <div
          style={{
            position: 'absolute',
            left: `${(x(hover) / W) * 100}%`,
            top: 0,
            transform: 'translate(-50%, -108%)',
            background: 'var(--surface)',
            border: '1px solid var(--border)',
            borderRadius: 8,
            padding: '7px 11px',
            fontSize: 12.5,
            whiteSpace: 'nowrap',
            pointerEvents: 'none',
            boxShadow: '0 2px 10px rgba(0,0,0,0.08)',
          }}
        >
          <strong>{points[hover].value.toLocaleString()}</strong> requests
          <span style={{ color: 'var(--ink-muted)' }}> · {points[hover].label}</span>
        </div>
      )}
    </div>
  )
}
