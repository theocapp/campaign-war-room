import { useEffect, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '../api/client'
import type { Issue, IssueDetail } from '../api/types'
import SourceCard from '../components/SourceCard'

function urgencyColor(u: string) {
  if (u === 'high') return 'var(--opponent)'
  if (u === 'medium') return 'var(--warning)'
  return 'var(--ok-light)'
}

function trendArrow(t: string) {
  if (t === 'rising') return { arrow: '↑', color: 'var(--opponent)' }
  if (t === 'falling') return { arrow: '↓', color: 'var(--ok-light)' }
  return { arrow: '→', color: 'var(--text-muted)' }
}

export default function IssueTracker() {
  const [searchParams] = useSearchParams()
  const [issues, setIssues] = useState<Issue[]>([])
  const [selected, setSelected] = useState<IssueDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)

  useEffect(() => {
    api.getIssues().then(d => { setIssues(d); setLoading(false) })
  }, [])

  useEffect(() => {
    if (loading) return
    const id = Number(searchParams.get('issue_id'))
    if (id > 0 && selected?.id !== id) selectIssue(id)
  }, [loading, searchParams, selected?.id])

  function selectIssue(id: number) {
    setDetailLoading(true)
    api.getIssue(id).then(d => { setSelected(d); setDetailLoading(false) })
  }

  if (loading) return <div className="loading-text">Loading issues…</div>

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '260px 1fr', minHeight: '100vh' }}>
      {/* Issue list */}
      <div style={{
        borderRight: '1px solid var(--border)',
        background: 'var(--surface-1)',
        padding: '1.5rem 0.75rem',
        overflowY: 'auto',
      }}>
        <div style={{ padding: '0 0.25rem', marginBottom: '1rem' }}>
          <div className="label" style={{ marginBottom: 4 }}>Issue Tracker</div>
          <h1 style={{ margin: 0, fontSize: '1.1rem', fontWeight: 700, letterSpacing: '-0.01em' }}>Active Issues</h1>
        </div>
        {issues.map(issue => {
          const trend = trendArrow(issue.trend)
          const isActive = selected?.id === issue.id
          return (
            <button
              key={issue.id}
              onClick={() => selectIssue(issue.id)}
              style={{
                width: '100%', textAlign: 'left', cursor: 'pointer',
                background: isActive ? 'var(--surface-3)' : 'transparent',
                border: `1px solid ${isActive ? 'var(--accent-border)' : 'transparent'}`,
                borderRadius: 'var(--radius-sm)',
                padding: '0.65rem 0.75rem',
                marginBottom: 2,
                transition: 'all 0.12s',
                fontFamily: 'inherit',
              }}
              onMouseEnter={e => { if (!isActive) (e.currentTarget as HTMLElement).style.background = 'var(--surface-2)' }}
              onMouseLeave={e => { if (!isActive) (e.currentTarget as HTMLElement).style.background = 'transparent' }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                <span style={{ fontWeight: 600, fontSize: '0.83rem', color: 'var(--text-primary)' }}>
                  {issue.name}
                </span>
                <span style={{
                  width: 8, height: 8, borderRadius: '50%',
                  background: urgencyColor(issue.urgency), flexShrink: 0,
                }} title={issue.urgency} />
              </div>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                <span style={{ fontSize: '0.68rem', color: trend.color, fontFamily: 'JetBrains Mono' }}>
                  {trend.arrow} {issue.trend}
                </span>
                <span style={{ fontSize: '0.66rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>
                  {issue.mention_count} mentions
                </span>
              </div>
            </button>
          )
        })}
      </div>

      {/* Detail panel */}
      <div style={{ padding: '2rem', overflowY: 'auto' }}>
        {detailLoading && <div className="loading-text" style={{ textAlign: 'left', padding: '1rem 0' }}>Loading…</div>}

        {!detailLoading && !selected && (
          <div className="empty-state" style={{ marginTop: '3rem' }}>
            <div className="empty-state-icon">◫</div>
            <div className="empty-state-title">Select an issue</div>
            <div className="empty-state-body">Click an issue from the list to see details, linked sources, and evidence.</div>
          </div>
        )}

        {!detailLoading && selected && (
          <>
            {/* Header */}
            <div style={{ marginBottom: '1.5rem' }}>
              <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 8, flexWrap: 'wrap' }}>
                <h2 style={{ margin: 0, fontSize: '1.3rem', fontWeight: 700, letterSpacing: '-0.02em' }}>
                  {selected.name}
                </h2>
                <span style={{
                  width: 9, height: 9, borderRadius: '50%',
                  background: urgencyColor(selected.urgency), flexShrink: 0,
                }} title={selected.urgency} />
                <span style={{ fontSize: '0.72rem', color: trendArrow(selected.trend).color, fontFamily: 'JetBrains Mono' }}>
                  {trendArrow(selected.trend).arrow} {selected.trend}
                </span>
                <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', marginLeft: 'auto' }}>
                  {selected.mention_count} mentions
                </span>
              </div>

              {/* Actions */}
              <Link
                to={`/talking?issue_id=${selected.id}`}
                className="btn btn-primary btn-sm"
                style={{ textDecoration: 'none', display: 'inline-flex' }}
              >
                Generate talking points →
              </Link>
            </div>

            {/* Summary card */}
            {selected.summary && (
              <div className="card" style={{ marginBottom: '1rem', borderLeft: '3px solid var(--accent-border)' }}>
                <p style={{ margin: 0, fontSize: '0.85rem', color: 'var(--text-secondary)', lineHeight: 1.65 }}>
                  {selected.summary}
                </p>
              </div>
            )}

            {/* Snapshot */}
            {selected.snapshot && (
              <div className="card" style={{ marginBottom: '1.5rem' }}>
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: '1rem' }}>
                  <span className="badge badge-ghost">{selected.snapshot.messaging_readiness} readiness</span>
                  <span className="badge badge-ghost">{selected.snapshot.evidence_strength} evidence</span>
                </div>
                <p style={{ margin: '0 0 0.75rem', fontSize: '0.87rem', color: 'var(--text-primary)', lineHeight: 1.6 }}>
                  {selected.snapshot.issue_snapshot}
                </p>
                <p style={{ margin: '0 0 1rem', fontSize: '0.8rem', color: 'var(--text-secondary)', lineHeight: 1.55 }}>
                  {selected.snapshot.why_it_matters_now}
                </p>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1rem' }}>
                  <div>
                    <div className="label" style={{ marginBottom: 4 }}>Top Geographies</div>
                    <div style={{ fontSize: '0.78rem', color: 'var(--text-secondary)' }}>
                      {selected.snapshot.top_geographies.length ? selected.snapshot.top_geographies.join(', ') : 'No clear concentration'}
                    </div>
                  </div>
                  <div>
                    <div className="label" style={{ marginBottom: 4 }}>Top Actors</div>
                    <div style={{ fontSize: '0.78rem', color: 'var(--text-secondary)' }}>
                      {selected.snapshot.top_actors.length ? selected.snapshot.top_actors.join(', ') : 'No dominant actor'}
                    </div>
                  </div>
                </div>
                {selected.snapshot.top_distinct_developments.length > 0 && (
                  <div style={{ marginTop: '1rem' }}>
                    <div className="label" style={{ marginBottom: 5 }}>Distinct Developments</div>
                    <ul style={{ margin: 0, paddingLeft: '1.1rem', color: 'var(--text-secondary)', fontSize: '0.78rem', lineHeight: 1.65 }}>
                      {selected.snapshot.top_distinct_developments.map(d => <li key={d}>{d}</li>)}
                    </ul>
                  </div>
                )}
              </div>
            )}

            {/* Sources */}
            <div className="section-title">
              Related Intelligence ({selected.recent_sources.length})
            </div>
            {selected.recent_sources.length === 0 ? (
              <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>No sources linked yet.</p>
            ) : selected.recent_sources.map(s => <SourceCard key={s.id} source={s} />)}
          </>
        )}
      </div>
    </div>
  )
}
