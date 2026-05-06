import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type { CanvassingInsights } from '../api/types'

const SENTIMENT_COLORS: Record<string, string> = {
  negative: 'var(--opponent)',
  positive: '#34d399',
  neutral:  'var(--text-muted)',
  mixed:    '#fbbf24',
}

function SentimentBar({ breakdown, total }: { breakdown: Record<string, number>; total: number }) {
  const order = ['negative', 'neutral', 'positive', 'mixed']
  const entries = order.filter(k => breakdown[k] > 0).map(k => ({
    key: k, count: breakdown[k], pct: Math.round(breakdown[k] / total * 100),
  }))
  return (
    <div>
      <div style={{ display: 'flex', height: 8, borderRadius: 99, overflow: 'hidden', marginBottom: 8, background: 'var(--surface-2)' }}>
        {entries.map(e => (
          <div key={e.key} style={{ width: `${e.pct}%`, background: SENTIMENT_COLORS[e.key], transition: 'width 0.3s' }} />
        ))}
      </div>
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        {entries.map(e => (
          <span key={e.key} style={{ fontSize: '0.72rem', color: SENTIMENT_COLORS[e.key], fontFamily: 'JetBrains Mono' }}>
            {e.key} {e.pct}% <span style={{ color: 'var(--text-muted)' }}>({e.count})</span>
          </span>
        ))}
      </div>
    </div>
  )
}

