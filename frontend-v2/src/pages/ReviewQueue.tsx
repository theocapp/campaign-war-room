import { CheckCircle, CheckSquare, Square, Star, Trash2, XCircle } from 'lucide-react'
import { useEffect, useState } from 'react'
import type { CSSProperties } from 'react'
import { api } from '@/api/client'
import type { ReviewQueueItem } from '@/api/types'

const C = {
  bg1: '#121212', bg2: '#171717', bg3: '#262626',
  border: '#434343', borderBright: '#555',
  text1: '#fff', text2: '#a1a1a1', text3: '#666',
  candidate: '#0059c2', opponent: '#d71913',
  accent: '#ffbf00',
  green: '#22c55e', red: '#ef4444',
}

const REL_COLORS: Record<string, { color: string; bg: string; border: string }> = {
  critical: { color: '#f87171', bg: 'rgba(215,25,19,0.08)', border: 'rgba(215,25,19,0.25)' },
  high: { color: '#fb923c', bg: 'rgba(234,88,12,0.08)', border: 'rgba(234,88,12,0.25)' },
  medium: { color: '#fbbf24', bg: 'rgba(202,138,4,0.08)', border: 'rgba(202,138,4,0.25)' },
  low: { color: '#a1a1a1', bg: 'rgba(161,161,161,0.08)', border: 'rgba(161,161,161,0.2)' },
  irrelevant: { color: '#555', bg: 'rgba(85,85,85,0.08)', border: 'rgba(85,85,85,0.2)' },
}

