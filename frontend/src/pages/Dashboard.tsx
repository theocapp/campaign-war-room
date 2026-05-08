import { useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../api/client'
import type {
  DashboardData, SetupStatus, DashboardNarrativeCard,
  DashboardReviewQueueItem, Issue,
} from '../api/types'

/* ── helpers ── */
function decodeHtml(s: string) {
  const txt = document.createElement('textarea')
  txt.innerHTML = s
  return txt.value
}

function timeAgo(s: string | null) {
  if (!s) return '—'
  const diff = (Date.now() - new Date(s).getTime()) / 1000
  if (diff < 60) return `${Math.round(diff)}s ago`
  if (diff < 3600) return `${Math.round(diff / 60)}m ago`
  if (diff < 86400) return `${Math.round(diff / 3600)}h ago`
  return `${Math.round(diff / 86400)}d ago`
}

function urgencyBadge(u: string) {
  if (u === 'urgent' || u === 'high')
    return { label: 'HIGH', bg: 'var(--urgent-bg)', color: 'var(--urgent-light)', border: 'var(--urgent-border)', icon: '🔥' }
  if (u === 'medium')
    return { label: 'MEDIUM', bg: 'var(--warning-bg)', color: 'var(--warning-light)', border: 'var(--warning-border)', icon: '⚡' }
  return { label: 'LOW', bg: 'var(--ok-bg)', color: 'var(--ok-light)', border: 'var(--ok-border)', icon: '◎' }
}

function directionColor(d: string) {
  if (d === 'against') return 'var(--urgent)'
  if (d === 'for') return 'var(--accent)'
  return 'var(--text-muted)'
}

function momentumArrow(status: string, shift: string | null | undefined) {
  if (status === 'rising' || shift === 'stronger') return { arrow: '↑', color: 'var(--urgent)' }
  if (status === 'fading' || shift === 'weaker') return { arrow: '↓', color: 'var(--ok)' }
  return { arrow: '—', color: 'var(--text-muted)' }
}

function riskBadge(n: DashboardNarrativeCard) {
  const score = n.traction_score
  if (n.owner_type === 'opponent' && score >= 15)
    return { label: 'High', bg: 'rgba(239,68,68,0.15)', color: '#f87171', border: 'rgba(239,68,68,0.4)' }
  if (score >= 8 || n.owner_type === 'opponent')
    return { label: 'High', bg: 'rgba(239,68,68,0.12)', color: '#f87171', border: 'rgba(239,68,68,0.35)' }
  if (score >= 4)
    return { label: 'Medium', bg: 'var(--warning-bg)', color: 'var(--warning-light)', border: 'var(--warning-border)' }
  return { label: 'Low', bg: 'var(--ok-bg)', color: 'var(--ok-light)', border: 'var(--ok-border)' }
}

function ownerDot(ownerType: string) {
  if (ownerType === 'candidate') return { color: 'var(--accent-light)', label: 'Candidate' }
  if (ownerType === 'opponent')  return { color: 'var(--opponent)', label: 'Opponent' }
  return { color: 'var(--ok-light)', label: 'Media' }
}

function priorityColor(p: string) {
  if (p === 'urgent' || p === 'high') return 'var(--opponent)'
  if (p === 'medium') return 'var(--warning-light)'
  return 'var(--ok-light)'
}

/* ── sub-components ── */

function SetupBanner({ status }: { status: SetupStatus }) {
  const done = status.items.filter(i => i.complete).length
  const total = status.items.length
  const pct = Math.round((done / total) * 100)
  if (status.complete) return null
  return (
    <div style={{
      margin: '0.75rem 1.5rem',
      padding: '0.75rem 1.1rem',
      borderRadius: 8, border: '1px solid var(--accent-border)',
      background: 'var(--accent-dim)',
      display: 'flex', alignItems: 'center', gap: 12,
    }}>
      <div style={{ flex: 1 }}>
        <span style={{ fontSize: '0.8rem', fontWeight: 600, color: 'var(--accent-light)' }}>
          Setup {pct}% complete
        </span>
        <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginLeft: 10 }}>
          {done}/{total} steps done
        </span>
      </div>
      {status.items.filter(i => !i.complete).slice(0, 2).map(item => (
        <Link key={item.id} to={item.action_path} style={{
          padding: '0.25rem 0.7rem', borderRadius: 99,
          fontSize: '0.72rem', background: 'var(--surface-3)',
          border: '1px solid var(--accent-border)', color: 'var(--accent-light)',
          whiteSpace: 'nowrap',
        }}>{item.label} →</Link>
      ))}
    </div>
  )
}

