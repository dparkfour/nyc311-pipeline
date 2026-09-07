function when(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) +
    ' ' + d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
}

/** Short, human labels for the flags a row can carry. */
function flagsFor(row) {
  const flags = []
  if (row.zip_borough_conflict) flags.push('borough conflict')
  if (row.unrecognized_type) flags.push('new category')
  if (row.is_duplicate_of) flags.push('duplicate')
  if (row.defect_count > 0 && flags.length === 0) flags.push(`${row.defect_count} defect${row.defect_count > 1 ? 's' : ''}`)
  return flags
}

export default function RequestTable({ rows }) {
  if (!rows?.length) {
    return <p className="section-note">No records match these filters.</p>
  }

  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>Created</th>
            <th>Complaint</th>
            <th>Descriptor</th>
            <th>Borough</th>
            <th>ZIP</th>
            <th>Status</th>
            <th>Quality</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const flags = flagsFor(row)
            return (
              <tr key={row.unique_key}>
                <td className="num" style={{ whiteSpace: 'nowrap' }}>{when(row.created_date)}</td>
                <td className="strong">{row.complaint_type_norm || row.complaint_type || '—'}</td>
                <td>{row.descriptor || '—'}</td>
                <td>{row.borough || <span style={{ color: 'var(--ink-muted)' }}>unspecified</span>}</td>
                <td className="num">
                  {row.incident_zip || <span style={{ color: 'var(--ink-muted)' }}>—</span>}
                </td>
                <td>{row.status || '—'}</td>
                <td>
                  {flags.length === 0
                    ? <span style={{ color: 'var(--ink-muted)' }}>clean</span>
                    : flags.map((f) => <span className="flag" key={f} style={{ marginRight: 4 }}>{f}</span>)}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
