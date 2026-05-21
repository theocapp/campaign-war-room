import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { MorningBriefing, BriefingArticle, NarrativePulseItem, SpikeAlert } from '../api/types'

const OWNER_COLOR: Record<string, string> = {
  candidate: '#0059c2',
  opponent:  '#d71913',
  media:     '#6b6b6b',
}
const OWNER_LABEL: Record<string, string> = {
  candidate: 'Campaign',
  opponent:  'Opposition',
  media:     'Media',
}
const TREND_ICON: Record<string, string>  = { up: '↑', down: '↓', flat: '→' }
const TREND_COLOR: Record<string, string> = { up: '#22c55e', down: '#d71913', flat: '#6b6b6b' }

function timeAgo(iso: string | null): string {
  if (!iso) return ''
  const diff = Date.now() - new Date(iso).getTime()
  const h = Math.floor(diff / 3600000)
  if (h < 1) return 'just now'
  if (h === 1) return '1h ago'
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

const mono: React.CSSProperties = { fontFamily: "'JetBrains Mono', monospace" }

function SectionDivider({ number, title, count, color }: {
  number: string; title: string; count?: number; color?: string
}) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, margin: '32px 0 16px' }}>
      <span style={{ ...mono, fontSize: 10, fontWeight: 700, color: color || '#475569', letterSpacing: '0.1em' }}>
        {number}
      </span>
      <span style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.12em', color: color || 'var(--text-muted, #64748b)' }}>
        {title}
      </span>
      {count != null && count > 0 && (
        <span style={{
          ...mono, fontSize: 10, fontWeight: 700,
          background: (color || '#475569') + '22',
          color: color || '#64748b',
          border: `1px solid ${(color || '#475569') + '44'}`,
          borderRadius: 4, padding: '1px 6px',
        }}>{count}</span>
      )}
      <div style={{ flex: 1, height: 1, background: 'var(--border, #1e3050)' }} />
    </div>
  )
}

function ArticleRow({ article, urgent }: { article: BriefingArticle; urgent?: boolean }) {
  const accentColor = urgent ? '#d71913' : '#434343'

  return (
    <div style={{
      borderLeft: `2px solid ${accentColor}`,
      paddingLeft: 14,
      paddingBottom: 16,
      marginBottom: 4,
    }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, marginBottom: 4 }}>
        <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary, #e2e8f0)', lineHeight: 1.4, flex: 1 }}>
          {article.source_url
            ? <a href={article.source_url} target="_blank" rel="noopener noreferrer"
                style={{ color: 'inherit', textDecoration: 'none' }}
                onMouseEnter={e => (e.currentTarget.style.color = '#ffbf00')}
                onMouseLeave={e => (e.currentTarget.style.color = 'inherit')}>
                {article.title || '(no title)'}
              </a>
            : article.title || '(no title)'
          }
        </div>
        {urgent && (
          <span style={{
            ...mono, fontSize: 9, fontWeight: 700, letterSpacing: '0.1em',
            color: '#d71913', border: '1px solid rgba(215,25,19,0.27)', borderRadius: 3,
            padding: '2px 6px', flexShrink: 0, textTransform: 'uppercase',
          }}>Respond</span>
        )}
      </div>

      {article.summary && (
        <div style={{ fontSize: 12, color: 'var(--text-secondary, #94a3b8)', lineHeight: 1.55, marginBottom: 5 }}>
          {article.summary.replace(/<[^>]+>/g, '').slice(0, 180)}
        </div>
      )}

      <div style={{ ...mono, fontSize: 10, color: 'var(--text-muted, #64748b)', display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        {article.source_name && <span style={{ color: '#94a3b8' }}>{article.source_name}</span>}
        {article.published_at && <span>{timeAgo(article.published_at)}</span>}
        {article.race_relevance_score != null && (
          <span>relevance {article.race_relevance_score}</span>
        )}
      </div>
    </div>
  )
}

function SpikeRow({ spike }: { spike: SpikeAlert }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '7px 0', borderBottom: '1px solid var(--border, #1e3050)' }}>
      <span style={{ ...mono, fontSize: 10, color: '#fbbf24', fontWeight: 700, flexShrink: 0 }}>
        ↑ {spike.ratio}×
      </span>
      <Link to={`/frames/${spike.frame_id}`} style={{
        fontSize: 13, fontWeight: 500, color: 'var(--text-primary, #e2e8f0)',
        textDecoration: 'none', flex: 1,
      }}
        onMouseEnter={e => (e.currentTarget.style.color = '#ffbf00')}
        onMouseLeave={e => (e.currentTarget.style.color = 'inherit')}>
        {spike.frame_name}
      </Link>
      <span style={{ ...mono, fontSize: 10, color: 'var(--text-muted, #64748b)', flexShrink: 0 }}>
        {spike.count_24h} mentions / 24h
      </span>
    </div>
  )
}

