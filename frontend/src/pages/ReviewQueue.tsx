import { useEffect, useState, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { ReviewQueueItem } from '../api/types'

function fmtDate(s: string | null) {
  if (!s) return '—'
  return new Date(s).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

const TYPE_META: Record<string, { label: string; color: string }> = {
  news:               { label: 'News',          color: 'var(--accent-light)' },
  opponent_statement: { label: 'Opponent',       color: 'var(--opponent-light)' },
  public_record:      { label: 'Public Record',  color: '#a78bfa' },
  canvassing:         { label: 'Canvassing',     color: 'var(--ok-light)' },
  campaign_note:      { label: 'Campaign Note',  color: 'var(--warning-light)' },
  social:             { label: 'Social',         color: '#f0abfc' },
}

function urgencyDot(urgency: string) {
  const colors: Record<string, string> = {
    high: 'var(--opponent)', medium: 'var(--warning)', low: 'var(--ok-light)',
  }
  return colors[urgency] ?? 'var(--text-muted)'
}

function scoreLabel(n: number) {
  if (n >= 70) return { label: 'Strong', color: 'var(--ok-light)' }
  if (n >= 40) return { label: 'Mod',    color: 'var(--warning-light)' }
  return                { label: 'Weak',  color: 'var(--opponent)' }
}

function tpLink(item: ReviewQueueItem) {
  const p = new URLSearchParams()
  if (item.related_issue_ids.length > 0) p.set('issue_id', String(item.related_issue_ids[0]))
  else p.set('custom_issue_text', item.title)
  p.set('source_id', String(item.id))
  return `/talking?${p}`
}

export default function ReviewQueue() {
  const [items, setItems] = useState<ReviewQueueItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [acting, setActing] = useState<number | null>(null)
  const [notes, setNotes] = useState<Record<number, string>>({})
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const [bulkLoading, setBulkLoading] = useState(false)
  const [expandedNote, setExpandedNote] = useState<number | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    api.getReviewQueue()
      .then(d => { setItems(d); setSelected(new Set()) })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => { load() }, [load])

  function toggle(id: number) {
    setSelected(prev => { const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n })
  }

  function selectAll() {
    setSelected(prev => prev.size === items.length ? new Set() : new Set(items.map(i => i.id)))
  }

  async function markReviewed(id: number) {
    setActing(id)
    try {
      await api.reviewSource(id, notes[id])
      setItems(prev => prev.filter(i => i.id !== id))
      setSelected(prev => { const n = new Set(prev); n.delete(id); return n })
    } finally { setActing(null) }
  }

  async function dismiss(id: number) {
    setActing(id)
    try {
      await api.dismissSource(id, notes[id])
      setItems(prev => prev.filter(i => i.id !== id))
      setSelected(prev => { const n = new Set(prev); n.delete(id); return n })
    } finally { setActing(null) }
  }

  async function boost(id: number, current: number) {
    setActing(id)
    try {
      const updated = await api.setSourcePriority(id, current + 20)
      setItems(prev => [...prev.map(i => i.id === id ? updated : i)].sort((a, b) => b.priority_score - a.priority_score))
    } finally { setActing(null) }
  }

  async function bulkAction(action: 'review' | 'dismiss') {
    if (!selected.size) return
    setBulkLoading(true)
    try {
      const ids = Array.from(selected)
      if (action === 'review') await api.bulkReviewSources(ids)
      else await api.bulkDismissSources(ids)
      setItems(prev => prev.filter(i => !selected.has(i.id)))
      setSelected(new Set())
    } finally { setBulkLoading(false) }
  }

  if (loading) return <div className="loading-text">Loading queue…</div>
  if (error)   return <div className="loading-text" style={{ color: 'var(--opponent)' }}>Error: {error}</div>

  return (
    <div className="page" style={{ maxWidth: 860 }}>
      {/* Header */}
      <div className="page-header">
        <div className="label" style={{ marginBottom: 5 }}>Intelligence Workflow</div>
        <h1 className="page-title">Review Queue</h1>
        <p className="page-subtitle">Triage new intelligence. Mark reviewed, dismiss noise, or jump straight to talking points.</p>
      </div>

      {items.length === 0 && (
        <div className="empty-state" style={{ marginTop: '1rem' }}>
          <div className="empty-state-icon">✓</div>
          <div className="empty-state-title">Queue is clear</div>
          <div className="empty-state-body">All sources have been triaged.</div>
          <Link to="/sources" style={{ marginTop: 8, fontSize: '0.78rem', color: 'var(--accent-light)' }}>
            Add new sources →
          </Link>
        </div>
      )}

      {/* Bulk toolbar */}
      {items.length > 0 && (
        <div style={{
          display: 'flex', gap: 8, alignItems: 'center',
          marginBottom: '1rem',
          padding: '0.6rem 1rem',
          background: 'var(--surface-2)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius-sm)',
        }}>
          <input
            type="checkbox"
            checked={selected.size === items.length && items.length > 0}
            onChange={selectAll}
            style={{ width: 15, height: 15, cursor: 'pointer', accentColor: 'var(--accent)' }}
          />
          <span style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
            {selected.size > 0 ? `${selected.size} selected` : `${items.length} items`}
          </span>
          {selected.size > 0 && (
            <>
              <button className="btn btn-primary btn-sm" onClick={() => bulkAction('review')} disabled={bulkLoading}>
                {bulkLoading ? '…' : 'Mark all reviewed'}
              </button>
              <button className="btn btn-ghost btn-sm" onClick={() => bulkAction('dismiss')} disabled={bulkLoading}>
                Dismiss all
              </button>
            </>
          )}
        </div>
      )}

      {/* Items */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {items.map(item => {
          const typeMeta = TYPE_META[item.source_type] ?? { label: item.source_type, color: 'var(--text-muted)' }
          const ev = scoreLabel(item.evidence_score)
          const cr = scoreLabel(item.credibility_score)
          const isSelected = selected.has(item.id)
          const noteOpen = expandedNote === item.id

          return (
            <div key={item.id} style={{
              background: isSelected ? 'var(--accent-dim)' : 'var(--surface-1)',
              border: `1px solid ${isSelected ? 'var(--accent-border)' : 'var(--border)'}`,
              borderRadius: 'var(--radius)',
              overflow: 'hidden',
              transition: 'border-color 0.15s',
            } as React.CSSProperties}>
              {/* Top stripe: priority indicator */}
              {item.priority_score >= 50 && (
                <div style={{
                  height: 2,
                  background: item.priority_score >= 70 ? 'var(--opponent)' : 'var(--warning)',
                }} />
              )}

              <div style={{ padding: '0.9rem 1.1rem' }}>
                {/* Row 1: checkbox + type + urgency dot + badges */}
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 7, flexWrap: 'wrap' }}>
                  <input
                    type="checkbox"
                    checked={isSelected}
                    onChange={() => toggle(item.id)}
                    style={{ width: 15, height: 15, cursor: 'pointer', accentColor: 'var(--accent)', flexShrink: 0 }}
                  />
                  <span style={{ fontSize: '0.68rem', color: typeMeta.color, fontFamily: 'JetBrains Mono', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                    {typeMeta.label}
                  </span>
                  <span style={{
                    width: 7, height: 7, borderRadius: '50%',
                    background: urgencyDot(item.urgency),
                    display: 'inline-block', flexShrink: 0,
                  }} title={item.urgency} />
                  <span className="badge badge-ghost" style={{ fontSize: '0.6rem' }}>{item.race_relevance_label} {item.race_relevance_score}</span>
                  <span className="badge badge-ghost" style={{ fontSize: '0.6rem' }}>{item.actionability_label}</span>
                  {item.opponent_attack_count > 0 && (
                    <span className="badge badge-high" style={{ fontSize: '0.6rem' }}>
                      {item.opponent_attack_count} attack{item.opponent_attack_count > 1 ? 's' : ''}
                    </span>
                  )}
                  {item.related_issue_names.map(n => (
                    <span key={n} className="badge badge-ghost" style={{ fontSize: '0.6rem' }}>{n}</span>
                  ))}
                  {item.priority_score > 0 && (
                    <span style={{ marginLeft: 'auto', fontFamily: 'JetBrains Mono', fontSize: '0.62rem', color: item.priority_score >= 50 ? 'var(--opponent)' : 'var(--text-muted)' }}>
                      P{item.priority_score}
                    </span>
                  )}
                </div>

                {/* Row 2: title */}
                <div style={{ fontWeight: 600, fontSize: '0.9rem', lineHeight: 1.35, marginBottom: 4, color: 'var(--text-primary)' }}>
                  {item.title}
                </div>

                {/* Row 3: meta */}
                <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', marginBottom: 8 }}>
                  {item.source_name || '—'} · {fmtDate(item.published_at)}
                  {item.source_url && (
                    <> · <a href={item.source_url} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--accent-light)' }}>link ↗</a></>
                  )}
                  <span style={{ marginLeft: 8, color: ev.color }}> Ev:{ev.label}</span>
                  <span style={{ marginLeft: 6, color: cr.color }}>Cr:{cr.label}</span>
                </div>

                {/* Row 4: summary */}
                {item.summary && (
                  <p style={{ margin: '0 0 8px', fontSize: '0.81rem', color: 'var(--text-secondary)', lineHeight: 1.55 }}>
                    {item.summary}
                  </p>
                )}

                {/* Relevance reasons */}
                {item.relevance_reasons.length > 0 && (
                  <div style={{ fontSize: '0.73rem', color: 'var(--text-muted)', marginBottom: 8, lineHeight: 1.45 }}>
                    {item.relevance_reasons.slice(0, 2).join(' · ')}
                  </div>
                )}

                {/* Credibility note */}
                {item.credibility_note && (
                  <div className="risk-banner" style={{ marginBottom: 10 }}>
                    <span style={{ fontSize: '0.73rem', color: 'var(--opponent-light)', lineHeight: 1.4 }}>
                      ⚠ {item.credibility_note}
                    </span>
                  </div>
                )}

                {/* Actions */}
                <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap', marginTop: 10 }}>
                  <button
                    className="btn btn-primary btn-sm"
                    onClick={() => markReviewed(item.id)}
                    disabled={acting === item.id}
                  >
                    {acting === item.id ? '…' : 'Mark reviewed'}
                  </button>
                  <Link
                    to={tpLink(item)}
                    className="btn btn-ghost btn-sm"
                    style={{ textDecoration: 'none', display: 'inline-flex', alignItems: 'center' }}
                  >
                    Generate TP
                  </Link>
                  <button
                    className="btn btn-ghost btn-sm"
                    onClick={() => boost(item.id, item.priority_score)}
                    disabled={acting === item.id}
                  >
                    ↑ Boost
                  </button>
                  <button
                    className="btn btn-ghost btn-sm"
                    onClick={() => setExpandedNote(noteOpen ? null : item.id)}
                  >
                    Note
                  </button>
                  <button
                    className="btn btn-danger btn-sm"
                    onClick={() => dismiss(item.id)}
                    disabled={acting === item.id}
                  >
                    Dismiss
                  </button>
                </div>

                {/* Note input (expandable) */}
                {noteOpen && (
                  <div style={{ marginTop: 10 }}>
                    <input
                      value={notes[item.id] ?? ''}
                      onChange={e => setNotes(prev => ({ ...prev, [item.id]: e.target.value }))}
                      placeholder="Add a note…"
                      style={{ fontSize: '0.78rem' }}
                      autoFocus
                    />
                  </div>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