export default function CanvassingInsights() {
  const [data, setData]         = useState<CanvassingInsights | null>(null)
  const [loading, setLoading]   = useState(true)
  const [uploading, setUploading] = useState(false)
  const [uploadMsg, setUploadMsg] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    api.getCanvassingInsights().then(d => { setData(d); setLoading(false) })
  }, [])

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    setUploading(true); setUploadMsg(null)
    try {
      const result = await api.uploadCanvassing(file)
      setUploadMsg(result.message)
      const fresh = await api.getCanvassingInsights()
      setData(fresh)
    } catch (err: unknown) {
      setUploadMsg(`Error: ${err instanceof Error ? err.message : 'Upload failed'}`)
    } finally { setUploading(false) }
  }

  if (loading) return <div className="loading-text">Loading…</div>
  if (!data) return null

  return (
    <div className="page">
      {/* Header */}
      <div className="page-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
        <div>
          <div className="label" style={{ marginBottom: 5 }}>Field Intelligence</div>
          <h1 className="page-title">Canvassing Insights</h1>
          <p className="page-subtitle">Voter contact data analyzed by precinct, issue, and sentiment.</p>
        </div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexShrink: 0 }}>
          {uploadMsg && (
            <span style={{ fontSize: '0.74rem', color: uploadMsg.startsWith('Error') ? 'var(--opponent)' : 'var(--ok-light)' }}>
              {uploadMsg.startsWith('Error') ? uploadMsg : `✓ ${uploadMsg}`}
            </span>
          )}
          <button className="btn btn-ghost btn-sm" onClick={() => fileRef.current?.click()} disabled={uploading}>
            {uploading ? 'Uploading…' : '↑ Upload CSV'}
          </button>
          <input ref={fileRef} type="file" accept=".csv" onChange={handleUpload} style={{ display: 'none' }} />
        </div>
      </div>

      {/* CSV format hint */}
      <div className="card" style={{ marginBottom: '1.25rem', borderLeft: '3px solid var(--accent-border)' }}>
        <div className="label" style={{ marginBottom: 6 }}>CSV Upload Format</div>
        <code style={{ fontSize: '0.7rem', color: 'var(--text-secondary)', fontFamily: 'JetBrains Mono', display: 'block', marginBottom: 4 }}>
          voter_name, address, precinct, issue, sentiment, notes, date
        </code>
        <p style={{ margin: 0, fontSize: '0.73rem', color: 'var(--text-muted)' }}>
          voter_name and address are optional (privacy-preserving). sentiment: positive | negative | neutral | mixed
        </p>
      </div>

      {data.total_contacts === 0 ? (
        <div className="empty-state" style={{ marginTop: '2rem' }}>
          <div className="empty-state-icon">◫</div>
          <div className="empty-state-title">No canvassing data yet</div>
          <div className="empty-state-body">Upload a CSV with voter contact data to see precinct-level insights.</div>
        </div>
      ) : (
        <>
          {/* Stats row */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, marginBottom: '1.25rem' }}>
            <div className="card" style={{ textAlign: 'center' }}>
              <div style={{ fontSize: '2.2rem', fontWeight: 700, fontFamily: 'JetBrains Mono', color: 'var(--accent-light)', lineHeight: 1 }}>
                {data.total_contacts}
              </div>
              <div style={{ fontSize: '0.62rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', marginTop: 6 }}>
                Total Contacts
              </div>
            </div>
            <div className="card" style={{ textAlign: 'center' }}>
              <div style={{ fontSize: '2.2rem', fontWeight: 700, fontFamily: 'JetBrains Mono', color: '#c084fc', lineHeight: 1 }}>
                {data.precincts.length}
              </div>
              <div style={{ fontSize: '0.62rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', marginTop: 6 }}>
                Precincts
              </div>
            </div>
            <div className="card">
              <div style={{ fontSize: '0.62rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.07em', marginBottom: 8 }}>
                Top Issues
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                {data.overall_top_issues.slice(0, 4).map(issue => (
                  <span key={issue} className="badge badge-purple" style={{ fontSize: '0.62rem' }}>{issue}</span>
                ))}
              </div>
            </div>
          </div>

          {/* Overall sentiment */}
          <div className="card" style={{ marginBottom: '1.25rem' }}>
            <div className="section-title" style={{ marginBottom: '0.75rem' }}>Overall Sentiment</div>
            <SentimentBar breakdown={data.sentiment_breakdown} total={data.total_contacts} />
          </div>

          {/* Precinct breakdown */}
          <div className="section-title" style={{ marginBottom: '0.75rem' }}>Precinct Breakdown</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 12 }}>
            {data.precincts.map(p => (
              <div key={p.precinct} className="card">
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 10 }}>
                  <span style={{ fontWeight: 700, fontFamily: 'JetBrains Mono', fontSize: '0.88rem', color: 'var(--text-primary)' }}>
                    Precinct {p.precinct}
                  </span>
                  <span style={{ fontSize: '0.68rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>
                    {p.contact_count} contacts
                  </span>
                </div>

                <div style={{ marginBottom: 8 }}>
                  <div style={{ fontSize: '0.6rem', color: 'var(--text-muted)', marginBottom: 5, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                    Top Issues
                  </div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                    {p.top_issues.map((issue, i) => (
                      <span key={issue} className={i === 0 ? 'badge badge-purple' : 'badge badge-ghost'} style={{ fontSize: '0.62rem' }}>
                        {issue}
                      </span>
                    ))}
                  </div>
                </div>

                <div style={{ marginBottom: 10 }}>
                  <div style={{ fontSize: '0.6rem', color: 'var(--text-muted)', marginBottom: 5, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                    Dominant Sentiment
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span style={{ width: 8, height: 8, borderRadius: '50%', background: SENTIMENT_COLORS[p.dominant_sentiment] ?? 'var(--text-muted)', flexShrink: 0 }} />
                    <span style={{ fontSize: '0.78rem', color: SENTIMENT_COLORS[p.dominant_sentiment] ?? 'var(--text-muted)', fontWeight: 500 }}>
                      {p.dominant_sentiment}
                    </span>
                  </div>
                </div>

                <p style={{ margin: 0, fontSize: '0.75rem', color: 'var(--text-secondary)', lineHeight: 1.5, borderTop: '1px solid var(--border)', paddingTop: 8 }}>
                  {p.summary}
                </p>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}
