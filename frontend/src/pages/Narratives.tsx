import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { DashboardNarrativeCard } from '../api/types'

type FilterKey = 'all' | string

const ALL = 'all'

function uniqueOptions(items: DashboardNarrativeCard[], key: keyof DashboardNarrativeCard) {
  return Array.from(new Set(items.map(item => String(item[key] || '')).filter(Boolean))).sort()
}

function changeSignals(n: DashboardNarrativeCard) {
  const lines: string[] = []
  if (n.momentum_shift && n.momentum_shift !== 'unchanged') lines.push(`Momentum: ${n.momentum_shift}`)
  if (n.recent_window_summary) lines.push(n.recent_window_summary)
  if (n.what_changed) lines.push(n.what_changed)
  if (n.spread_summary) lines.push(n.spread_summary)
  if (typeof n.new_source_clusters_count === 'number' && n.new_source_clusters_count > 0)
    lines.push(`${n.new_source_clusters_count} new cluster${n.new_source_clusters_count === 1 ? '' : 's'}`)
  if (n.escaped_owned_recently) lines.push('Now spreading outside owned channels')
  const messengers = (n.new_messenger_types || []).filter(Boolean).slice(0, 2)
  if (messengers.length > 0) lines.push(`New messenger${messengers.length > 1 ? 's' : ''}: ${messengers.join(', ')}`)
  return lines.slice(0, 3)
}

function ownerBorderColor(ownerType: string) {
  if (ownerType === 'opponent')  return 'var(--opponent-border)'
  if (ownerType === 'candidate') return 'var(--candidate-border)'
  return 'var(--border)'
}

function tractionColor(score: number) {
  if (score >= 65) return 'var(--opponent)'
  if (score >= 35) return 'var(--warning)'
  return 'var(--text-muted)'
}

const OWNER_TABS = ['all', 'opponent', 'candidate', 'media'] as const

