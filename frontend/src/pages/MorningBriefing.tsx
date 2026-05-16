import { useEffect, useState, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { MorningBriefing, BriefingArticle, NarrativePulseItem, SpikeAlert } from '../api/types'
import FilterChips from '../components/FilterChips'

const OWNER_COLOR: Record<string, string> = {
  candidate: '#22c55e',
  opponent: '#ef4444',
  media: '#64748b',
}

const TREND_ICON: Record<string, string> = { up: '↑', down: '↓', flat: '→' }
const TREND_COLOR: Record<string, string> = { up: '#f97316', down: '#64748b', flat: '#94a3b8' }

const ACTION_COLOR: Record<string, string> = {
  respond: '#ef4444',
  review:  '#f97316',
  monitor: '#64748b',
  ignore:  '#334155',
}

const ACTION_LABEL: Record<string, string> = {
  respond: 'Needs response',
  review:  'Worth reviewing',
  monitor: 'Monitor',
  ignore:  'Low priority',
}

function timeAgo(iso: string | null): string {
  if (!iso) return ''
  const diff = Date.now() - new Date(iso).getTime()
  const h = Math.floor(diff / 3600000)
  if (h < 1) return 'Just now'
  if (h === 1) return '1 hour ago'
  if (h < 24) return `${h} hours ago`
  return `${Math.floor(h / 24)}d ago`
}

function ArticleCard({ article, urgent }: { article: BriefingArticle; urgent?: boolean }) {
  const label = article.actionability_label || 'monitor'
  const color = urgent ? '#ef4444' : ACTION_COLOR[label] || '#64748b'

  return (
    <div style={{
      background: 'var(--surface, #1e293b)',
      border: `1px solid ${urgent ? '#ef4444' : 'var(--border, #334155)'}`,
      borderLeft: `4px solid ${color}`,
      borderRadius: 8,
      padding: '14px 16px',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12, marginBottom: 6 }}>
        <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text, #f1f5f9)', lineHeight: 1.4 }}>
          {article.source_url
            ? <a href={article.source_url} target="_blank" rel="noopener noreferrer" style={{ color: 'inherit', textDecoration: 'none' }}>{article.title || '(no title)'}</a>
            : (article.title || '(no title)')
          }
        </div>
        <span style={{
          fontSize: 11, fontWeight: 600, whiteSpace: 'nowrap',
          color: color, textTransform: 'uppercase', letterSpacing: '0.06em',
        }}>
          {urgent ? 'Respond' : ACTION_LABEL[label] || label}
        </span>
      </div>

      {article.summary && (
        <div style={{ fontSize: 13, color: 'var(--text-muted, #94a3b8)', lineHeight: 1.5, marginBottom: 6 }}>
          {article.summary.replace(/<[^>]+>/g, '').slice(0, 200)}
        </div>
      )}

      <div style={{ fontSize: 11, color: 'var(--text-muted, #94a3b8)', display: 'flex', gap: 12 }}>
        {article.source_name && <span>{article.source_name}</span>}
        {article.published_at && <span>{timeAgo(article.published_at)}</span>}
        {article.race_relevance_score != null && (
          <span>Relevance: {article.race_relevance_score}</span>
        )}
      </div>
    </div>
  )
}

function PulseRow({ item }: { item: NarrativePulseItem }) {
  const color = OWNER_COLOR[item.owner_type] || '#64748b'
  const trendColor = TREND_COLOR[item.trend]
  const trendIcon = TREND_ICON[item.trend]

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 12, padding: '10px 0',
      borderBottom: '1px solid var(--border, #334155)',
    }}>
      <div style={{ width: 10, height: 10, borderRadius: '50%', background: color, flexShrink: 0 }} />
      <div style={{ flex: 1, fontSize: 13, color: 'var(--text, #f1f5f9)', fontWeight: 500 }}>{item.name}</div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ fontSize: 18, fontWeight: 700, color: 'var(--text, #f1f5f9)' }}>{item.this_week}</span>
        <span style={{ fontSize: 13, color: trendColor, fontWeight: 600 }}>{trendIcon}</span>
        <span style={{ fontSize: 12, color: 'var(--text-muted, #94a3b8)', minWidth: 24 }}>{item.last_week} last wk</span>
      </div>
    </div>
  )
}

