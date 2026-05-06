import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { NarrativeComparisonOut, NarrativeComparisonItem } from '../api/types'

function fmtDate(s: string) {
  return new Date(s).toLocaleDateString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function tractionColor(score: number) {
  if (score >= 65) return 'var(--opponent)'
  if (score >= 40) return 'var(--warning-light)'
  return 'var(--text-muted)'
}

function NarrativeComparisonCard({ item, dim = false }: { item: NarrativeComparisonItem; dim?: boolean }) {
  const isOpponent = item.owner_type === 'opponent'
  return (
    <Link to={`/narratives/${item.narrative_id}`} style={{ textDecoration: 'none', display: 'block', marginBottom: 6 }}>
      <div className="card card-hover" style={{
        opacity: dim ? 0.65 : 1,
        borderLeft: `3px solid ${isOpponent ? 'var(--opponent-border)' : 'var(--candidate-border)'}`,
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, marginBottom: 6 }}>
          <div style={{ fontWeight: 650, fontSize: '0.86rem', lineHeight: 1.3, flex: 1, minWidth: 0, color: 'var(--text-primary)' }}>
            {item.short_label}
          </div>
          <span style={{ fontFamily: 'JetBrains Mono', fontSize: '0.72rem', fontWeight: 700, color: tractionColor(item.traction_score), whiteSpace: 'nowrap', flexShrink: 0 }}>
            {item.traction_score}/100
          </span>
        </div>
        <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', marginBottom: 6 }}>
          <span className="badge badge-ghost" style={{ fontSize: '0.56rem' }}>{item.status}</span>
          <span className="badge badge-ghost" style={{ fontSize: '0.56rem' }}>{item.evidence_strength} evidence</span>
          {item.outside_owned_channels && (
            <span className="badge badge-purple" style={{ fontSize: '0.56rem' }}>outside owned</span>
          )}
        </div>
        <div style={{ fontSize: '0.76rem', color: 'var(--text-secondary)', lineHeight: 1.45 }}>
          {item.practical_read}
        </div>
      </div>
    </Link>
  )
}

function SubSectionHeader({ label, color }: { label: string; color: string }) {
  return (
    <div style={{ fontSize: '0.64rem', fontWeight: 600, color, textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 8, fontFamily: 'JetBrains Mono' }}>
      {label}
    </div>
  )
}

export default function MessageBattle() {
  const [data, setData]     = useState<NarrativeComparisonOut | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError]   = useState<string | null>(null)

  useEffect(() => {
    api.getNarrativeComparison()
      .then(setData)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <div className="loading-text">Loading comparison…</div>
  if (error || !data) return <div className="loading-text" style={{ color: 'var(--opponent)' }}>Error: {error}</div>

  const needsResponseIds = new Set(data.needs_response.map(i => i.narrative_id))
  const monitorOnly = data.top_opponent_narratives.filter(i => !needsResponseIds.has(i.narrative_id))

  return (
    <div className="page-wide">
      {/* Header */}
      <div className="page-header">
        <div className="label" style={{ marginBottom: 5 }}>Intelligence</div>
        <h1 className="page-title">Message Battle</h1>
        <p className="page-subtitle" style={{ maxWidth: 680 }}>{data.summary}</p>
      </div>

      {/* Two-column battlefield */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '1.5rem' }}>
        {/* LEFT: Opponent */}
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: '0.75rem' }}>
            <span style={{ width: 10, height: 10, borderRadius: '50%', background: 'var(--opponent)', flexShrink: 0 }} />
            <div className="section-title" style={{ margin: 0 }}>Opponent Narratives</div>
          </div>

          {data.needs_response.length > 0 && (
            <div style={{ marginBottom: '1.25rem' }}>
              <SubSectionHeader label="Needs Response" color="var(--opponent-light)" />
              {data.needs_response.map(item => <NarrativeComparisonCard key={item.narrative_id} item={item} />)}
            </div>
          )}

          {monitorOnly.length > 0 && (
            <div>
              <SubSectionHeader label="Monitor" color="var(--text-muted)" />
              {monitorOnly.map(item => <NarrativeComparisonCard key={item.narrative_id} item={item} dim />)}
            </div>
          )}

          {data.top_opponent_narratives.length === 0 && (
            <div className="card" style={{ fontSize: '0.82rem', color: 'var(--text-secondary)' }}>
              No opponent narratives with traction evidence yet. Add opponent statements or ingest sources attributed to your opponent.
            </div>
          )}
        </div>

        {/* RIGHT: Candidate */}
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: '0.75rem' }}>
            <span style={{ width: 10, height: 10, borderRadius: '50%', background: 'var(--accent-light)', flexShrink: 0 }} />
            <div className="section-title" style={{ margin: 0 }}>Candidate Narratives</div>
          </div>

          {data.ready_to_amplify.length > 0 && (
            <div style={{ marginBottom: '1.25rem' }}>
              <SubSectionHeader label="Ready to Amplify" color="var(--ok-light)" />
              {data.ready_to_amplify.map(item => <NarrativeComparisonCard key={item.narrative_id} item={item} />)}
            </div>
          )}

          {data.candidate_owned_only.length > 0 && (
            <div>
              <SubSectionHeader label="Owned-Only Frames" color="var(--text-muted)" />
              {data.candidate_owned_only.map(item => <NarrativeComparisonCard key={item.narrative_id} item={item} dim />)}
            </div>
          )}

          {data.top_candidate_narratives.length === 0 && (
            <div className="card" style={{ fontSize: '0.82rem', color: 'var(--text-secondary)' }}>
              No candidate narratives with matched evidence yet. Add frames to the{' '}
              <Link to="/message-library" style={{ color: 'var(--accent-light)' }}>Message Library</Link>{' '}
              and ingest evidence sources.
            </div>
          )}
        </div>
      </div>

      {/* Footer */}
      <div style={{ marginTop: '1.5rem', paddingTop: '1rem', borderTop: '1px solid var(--border)', fontSize: '0.68rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', display: 'flex', justifyContent: 'space-between', gap: 16 }}>
        <span>Traction scores reflect evidence spread, not proof of voter persuasion.</span>
        <span>Generated {fmtDate(data.generated_at)}</span>
      </div>
    </div>
  )
}