export default function Narratives() {
  const [narratives, setNarratives]     = useState<DashboardNarrativeCard[]>([])
  const [loading, setLoading]           = useState(true)
  const [error, setError]               = useState<string | null>(null)
  const [ownerType, setOwnerType]       = useState<FilterKey>(ALL)
  const [direction, setDirection]       = useState<FilterKey>(ALL)
  const [status, setStatus]             = useState<FilterKey>(ALL)
  const [evidence, setEvidence]         = useState<FilterKey>(ALL)
  const [responseStatus, setResponseStatus] = useState<FilterKey>(ALL)

  useEffect(() => {
    api.getNarrativeBriefs(50)
      .then(setNarratives)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  const filtered = useMemo(() => narratives.filter(n => (
    (ownerType === ALL || n.owner_type === ownerType) &&
    (direction === ALL || n.direction === direction) &&
    (status === ALL || n.status === status) &&
    (evidence === ALL || n.evidence_strength === evidence) &&
    (responseStatus === ALL || n.response_status === responseStatus)
  )), [narratives, ownerType, direction, status, evidence, responseStatus])

  const hasFilters = direction !== ALL || status !== ALL || evidence !== ALL || responseStatus !== ALL

  if (loading) return <div className="loading-text">Loading narratives…</div>
  if (error)   return <div className="loading-text" style={{ color: 'var(--opponent)' }}>Error: {error}</div>

  return (
    <div className="page-wide">
      {/* Header */}
      <div className="page-header" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 16 }}>
        <div>
          <div className="label" style={{ marginBottom: 5 }}>Intelligence</div>
          <h1 className="page-title">Narratives</h1>
          <p className="page-subtitle">Campaign frames, opponent frames, and media frames with evidence underneath.</p>
        </div>
        <Link to="/sources" style={{ color: 'var(--accent-light)', fontSize: '0.78rem', textDecoration: 'none', flexShrink: 0, marginTop: 6 }}>
          View sources →
        </Link>
      </div>

      {/* Owner type pill tabs */}
      <div className="pill-tabs" style={{ marginBottom: '1rem' }}>
        {OWNER_TABS.map(tab => (
          <button
            key={tab}
            className={`pill-tab${ownerType === tab ? ' active' : ''}`}
            onClick={() => setOwnerType(tab)}
          >
            {tab === 'all' ? 'All' : tab.charAt(0).toUpperCase() + tab.slice(1)}
          </button>
        ))}
      </div>

      {/* Filters */}
      <div className="card" style={{ marginBottom: '1rem' }}>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'flex-end' }}>
          {([
            { label: 'Direction',       value: direction,      options: uniqueOptions(narratives, 'direction'),      set: setDirection },
            { label: 'Status',          value: status,         options: uniqueOptions(narratives, 'status'),         set: setStatus },
            { label: 'Evidence',        value: evidence,       options: uniqueOptions(narratives, 'evidence_strength'), set: setEvidence },
            { label: 'Response',        value: responseStatus, options: uniqueOptions(narratives, 'response_status'), set: setResponseStatus },
          ] as const).map(({ label, value, options, set }) => (
            <label key={label} style={{ display: 'grid', gap: 4, fontSize: '0.68rem', color: 'var(--text-muted)' }}>
              {label}
              <select value={value} onChange={e => (set as (v: string) => void)(e.target.value)} style={{ minWidth: 130 }}>
                {[ALL, ...options].map(o => (
                  <option key={o} value={o}>{o === ALL ? 'All' : o.replace(/_/g, ' ')}</option>
                ))}
              </select>
            </label>
          ))}
          {hasFilters && (
            <button
              className="btn btn-ghost btn-sm"
              onClick={() => { setDirection(ALL); setStatus(ALL); setEvidence(ALL); setResponseStatus(ALL) }}
            >
              Reset filters
            </button>
          )}
        </div>
      </div>

      {/* Result count */}
      <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', marginBottom: '0.75rem' }}>
        {filtered.length} narrative{filtered.length !== 1 ? 's' : ''}
        {hasFilters || ownerType !== ALL ? ' (filtered)' : ''}
      </div>

      {/* Narrative list */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {filtered.length === 0 && (
          <div className="empty-state">
            <div className="empty-state-icon">◻</div>
            <div className="empty-state-title">No narratives match</div>
            <div className="empty-state-body">Try adjusting the filters above.</div>
          </div>
        )}

        {filtered.map(n => {
          const signals = changeSignals(n)
          return (
            <Link
              key={n.narrative_id}
              to={`/narratives/${n.narrative_id}`}
              style={{ textDecoration: 'none' }}
            >
              <div className="card card-hover" style={{ borderLeft: `3px solid ${ownerBorderColor(n.owner_type)}` }}>
                <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 160px', gap: 16 }}>
                  {/* Left: content */}
                  <div>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start', marginBottom: 6, flexWrap: 'wrap' }}>
                      <h2 style={{ margin: 0, fontSize: '0.93rem', fontWeight: 700, lineHeight: 1.3, flex: 1, minWidth: 0 }}>
                        {n.short_label}
                      </h2>
                      <div style={{ display: 'flex', gap: 4, flexShrink: 0 }}>
                        <span className="badge badge-ghost" style={{ fontSize: '0.58rem' }}>{n.status}</span>
                        <span className="badge badge-ghost" style={{ fontSize: '0.58rem' }}>{n.evidence_strength}</span>
                      </div>
                    </div>

                    <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', marginBottom: 8 }}>
                      <span style={{ fontSize: '0.62rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                        {n.owner_type}
                      </span>
                      <span style={{ color: 'var(--text-xmuted)', fontSize: '0.62rem' }}>·</span>
                      <span style={{ fontSize: '0.62rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>
                        {n.attribution_type.replace(/_/g, ' ')}
                      </span>
                      <span style={{ color: 'var(--text-xmuted)', fontSize: '0.62rem' }}>·</span>
                      <span style={{ fontSize: '0.62rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>
                        {n.direction.replace(/_/g, ' ')}
                      </span>
                      <span style={{ color: 'var(--text-xmuted)', fontSize: '0.62rem' }}>·</span>
                      <span style={{ fontSize: '0.62rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>
                        {n.response_status.replace(/_/g, ' ')}
                      </span>
                    </div>

                    <p style={{ margin: '0 0 8px', fontSize: '0.79rem', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                      {n.canonical_text}
                    </p>

                    {signals.length > 0 && (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                        {signals.map(line => (
                          <div key={line} style={{ fontSize: '0.71rem', color: 'var(--text-muted)', lineHeight: 1.35 }}>
                            · {line}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>

                  {/* Right: metrics */}
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 8, alignItems: 'flex-end', justifyContent: 'flex-start' }}>
                    <div style={{ textAlign: 'right' }}>
                      <div style={{ fontSize: '1.35rem', fontWeight: 700, color: tractionColor(n.traction_score), fontFamily: 'JetBrains Mono', lineHeight: 1 }}>
                        {n.traction_score}
                      </div>
                      <div style={{ fontSize: '0.6rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>traction</div>
                    </div>
                    <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', textAlign: 'right', lineHeight: 1.7 }}>
                      <div>{n.source_cluster_count} cluster{n.source_cluster_count !== 1 ? 's' : ''}</div>
                      <div>{n.messenger_diversity_count} messenger{n.messenger_diversity_count !== 1 ? 's' : ''}</div>
                      <div>{n.source_count} source{n.source_count !== 1 ? 's' : ''}</div>
                    </div>
                    <div style={{ fontSize: '0.68rem', color: 'var(--accent-light)', fontFamily: 'JetBrains Mono' }}>
                      Open brief →
                    </div>
                  </div>
                </div>
              </div>
            </Link>
          )
        })}
      </div>
    </div>
  )
}