function NarrativeTable({ items }: { items: NarrativePulseItem[] }) {
  if (items.length === 0) return (
    <div style={{ fontSize: 12, color: 'var(--text-muted, #64748b)', padding: '12px 0' }}>
      Narrative frames will appear here after the first ingestion cycle.
    </div>
  )

  const sorted = [...items].sort((a, b) => (b.this_week - a.this_week))

  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
      <thead>
        <tr style={{ borderBottom: '1px solid var(--border, #1e3050)' }}>
          {['Narrative', 'Side', 'This week', 'Last week', 'Trend'].map(h => (
            <th key={h} style={{
              ...mono, textAlign: h === 'Narrative' ? 'left' : 'center',
              fontSize: 9, fontWeight: 700, textTransform: 'uppercase',
              letterSpacing: '0.1em', color: 'var(--text-muted, #64748b)',
              padding: '0 8px 8px', paddingLeft: h === 'Narrative' ? 0 : undefined,
            }}>{h}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {sorted.map(item => {
          const ownerColor = OWNER_COLOR[item.owner_type] || '#64748b'
          const trendColor = TREND_COLOR[item.trend]
          return (
            <tr key={item.id} style={{ borderBottom: '1px solid var(--border, #1e3050)' }}>
              <td style={{ padding: '9px 8px 9px 0', color: 'var(--text-primary, #e2e8f0)', fontWeight: 500 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ width: 6, height: 6, borderRadius: '50%', background: ownerColor, flexShrink: 0 }} />
                  {item.name}
                </div>
              </td>
              <td style={{ ...mono, padding: '9px 8px', textAlign: 'center', fontSize: 10, color: ownerColor, fontWeight: 700, letterSpacing: '0.05em' }}>
                {OWNER_LABEL[item.owner_type] || item.owner_type}
              </td>
              <td style={{ ...mono, padding: '9px 8px', textAlign: 'center', fontWeight: 700, color: 'var(--text-primary, #e2e8f0)', fontSize: 13 }}>
                {item.this_week}
              </td>
              <td style={{ ...mono, padding: '9px 8px', textAlign: 'center', color: 'var(--text-muted, #64748b)' }}>
                {item.last_week}
              </td>
              <td style={{ ...mono, padding: '9px 8px', textAlign: 'center', fontWeight: 700, color: trendColor, fontSize: 13 }}>
                {TREND_ICON[item.trend]}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

export default function MorningBriefing() {
  const [data, setData] = useState<MorningBriefing | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.getMorningBriefing()
      .then(setData)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))

    const t = setInterval(() => {
      api.getMorningBriefing().then(setData).catch(() => {})
    }, 5 * 60 * 1000)
    return () => clearInterval(t)
  }, [])

  if (loading) return <div style={{ padding: 40, color: 'var(--text-muted, #64748b)', ...mono, fontSize: 12 }}>Loading briefing…</div>
  if (error)   return <div style={{ padding: 40, color: '#d71913' }}>Error: {error}</div>
  if (!data)   return null

  const today = new Date().toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', year: 'numeric' })
  const genTime = new Date(data.generated_at).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
  const hasSpikes   = (data.spike_alerts?.length ?? 0) > 0
  const hasRespond  = data.needs_response.length > 0
  const hasArticles = data.new_articles.length > 0

  return (
    <div style={{ padding: '32px 40px 64px', maxWidth: 1200 }}>

      {/* Document header */}
      <div style={{ borderBottom: '1px solid var(--border, #1e3050)', paddingBottom: 16, marginBottom: 24 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div>
            <div style={{ ...mono, fontSize: 10, fontWeight: 700, letterSpacing: '0.18em', textTransform: 'uppercase', color: '#475569', marginBottom: 6 }}>
              Daily Intelligence Brief
            </div>
            <h1 style={{ margin: 0, fontSize: 26, fontWeight: 700, letterSpacing: '-0.02em', color: 'var(--text-primary, #e2e8f0)', lineHeight: 1.15 }}>
              Morning Briefing
            </h1>
          </div>
          <div style={{ textAlign: 'right' }}>
            <div style={{ ...mono, fontSize: 11, color: 'var(--text-secondary, #94a3b8)', marginBottom: 3 }}>{today}</div>
            <div style={{ ...mono, fontSize: 10, color: 'var(--text-muted, #64748b)' }}>
              Generated {genTime} · {data.meta.relevant_articles_today} relevant / {data.meta.total_articles_today} ingested
            </div>
          </div>
        </div>
      </div>

      {/* Situation summary */}
      {data.race_memo && (
        <div style={{ marginBottom: 8 }}>
          <div style={{ ...mono, fontSize: 9, fontWeight: 700, letterSpacing: '0.12em', textTransform: 'uppercase', color: '#ffbf00', marginBottom: 10 }}>
            Situation Summary
          </div>
          <p style={{
            margin: 0, fontSize: 14, lineHeight: 1.75,
            color: 'var(--text-primary, #e2e8f0)',
            borderLeft: '2px solid rgba(255,191,0,0.3)',
            paddingLeft: 16,
          }}>
            {data.race_memo}
          </p>
        </div>
      )}

      {/* I. Alerts */}
      {(hasSpikes || hasRespond) && (
        <>
          <SectionDivider number="I." title="Alerts" count={(data.spike_alerts?.length ?? 0) + data.needs_response.length} color="#d71913" />

          {hasSpikes && (
            <div style={{ marginBottom: 20 }}>
              <div style={{ ...mono, fontSize: 9, fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase', color: '#fbbf24', marginBottom: 8 }}>
                Coverage Spikes
              </div>
              {data.spike_alerts.map(s => <SpikeRow key={s.frame_id} spike={s} />)}
            </div>
          )}

          {hasRespond && (
            <div>
              <div style={{ ...mono, fontSize: 9, fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase', color: '#d71913', marginBottom: 12 }}>
                Needs a Response
              </div>
              {data.needs_response.map(a => <ArticleRow key={a.id} article={a} urgent />)}
            </div>
          )}
        </>
      )}

      {/* II. Overnight Developments */}
      <SectionDivider number="II." title="Overnight Developments" count={data.new_articles.length} />
      {hasArticles ? (
        data.new_articles.map(a => <ArticleRow key={a.id} article={a} />)
      ) : (
        <div style={{ fontSize: 12, color: 'var(--text-muted, #64748b)', padding: '12px 0' }}>
          No new relevant articles in the last 48 hours.
        </div>
      )}

      {/* III. Narrative Monitor */}
      <SectionDivider number="III." title="Narrative Monitor" />
      <NarrativeTable items={data.narrative_pulse} />

      {/* Footer */}
      <div style={{ marginTop: 40, paddingTop: 16, borderTop: '1px solid var(--border, #1e3050)', display: 'flex', justifyContent: 'space-between' }}>
        <span style={{ ...mono, fontSize: 10, color: 'var(--text-muted, #475569)' }}>
          For internal campaign use only
        </span>
        <Link to="/review" style={{ ...mono, fontSize: 10, color: '#ffbf00', textDecoration: 'none' }}>
          View full review queue →
        </Link>
      </div>

    </div>
  )
}