const OWNER_COLOR_SPIKE: Record<string, string> = { candidate: '#22c55e', opponent: '#ef4444', media: '#64748b' }

function SpikeCallout({ spikes }: { spikes: SpikeAlert[] }) {
  if (spikes.length === 0) return null
  return (
    <div style={{
      background: 'rgba(251, 191, 36, 0.08)',
      border: '1px solid #f59e0b',
      borderLeft: '4px solid #f59e0b',
      borderRadius: 8,
      padding: '14px 18px',
      marginBottom: 24,
    }}>
      <div style={{ fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: '#f59e0b', marginBottom: 10 }}>
        Trending — volume spike detected
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {spikes.map(s => (
          <div key={s.frame_id} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: OWNER_COLOR_SPIKE[s.owner_type] || '#64748b', flexShrink: 0 }} />
            <Link
              to={`/frames/${s.frame_id}`}
              style={{ fontSize: 13, fontWeight: 600, color: 'var(--text, #f1f5f9)', textDecoration: 'none', flex: 1 }}
            >
              {s.frame_name}
            </Link>
            <span style={{ fontSize: 12, color: '#f59e0b', fontWeight: 700, flexShrink: 0 }}>
              {s.count_24h} mentions in 24h
            </span>
            <span style={{ fontSize: 11, color: 'var(--text-muted, #94a3b8)', flexShrink: 0 }}>
              ({s.ratio}× avg)
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

function SectionHeader({ title, count, color }: { title: string; count?: number; color?: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
      <h2 style={{ margin: 0, fontSize: 13, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: color || 'var(--text-muted, #94a3b8)' }}>
        {title}
      </h2>
      {count != null && (
        <span style={{ fontSize: 11, background: color || '#334155', color: '#fff', borderRadius: 10, padding: '1px 7px', fontWeight: 700 }}>
          {count}
        </span>
      )}
    </div>
  )
}

export default function MorningBriefing() {
  const [data, setData] = useState<MorningBriefing | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [pulseFilter, setPulseFilter] = useState('all')

  useEffect(() => {
    api.getMorningBriefing()
      .then(setData)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))

    // Silently refresh every 5 minutes
    const t = setInterval(() => {
      api.getMorningBriefing().then(setData).catch(() => {})
    }, 5 * 60 * 1000)
    return () => clearInterval(t)
  }, [])

  if (loading) return (
    <div style={{ padding: 32, color: 'var(--text-muted, #94a3b8)' }}>Loading briefing…</div>
  )

  if (error) return (
    <div style={{ padding: 32, color: '#ef4444' }}>Error: {error}</div>
  )

  if (!data) return null

  const today = new Date().toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' })
  const hasRespond = data.needs_response.length > 0
  const hasArticles = data.new_articles.length > 0
  const hasPulse = data.narrative_pulse.length > 0

  return (
    <div className="page">

      {/* Header */}
      <div style={{ marginBottom: data.race_memo ? 20 : 28 }}>
        <div style={{ fontSize: 12, color: 'var(--text-muted, #94a3b8)', marginBottom: 4 }}>{today}</div>
        <h1 style={{ margin: '0 0 4px', fontSize: 24, fontWeight: 700, color: 'var(--text, #f1f5f9)' }}>
          Briefing
        </h1>
        <div style={{ fontSize: 13, color: 'var(--text-muted, #94a3b8)' }}>
          {data.meta.relevant_articles_today} relevant articles · {data.meta.total_articles_today} ingested today
        </div>
      </div>

      {/* Race situation memo */}
      {data.race_memo && (
        <div style={{
          background: 'linear-gradient(135deg, #1e293b 0%, #0f1f35 100%)',
          border: '1px solid #334155',
          borderLeft: '4px solid #6d28d9',
          borderRadius: 10,
          padding: '18px 20px',
          marginBottom: 28,
        }}>
          <div style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.1em', color: '#7c3aed', marginBottom: 10 }}>
            Race Situation
          </div>
          <p style={{ margin: 0, fontSize: 14, color: 'var(--text, #f1f5f9)', lineHeight: 1.7 }}>
            {data.race_memo}
          </p>
        </div>
      )}

      {/* Spike callout */}
      {data.spike_alerts?.length > 0 && <SpikeCallout spikes={data.spike_alerts} />}

      {/* Needs Response */}
      {hasRespond && (
        <div style={{ marginBottom: 32 }}>
          <SectionHeader title="Needs a Response" count={data.needs_response.length} color="#ef4444" />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {data.needs_response.map(a => <ArticleCard key={a.id} article={a} urgent />)}
          </div>
        </div>
      )}

      {/* New Articles */}
      <div style={{ marginBottom: 32 }}>
        <SectionHeader title="New Since Yesterday" count={hasArticles ? data.new_articles.length : undefined} />
        {hasArticles ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {data.new_articles.map(a => <ArticleCard key={a.id} article={a} />)}
          </div>
        ) : (
          <div style={{ color: 'var(--text-muted, #94a3b8)', fontSize: 13, padding: '16px 0' }}>
            No new relevant articles in the last 48 hours.
          </div>
        )}
      </div>

      {/* Narrative Pulse — full width row */}
      <div style={{ marginBottom: 32 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12, flexWrap: 'wrap', gap: 8 }}>
          <h2 style={{ margin: 0, fontSize: 13, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-muted, #94a3b8)' }}>
            Narrative Pulse
          </h2>
          {hasPulse && (
            <FilterChips
              value={pulseFilter}
              onChange={setPulseFilter}
              options={[
                { label: 'All', value: 'all' },
                { label: 'Our message', value: 'candidate' },
                { label: 'Opponent', value: 'opponent' },
                { label: 'Media', value: 'media' },
              ]}
            />
          )}
        </div>
        {hasPulse ? (
          <>
            <div style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))',
              gap: 10,
              marginBottom: 12,
            }}>
              {data.narrative_pulse.filter(item => pulseFilter === 'all' || item.owner_type === pulseFilter).slice(0, 6).map(item => {
                const dotColor = OWNER_COLOR[item.owner_type] || '#64748b'
                const trendColor = TREND_COLOR[item.trend]
                const trendIcon = TREND_ICON[item.trend]
                return (
                  <div key={item.id} style={{
                    background: 'var(--surface, #1e293b)',
                    border: '1px solid var(--border, #334155)',
                    borderRadius: 8,
                    padding: '12px 14px',
                    display: 'flex', flexDirection: 'column', gap: 6,
                  }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                      <span style={{ width: 8, height: 8, borderRadius: '50%', background: dotColor, flexShrink: 0 }} />
                      <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--text, #f1f5f9)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {item.name}
                      </span>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
                      <span style={{ fontSize: 22, fontWeight: 700, color: 'var(--text, #f1f5f9)' }}>{item.this_week}</span>
                      <span style={{ fontSize: 13, color: trendColor, fontWeight: 700 }}>{trendIcon}</span>
                      <span style={{ fontSize: 11, color: 'var(--text-muted, #94a3b8)' }}>{item.last_week} last wk</span>
                    </div>
                  </div>
                )
              })}
            </div>
            <div style={{ display: 'flex', gap: 16, fontSize: 11, color: 'var(--text-muted, #94a3b8)' }}>
              <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}><span style={{ width: 7, height: 7, borderRadius: '50%', background: '#22c55e', display: 'inline-block' }} /> Our message</span>
              <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}><span style={{ width: 7, height: 7, borderRadius: '50%', background: '#ef4444', display: 'inline-block' }} /> Opponent</span>
              <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}><span style={{ width: 7, height: 7, borderRadius: '50%', background: '#64748b', display: 'inline-block' }} /> Media</span>
            </div>
          </>
        ) : (
          <div style={{ color: 'var(--text-muted, #94a3b8)', fontSize: 13, padding: '16px 0' }}>
            Narrative frames will appear here after the first ingestion cycle.
          </div>
        )}
      </div>

      {!hasRespond && !hasArticles && !hasPulse && (
        <div style={{ textAlign: 'center', padding: '48px 0', color: 'var(--text-muted, #94a3b8)' }}>
          <div style={{ fontSize: 14 }}>No activity yet. The system checks for new articles every 30 minutes.</div>
        </div>
      )}

      <div style={{ marginTop: 8, fontSize: 11, color: 'var(--text-muted, #334155)', textAlign: 'right' }}>
        Generated {new Date(data.generated_at).toLocaleTimeString()}
      </div>
    </div>
  )
}
