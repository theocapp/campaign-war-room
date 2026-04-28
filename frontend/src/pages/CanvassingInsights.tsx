import { useEffect, useRef, useState } from 'react'
import { api } from '../api/client'
import type { CanvassingInsights } from '../api/types'

const SENTIMENT_COLORS: Record<string, string> = {
  negative: '#f87171',
  positive: '#34d399',
  neutral: '#8892a4',
  mixed: '#fbbf24',
}

function SentimentBar({ breakdown, total }: { breakdown: Record<string, number>; total: number }) {
  const order = ['negative', 'neutral', 'positive', 'mixed']
  const entries = order.filter(k => breakdown[k] > 0).map(k => ({ key: k, count: breakdown[k], pct: Math.round(breakdown[k] / total * 100) }))
  return (
    <div>
      <div style={{ display: 'flex', height: 8, borderRadius: 4, overflow: 'hidden', marginBottom: 6 }}>
        {entries.map(e => (
          <div key={e.key} style={{ width: `${e.pct}%`, background: SENTIMENT_COLORS[e.key] }} />
        ))}
      </div>
      <div style={{ display: 'flex', gap: 12 }}>
        {entries.map(e => (
          <span key={e.key} style={{ fontSize: '0.7rem', color: SENTIMENT_COLORS[e.key], fontFamily: 'JetBrains Mono' }}>
            {e.key} {e.pct}%
          </span>
        ))}
      </div>
    </div>
  )
}

export default function CanvassingInsights() {
  const [data, setData] = useState<CanvassingInsights | null>(null)
  const [loading, setLoading] = useState(true)
  const [uploading, setUploading] = useState(false)
  const [uploadMsg, setUploadMsg] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    api.getCanvassingInsights().then(d => { setData(d); setLoading(false) })
  }, [])

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    if (!file) return
    setUploading(true)
    setUploadMsg(null)
    try {
      const result = await api.uploadCanvassing(file)
      setUploadMsg(`✓ ${result.message}`)
      const fresh = await api.getCanvassingInsights()
      setData(fresh)
    } catch (err: unknown) {
      setUploadMsg(`Error: ${err instanceof Error ? err.message : 'Upload failed'}`)
    } finally {
      setUploading(false)
    }
  }

  if (loading) return <div style={{ padding: '2rem', color: 'var(--text-muted)' }}>Loading…</div>
  if (!data) return null

  return (
    <div style={{ padding: '1.5rem', maxWidth: 1000 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '1.5rem' }}>
        <div>
          <div className="label" style={{ marginBottom: 4 }}>Field Intelligence</div>
          <h1 style={{ margin: 0, fontSize: '1.2rem', fontWeight: 700 }}>Canvassing Insights</h1>
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {uploadMsg && <span style={{ fontSize: '0.75rem', color: uploadMsg.startsWith('✓') ? '#34d399' : '#f87171' }}>{uploadMsg}</span>}
          <button className="btn-ghost" onClick={() => fileRef.current?.click()} disabled={uploading}>
            {uploading ? 'Uploading…' : '↑ Upload CSV'}
          </button>
          <input ref={fileRef} type="file" accept=".csv" onChange={handleUpload} style={{ display: 'none' }} />
        </div>
      </div>

      {/* CSV format hint */}
      <div className="card" style={{ marginBottom: '1.5rem', borderLeft: '3px solid var(--accent)' }}>
        <div className="section-title">CSV Upload Format</div>
        <code style={{ fontSize: '0.72rem', color: 'var(--text-secondary)', fontFamily: 'JetBrains Mono' }}>
          voter_name, address, precinct, issue, sentiment, notes, date
        </code>
        <p style={{ margin: '6px 0 0', fontSize: '0.75rem', color: 'var(--text-muted)' }}>
          voter_name and address are optional (privacy-preserving). sentiment: positive | negative | neutral | mixed
        </p>
      </div>

      {data.total_contacts === 0 ? (
        <div style={{ color: 'var(--text-muted)', textAlign: 'center', padding: '3rem' }}>
          No canvassing data yet. Upload a CSV or wait for seed data to load.
        </div>
      ) : (
        <>
          {/* Stats row */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12, marginBottom: '1.5rem' }}>
            <div className="card" style={{ textAlign: 'center' }}>
              <div style={{ fontSize: '2rem', fontWeight: 700, fontFamily: 'JetBrains Mono', color: 'var(--accent)' }}>
                {data.total_contacts}
              </div>
              <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Total Contacts</div>
            </div>
            <div className="card" style={{ textAlign: 'center' }}>
              <div style={{ fontSize: '2rem', fontWeight: 700, fontFamily: 'JetBrains Mono', color: '#c084fc' }}>
                {data.precincts.length}
              </div>
              <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>Precincts</div>
            </div>
            <div className="card">
              <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 8 }}>Top Issues</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                {data.overall_top_issues.slice(0, 4).map(issue => (
                  <span key={issue} className="badge badge-info" style={{ fontSize: '0.65rem' }}>{issue}</span>
                ))}
              </div>
            </div>
          </div>

          {/* Overall sentiment */}
          <div className="card" style={{ marginBottom: '1.5rem' }}>
            <div className="section-title">Overall Sentiment</div>
            <SentimentBar breakdown={data.sentiment_breakdown} total={data.total_contacts} />
          </div>

          {/* Precinct breakdown */}
          <div className="section-title">Precinct Breakdown</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 12 }}>
            {data.precincts.map(p => (
              <div key={p.precinct} className="card">
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                  <span style={{ fontWeight: 700, fontFamily: 'JetBrains Mono', fontSize: '0.9rem' }}>
                    Precinct {p.precinct}
                  </span>
                  <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>
                    {p.contact_count} contacts
                  </span>
                </div>
                <div style={{ marginBottom: 10 }}>
                  <div style={{ fontSize: '0.65rem', color: 'var(--text-muted)', marginBottom: 4, textTransform: 'uppercase', letterSpacing: '0.06em' }}>Top Issues</div>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                    {p.top_issues.map((issue, i) => (
                      <span key={issue} className={i === 0 ? 'badge badge-medium' : 'badge badge-ghost'} style={{ fontSize: '0.65rem' }}>{issue}</span>
                    ))}
                  </div>
                </div>
                <div style={{ marginBottom: 10 }}>
                  <div style={{ fontSize: '0.65rem', color: 'var(--text-muted)', marginBottom: 4, textTransform: 'uppercase', letterSpacing: '0.06em' }}>Sentiment</div>
                  <span style={{ fontSize: '0.78rem', color: SENTIMENT_COLORS[p.dominant_sentiment] ?? '#8892a4', fontWeight: 500 }}>
                    {p.dominant_sentiment}
                  </span>
                </div>
                <p style={{ margin: 0, fontSize: '0.76rem', color: 'var(--text-secondary)', lineHeight: 1.5, borderTop: '1px solid var(--border)', paddingTop: 8 }}>
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
