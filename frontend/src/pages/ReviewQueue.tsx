import { useEffect, useState, useCallback, useMemo } from 'react'
import { api } from '../api/client'
import type { ReviewQueueItem } from '../api/types'
import FilterChips from '../components/FilterChips'

function timeAgo(iso: string | null): string {
  if (!iso) return ''
  const diff = Date.now() - new Date(iso).getTime()
  const h = Math.floor(diff / 3600000)
  if (h < 1) return 'Just now'
  if (h === 1) return '1h ago'
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

const FRAMING_LABEL: Record<string, { label: string; color: string }> = {
  respond:  { label: 'Needs response', color: '#ef4444' },
  review:   { label: 'Worth reviewing', color: '#f97316' },
  monitor:  { label: 'Monitor',        color: '#6b6b6b' },
  ignore:   { label: 'Low priority',   color: '#3f3f3f' },
}

function ScoreBadge({ score }: { score: number | null }) {
  if (score == null) return null
  const color = score >= 60 ? '#22c55e' : score >= 35 ? '#f97316' : '#6b6b6b'
  return (
    <span style={{
      fontSize: 11, fontWeight: 700, color, border: `1px solid ${color}`,
      borderRadius: 4, padding: '1px 6px', fontFamily: 'monospace',
    }}>
      {score}
    </span>
  )
}

export default function ReviewQueue() {
  const [items, setItems] = useState<ReviewQueueItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [acting, setActing] = useState<number | null>(null)
  const [filterAction, setFilterAction] = useState('all')
  const [filterScore, setFilterScore] = useState('60')

  const load = useCallback(() => {
    setLoading(true)
    api.getReviewQueue()
      .then(setItems)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  const filtered = useMemo(() => {
    return items.filter(item => {
      if (filterAction !== 'all' && item.actionability_label !== filterAction) return false
      if (filterScore === '40' && (item.race_relevance_score ?? 0) < 40) return false
      if (filterScore === '60' && (item.race_relevance_score ?? 0) < 60) return false
      if (filterScore === '80' && (item.race_relevance_score ?? 0) < 80) return false
      return true
    })
  }, [items, filterAction, filterScore])

  async function markRelevant(id: number) {
    setActing(id)
    try {
      await api.markRelevant(id)
      setItems(prev => prev.filter(i => i.id !== id))
    } finally { setActing(null) }
  }

  async function markIrrelevant(id: number) {
    setActing(id)
    try {
      await api.markIrrelevant(id)
      setItems(prev => prev.filter(i => i.id !== id))
    } finally { setActing(null) }
  }

  if (loading) return <div style={{ padding: 32, color: 'var(--text-muted)' }}>Loading…</div>
  if (error)   return <div style={{ padding: 32, color: '#ef4444' }}>Error: {error}</div>

  return (
    <div className="page">

      {/* Header */}
      <div style={{ marginBottom: 16 }}>
        <h1 style={{ margin: '0 0 6px', fontSize: 22, fontWeight: 700, color: 'var(--text, #f1f5f9)' }}>
          Review Queue
        </h1>
        <p style={{ margin: 0, fontSize: 13, color: 'var(--text-muted)', lineHeight: 1.5 }}>
          These are articles the AI scored in the last 48 hours. Confirm or correct its calls —
          your feedback helps you spot when the AI is getting things wrong.
          Articles you mark will be removed from this list.
        </p>
      </div>

      {/* Filters */}
      <div style={{ display: 'flex', gap: 16, marginBottom: 20, flexWrap: 'wrap' }}>
        <FilterChips
          label="Action"
          value={filterAction}
          onChange={setFilterAction}
          options={[
            { label: 'All', value: 'all' },
            { label: 'Respond', value: 'respond' },
            { label: 'Review', value: 'review' },
            { label: 'Monitor', value: 'monitor' },
          ]}
        />
        <FilterChips
          label="Min score"
          value={filterScore}
          onChange={setFilterScore}
          options={[
            { label: 'Any', value: 'all' },
            { label: '40+', value: '40' },
            { label: '60+', value: '60' },
            { label: '80+', value: '80' },
          ]}
        />
        {filtered.length !== items.length && (
          <span style={{ fontSize: 12, color: 'var(--text-muted)', alignSelf: 'center' }}>
            Showing {filtered.length} of {items.length}
          </span>
        )}
      </div>

      {filtered.length === 0 && items.length > 0 && (
        <div style={{ textAlign: 'center', padding: '32px 0', color: 'var(--text-muted)', fontSize: 13 }}>
          No items match these filters.
        </div>
      )}

      {items.length === 0 && (
        <div style={{ textAlign: 'center', padding: '48px 0', color: 'var(--text-muted)' }}>
          <div style={{ fontSize: 32, marginBottom: 12 }}>✓</div>
          <div style={{ fontWeight: 600, color: 'var(--text, #f1f5f9)', marginBottom: 4 }}>All caught up</div>
          <div style={{ fontSize: 13 }}>No new articles to review in the last 48 hours.</div>
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {filtered.map(item => {
          const framing = FRAMING_LABEL[item.actionability_label] ?? FRAMING_LABEL.monitor
          const isRelevant = !item.archived_as_irrelevant
          const busy = acting === item.id

          return (
            <div key={item.id} style={{
              background: 'var(--surface-1)',
              border: '1px solid var(--border)',
              borderLeft: `4px solid ${isRelevant ? framing.color : '#3f3f3f'}`,
              borderRadius: 8,
              padding: '14px 16px',
            }}>
              {/* Top row: AI verdict + score + source + time */}
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6, flexWrap: 'wrap' }}>
                <span style={{
                  fontSize: 10, fontWeight: 700, textTransform: 'uppercase',
                  letterSpacing: '0.08em', color: isRelevant ? framing.color : '#6b6b6b',
                  background: isRelevant ? `${framing.color}18` : 'var(--surface-2)',
                  border: `1px solid ${isRelevant ? framing.color : '#3f3f3f'}`,
                  borderRadius: 4, padding: '1px 6px',
                }}>
                  AI: {isRelevant ? framing.label : 'Irrelevant'}
                </span>
                <ScoreBadge score={item.race_relevance_score} />
                <span style={{ fontSize: 11, color: 'var(--text-muted)', marginLeft: 'auto' }}>
                  {item.source_name} · {timeAgo(item.created_at)}
                </span>
              </div>

              {/* AI evidence — the reason the AI scored it this way */}
              {item.relevance_reasons?.length > 0 && (
                <div style={{
                  fontSize: 13, fontStyle: 'italic',
                  color: 'var(--text-secondary)',
                  borderLeft: '2px solid #3f3f3f',
                  paddingLeft: 10,
                  lineHeight: 1.55,
                  marginBottom: 8,
                }}>
                  "{Array.isArray(item.relevance_reasons)
                    ? item.relevance_reasons.join(' ')
                    : item.relevance_reasons}"
                </div>
              )}

              {/* Title — secondary, just for navigation */}
              <div style={{ fontSize: 12, color: 'var(--text-muted)', lineHeight: 1.4, marginBottom: 8 }}>
                {item.source_url
                  ? <a href={item.source_url} target="_blank" rel="noopener noreferrer"
                      style={{ color: 'inherit', textDecoration: 'none' }}
                      onMouseEnter={e => (e.currentTarget.style.color = '#ffbf00')}
                      onMouseLeave={e => (e.currentTarget.style.color = 'inherit')}>
                      {item.title || '(no title)'} ↗
                    </a>
                  : (item.title || '(no title)')
                }
              </div>

              {/* Action buttons */}
              <div style={{ display: 'flex', gap: 8 }}>
                <button
                  onClick={() => markRelevant(item.id)}
                  disabled={busy}
                  style={{
                    padding: '5px 14px', borderRadius: 6, fontSize: 13, fontWeight: 600,
                    background: '#22c55e22', border: '1px solid #22c55e',
                    color: '#22c55e', cursor: busy ? 'not-allowed' : 'pointer',
                    opacity: busy ? 0.6 : 1,
                  }}
                >
                  {busy ? '…' : '✓ Relevant'}
                </button>
                <button
                  onClick={() => markIrrelevant(item.id)}
                  disabled={busy}
                  style={{
                    padding: '5px 14px', borderRadius: 6, fontSize: 13, fontWeight: 600,
                    background: '#ef444422', border: '1px solid #ef4444',
                    color: '#ef4444', cursor: busy ? 'not-allowed' : 'pointer',
                    opacity: busy ? 0.6 : 1,
                  }}
                >
                  {busy ? '…' : '✗ Not Relevant'}
                </button>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
