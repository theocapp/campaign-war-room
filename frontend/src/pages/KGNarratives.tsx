import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { KGNarrativeSummary } from '../api/types'

function velocityColor(v: number) {
  if (v >= 2.0) return 'var(--opponent)'
  if (v >= 0.8) return 'var(--warning)'
  return 'var(--text-muted)'
}

function formatDate(iso: string | null) {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
}

export default function KGNarratives() {
  const [narratives, setNarratives] = useState<KGNarrativeSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.getKGNarratives(50)
      .then(setNarratives)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  if (loading) {
    return (
      <div style={{ padding: '2rem', color: 'var(--text-muted)', fontSize: '0.9rem' }}>
        Loading emerging narratives…
      </div>
    )
  }

  if (error) {
    return (
      <div style={{ padding: '2rem', color: 'var(--opponent)', fontSize: '0.9rem' }}>
        Error: {error}
      </div>
    )
  }

  return (
    <div style={{ padding: '1.5rem 2rem', maxWidth: '900px' }}>
      <div style={{ marginBottom: '1.5rem' }}>
        <h1 style={{ fontSize: '1.4rem', fontWeight: 700, margin: 0 }}>
          Emerging Narratives
        </h1>
        <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem', marginTop: '0.3rem' }}>
          Active KG clusters ranked by credibility-weighted velocity
        </p>
      </div>

      {narratives.length === 0 ? (
        <div style={{
          padding: '2rem',
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          borderRadius: '8px',
          color: 'var(--text-muted)',
          fontSize: '0.9rem',
          textAlign: 'center',
        }}>
          No active narratives yet. Ingest sources to begin KG clustering.
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
          {narratives.map(n => (
            <Link
              key={n.id}
              to={`/kg/narratives/${n.id}`}
              style={{ textDecoration: 'none', color: 'inherit' }}
            >
              <div style={{
                background: 'var(--surface)',
                border: '1px solid var(--border)',
                borderLeft: `4px solid ${velocityColor(n.velocity_score)}`,
                borderRadius: '8px',
                padding: '1rem 1.25rem',
                display: 'flex',
                alignItems: 'flex-start',
                gap: '1.25rem',
                cursor: 'pointer',
                transition: 'border-color 0.15s',
              }}>
                {/* Velocity score */}
                <div style={{ minWidth: '60px', textAlign: 'center' }}>
                  <div style={{
                    fontSize: '1.6rem',
                    fontWeight: 700,
                    color: velocityColor(n.velocity_score),
                    lineHeight: 1,
                  }}>
                    {n.velocity_score.toFixed(2)}
                  </div>
                  <div style={{ fontSize: '0.65rem', color: 'var(--text-muted)', marginTop: '0.2rem' }}>
                    velocity
                  </div>
                </div>

                {/* Content */}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontWeight: 600, fontSize: '0.95rem', marginBottom: '0.25rem' }}>
                    {n.label}
                  </div>
                  {n.description && (
                    <div style={{
                      color: 'var(--text-muted)',
                      fontSize: '0.82rem',
                      marginBottom: '0.5rem',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                    }}>
                      {n.description}
                    </div>
                  )}
                  <div style={{ display: 'flex', gap: '1rem', fontSize: '0.78rem', color: 'var(--text-muted)' }}>
                    <span>{n.claim_count} claim{n.claim_count !== 1 ? 's' : ''}</span>
                    <span>{n.source_count} source{n.source_count !== 1 ? 's' : ''}</span>
                    <span>first {formatDate(n.first_seen_at)}</span>
                    <span>last {formatDate(n.last_seen_at)}</span>
                  </div>
                </div>

                {/* Status badge */}
                <div style={{
                  fontSize: '0.72rem',
                  padding: '0.2rem 0.5rem',
                  borderRadius: '4px',
                  background: n.status === 'active' ? 'var(--candidate-bg, #e6f4ea)' : 'var(--surface-alt, #f5f5f5)',
                  color: n.status === 'active' ? 'var(--candidate, #1a7340)' : 'var(--text-muted)',
                  fontWeight: 600,
                  whiteSpace: 'nowrap',
                }}>
                  {n.status}
                </div>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}
