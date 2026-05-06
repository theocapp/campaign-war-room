import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { api } from '../api/client'
import type { NarrativeDetail, SourceItem } from '../api/types'

function fmtDate(s: string | null) {
  if (!s) return '—'
  return new Date(s).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: '2-digit', hour: '2-digit', minute: '2-digit' })
}

function narrativeLabel(type: string, attribution: string, confidence: string) {
  if (type === 'opponent_attack' && confidence === 'high') return 'Opponent Attack'
  if (type === 'possible_attack') return 'Possible Attack Frame'
  if (attribution === 'media_frame' || type === 'media_frame') return 'Media Frame'
  if (attribution === 'unclear') return 'Unclear Attribution'
  return type.replace(/_/g, ' ')
}

function tractionColor(score: number) {
  if (score >= 65) return 'var(--opponent)'
  if (score >= 35) return 'var(--warning)'
  return 'var(--ok-light)'
}

export default function NarrativeDetail() {
  const { id } = useParams<{ id: string }>()
  const [narrative, setNarrative] = useState<NarrativeDetail | null>(null)
  const [loading, setLoading]     = useState(true)
  const [error, setError]         = useState<string | null>(null)

  useEffect(() => {
    if (!id) return
    api.getNarrativeDetail(parseInt(id))
      .then(setNarrative)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [id])

  if (loading) return <div className="loading-text">Loading narrative…</div>
  if (error || !narrative) return <div className="loading-text" style={{ color: 'var(--opponent)' }}>Error: {error}</div>

  const borderColor = narrative.owner_type === 'opponent' ? 'var(--opponent-border)' : 'var(--candidate-border)'

  // Group sources by cluster
  const byCluster = new Map<string, typeof narrative.mentions>()
  const unclustered: typeof narrative.mentions = []
  narrative.mentions.forEach(m => {
    if (m.source_cluster_id && m.source_item) {
      if (!byCluster.has(m.source_cluster_id)) byCluster.set(m.source_cluster_id, [])
      byCluster.get(m.source_cluster_id)!.push(m)
    } else if (m.source_item) {
      unclustered.push(m)
    }
  })

  return (
    <div className="page" style={{ maxWidth: 840 }}>
      {/* Back */}
      <Link to="/narratives" style={{ display: 'inline-flex', alignItems: 'center', gap: 4, color: 'var(--accent-light)', fontSize: '0.78rem', textDecoration: 'none', marginBottom: '1.25rem' }}>
        ← All Narratives
      </Link>

      {/* Title */}
      <div style={{ marginBottom: '1.5rem' }}>
        <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 6 }}>
          {narrativeLabel(narrative.narrative_type, narrative.attribution_type, narrative.owner_confidence)}
        </div>
        <h1 style={{ margin: 0, fontSize: '1.4rem', fontWeight: 700, letterSpacing: '-0.02em', lineHeight: 1.25 }}>
          {narrative.short_label}
        </h1>
      </div>

      {/* Overview card */}
      <div className="card" style={{ borderLeft: `3px solid ${borderColor}`, marginBottom: '1.25rem' }}>
        <p style={{ margin: '0 0 1rem', fontSize: '0.87rem', color: 'var(--text-secondary)', lineHeight: 1.65 }}>
          {narrative.canonical_text}
        </p>

        {/* Badges */}
        <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', marginBottom: '1rem' }}>
          <span className="badge badge-ghost" style={{ fontSize: '0.58rem' }}>{narrative.direction.replace(/_/g, ' ')}</span>
          <span className="badge badge-ghost" style={{ fontSize: '0.58rem' }}>{narrative.owner_confidence} confidence</span>
          <span className="badge badge-ghost" style={{ fontSize: '0.58rem' }}>{narrative.response_status}</span>
          {narrative.action && (
            <span className="badge badge-purple" style={{ fontSize: '0.58rem' }}>
              {narrative.risk_or_opportunity || narrative.action}
            </span>
          )}
        </div>

        {/* Stats grid */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(100px, 1fr))', gap: 12, marginBottom: '1rem' }}>
          {[
            { label: 'Traction',  value: `${narrative.traction_score}/100`, color: tractionColor(narrative.traction_score) },
            { label: 'Clusters',  value: narrative.source_cluster_count },
            { label: 'Sources',   value: narrative.source_count },
            { label: 'Messengers', value: narrative.messenger_diversity_count },
            { label: 'Geographies', value: narrative.geography_count },
          ].map(({ label, value, color }) => (
            <div key={label}>
              <div className="label" style={{ marginBottom: 3 }}>{label}</div>
              <div style={{ fontSize: '1.1rem', fontWeight: 700, color: color ?? 'var(--text-primary)', fontFamily: 'JetBrains Mono' }}>{value}</div>
            </div>
          ))}
        </div>

        {/* Signals */}
        {(narrative.why_it_matters || narrative.momentum_shift || narrative.recent_window_summary || narrative.what_changed || narrative.spread_summary) && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 5, borderTop: '1px solid var(--border)', paddingTop: '0.75rem' }}>
            {narrative.why_it_matters && (
              <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                <strong style={{ color: 'var(--text-primary)' }}>Why it matters:</strong> {narrative.why_it_matters}
              </div>
            )}
            {narrative.momentum_shift && (
              <div style={{ fontSize: '0.78rem', color: 'var(--text-secondary)' }}>
                <strong style={{ color: 'var(--text-primary)' }}>Momentum:</strong> {narrative.momentum_shift}
              </div>
            )}
            {narrative.recent_window_summary && (
              <div style={{ fontSize: '0.78rem', color: 'var(--text-secondary)' }}>{narrative.recent_window_summary}</div>
            )}
            {narrative.what_changed && (
              <div style={{ fontSize: '0.78rem', color: 'var(--text-secondary)' }}>
                <strong style={{ color: 'var(--text-primary)' }}>What changed:</strong> {narrative.what_changed}
              </div>
            )}
            {narrative.spread_summary && (
              <div style={{ fontSize: '0.78rem', color: 'var(--text-secondary)' }}>
                <strong style={{ color: 'var(--text-primary)' }}>Spread:</strong> {narrative.spread_summary}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Evidence */}
      <div style={{ marginBottom: '1.5rem' }}>
        <div className="section-title" style={{ marginBottom: '0.75rem' }}>
          Supporting Evidence
          <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)', fontWeight: 400, marginLeft: 8 }}>
            {narrative.source_count} source{narrative.source_count !== 1 ? 's' : ''}
          </span>
        </div>

        {narrative.mentions.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-icon">◻</div>
            <div className="empty-state-title">No sources linked</div>
            <div className="empty-state-body">Ingest sources and the system will link evidence here automatically.</div>
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {Array.from(byCluster.entries()).map(([clusterId, mentions], i) => (
              <div key={clusterId}>
                <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', marginBottom: 6, display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{ width: 20, height: 1, background: 'var(--border)', display: 'inline-block' }} />
                  Evidence Group {i + 1} · {mentions.length} source{mentions.length !== 1 ? 's' : ''}
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 5, paddingLeft: 12, borderLeft: '2px solid var(--border)' }}>
                  {mentions.map(m => <SourceMentionCard key={m.id} mention={m} />)}
                </div>
              </div>
            ))}

            {unclustered.length > 0 && (
              <div>
                {byCluster.size > 0 && (
                  <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', marginBottom: 6, display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ width: 20, height: 1, background: 'var(--border)', display: 'inline-block' }} />
                    Other Sources
                  </div>
                )}
                <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                  {unclustered.map(m => <SourceMentionCard key={m.id} mention={m} />)}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Footer timestamps */}
      <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', paddingTop: '1rem', borderTop: '1px solid var(--border)', display: 'flex', gap: 20, flexWrap: 'wrap' }}>
        <span>First seen: {fmtDate(narrative.first_seen_at)}</span>
        <span>Last seen: {fmtDate(narrative.last_seen_at)}</span>
        {narrative.notes && <span>Notes: {narrative.notes}</span>}
      </div>
    </div>
  )
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function SourceMentionCard({ mention }: { mention: any }) {
  const source: SourceItem = mention.source_item
  if (!source) return null

  const href = source.source_url || `/sources?source_id=${source.id}`
  const isExternal = !!source.source_url

  return (
    <a href={href} target={isExternal ? '_blank' : undefined} rel={isExternal ? 'noopener noreferrer' : undefined} style={{ textDecoration: 'none' }}>
      <div className="card card-hover" style={{ padding: '0.65rem 0.85rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, marginBottom: 5 }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontWeight: 600, fontSize: '0.82rem', marginBottom: 3, lineHeight: 1.3, color: 'var(--text-primary)' }}>
              {source.title || source.source_name || 'Untitled'}
            </div>
            <div style={{ display: 'flex', gap: 8, fontSize: '0.68rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>
              <span>{source.source_type.replace('_', ' ')}</span>
              {source.source_name && <span>{source.source_name}</span>}
              {source.geo_relevance && source.geo_relevance !== 'none' && (
                <span className="badge badge-ghost" style={{ fontSize: '0.56rem' }}>{source.geo_relevance}</span>
              )}
            </div>
          </div>
          <div style={{ textAlign: 'right', flexShrink: 0 }}>
            <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>{mention.mention_role}</div>
            <div style={{ fontSize: '0.7rem', fontWeight: 600, color: mention.confidence_score >= 70 ? 'var(--ok-light)' : 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>
              {mention.confidence_score}/100
            </div>
          </div>
        </div>
        {mention.matched_text && (
          <p style={{ margin: 0, fontSize: '0.74rem', color: 'var(--text-secondary)', fontStyle: 'italic', lineHeight: 1.45 }}>
            "{mention.matched_text.substring(0, 150)}{mention.matched_text.length > 150 ? '…' : ''}"
          </p>
        )}
      </div>
    </a>
  )
}
