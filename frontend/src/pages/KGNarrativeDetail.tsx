import { useEffect, useState } from 'react'
import { useParams, Link } from 'react-router-dom'
import { api } from '../api/client'
import type { KGNarrativeDetail, KGClaim, KGSource } from '../api/types'

function formatDate(iso: string | null) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

function credibilityBar(score: number) {
  const pct = Math.round(score * 100)
  const color = score >= 0.7 ? 'var(--candidate, #1a7340)' : score >= 0.4 ? 'var(--warning, #b45309)' : 'var(--opponent, #b91c1c)'
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.4rem' }}>
      <span style={{
        display: 'inline-block', width: '60px', height: '6px',
        background: 'var(--border)', borderRadius: '3px', overflow: 'hidden',
      }}>
        <span style={{
          display: 'block', width: `${pct}%`, height: '100%',
          background: color, borderRadius: '3px',
        }} />
      </span>
      <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>{pct}%</span>
    </span>
  )
}

function stanceBadge(stance: string) {
  const map: Record<string, { bg: string; fg: string }> = {
    support:  { bg: '#e6f4ea', fg: '#1a7340' },
    oppose:   { bg: '#fee2e2', fg: '#b91c1c' },
    neutral:  { bg: '#f5f5f5', fg: '#555' },
    unknown:  { bg: '#f5f5f5', fg: '#999' },
  }
  const s = map[stance] ?? map.unknown
  return (
    <span style={{
      fontSize: '0.68rem', padding: '0.15rem 0.4rem', borderRadius: '3px',
      background: s.bg, color: s.fg, fontWeight: 600,
    }}>
      {stance}
    </span>
  )
}

function SourceBadge({ source }: { source: KGSource | null }) {
  if (!source) return <span style={{ color: 'var(--text-muted)', fontSize: '0.78rem' }}>unknown source</span>
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
      <a
        href={source.url}
        target="_blank"
        rel="noreferrer"
        style={{ color: 'var(--accent)', fontSize: '0.78rem', textDecoration: 'none' }}
      >
        {source.source_name || source.domain || 'source'}
      </a>
      {source.verified_official && (
        <span style={{
          fontSize: '0.65rem', padding: '0.1rem 0.35rem', borderRadius: '3px',
          background: '#dbeafe', color: '#1d4ed8', fontWeight: 600,
        }}>official</span>
      )}
      {credibilityBar(source.credibility_score)}
    </span>
  )
}

function ClaimCard({ claim }: { claim: KGClaim }) {
  return (
    <div style={{
      border: '1px solid var(--border)',
      borderRadius: '6px',
      padding: '0.75rem 1rem',
      background: 'var(--surface)',
      display: 'flex',
      flexDirection: 'column',
      gap: '0.4rem',
    }}>
      <div style={{ fontSize: '0.88rem', lineHeight: 1.45 }}>{claim.text}</div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap' }}>
        {stanceBadge(claim.stance)}
        <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
          conf {(claim.confidence * 100).toFixed(0)}%
        </span>
        <SourceBadge source={claim.source} />
      </div>
    </div>
  )
}