/* ── Narrative Detail Side Panel ── */
function NarrativePanel({ narrative, onClose }: {
  narrative: DashboardNarrativeCard
  onClose: () => void
}) {
  const ownerLabel = narrative.owner_type === 'opponent' ? 'OPPONENT NARRATIVE'
    : narrative.owner_type === 'candidate' ? 'CANDIDATE NARRATIVE' : 'MEDIA NARRATIVE'
  const ownerColor = narrative.owner_type === 'opponent' ? 'var(--opponent)'
    : narrative.owner_type === 'candidate' ? 'var(--accent-light)' : 'var(--ok-light)'
  const [activeTab, setActiveTab] = useState<'evidence' | 'claims' | 'timeline' | 'risk' | 'actions'>('evidence')

  const sources = narrative.top_supporting_sources ?? []

  return (
    <div style={{
      width: 330, flexShrink: 0,
      borderLeft: '1px solid var(--border)',
      background: 'var(--surface-0)',
      display: 'flex', flexDirection: 'column',
      overflowY: 'auto',
    }}>
      {/* Header */}
      <div style={{
        padding: '1rem 1.1rem 0.75rem',
        borderBottom: '1px solid var(--border)',
        position: 'sticky', top: 0,
        background: 'var(--surface-0)', zIndex: 1,
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <span style={{
            fontSize: '0.58rem', fontWeight: 700, letterSpacing: '0.1em',
            textTransform: 'uppercase', color: ownerColor,
            fontFamily: 'JetBrains Mono',
          }}>{ownerLabel}</span>
          <button onClick={onClose} style={{
            background: 'none', border: 'none', cursor: 'pointer',
            color: 'var(--text-muted)', fontSize: 16, lineHeight: 1,
            padding: '0 2px',
          }}>×</button>
        </div>
        <h3 style={{ margin: '0.5rem 0 0.4rem', fontSize: '1rem', fontWeight: 700, lineHeight: 1.25, color: 'var(--text-primary)' }}>
          {decodeHtml(narrative.short_label)}
        </h3>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 6 }}>
          <span style={{
            fontSize: '0.65rem', color: ownerColor,
            background: `${ownerColor}18`, border: `1px solid ${ownerColor}40`,
            borderRadius: 4, padding: '1px 6px', fontWeight: 600,
          }}>
            {narrative.owner_type === 'opponent' ? 'Against Candidate' : narrative.direction === 'for' ? 'For Candidate' : 'Neutral'}
          </span>
          <span style={{
            fontSize: '0.65rem', color: 'var(--text-secondary)',
            background: 'var(--surface-2)', border: '1px solid var(--border)',
            borderRadius: 4, padding: '1px 6px',
          }}>
            Confidence: {narrative.owner_confidence}
          </span>
          <span style={{
            fontSize: '0.65rem', color: 'var(--text-secondary)',
            background: 'var(--surface-2)', border: '1px solid var(--border)',
            borderRadius: 4, padding: '1px 6px',
          }}>
            {narrative.source_count} sources
          </span>
        </div>
        <div style={{ fontSize: '0.65rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>
          {narrative.source_count} source{narrative.source_count !== 1 ? 's' : ''} · {narrative.evidence_strength} evidence · {narrative.status}
        </div>
      </div>

      {/* Tabs */}
      <div style={{ display: 'flex', borderBottom: '1px solid var(--border)', padding: '0 1.1rem' }}>
        {(['evidence', 'claims', 'timeline', 'risk', 'actions'] as const).map(tab => (
          <button key={tab} onClick={() => setActiveTab(tab)} style={{
            padding: '0.5rem 0.6rem', fontSize: '0.72rem', fontWeight: 500,
            background: 'none', border: 'none', cursor: 'pointer',
            color: activeTab === tab ? 'var(--text-primary)' : 'var(--text-muted)',
            borderBottom: activeTab === tab ? '2px solid var(--accent)' : '2px solid transparent',
            textTransform: 'capitalize', marginBottom: -1,
          }}>{tab}</button>
        ))}
      </div>

      {/* Content */}
      <div style={{ flex: 1, padding: '1rem 1.1rem' }}>
        {activeTab === 'evidence' && (
          <>
            {sources.length > 0 && (
              <>
                <div style={{ fontSize: '0.65rem', fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 8 }}>
                  Top Evidence
                </div>
                {sources.slice(0, 3).map(src => (
                  <div key={src.id} style={{
                    padding: '0.6rem', marginBottom: 8,
                    border: '1px solid var(--border)', borderRadius: 6,
                    background: 'var(--surface-1)',
                  }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                      <span style={{ fontSize: '0.7rem', fontWeight: 600, color: 'var(--text-secondary)' }}>
                        {src.source_name || 'Unknown'}
                      </span>
                      <span style={{ fontSize: '0.62rem', color: 'var(--text-muted)' }}>{timeAgo(src.published_at)}</span>
                    </div>
                    {src.snapshot?.key_claim_or_quote && (
                      <p style={{ margin: '0 0 4px', fontSize: '0.74rem', color: 'var(--text-secondary)', lineHeight: 1.45, fontStyle: 'italic' }}>
                        "{src.snapshot.key_claim_or_quote}"
                      </p>
                    )}
                    {src.source_url && (
                      <a href={src.source_url} target="_blank" rel="noreferrer" style={{ fontSize: '0.65rem', color: 'var(--accent-light)' }}>
                        View source →
                      </a>
                    )}
                  </div>
                ))}
              </>
            )}

            <div style={{
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              padding: '0.5rem 0', borderTop: sources.length ? '1px solid var(--border)' : 'none',
              marginTop: sources.length ? 4 : 0,
            }}>
              <span style={{ fontSize: '0.72rem', fontWeight: 700, color: 'var(--text-primary)' }}>
                All Sources ({narrative.source_count})
              </span>
              <Link to={`/narratives/${narrative.narrative_id}`} style={{ fontSize: '0.65rem', color: 'var(--accent-light)' }}>
                View all sources
              </Link>
            </div>

            {narrative.why_it_matters && (
              <div style={{ marginTop: 12 }}>
                <div style={{ fontSize: '0.65rem', fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 6 }}>
                  About This Narrative
                </div>
                <p style={{ margin: 0, fontSize: '0.75rem', color: 'var(--text-secondary)', lineHeight: 1.55 }}>
                  {narrative.why_it_matters}
                </p>
              </div>
            )}

            {/* Tags */}
            <div style={{ marginTop: 12 }}>
              <div style={{ fontSize: '0.65rem', fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 6 }}>
                Tags
              </div>
              <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
                {narrative.narrative_type && (
                  <span style={{ fontSize: '0.68rem', padding: '2px 8px', borderRadius: 99, background: 'var(--surface-3)', border: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
                    {narrative.narrative_type.replace(/_/g, ' ')}
                  </span>
                )}
                {narrative.owner_type && (
                  <span style={{ fontSize: '0.68rem', padding: '2px 8px', borderRadius: 99, background: 'var(--surface-3)', border: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
                    {narrative.owner_type}
                  </span>
                )}
                {narrative.action && (
                  <span style={{ fontSize: '0.68rem', padding: '2px 8px', borderRadius: 99, background: 'var(--surface-3)', border: '1px solid var(--border)', color: 'var(--text-secondary)' }}>
                    {narrative.action}
                  </span>
                )}
              </div>
            </div>
          </>
        )}

        {activeTab === 'claims' && (
          <div>
            {narrative.canonical_text && (
              <blockquote style={{
                margin: '0 0 12px', padding: '0.75rem', borderRadius: 6,
                borderLeft: '3px solid var(--accent-border)',
                background: 'var(--accent-dim)',
                fontSize: '0.8rem', color: 'var(--accent-light)', lineHeight: 1.6,
                fontStyle: 'italic',
              }}>
                "{decodeHtml(narrative.canonical_text)}"
              </blockquote>
            )}
            {narrative.spread_summary && (
              <p style={{ fontSize: '0.78rem', color: 'var(--text-secondary)', lineHeight: 1.55 }}>
                {narrative.spread_summary}
              </p>
            )}
          </div>
        )}

        {activeTab === 'risk' && (
          <div>
            {narrative.risk_or_opportunity && (
              <p style={{ margin: '0 0 10px', fontSize: '0.78rem', color: 'var(--text-secondary)', lineHeight: 1.55 }}>
                {narrative.risk_or_opportunity}
              </p>
            )}
            <div style={{ display: 'flex', gap: 8 }}>
              <div style={{
                flex: 1, padding: '0.6rem', borderRadius: 6,
                background: 'var(--surface-2)', border: '1px solid var(--border)',
                fontSize: '0.72rem',
              }}>
                <div style={{ color: 'var(--text-muted)', marginBottom: 2 }}>Traction</div>
                <div style={{ fontWeight: 700, color: 'var(--text-primary)', fontSize: '1.1rem' }}>{narrative.traction_score}</div>
              </div>
              <div style={{
                flex: 1, padding: '0.6rem', borderRadius: 6,
                background: 'var(--surface-2)', border: '1px solid var(--border)',
                fontSize: '0.72rem',
              }}>
                <div style={{ color: 'var(--text-muted)', marginBottom: 2 }}>Messengers</div>
                <div style={{ fontWeight: 700, color: 'var(--text-primary)', fontSize: '1.1rem' }}>{narrative.messenger_diversity_count}</div>
              </div>
            </div>
          </div>
        )}

        {activeTab === 'actions' && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <Link to={`/talking?narrative_id=${narrative.narrative_id}`} style={{
              display: 'flex', alignItems: 'center', gap: 8,
              padding: '0.6rem 0.875rem', borderRadius: 6,
              border: '1px solid var(--border)', background: 'var(--surface-1)',
              color: 'var(--accent-light)', fontSize: '0.78rem', fontWeight: 500,
            }}>
              <span>💬</span> Generate Talking Points
            </Link>
            <Link to={`/narratives/${narrative.narrative_id}`} style={{
              display: 'flex', alignItems: 'center', gap: 8,
              padding: '0.6rem 0.875rem', borderRadius: 6,
              border: '1px solid var(--border)', background: 'var(--surface-1)',
              color: 'var(--opponent-light)', fontSize: '0.78rem', fontWeight: 500,
            }}>
              <span>⚔</span> Draft Rebuttal
            </Link>
            <Link to={`/talking?narrative_id=${narrative.narrative_id}&format=social`} style={{
              display: 'flex', alignItems: 'center', gap: 8,
              padding: '0.6rem 0.875rem', borderRadius: 6,
              border: '1px solid var(--border)', background: 'var(--surface-1)',
              color: 'var(--text-secondary)', fontSize: '0.78rem', fontWeight: 500,
            }}>
              <span>📱</span> Create Social Response
            </Link>
            <Link to="/canvassing" style={{
              display: 'flex', alignItems: 'center', gap: 8,
              padding: '0.6rem 0.875rem', borderRadius: 6,
              border: '1px solid var(--border)', background: 'var(--surface-1)',
              color: 'var(--text-secondary)', fontSize: '0.78rem', fontWeight: 500,
            }}>
              <span>🗺</span> Add to Canvassing Script
            </Link>
            <Link to={`/narratives/${narrative.narrative_id}`} style={{
              display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8,
              padding: '0.7rem 0.875rem', borderRadius: 6,
              background: 'var(--accent-2)', color: '#fff',
              fontSize: '0.8rem', fontWeight: 600, marginTop: 4,
            }}>
              📄 Export Briefing PDF
            </Link>
          </div>
        )}

        {activeTab === 'timeline' && (
          <div style={{ fontSize: '0.78rem', color: 'var(--text-secondary)', lineHeight: 1.6 }}>
            {narrative.what_changed
              ? <p style={{ margin: 0 }}>{narrative.what_changed}</p>
              : <p style={{ margin: 0, color: 'var(--text-muted)' }}>No timeline data available yet.</p>
            }
          </div>
        )}
      </div>

      {/* Quick Actions bar (always visible at bottom) */}
      <div style={{
        padding: '0.75rem 1.1rem',
        borderTop: '1px solid var(--border)',
        background: 'var(--surface-0)',
        position: 'sticky', bottom: 0,
      }}>
        <div style={{ fontSize: '0.65rem', fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 8 }}>
          Quick Actions
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6, marginBottom: 8 }}>
          {[
            { label: '💬 Generate Talking Points', to: `/talking?narrative_id=${narrative.narrative_id}` },
            { label: '⚔ Draft Rebuttal', to: `/narratives/${narrative.narrative_id}` },
            { label: '📱 Create Social Response', to: `/talking?narrative_id=${narrative.narrative_id}&format=social` },
            { label: '🗺 Add to Canvassing Script', to: '/canvassing' },
          ].map(({ label, to }) => (
            <Link key={label} to={to} style={{
              padding: '0.4rem 0.5rem', borderRadius: 6,
              border: '1px solid var(--border)', background: 'var(--surface-1)',
              color: 'var(--text-secondary)', fontSize: '0.68rem', fontWeight: 500,
              display: 'flex', alignItems: 'center', gap: 4,
            }}>{label}</Link>
          ))}
        </div>
        <Link to={`/narratives/${narrative.narrative_id}`} style={{
          display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
          padding: '0.6rem', borderRadius: 6,
          background: 'var(--surface-2)', color: 'var(--text-primary)',
          fontSize: '0.78rem', fontWeight: 600, border: '1px solid var(--border)',
        }}>
          📄 Export Briefing PDF
        </Link>
      </div>
    </div>
  )
}

/* ── Main Dashboard ── */
export default function Dashboard() {
  const [data, setData] = useState<DashboardData | null>(null)
  const [setup, setSetup] = useState<SetupStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [narrativeTab, setNarrativeTab] = useState<'rising' | 'fading' | 'stable'>('rising')
  const [selectedNarrative, setSelectedNarrative] = useState<DashboardNarrativeCard | null>(null)
  const navigate = useNavigate()

  useEffect(() => {
    Promise.all([api.getDashboard(), api.getSetupStatus()])
      .then(([d, s]) => { setData(d); setSetup(s) })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <div className="loading-text">Loading briefing…</div>
  if (error || !data) return <div className="loading-text" style={{ color: 'var(--opponent)' }}>Error: {error}</div>

  const review = data.review_snapshot

  // Pad signals to 4 with top narrative cards when attention_now has fewer
  const rawSignals = data.attention_now.slice(0, 4)
  const narrativePadding: typeof rawSignals = data.narrative_briefing
    .filter(n => n.owner_type === 'opponent' || n.status === 'rising')
    .sort((a, b) => b.traction_score - a.traction_score)
    .slice(0, 4 - rawSignals.length)
    .map(n => ({
      card_type: 'narrative',
      priority: (n.traction_score >= 15 ? 'urgent' : n.traction_score >= 6 ? 'high' : 'medium') as 'urgent' | 'high' | 'medium',
      title: n.short_label,
      explanation: n.why_it_matters || n.spread_summary || '',
      action_label: 'View',
      destination: `/narratives/${n.narrative_id}`,
    }))
  const signals = [...rawSignals, ...narrativePadding].slice(0, 4)

  const filteredNarratives = data.narrative_briefing.filter(n => {
    if (narrativeTab === 'rising') return n.status === 'rising' || n.status === 'emerging'
    if (narrativeTab === 'fading') return n.status === 'fading'
    if (narrativeTab === 'stable') return n.status === 'stable'
    return true
  })

  const opponentActivity = data.opponent_activity || []
  // Build heatmap: last 4 weeks, 7 days each
  const now = new Date()
  const heatmap: Record<string, number> = {}
  opponentActivity.forEach(a => {
    const d = new Date(a.created_at)
    const key = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`
    heatmap[key] = (heatmap[key] || 0) + 1
  })
  const weeks = [4, 3, 2, 1].map(weeksAgo => {
    const days = [0, 1, 2, 3, 4, 5, 6].map(dayOffset => {
      const d = new Date(now)
      d.setDate(d.getDate() - (weeksAgo * 7 - dayOffset))
      const key = `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`
      return { date: d, count: heatmap[key] || 0, label: d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) }
    })
    const weekLabel = (() => {
      const d = new Date(now)
      d.setDate(d.getDate() - (weeksAgo * 7))
      return d.toLocaleDateString('en-US', { month: 'numeric', day: 'numeric' })
    })()
    return { label: weekLabel, days }
  })

  // Message penetration: candidate narratives with traction
  const candNarratives = data.narrative_briefing
    .filter(n => n.owner_type === 'candidate')
    .sort((a, b) => b.traction_score - a.traction_score)
    .slice(0, 5)
  const maxTraction = candNarratives[0]?.traction_score || 1

  return (
    <div style={{ display: 'flex', height: '100%', minHeight: 0 }}>
      {/* ── Main scroll area ── */}
      <div style={{ flex: 1, minWidth: 0, overflowY: 'auto', padding: '0' }}>
        {setup && !setup.complete && <SetupBanner status={setup} />}

        {/* ── SIGNALS ── */}
        <section style={{ padding: '1.1rem 1.5rem 0' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ fontSize: 16 }}>🔥</span>
              <span style={{ fontSize: '0.72rem', fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--text-primary)' }}>
                Signals
              </span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <span style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
                Sort by: <span style={{ color: 'var(--text-secondary)' }}>Urgency ▾</span>
              </span>
              <Link to="/sources" style={{ fontSize: '0.72rem', color: 'var(--accent-light)' }}>View all →</Link>
            </div>
          </div>

          {signals.length === 0 ? (
            <div className="empty-state" style={{ marginBottom: '1rem' }}>
              <div className="empty-state-icon">🔥</div>
              <div className="empty-state-title">No signals yet</div>
              <div className="empty-state-body">Ingest sources to start seeing signals.</div>
            </div>
          ) : (
            <div style={{ display: 'grid', gridTemplateColumns: `repeat(${Math.min(signals.length, 4)}, 1fr)`, gap: 10, marginBottom: '0.25rem' }}>
              {signals.map((card, i) => {
                const ub = urgencyBadge(card.priority)
                return (
                  <div key={i}
                    onClick={() => card.destination && navigate(card.destination)}
                    style={{
                      background: 'var(--surface-1)', border: '1px solid var(--border)',
                      borderLeft: `3px solid ${ub.color}`,
                      borderRadius: 8, padding: '0.875rem',
                      cursor: card.destination ? 'pointer' : 'default',
                      transition: 'border-color 0.15s, background 0.15s',
                    }}
                    onMouseEnter={e => { if (card.destination) { (e.currentTarget as HTMLDivElement).style.background = 'var(--surface-2)' } }}
                    onMouseLeave={e => { (e.currentTarget as HTMLDivElement).style.background = 'var(--surface-1)' }}
                  >
                    {/* Urgency + time */}
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
                      <span style={{
                        display: 'inline-flex', alignItems: 'center', gap: 4,
                        fontSize: '0.6rem', fontWeight: 700, letterSpacing: '0.08em',
                        padding: '2px 6px', borderRadius: 4,
                        background: ub.bg, color: ub.color,
                        border: `1px solid ${ub.border}`,
                      }}>
                        {ub.icon} {ub.label}
                      </span>
                      <span style={{ fontSize: '0.65rem', color: 'var(--text-muted)' }}>
                        {timeAgo(data.last_updated)}
                      </span>
                    </div>

                    {/* Title */}
                    <div style={{ fontWeight: 600, fontSize: '0.84rem', lineHeight: 1.3, marginBottom: 5, color: 'var(--text-primary)' }}
                      className="line-clamp-2">
                      {card.title}
                    </div>

                    {/* Body */}
                    <div style={{ fontSize: '0.74rem', color: 'var(--text-secondary)', lineHeight: 1.5, marginBottom: 8 }}
                      className="line-clamp-2">
                      {card.explanation}
                    </div>

                    {/* Footer */}
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                      <span style={{ fontSize: '0.65rem', color: 'var(--text-muted)' }}>
                        {card.card_type === 'narrative' ? 'narrative' : card.card_type}
                      </span>
                      {card.destination && <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>→</span>}
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </section>

        {/* ── NARRATIVE BRIEFING TABLE ── */}
        <section style={{ padding: '1.1rem 1.5rem 0' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.625rem' }}>
            <span style={{ fontSize: '0.72rem', fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--text-primary)' }}>
              Narrative Briefing
            </span>
            <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              {(['rising', 'fading', 'stable'] as const).map(tab => (
                <button key={tab} onClick={() => setNarrativeTab(tab)} style={{
                  padding: '0.22rem 0.7rem', borderRadius: 99, fontSize: '0.72rem',
                  fontWeight: narrativeTab === tab ? 600 : 400,
                  border: 'none', cursor: 'pointer',
                  background: narrativeTab === tab ? 'var(--accent-2)' : 'transparent',
                  color: narrativeTab === tab ? '#fff' : 'var(--text-muted)',
                  transition: 'all 0.12s',
                }}>
                  {tab.charAt(0).toUpperCase() + tab.slice(1)}
                </button>
              ))}
              <Link to="/narratives" style={{
                padding: '0.22rem 0.7rem', borderRadius: 99, fontSize: '0.72rem',
                color: 'var(--text-muted)', border: '1px solid var(--border)',
              }}>View all</Link>
            </div>
          </div>

          <div style={{
            background: 'var(--surface-1)', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden',
          }}>
            {/* Table header */}
            <div style={{
              display: 'grid',
              gridTemplateColumns: '2.5fr 0.9fr 0.8fr 0.8fr 0.7fr 0.9fr 0.7fr 28px',
              padding: '0.5rem 0.875rem',
              borderBottom: '1px solid var(--border)',
              background: 'var(--surface-2)',
            }}>
              {['Narrative', 'Owner', 'Direction', 'Momentum', 'Evidence', 'Last Seen', 'Risk', ''].map(h => (
                <div key={h} style={{ fontSize: '0.6rem', fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--text-muted)' }}>{h}</div>
              ))}
            </div>

            {/* Table rows */}
            {filteredNarratives.length === 0 ? (
              <div style={{ padding: '1.5rem', textAlign: 'center', color: 'var(--text-muted)', fontSize: '0.78rem' }}>
                No {narrativeTab} narratives found.
              </div>
            ) : filteredNarratives.slice(0, 8).map(n => {
              const owner = ownerDot(n.owner_type)
              const mom = momentumArrow(n.status, n.momentum_shift)
              const risk = riskBadge(n)
              const isSelected = selectedNarrative?.narrative_id === n.narrative_id
              return (
                <div
                  key={n.narrative_id}
                  onClick={() => setSelectedNarrative(isSelected ? null : n)}
                  style={{
                    display: 'grid',
                    gridTemplateColumns: '2.5fr 0.9fr 0.8fr 0.8fr 0.7fr 0.9fr 0.7fr 28px',
                    padding: '0.6rem 0.875rem',
                    borderBottom: '1px solid var(--border)',
                    cursor: 'pointer',
                    background: isSelected ? 'var(--accent-dim)' : 'transparent',
                    transition: 'background 0.1s',
                    alignItems: 'center',
                  }}
                  onMouseEnter={e => { if (!isSelected) (e.currentTarget as HTMLDivElement).style.background = 'var(--surface-2)' }}
                  onMouseLeave={e => { if (!isSelected) (e.currentTarget as HTMLDivElement).style.background = 'transparent' }}
                >
                  {/* Narrative name */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
                    <span style={{
                      width: 3, height: 28, borderRadius: 99, flexShrink: 0,
                      background: n.owner_type === 'opponent' ? '#ef4444' : 'var(--accent)',
                    }} />
                    <span className="line-clamp-1" style={{ fontSize: '0.8rem', fontWeight: 500, color: 'var(--text-primary)' }}>
                      {decodeHtml(n.short_label)}
                    </span>
                  </div>

                  {/* Owner */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                    <span style={{ width: 7, height: 7, borderRadius: '50%', background: owner.color, flexShrink: 0 }} />
                    <span style={{ fontSize: '0.72rem', color: 'var(--text-secondary)' }}>{owner.label}</span>
                  </div>

                  {/* Direction */}
                  <div style={{ fontSize: '0.72rem', fontWeight: 500, color: directionColor(n.direction) }}>
                    {n.direction === 'for' ? 'For' : n.direction === 'against' ? 'Against' : 'Neutral'}
                  </div>

                  {/* Momentum */}
                  <div style={{ fontSize: '1rem', color: mom.color, fontWeight: 700 }}>
                    {mom.arrow}
                  </div>

                  {/* Evidence */}
                  <div style={{ fontSize: '0.75rem', color: 'var(--text-secondary)', fontFamily: 'JetBrains Mono' }}>
                    {n.source_count}
                  </div>

                  {/* Last Seen */}
                  <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>
                    {timeAgo(data.last_updated)}
                  </div>

                  {/* Risk */}
                  <div>
                    <span style={{
                      fontSize: '0.6rem', fontWeight: 600, padding: '2px 6px', borderRadius: 4,
                      background: risk.bg, color: risk.color, border: `1px solid ${risk.border}`,
                    }}>{risk.label}</span>
                  </div>

                  {/* Menu */}
                  <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', textAlign: 'center' }}>⋯</div>
                </div>
              )
            })}
          </div>
        </section>

        {/* ── REVIEW QUEUE ── */}
        {review && review.top_items.length > 0 && (
          <section style={{ padding: '1.1rem 1.5rem 0' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.625rem' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <span style={{ fontSize: '0.72rem', fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--text-primary)' }}>
                  Review Queue
                </span>
                <span style={{
                  background: 'var(--opponent)', color: '#fff',
                  fontSize: '0.6rem', fontWeight: 700, padding: '1px 6px', borderRadius: 99,
                }}>{review.review_worthy_count + review.respond_now_count}</span>
              </div>
              <Link to="/review" style={{ fontSize: '0.72rem', color: 'var(--accent-light)' }}>View all →</Link>
            </div>

            <div style={{
              background: 'var(--surface-1)', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden',
            }}>
              {/* Header */}
              <div style={{
                display: 'grid', gridTemplateColumns: '24px 2.5fr 1fr 1.5fr 0.8fr 100px',
                padding: '0.5rem 0.875rem', borderBottom: '1px solid var(--border)',
                background: 'var(--surface-2)',
              }}>
                {['#', 'Source', 'Outlet', 'Why Flagged', 'Urgency', 'Actions'].map(h => (
                  <div key={h} style={{ fontSize: '0.6rem', fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--text-muted)' }}>{h}</div>
                ))}
              </div>

              {review.top_items.map((item, idx) => (
                <ReviewRow key={item.source_id} item={item} idx={idx} />
              ))}

              {/* Bulk actions */}
              <div style={{
                padding: '0.6rem 0.875rem', borderTop: '1px solid var(--border)',
                display: 'flex', alignItems: 'center', gap: 8,
                background: 'var(--surface-2)',
              }}>
                <button className="btn btn-ghost btn-sm">✓ Approve</button>
                <button className="btn btn-ghost btn-sm">× Dismiss</button>
                <button className="btn btn-ghost btn-sm">▣ Archive</button>
                <span style={{ marginLeft: 'auto', fontSize: '0.65rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>
                  {review.review_worthy_count + review.respond_now_count} items
                </span>
              </div>
            </div>
          </section>
        )}

        {/* ── BOTTOM ROW ── */}
        <section style={{ padding: '1.1rem 1.5rem 2rem', display: 'grid', gridTemplateColumns: '1fr 1.2fr 1fr', gap: '1rem' }}>
          {/* Top Issues */}
          <div>
            <div style={{ fontSize: '0.65rem', fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 8 }}>
              Top Issues This Week
            </div>
            <div style={{ background: 'var(--surface-1)', border: '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
              {(data.top_issues.length ? data.top_issues : (data.priority_issues || []).map(p => ({
                id: p.issue_id, name: p.name, urgency: 'medium' as const,
                mention_count: p.distinct_development_count,
                trend: p.trend, summary: null, last_seen_at: null,
              } as Issue))).slice(0, 5).map((issue, idx) => (
                <Link key={issue.id} to={`/issues`} style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  padding: '0.6rem 0.875rem',
                  borderBottom: idx < 4 ? '1px solid var(--border)' : 'none',
                  color: 'inherit',
                }}>
                  <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', width: 12, flexShrink: 0 }}>
                    {idx + 1}
                  </span>
                  <span style={{ flex: 1, fontSize: '0.78rem', color: 'var(--text-primary)', fontWeight: 500 }}
                    className="line-clamp-1">
                    {issue.name}
                  </span>
                  <span style={{ fontSize: '0.75rem', color: issue.trend === 'rising' ? 'var(--opponent)' : issue.trend === 'falling' ? 'var(--ok-light)' : 'var(--text-muted)' }}>
                    {issue.trend === 'rising' ? '↑' : issue.trend === 'falling' ? '↓' : '—'}
                  </span>
                  <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', minWidth: 24, textAlign: 'right' }}>
                    {issue.mention_count}
                  </span>
                </Link>
              ))}
              {data.top_issues.length === 0 && data.priority_issues.length === 0 && (
                <div style={{ padding: '1rem', textAlign: 'center', color: 'var(--text-muted)', fontSize: '0.75rem' }}>No issues tracked yet.</div>
              )}
            </div>
          </div>

          {/* Opponent Activity Heatmap */}
          <div>
            <div style={{ fontSize: '0.65rem', fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 8 }}>
              Opponent Activity Heatmap
            </div>
            <div style={{ background: 'var(--surface-1)', border: '1px solid var(--border)', borderRadius: 8, padding: '0.75rem' }}>
              {/* Day labels */}
              <div style={{ display: 'grid', gridTemplateColumns: '48px repeat(7, 1fr)', gap: 3, marginBottom: 4 }}>
                <div />
                {['Mon','Tue','Wed','Thu','Fri','Sat','Sun'].map(d => (
                  <div key={d} style={{ fontSize: '0.58rem', color: 'var(--text-muted)', textAlign: 'center' }}>{d}</div>
                ))}
              </div>
              {weeks.map(week => {
                const maxCount = Math.max(...week.days.map(d => d.count), 1)
                return (
                  <div key={week.label} style={{ display: 'grid', gridTemplateColumns: '48px repeat(7, 1fr)', gap: 3, marginBottom: 3 }}>
                    <div style={{ fontSize: '0.58rem', color: 'var(--text-muted)', display: 'flex', alignItems: 'center' }}>
                      {week.label}
                    </div>
                    {week.days.map(day => {
                      const intensity = day.count / maxCount
                      return (
                        <div
                          key={day.label}
                          title={`${day.label}: ${day.count} activities`}
                          style={{
                            height: 20, borderRadius: 3,
                            background: day.count === 0
                              ? 'var(--surface-2)'
                              : `rgba(248,113,113,${0.15 + intensity * 0.75})`,
                            border: '1px solid var(--border)',
                          }}
                        />
                      )
                    })}
                  </div>
                )
              })}
              <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 6 }}>
                <span style={{ fontSize: '0.58rem', color: 'var(--text-muted)' }}>Low</span>
                {[0.1, 0.3, 0.5, 0.7, 0.9].map(v => (
                  <div key={v} style={{
                    width: 14, height: 14, borderRadius: 2,
                    background: `rgba(248,113,113,${v})`,
                  }} />
                ))}
                <span style={{ fontSize: '0.58rem', color: 'var(--text-muted)' }}>High</span>
              </div>
            </div>
          </div>

          {/* Message Penetration */}
          <div>
            <div style={{ fontSize: '0.65rem', fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--text-muted)', marginBottom: 8 }}>
              Message Penetration
            </div>
            <div style={{ background: 'var(--surface-1)', border: '1px solid var(--border)', borderRadius: 8, padding: '0.75rem' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                <span style={{ fontSize: '0.65rem', color: 'var(--text-muted)' }}>Your Messaging Themes</span>
                <span style={{ fontSize: '0.65rem', color: 'var(--text-muted)' }}>Media Pickup</span>
              </div>
              {candNarratives.length === 0 ? (
                <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', textAlign: 'center', padding: '0.5rem' }}>
                  No candidate narratives tracked.
                </div>
              ) : candNarratives.map(n => {
                const pct = Math.round((n.traction_score / maxTraction) * 100)
                const barColor = pct >= 70 ? 'var(--ok)' : pct >= 40 ? 'var(--warning)' : 'var(--opponent)'
                return (
                  <div key={n.narrative_id} style={{ marginBottom: 8 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
                      <span className="line-clamp-1" style={{ fontSize: '0.72rem', color: 'var(--text-secondary)', flex: 1 }}>
                        {n.short_label}
                      </span>
                      <span style={{ fontSize: '0.65rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', marginLeft: 6 }}>
                        {pct}%
                      </span>
                    </div>
                    <div style={{ height: 6, borderRadius: 99, background: 'var(--surface-3)' }}>
                      <div style={{ height: '100%', borderRadius: 99, width: `${pct}%`, background: barColor, transition: 'width 0.3s' }} />
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        </section>
      </div>

      {/* ── Right Panel (Narrative Detail) ── */}
      {selectedNarrative && (
        <NarrativePanel
          narrative={selectedNarrative}
          onClose={() => setSelectedNarrative(null)}
        />
      )}
    </div>
  )
}

/* ── Review Queue Row ── */
function ReviewRow({ item, idx }: { item: DashboardReviewQueueItem; idx: number }) {
  const urgencyColor = item.relevance_score >= 70 ? 'var(--opponent)' : item.relevance_score >= 40 ? 'var(--warning-light)' : 'var(--ok-light)'
  const urgencyLabel = item.relevance_score >= 70 ? 'High' : item.relevance_score >= 40 ? 'Medium' : 'Low'

  return (
    <div style={{
      display: 'grid', gridTemplateColumns: '24px 2.5fr 1fr 1.5fr 0.8fr 100px',
      padding: '0.6rem 0.875rem', borderBottom: '1px solid var(--border)',
      alignItems: 'center',
      transition: 'background 0.1s',
    }}
      onMouseEnter={e => (e.currentTarget as HTMLDivElement).style.background = 'var(--surface-2)'}
      onMouseLeave={e => (e.currentTarget as HTMLDivElement).style.background = 'transparent'}
    >
      <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>{idx + 1}</div>

      <Link to={`/sources?source_id=${item.source_id}`} style={{
        fontSize: '0.78rem', color: 'var(--text-primary)', fontWeight: 500,
        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
      }}
        className="line-clamp-1"
      >
        {item.title}
      </Link>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0 }}>
        <span style={{ fontSize: '0.72rem', color: 'var(--text-secondary)', fontWeight: 500 }}
          className="line-clamp-1">
          {item.geography || item.relevance_label || '—'}
        </span>
        <span style={{
          fontSize: '0.6rem', padding: '1px 5px', borderRadius: 3,
          background: 'var(--surface-3)', border: '1px solid var(--border)',
          color: 'var(--text-muted)', alignSelf: 'flex-start',
        }}>
          {item.source_type.replace(/_/g, ' ')}
        </span>
      </div>

      <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }} className="line-clamp-1">
        {item.issue || item.relevance_label || 'Flagged for review'}
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
        <span style={{ fontSize: 12 }}>📊</span>
        <span style={{ fontSize: '0.7rem', fontWeight: 600, color: urgencyColor }}>{urgencyLabel}</span>
      </div>

      <div style={{ display: 'flex', gap: 6 }}>
        <button style={{
          width: 24, height: 24, borderRadius: 4, border: '1px solid var(--ok-border)',
          background: 'transparent', cursor: 'pointer', color: 'var(--ok-light)',
          fontSize: 12, display: 'flex', alignItems: 'center', justifyContent: 'center',
        }} title="Approve">✓</button>
        <button style={{
          width: 24, height: 24, borderRadius: 4, border: '1px solid var(--opponent-border)',
          background: 'transparent', cursor: 'pointer', color: 'var(--opponent)',
          fontSize: 12, display: 'flex', alignItems: 'center', justifyContent: 'center',
        }} title="Dismiss">×</button>
        <button style={{
          width: 24, height: 24, borderRadius: 4, border: '1px solid var(--border)',
          background: 'transparent', cursor: 'pointer', color: 'var(--text-muted)',
          fontSize: 12, display: 'flex', alignItems: 'center', justifyContent: 'center',
        }} title="Archive">▣</button>
      </div>
    </div>
  )
}
