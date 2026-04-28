import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { Issue, IssueDetail } from '../api/types'
import UrgencyBadge from '../components/UrgencyBadge'
import TrendBadge from '../components/TrendBadge'
import SourceCard from '../components/SourceCard'

export default function IssueTracker() {
  const [issues, setIssues] = useState<Issue[]>([])
  const [selected, setSelected] = useState<IssueDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)

  useEffect(() => {
    api.getIssues().then(d => { setIssues(d); setLoading(false) })
  }, [])

  function selectIssue(id: number) {
    setDetailLoading(true)
    api.getIssue(id).then(d => { setSelected(d); setDetailLoading(false) })
  }

  if (loading) return <div style={{ padding: '2rem', color: 'var(--text-muted)' }}>Loading…</div>

  return (
    <div style={{ padding: '1.5rem', display: 'grid', gridTemplateColumns: '300px 1fr', gap: '1.5rem', maxWidth: 1200 }}>
      {/* Issue list */}
      <div>
        <div className="label" style={{ marginBottom: 4 }}>Issue Tracker</div>
        <h1 style={{ margin: '0 0 1rem', fontSize: '1.2rem', fontWeight: 700 }}>Active Issues</h1>
        {issues.map(issue => (
          <button
            key={issue.id}
            onClick={() => selectIssue(issue.id)}
            style={{
              width: '100%', textAlign: 'left', cursor: 'pointer',
              background: selected?.id === issue.id ? 'var(--surface-3)' : 'var(--surface-1)',
              border: `1px solid ${selected?.id === issue.id ? 'var(--accent)' : 'var(--border)'}`,
              borderRadius: 8, padding: '0.75rem', marginBottom: 8,
              transition: 'all 0.12s',
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
              <span style={{ fontWeight: 600, fontSize: '0.85rem', color: 'var(--text-primary)' }}>{issue.name}</span>
              <UrgencyBadge urgency={issue.urgency} size="sm" />
            </div>
            <div style={{ display: 'flex', gap: 10 }}>
              <TrendBadge trend={issue.trend} />
              <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>
                {issue.mention_count} mentions
              </span>
            </div>
          </button>
        ))}
      </div>

      {/* Issue detail */}
      <div>
        {detailLoading && <div style={{ color: 'var(--text-muted)', padding: '2rem' }}>Loading…</div>}
        {!detailLoading && !selected && (
          <div style={{ padding: '2rem', color: 'var(--text-muted)', textAlign: 'center' }}>
            Select an issue to see details
          </div>
        )}
        {!detailLoading && selected && (
          <>
            <div style={{ marginBottom: '1.5rem' }}>
              <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 8 }}>
                <h2 style={{ margin: 0, fontSize: '1.1rem', fontWeight: 700 }}>{selected.name}</h2>
                <UrgencyBadge urgency={selected.urgency} />
                <TrendBadge trend={selected.trend} />
              </div>
              <div style={{ display: 'flex', gap: 12, marginBottom: 12 }}>
                <span style={{ fontSize: '0.75rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>
                  {selected.mention_count} total mentions
                </span>
              </div>
              {selected.summary && (
                <div className="card" style={{ borderLeft: '3px solid var(--accent)' }}>
                  <p style={{ margin: 0, fontSize: '0.85rem', color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                    {selected.summary}
                  </p>
                </div>
              )}
            </div>

            <div>
              <div className="section-title">Related Intelligence ({selected.recent_sources.length})</div>
              {selected.recent_sources.length === 0 && (
                <div style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>No source items linked to this issue yet.</div>
              )}
              {selected.recent_sources.map(s => (
                <SourceCard key={s.id} source={s} />
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
