import { useEffect, useState, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { ReviewQueueItem } from '../api/types'
import UrgencyBadge from '../components/UrgencyBadge'

function fmtDate(s: string | null) {
  if (!s) return '—'
  return new Date(s).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

function SourceTypeLabel({ type }: { type: string }) {
  const labels: Record<string, { label: string; color: string }> = {
    news: { label: 'News', color: 'rgba(59,130,246,0.2)' },
    opponent_statement: { label: 'Opponent', color: 'rgba(239,68,68,0.15)' },
    public_record: { label: 'Public Record', color: 'rgba(167,139,250,0.2)' },
    canvassing: { label: 'Canvassing', color: 'rgba(34,197,94,0.15)' },
    campaign_note: { label: 'Campaign Note', color: 'rgba(251,191,36,0.15)' },
    social: { label: 'Social', color: 'rgba(236,72,153,0.15)' },
  }
  const { label, color } = labels[type] ?? { label: type, color: 'var(--surface-2)' }
  return (
    <span style={{
      padding: '2px 8px', borderRadius: 4, fontSize: '0.65rem',
      background: color, color: 'var(--text-secondary)', fontFamily: 'JetBrains Mono',
    }}>
      {label}
    </span>
  )
}

function scoreLabel(score: number): { label: string; color: string } {
  if (score >= 70) return { label: 'Strong', color: '#86efac' }
  if (score >= 40) return { label: 'Moderate', color: '#fbbf24' }
  return { label: 'Weak', color: '#f87171' }
}

function tpLink(item: ReviewQueueItem): string {
  const params = new URLSearchParams()
  if (item.related_issue_ids.length > 0) {
    params.set('issue_id', String(item.related_issue_ids[0]))
  } else {
    params.set('custom_issue_text', item.title)
  }
  params.set('source_id', String(item.id))
  return `/talking?${params.toString()}`
}

export default function ReviewQueue() {
  const [items, setItems] = useState<ReviewQueueItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [actionLoading, setActionLoading] = useState<number | null>(null)
  const [noteInputs, setNoteInputs] = useState<Record<number, string>>({})
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [bulkLoading, setBulkLoading] = useState(false)

  const load = useCallback(() => {
    setLoading(true)
    api.getReviewQueue()
      .then(data => { setItems(data); setSelected(new Set()) })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  function toggleSelect(id: number) {
    setSelected(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  function selectAll() {
    setSelected(prev => prev.size === items.length ? new Set() : new Set(items.map(i => i.id)))
  }

  async function markReviewed(id: number) {
    setActionLoading(id)
    try {
      await api.reviewSource(id, noteInputs[id])
      setItems(prev => prev.filter(i => i.id !== id))
      setSelected(prev => { const n = new Set(prev); n.delete(id); return n })
    } catch { /* silent */ } finally {
      setActionLoading(null)
    }
  }

  async function dismiss(id: number) {
    setActionLoading(id)
    try {
      await api.dismissSource(id, noteInputs[id])
      setItems(prev => prev.filter(i => i.id !== id))
      setSelected(prev => { const n = new Set(prev); n.delete(id); return n })
    } catch { /* silent */ } finally {
      setActionLoading(null)
    }
  }

  async function boost(id: number, current: number) {
    setActionLoading(id)
    try {
      const updated = await api.setSourcePriority(id, current + 20)
      setItems(prev => prev.map(i => i.id === id ? updated : i)
        .sort((a, b) => b.priority_score - a.priority_score))
    } catch { /* silent */ } finally {
      setActionLoading(null)
    }
  }

  async function bulkAction(action: 'review' | 'dismiss') {
    if (selected.size === 0) return
    setBulkLoading(true)
    try {
      const ids = Array.from(selected)
      if (action === 'review') {
        await api.bulkReviewSources(ids)
      } else {
        await api.bulkDismissSources(ids)
      }
      setItems(prev => prev.filter(i => !selected.has(i.id)))
      setSelected(new Set())
    } catch { /* silent */ } finally {
      setBulkLoading(false)
    }
  }

  if (loading) return <div style={{ padding: '2rem', color: 'var(--text-muted)' }}>Loading…</div>
  if (error) return <div style={{ padding: '2rem', color: '#f87171' }}>Error: {error}</div>

  return (
    <div style={{ padding: '1.5rem', maxWidth: 900 }}>
      <div className="label" style={{ marginBottom: 4 }}>Intelligence Workflow</div>
      <h1 style={{ margin: '0 0 0.25rem', fontSize: '1.2rem', fontWeight: 700 }}>Review Queue</h1>
      <p style={{ margin: '0 0 1.5rem', fontSize: '0.8rem', color: 'var(--text-muted)' }}>
        Triage new intelligence. Mark items reviewed, dismiss noise, or boost priority.
      </p>

      {items.length === 0 && (
        <div className="card" style={{ textAlign: 'center', padding: '2rem', color: 'var(--text-muted)' }}>
          <div style={{ fontSize: '1.5rem', marginBottom: 8 }}>✓</div>
          <div style={{ fontSize: '0.85rem' }}>Queue is empty — all sources have been reviewed.</div>
          <Link to="/sources" style={{ display: 'inline-block', marginTop: 12, fontSize: '0.75rem', color: 'var(--accent)' }}>
            Add new sources →
          </Link>
        </div>
      )}

      {/* Bulk action bar */}
      {items.length > 0 && (
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 12 }}>
          <button
            className="btn-ghost"
            style={{ fontSize: '0.72rem', padding: '0.3rem 0.75rem' }}
            onClick={selectAll}
          >
            {selected.size === items.length ? 'Deselect All' : `Select All (${items.length})`}
          </button>
          {selected.size > 0 && (
            <>
              <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>{selected.size} selected</span>
              <button
                className="btn-primary"
                style={{ fontSize: '0.72rem', padding: '0.3rem 0.75rem' }}
                onClick={() => bulkAction('review')}
                disabled={bulkLoading}
              >
                {bulkLoading ? '…' : 'Mark All Reviewed'}
              </button>
              <button
                style={{
                  fontSize: '0.72rem', padding: '0.3rem 0.75rem', borderRadius: 5,
                  background: 'transparent', border: '1px solid var(--border)',
                  color: 'var(--text-muted)', cursor: 'pointer',
                }}
                onClick={() => bulkAction('dismiss')}
                disabled={bulkLoading}
              >
                Dismiss All
              </button>
            </>
          )}
        </div>
      )}

      {items.map(item => {
        const evLabel = scoreLabel(item.evidence_score)
        const crLabel = scoreLabel(item.credibility_score)
        return (
          <div key={item.id} className="card" style={{
            marginBottom: 12, position: 'relative',
            border: selected.has(item.id) ? '1px solid rgba(59,130,246,0.5)' : '1px solid var(--border)',
            background: selected.has(item.id) ? 'rgba(59,130,246,0.04)' : undefined,
          }}>
            {/* Checkbox + priority score */}
            <div style={{ position: 'absolute', top: 12, left: 12 }}>
              <input
                type="checkbox"
                checked={selected.has(item.id)}
                onChange={() => toggleSelect(item.id)}
                style={{ cursor: 'pointer' }}
              />
            </div>
            {item.priority_score > 0 && (
              <div style={{
                position: 'absolute', top: 12, right: 12,
                fontSize: '0.6rem', fontFamily: 'JetBrains Mono',
                color: item.priority_score >= 50 ? '#f87171' : 'var(--text-muted)',
              }}>
                P{item.priority_score}
              </div>
            )}

            {/* Header row */}
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 6, flexWrap: 'wrap', paddingLeft: 28, paddingRight: 40 }}>
              <SourceTypeLabel type={item.source_type} />
              <UrgencyBadge urgency={item.urgency} size="sm" />
              <span style={{ fontSize: '0.6rem', color: evLabel.color, fontFamily: 'JetBrains Mono' }}>
                Ev:{evLabel.label}
              </span>
              <span style={{ fontSize: '0.6rem', color: crLabel.color, fontFamily: 'JetBrains Mono' }}>
                Cr:{crLabel.label}
              </span>
              {item.related_issue_names.map(n => (
                <span key={n} className="badge badge-ghost" style={{ fontSize: '0.6rem' }}>{n}</span>
              ))}
              {item.opponent_attack_count > 0 && (
                <span className="badge" style={{ fontSize: '0.6rem', background: 'rgba(239,68,68,0.15)', color: '#fca5a5' }}>
                  {item.opponent_attack_count} attack{item.opponent_attack_count > 1 ? 's' : ''}
                </span>
              )}
            </div>

            {/* Title */}
            <div style={{ fontWeight: 600, fontSize: '0.88rem', marginBottom: 4, paddingLeft: 28 }}>{item.title}</div>

            {/* Source meta */}
            <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', marginBottom: 8, paddingLeft: 28 }}>
              {item.source_name ?? '—'} · {fmtDate(item.published_at)}
              {item.source_url && (
                <> · <a href={item.source_url} target="_blank" rel="noopener noreferrer"
                  style={{ color: 'var(--accent)' }}>link ↗</a></>
              )}
            </div>

            {/* Summary */}
            {item.summary && (
              <p style={{ margin: '0 0 8px', fontSize: '0.8rem', color: 'var(--text-secondary)', lineHeight: 1.5, paddingLeft: 28 }}>
                {item.summary}
              </p>
            )}

            {/* Credibility note */}
            {item.credibility_note && (
              <div style={{
                padding: '6px 10px', borderRadius: 4, marginBottom: 10, marginLeft: 28,
                background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)',
                fontSize: '0.75rem', color: '#fca5a5', lineHeight: 1.4,
              }}>
                ⚠ {item.credibility_note}
              </div>
            )}

            {/* Note input + actions */}
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', paddingLeft: 28 }}>
              <input
                value={noteInputs[item.id] ?? ''}
                onChange={e => setNoteInputs(prev => ({ ...prev, [item.id]: e.target.value }))}
                placeholder="Optional note…"
                style={{ flex: 1, minWidth: 160, fontSize: '0.75rem', padding: '0.3rem 0.6rem' }}
              />
              <button
                className="btn-primary"
                style={{ fontSize: '0.72rem', padding: '0.3rem 0.75rem' }}
                disabled={actionLoading === item.id}
                onClick={() => markReviewed(item.id)}
              >
                Mark Reviewed
              </button>
              <Link
                to={tpLink(item)}
                className="btn-ghost"
                style={{ fontSize: '0.72rem', padding: '0.3rem 0.75rem', textDecoration: 'none' }}
              >
                Generate TP
              </Link>
              <button
                className="btn-ghost"
                style={{ fontSize: '0.72rem', padding: '0.3rem 0.75rem' }}
                disabled={actionLoading === item.id}
                onClick={() => boost(item.id, item.priority_score)}
              >
                ↑ Boost
              </button>
              <button
                style={{
                  fontSize: '0.72rem', padding: '0.3rem 0.75rem', borderRadius: 5,
                  background: 'transparent', border: '1px solid var(--border)',
                  color: 'var(--text-muted)', cursor: 'pointer',
                }}
                disabled={actionLoading === item.id}
                onClick={() => dismiss(item.id)}
              >
                Dismiss
              </button>
            </div>
          </div>
        )
      })}
    </div>
  )
}