function formatDate(iso?: string) {
  if (!iso) return ''
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

function RelBadge({ label }: { label?: string }) {
  if (!label) return null
  const style = REL_COLORS[label] ?? REL_COLORS.low
  return (
    <span style={{
      fontSize: 10, color: style.color, background: style.bg,
      border: `1px solid ${style.border}`, padding: '2px 7px',
      borderRadius: 4, letterSpacing: '0.07em', flexShrink: 0, fontWeight: 600,
    }}>
      {label.toUpperCase()}
    </span>
  )
}

function SentimentDot({ s }: { s?: string }) {
  const colors: Record<string, string> = {
    positive: '#4ade80', negative: '#f87171', neutral: '#a1a1a1', mixed: '#fbbf24',
  }
  if (!s) return null
  return (
    <span style={{ display: 'inline-block', width: 6, height: 6, borderRadius: '50%', background: colors[s] ?? '#a1a1a1' }} />
  )
}

export function ReviewQueue() {
  const [items, setItems] = useState<ReviewQueueItem[]>([])
  const [loading, setLoading] = useState(true)
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [processing, setProcessing] = useState<Set<number>>(new Set())
  const [done, setDone] = useState<Set<number>>(new Set())

  useEffect(() => {
    api.reviewQueue().then(setItems).catch(() => {}).finally(() => setLoading(false))
  }, [])

  const visibleItems = items.filter(i => !done.has(i.id))

  function toggleSelect(id: number) {
    setSelected(s => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n })
  }

  function toggleAll() {
    if (selected.size === visibleItems.length) setSelected(new Set())
    else setSelected(new Set(visibleItems.map(i => i.id)))
  }

  async function doAction(id: number, action: () => Promise<unknown>) {
    setProcessing(p => new Set([...p, id]))
    try {
      await action()
      setDone(d => new Set([...d, id]))
      setSelected(s => { const n = new Set(s); n.delete(id); return n })
    } catch { /* silently fail */ } finally {
      setProcessing(p => { const n = new Set(p); n.delete(id); return n })
    }
  }

  async function bulkAction(action: (ids: number[]) => Promise<unknown>) {
    const ids = Array.from(selected)
    if (!ids.length) return
    ids.forEach(id => setProcessing(p => new Set([...p, id])))
    try {
      await action(ids)
      setDone(d => new Set([...d, ...ids]))
      setSelected(new Set())
    } catch { /* silently fail */ } finally {
      ids.forEach(id => setProcessing(p => { const n = new Set(p); n.delete(id); return n }))
    }
  }

  return (
    <div style={{ minHeight: '100%', background: C.bg1 }}>
      {/* Header */}
      <div style={{
        padding: '16px 28px', borderBottom: `1px solid ${C.border}`,
        background: 'rgba(18,18,18,0.95)', backdropFilter: 'blur(8px)',
        position: 'sticky', top: 0, zIndex: 10,
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      }}>
        <div>
          <div style={{ fontSize: 22, fontWeight: 800, color: C.text1, letterSpacing: '-0.01em' }}>
            Review Queue
          </div>
          <div className="section-label" style={{ marginTop: 2 }}>
            {loading ? '...' : `${visibleItems.length} items pending triage`}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          {visibleItems.length > 0 && (
            <button onClick={toggleAll} className="btn btn-ghost">
              {selected.size === visibleItems.length ? <CheckSquare size={13} /> : <Square size={13} />}
              {selected.size === visibleItems.length ? 'Deselect All' : 'Select All'}
            </button>
          )}
        </div>
      </div>

      {/* Bulk action bar */}
      {selected.size > 0 && (
        <div style={{
          padding: '10px 28px', background: C.bg3, borderBottom: `1px solid ${C.border}`,
          display: 'flex', alignItems: 'center', gap: 10,
        }}>
          <span style={{ fontSize: 13, color: C.text2, fontWeight: 600 }}>
            {selected.size} selected
          </span>
          <div style={{ flex: 1, display: 'flex', gap: 8 }}>
            <button onClick={() => bulkAction(ids => api.bulkReview(ids))} className="btn btn-success">
              <CheckCircle size={13} />
              Bulk Review
            </button>
            <button onClick={() => bulkAction(ids => api.bulkDismiss(ids))} className="btn btn-danger">
              <Trash2 size={13} />
              Bulk Dismiss
            </button>
          </div>
        </div>
      )}

      {/* Content */}
      <div style={{ padding: '20px 28px', maxWidth: 860, margin: '0 auto' }}>
        {loading && Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="skeleton" style={{ height: 110, marginBottom: 10 }} />
        ))}

        {!loading && visibleItems.length === 0 && (
          <div style={{ textAlign: 'center', padding: '80px 20px', color: C.text3 }}>
            <CheckCircle size={52} style={{ margin: '0 auto 20px', color: C.green, opacity: 0.4 }} />
            <div style={{ fontSize: 24, fontWeight: 700, color: C.text2, marginBottom: 8 }}>
              Queue Clear
            </div>
            <div style={{ fontSize: 13 }}>All items have been reviewed or dismissed.</div>
          </div>
        )}

        {visibleItems.map(item => {
          const rel = item.race_relevance_label
          const relStyle = REL_COLORS[rel ?? ''] ?? REL_COLORS.low
          const isCritical = rel === 'critical'
          const isProcessing = processing.has(item.id)
          const isSelected = selected.has(item.id)

          return (
            <div
              key={item.id}
              style={{
                marginBottom: 8,
                background: isSelected ? C.bg3 : C.bg2,
                border: `1px solid ${isSelected ? C.borderBright : isCritical ? 'rgba(215,25,19,0.35)' : C.border}`,
                borderLeft: `3px solid ${isCritical ? C.opponent : isSelected ? C.accent : C.border}`,
                borderRadius: '0.625rem', overflow: 'hidden',
                opacity: isProcessing ? 0.5 : 1,
                transition: 'all 0.15s ease',
              }}
            >
              <div style={{ padding: '14px 16px' }}>
                <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
                  {/* Checkbox */}
                  <button
                    onClick={() => toggleSelect(item.id)}
                    style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0, marginTop: 2, color: C.text3 }}
                  >
                    {isSelected
                      ? <CheckSquare size={16} style={{ color: C.accent }} />
                      : <Square size={16} />
                    }
                  </button>

                  {/* Content */}
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', gap: 7, marginBottom: 6, flexWrap: 'wrap', alignItems: 'center' }}>
                      <RelBadge label={item.race_relevance_label} />
                      {item.actionability_label && (
                        <span style={{
                          fontSize: 10, color: C.text2, border: `1px solid ${C.border}`,
                          padding: '2px 6px', borderRadius: 4, letterSpacing: '0.06em',
                        }}>
                          {item.actionability_label.toUpperCase()}
                        </span>
                      )}
                      {item.source_type && (
                        <span style={{ fontSize: 10, color: C.text3, letterSpacing: '0.06em' }}>
                          {item.source_type.toUpperCase()}
                        </span>
                      )}
                      <SentimentDot s={item.sentiment} />
                    </div>

                    <div style={{ fontSize: 14, fontWeight: 500, color: C.text1, lineHeight: 1.35, marginBottom: 6 }}>
                      {item.title}
                    </div>

                    {item.summary && (
                      <div style={{
                        fontSize: 13, color: C.text2, lineHeight: 1.5, marginBottom: 8,
                        overflow: 'hidden', display: '-webkit-box',
                        WebkitLineClamp: 2, WebkitBoxOrient: 'vertical',
                      } as CSSProperties}>
                        {item.summary}
                      </div>
                    )}

                    <div style={{ display: 'flex', alignItems: 'center', gap: 12, fontSize: 12, color: C.text3 }}>
                      {item.source_name && <span>{item.source_name}</span>}
                      {(item.published_at ?? item.created_at) && (
                        <span>{formatDate(item.published_at ?? item.created_at)}</span>
                      )}
                      {item.opponent_attack_count > 0 && (
                        <span style={{ color: '#f87171' }}>
                          {item.opponent_attack_count} opp. attack{item.opponent_attack_count > 1 ? 's' : ''}
                        </span>
                      )}
                      {item.source_url && (
                        <a href={item.source_url} target="_blank" rel="noopener noreferrer"
                          style={{ color: C.accent, textDecoration: 'none' }}
                          onClick={e => e.stopPropagation()}>
                          Source ↗
                        </a>
                      )}
                    </div>
                  </div>

                  {/* Action buttons */}
                  <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
                    <button
                      title="Mark relevant"
                      onClick={() => doAction(item.id, () => api.markRelevant(item.id))}
                      disabled={isProcessing}
                      style={{ background: 'none', border: `1px solid rgba(34,197,94,0.3)`, borderRadius: 6, padding: '5px 8px', cursor: 'pointer', color: C.green }}
                    >
                      <Star size={13} />
                    </button>
                    <button
                      title="Reviewed"
                      onClick={() => doAction(item.id, () => api.reviewItem(item.id))}
                      disabled={isProcessing}
                      style={{ background: 'none', border: `1px solid rgba(34,197,94,0.3)`, borderRadius: 6, padding: '5px 8px', cursor: 'pointer', color: C.green }}
                    >
                      <CheckCircle size={13} />
                    </button>
                    <button
                      title="Dismiss"
                      onClick={() => doAction(item.id, () => api.dismissItem(item.id))}
                      disabled={isProcessing}
                      style={{ background: 'none', border: `1px solid rgba(215,25,19,0.3)`, borderRadius: 6, padding: '5px 8px', cursor: 'pointer', color: '#f87171' }}
                    >
                      <XCircle size={13} />
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
