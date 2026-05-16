import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine,
  PieChart, Pie, Cell, Legend,
} from 'recharts'
import { api } from '../api/client'
import type { NarrativeFrameWithCounts } from '../api/types'

const OWNER_COLORS: Record<string, string> = {
  candidate: '#22c55e',
  opponent: '#ef4444',
  media: '#64748b',
}

const OWNER_LABELS: Record<string, string> = {
  candidate: 'Our message',
  opponent: 'Opponent attack',
  media: 'Media theme',
}

function stripHtml(html: string): string {
  return html.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim()
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

interface Timeseries { date: string; count: number }
interface ShareOfVoice { total: number; candidate: number; opponent: number; neutral: number }

export default function FrameDetail() {
  const { id } = useParams<{ id: string }>()
  const frameId = Number(id)

  const [frame, setFrame] = useState<NarrativeFrameWithCounts | null>(null)
  const [series, setSeries] = useState<Timeseries[]>([])
  const [sov, setSov] = useState<ShareOfVoice | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [days, setDays] = useState(0)
  const [monitoringStart, setMonitoringStart] = useState<string | null>(null)

  useEffect(() => {
    api.getMonitoringStartDate().then(r => setMonitoringStart(r.monitoring_start)).catch(() => {})
  }, [])

  useEffect(() => {
    if (!frameId) return
    setLoading(true)
    Promise.all([
      api.getNarrativeFrames(),
      api.getFrameTimeseries(frameId, days),
      api.getFrameShareOfVoice(frameId, 7),
    ])
      .then(([frames, ts, sovData]) => {
        const f = frames.find(fr => fr.id === frameId) ?? null
        setFrame(f)
        setSeries(ts.series)
        setSov(sovData)
      })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [frameId, days])

  if (loading) return <div className="page" style={{ color: 'var(--text-muted, #94a3b8)' }}>Loading…</div>
  if (error) return <div className="page" style={{ color: '#ef4444' }}>Error: {error}</div>
  if (!frame) return <div className="page" style={{ color: '#ef4444' }}>Frame not found.</div>

  const accentColor = OWNER_COLORS[frame.owner_type] || '#3b82f6'

  // Build bar chart data — show every Nth label to avoid overlap
  const labelStep = days <= 14 ? 1 : days <= 30 ? 3 : 7
  const barData = series.map((s, i) => ({
    ...s,
    label: i % labelStep === 0 ? formatDate(s.date) : '',
  }))

  // Share-of-voice donut
  const sovSlices = sov && sov.total > 0
    ? [
        { name: 'Candidate', value: sov.candidate, color: '#22c55e' },
        { name: 'Opponent', value: sov.opponent, color: '#ef4444' },
        { name: 'Neutral', value: sov.neutral, color: '#64748b' },
      ].filter(s => s.value > 0)
    : []

  // Top outlets from recent articles
  const outletCounts: Record<string, number> = {}
  for (const a of frame.recent_articles) {
    if (a.source_name) outletCounts[a.source_name] = (outletCounts[a.source_name] || 0) + 1
  }
  const topOutlets = Object.entries(outletCounts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5)

  return (
    <div className="page">
      {/* Header */}
      <div style={{ marginBottom: 24 }}>
        <Link
          to="/narratives"
          style={{ fontSize: 12, color: 'var(--text-muted, #94a3b8)', textDecoration: 'none', display: 'inline-flex', alignItems: 'center', gap: 4, marginBottom: 12 }}
        >
          ← Back to Narratives
        </Link>
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
          <div>
            <span style={{
              fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em',
              color: accentColor, display: 'block', marginBottom: 4,
            }}>
              {OWNER_LABELS[frame.owner_type] || frame.owner_type}
            </span>
            <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: 'var(--text, #f1f5f9)' }}>{frame.name}</h1>
            {frame.description && (
              <p style={{ margin: '6px 0 0', fontSize: 14, color: 'var(--text-muted, #94a3b8)', maxWidth: 600 }}>
                {frame.description}
              </p>
            )}
          </div>
          <div style={{ display: 'flex', gap: 24, flexShrink: 0 }}>
            {[
              { label: 'This week', value: frame.mentions_this_week },
              { label: 'Last week', value: frame.mentions_last_week },
              { label: 'Total', value: frame.mentions_total },
            ].map(({ label, value }) => (
              <div key={label} style={{ textAlign: 'center' }}>
                <div style={{ fontSize: 26, fontWeight: 700, color: 'var(--text, #f1f5f9)' }}>{value}</div>
                <div style={{ fontSize: 11, color: 'var(--text-muted, #94a3b8)' }}>{label}</div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {/* Main grid */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr auto', gap: 24, alignItems: 'start' }}>
        {/* Bar chart */}
        <div style={{ background: 'var(--surface, #1e293b)', border: '1px solid var(--border, #334155)', borderRadius: 8, padding: '20px 20px 12px' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text, #f1f5f9)' }}>Daily mentions</div>
            <div style={{ display: 'flex', gap: 4 }}>
              {[0, 30, 90].map(d => (
                <button
                  key={d}
                  onClick={() => setDays(d)}
                  style={{
                    padding: '3px 10px', fontSize: 11, borderRadius: 4, cursor: 'pointer',
                    background: days === d ? accentColor : 'transparent',
                    border: `1px solid ${days === d ? accentColor : 'var(--border, #334155)'}`,
                    color: days === d ? '#fff' : 'var(--text-muted, #94a3b8)',
                  }}
                >
                  {d === 0 ? 'All' : `${d}d`}
                </button>
              ))}
            </div>
          </div>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={barData} margin={{ top: 0, right: 0, left: -20, bottom: 0 }}>
              <XAxis dataKey="label" tick={{ fontSize: 10, fill: '#64748b' }} axisLine={false} tickLine={false} interval={0} />
              <YAxis allowDecimals={false} tick={{ fontSize: 10, fill: '#64748b' }} axisLine={false} tickLine={false} />
              <Tooltip
                contentStyle={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 4, fontSize: 12 }}
                labelFormatter={(_label, payload) => (payload as unknown as { payload?: { date: string } }[])?.[0]?.payload?.date ?? ''}
                formatter={(v) => [v as number, 'mentions']}
              />
              <Bar dataKey="count" fill={accentColor} radius={[2, 2, 0, 0]} />
              {monitoringStart && (
                <ReferenceLine
                  x={monitoringStart}
                  stroke="#f59e0b"
                  strokeDasharray="4 2"
                  label={{ value: 'Monitoring started', position: 'insideTopRight', fontSize: 10, fill: '#f59e0b' }}
                />
              )}
            </BarChart>
          </ResponsiveContainer>
          {monitoringStart && (
            <div style={{ fontSize: 11, color: '#f59e0b', marginTop: 6 }}>
              Data before {formatDate(monitoringStart)} is a partial backfill and may undercount coverage.
            </div>
          )}
        </div>

        {/* Share-of-voice donut */}
        <div style={{ background: 'var(--surface, #1e293b)', border: '1px solid var(--border, #334155)', borderRadius: 8, padding: '20px', width: 220 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text, #f1f5f9)', marginBottom: 4 }}>Share of voice</div>
          <div style={{ fontSize: 11, color: 'var(--text-muted, #94a3b8)', marginBottom: 12 }}>last 7 days</div>
          {sovSlices.length > 0 ? (
            <>
              <PieChart width={180} height={140}>
                <Pie
                  data={sovSlices}
                  cx={90}
                  cy={65}
                  innerRadius={42}
                  outerRadius={64}
                  dataKey="value"
                  strokeWidth={0}
                  isAnimationActive={false}
                >
                  {sovSlices.map(s => <Cell key={s.name} fill={s.color} />)}
                </Pie>
                <Legend iconSize={8} iconType="circle" wrapperStyle={{ fontSize: 11, color: '#94a3b8' }} />
              </PieChart>
              <div style={{ fontSize: 11, color: 'var(--text-muted, #94a3b8)', textAlign: 'center' }}>
                {sov?.total} article{sov?.total !== 1 ? 's' : ''}
              </div>
            </>
          ) : (
            <div style={{ fontSize: 12, color: 'var(--text-muted, #94a3b8)', padding: '16px 0', textAlign: 'center' }}>No mentions this week</div>
          )}
        </div>
      </div>

      {/* Bottom row: top outlets + recent articles */}
      <div style={{ display: 'grid', gridTemplateColumns: '200px 1fr', gap: 24, marginTop: 24 }}>
        {/* Top outlets */}
        <div style={{ background: 'var(--surface, #1e293b)', border: '1px solid var(--border, #334155)', borderRadius: 8, padding: 16 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text, #f1f5f9)', marginBottom: 12 }}>Top outlets</div>
          {topOutlets.length === 0 ? (
            <div style={{ fontSize: 12, color: 'var(--text-muted, #94a3b8)' }}>No outlet data</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {topOutlets.map(([name, count]) => (
                <div key={name} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 8 }}>
                  <div style={{ fontSize: 12, color: 'var(--text, #f1f5f9)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{name}</div>
                  <div style={{ fontSize: 12, fontWeight: 600, color: accentColor, flexShrink: 0 }}>{count}</div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Recent articles */}
        <div style={{ background: 'var(--surface, #1e293b)', border: '1px solid var(--border, #334155)', borderRadius: 8, padding: 16 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text, #f1f5f9)', marginBottom: 12 }}>Recent articles</div>
          {frame.recent_articles.length === 0 ? (
            <div style={{ fontSize: 12, color: 'var(--text-muted, #94a3b8)' }}>No articles yet</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {frame.recent_articles.map(a => (
                <div key={a.id} style={{ borderLeft: `2px solid ${accentColor}`, paddingLeft: 10 }}>
                  <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--text, #f1f5f9)' }}>
                    {a.source_url
                      ? <a href={a.source_url} target="_blank" rel="noopener noreferrer" style={{ color: 'inherit', textDecoration: 'none' }}>{stripHtml(a.title || '(no title)')}</a>
                      : stripHtml(a.title || '(no title)')}
                    {' '}
                    <Link to={`/sources/${a.id}`} style={{ fontSize: 11, color: 'var(--text-muted, #94a3b8)', textDecoration: 'none' }}>detail</Link>
                  </div>
                  {a.summary && (
                    <div style={{ fontSize: 12, color: 'var(--text-muted, #94a3b8)', marginTop: 2 }}>
                      {stripHtml(a.summary).slice(0, 160)}{stripHtml(a.summary).length > 160 ? '…' : ''}
                    </div>
                  )}
                  {a.source_name && <div style={{ fontSize: 11, color: 'var(--text-muted, #94a3b8)', marginTop: 2 }}>{a.source_name}</div>}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