export default function KGNarrativeDetail() {
  const { id } = useParams<{ id: string }>()
  const [narrative, setNarrative] = useState<KGNarrativeDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!id) return
    api.getKGNarrativeDetail(parseInt(id))
      .then(setNarrative)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [id])

  if (loading) {
    return <div style={{ padding: '2rem', color: 'var(--text-muted)', fontSize: '0.9rem' }}>Loading…</div>
  }

  if (error) {
    return <div style={{ padding: '2rem', color: 'var(--opponent)', fontSize: '0.9rem' }}>Error: {error}</div>
  }

  if (!narrative) return null

  // Group claims by domain/source for credibility breakdown
  const domainMap = new Map<string, { name: string; credibility: number; count: number }>()
  for (const claim of narrative.claims) {
    if (!claim.source) continue
    const key = claim.source.domain || claim.source.url
    const existing = domainMap.get(key)
    if (existing) {
      existing.count++
    } else {
      domainMap.set(key, {
        name: claim.source.source_name || claim.source.domain || key,
        credibility: claim.source.credibility_score,
        count: 1,
      })
    }
  }
  const sourceSummary = Array.from(domainMap.entries())
    .map(([key, v]) => ({ key, ...v }))
    .sort((a, b) => b.count - a.count)

  return (
    <div style={{ padding: '1.5rem 2rem', maxWidth: '860px' }}>
      {/* Back */}
      <Link
        to="/kg/narratives"
        style={{ fontSize: '0.82rem', color: 'var(--text-muted)', textDecoration: 'none', display: 'block', marginBottom: '1rem' }}
      >
        ← Emerging Narratives
      </Link>

      {/* Header */}
      <div style={{ marginBottom: '1.5rem' }}>
        <h1 style={{ fontSize: '1.35rem', fontWeight: 700, margin: 0, marginBottom: '0.4rem' }}>
          {narrative.label}
        </h1>
        {narrative.description && (
          <p style={{ color: 'var(--text-muted)', fontSize: '0.88rem', margin: 0, marginBottom: '0.75rem' }}>
            {narrative.description}
          </p>
        )}

        {/* Meta row */}
        <div style={{ display: 'flex', gap: '1.5rem', flexWrap: 'wrap', fontSize: '0.82rem', color: 'var(--text-muted)' }}>
          <span>
            <strong style={{ color: 'var(--text)', fontSize: '1.1rem' }}>
              {narrative.velocity_score.toFixed(2)}
            </strong>{' '}velocity
          </span>
          <span>{narrative.claims.length} claims</span>
          <span>first seen {formatDate(narrative.first_seen_at)}</span>
          <span>last seen {formatDate(narrative.last_seen_at)}</span>
          <span style={{
            padding: '0.15rem 0.5rem', borderRadius: '4px',
            background: narrative.status === 'active' ? '#e6f4ea' : '#f5f5f5',
            color: narrative.status === 'active' ? '#1a7340' : '#888',
            fontWeight: 600, fontSize: '0.75rem',
          }}>
            {narrative.status}
          </span>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 280px', gap: '1.5rem', alignItems: 'start' }}>
        {/* Claims column */}
        <div>
          <h2 style={{ fontSize: '1rem', fontWeight: 600, marginBottom: '0.75rem' }}>
            Supporting Claims
          </h2>
          {narrative.claims.length === 0 ? (
            <div style={{ color: 'var(--text-muted)', fontSize: '0.88rem' }}>No claims linked yet.</div>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.6rem' }}>
              {narrative.claims.map(c => <ClaimCard key={c.id} claim={c} />)}
            </div>
          )}
        </div>

        {/* Sidebar */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
          {/* Source credibility breakdown */}
          <div style={{
            background: 'var(--surface)', border: '1px solid var(--border)',
            borderRadius: '8px', padding: '1rem',
          }}>
            <h3 style={{ fontSize: '0.85rem', fontWeight: 600, marginBottom: '0.75rem', margin: '0 0 0.75rem' }}>
              Source Breakdown
            </h3>
            {sourceSummary.length === 0 ? (
              <div style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>No sources.</div>
            ) : (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                {sourceSummary.slice(0, 8).map(s => (
                  <div key={s.key} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '0.5rem' }}>
                    <span style={{ fontSize: '0.78rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>
                      {s.name}
                    </span>
                    <span style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', flexShrink: 0 }}>
                      {credibilityBar(s.credibility)}
                      <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)', minWidth: '20px', textAlign: 'right' }}>
                        ×{s.count}
                      </span>
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Top entities */}
          {narrative.top_entities.length > 0 && (
            <div style={{
              background: 'var(--surface)', border: '1px solid var(--border)',
              borderRadius: '8px', padding: '1rem',
            }}>
              <h3 style={{ fontSize: '0.85rem', fontWeight: 600, margin: '0 0 0.75rem' }}>
                Key Entities
              </h3>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
                {narrative.top_entities.map(e => (
                  <div key={e.id} style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
                    <span style={{
                      fontSize: '0.65rem', padding: '0.1rem 0.35rem', borderRadius: '3px',
                      background: 'var(--surface-alt, #f0f0f0)', color: 'var(--text-muted)',
                      fontWeight: 600, flexShrink: 0,
                    }}>
                      {e.entity_type}
                    </span>
                    <span style={{ fontSize: '0.82rem', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {e.canonical_name || e.name}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
