import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { DashboardData, DashboardChanges, SetupStatus } from '../api/types'
import UrgencyBadge from '../components/UrgencyBadge'
import TrendBadge from '../components/TrendBadge'
import SourceCard from '../components/SourceCard'

function fmtDate(s: string) {
  return new Date(s).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })
}

function SetupChecklist({ status }: { status: SetupStatus }) {
  const incomplete = status.items.filter(i => !i.complete)
  if (incomplete.length === 0) return null
  return (
    <div className="card" style={{ marginBottom: '1.5rem', borderLeft: '3px solid rgba(251,191,36,0.5)' }}>
      <div style={{ fontSize: '0.65rem', fontFamily: 'JetBrains Mono', color: '#fbbf24', letterSpacing: '0.06em', marginBottom: 10 }}>
        SETUP CHECKLIST — {status.items.filter(i => i.complete).length}/{status.items.length} COMPLETE
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {status.items.map(item => (
          <div key={item.id} style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
            <span style={{
              width: 16, height: 16, borderRadius: '50%', flexShrink: 0, marginTop: 1,
              background: item.complete ? 'rgba(34,197,94,0.3)' : 'var(--surface-2)',
              border: `1px solid ${item.complete ? 'rgba(34,197,94,0.5)' : 'var(--border)'}`,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: '0.6rem', color: item.complete ? '#86efac' : 'transparent',
            }}>✓</span>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: '0.8rem', fontWeight: item.complete ? 400 : 600, color: item.complete ? 'var(--text-muted)' : 'var(--text-primary)' }}>
                {item.complete ? (
                  <span style={{ textDecoration: 'line-through' }}>{item.label}</span>
                ) : (
                  <Link to={item.action_path} style={{ color: 'var(--text-primary)', textDecoration: 'none' }}>
                    {item.label} →
                  </Link>
                )}
              </div>
              {!item.complete && (
                <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: 1 }}>{item.helper_text}</div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function Dashboard() {
  const [data, setData] = useState<DashboardData | null>(null)
  const [setupStatus, setSetupStatus] = useState<SetupStatus | null>(null)
  const [changes, setChanges] = useState<DashboardChanges | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([
      api.getDashboard(),
      api.getSetupStatus(),
      api.getDashboardChanges(24),
    ])
      .then(([d, s, c]) => { setData(d); setSetupStatus(s); setChanges(c) })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <div style={{ padding: '2rem', color: 'var(--text-muted)' }}>Loading…</div>
  if (error || !data) return <div style={{ padding: '2rem', color: '#f87171' }}>Error: {error}</div>

  const urgentActions = data.suggested_actions.filter(a => a.priority === 'urgent')
  const otherActions = data.suggested_actions.filter(a => a.priority !== 'urgent')

  return (
    <div style={{ padding: '1.5rem', maxWidth: 1300 }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '1.5rem' }}>
        <div>
          <div className="label" style={{ marginBottom: 4 }}>Campaign War Room</div>
          <h1 style={{ margin: 0, fontSize: '1.3rem', fontWeight: 700 }}>{data.candidate_name}</h1>
          <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: 2 }}>{data.race}</div>
        </div>
        <div style={{ textAlign: 'right', fontSize: '0.7rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>
          Updated {fmtDate(data.last_updated)}
        </div>
      </div>

      {/* Setup checklist */}
      {setupStatus && !setupStatus.complete && <SetupChecklist status={setupStatus} />}

      {/* Review queue count */}
      {data.review_queue_count > 0 && (
        <Link to="/review" style={{ textDecoration: 'none', display: 'block', marginBottom: '1rem' }}>
          <div style={{
            padding: '0.65rem 1rem', borderRadius: 6,
            background: 'rgba(59,130,246,0.08)', border: '1px solid rgba(59,130,246,0.2)',
            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          }}>
            <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
              <span style={{ fontWeight: 600, color: 'var(--text-primary)' }}>{data.review_queue_count}</span> source{data.review_queue_count !== 1 ? 's' : ''} in review queue
            </span>
            <span style={{ fontSize: '0.72rem', color: 'var(--accent)' }}>Review →</span>
          </div>
        </Link>
      )}

      {/* Risk warnings — top priority */}
      {data.risk_warnings.length > 0 && (
        <div style={{ marginBottom: '1.5rem' }}>
          <div className="section-title">⚠ Requires Attention</div>
          {data.risk_warnings.map(rw => (
            <div key={rw.source_id} className="risk-banner" style={{ marginBottom: 8 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                <span style={{ fontSize: '0.7rem', fontWeight: 700, color: '#f87171', fontFamily: 'JetBrains Mono', letterSpacing: '0.06em' }}>
                  ⚠ RISK ALERT
                </span>
                <UrgencyBadge urgency="high" size="sm" />
              </div>
              <div style={{ fontSize: '0.8rem', fontWeight: 500, color: 'var(--text-primary)', marginBottom: 4 }}>{rw.source_title}</div>
              <div style={{ fontSize: '0.78rem', color: '#fca5a5', lineHeight: 1.5 }}>{rw.warning}</div>
            </div>
          ))}
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 340px', gap: '1.5rem' }}>
        {/* Left column */}
        <div>
          {/* Issues */}
          <div style={{ marginBottom: '1.5rem' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
              <div className="section-title" style={{ marginBottom: 0 }}>Top Local Issues</div>
              <Link to="/issues" style={{ fontSize: '0.7rem', color: 'var(--accent)' }}>View all →</Link>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 8 }}>
              {data.top_issues.map(issue => (
                <Link key={issue.id} to={`/issues`} style={{ textDecoration: 'none' }}>
                  <div className="card card-hover" style={{ height: '100%' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 6 }}>
                      <div style={{ fontWeight: 600, fontSize: '0.85rem', flex: 1 }}>{issue.name}</div>
                      <UrgencyBadge urgency={issue.urgency} size="sm" />
                    </div>
                    <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginBottom: 8 }}>
                      <TrendBadge trend={issue.trend} />
                      <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>
                        {issue.mention_count} mentions
                      </span>
                    </div>
                    {issue.summary && (
                      <p style={{ margin: 0, fontSize: '0.75rem', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                        {issue.summary.slice(0, 120)}…
                      </p>
                    )}
                  </div>
                </Link>
              ))}
            </div>
          </div>

          {/* Suggested actions */}
          <div style={{ marginBottom: '1.5rem' }}>
            <div className="section-title">Recommended Actions</div>
            {urgentActions.map((a, i) => (
              <div key={i} className="action-card urgent" style={{ marginBottom: 8 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                  <span style={{ fontWeight: 600, fontSize: '0.82rem' }}>{a.action}</span>
                  <span className="badge badge-urgent" style={{ fontSize: '0.6rem' }}>URGENT</span>
                </div>
                <p style={{ margin: 0, fontSize: '0.76rem', color: 'var(--text-secondary)', lineHeight: 1.4 }}>{a.rationale}</p>
              </div>
            ))}
            {otherActions.map((a, i) => (
              <div key={i} className={`action-card ${a.priority}`} style={{ marginBottom: 8 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                  <span style={{ fontWeight: 600, fontSize: '0.82rem' }}>{a.action}</span>
                  <span className={`badge badge-${a.priority === 'high' ? 'medium' : 'info'}`} style={{ fontSize: '0.6rem' }}>
                    {a.priority.toUpperCase()}
                  </span>
                </div>
                <p style={{ margin: 0, fontSize: '0.76rem', color: 'var(--text-secondary)', lineHeight: 1.4 }}>{a.rationale}</p>
              </div>
            ))}
          </div>

          {/* Recent sources */}
          <div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
              <div className="section-title" style={{ marginBottom: 0 }}>Recent Intelligence</div>
              <Link to="/sources" style={{ fontSize: '0.7rem', color: 'var(--accent)' }}>View all →</Link>
            </div>
            {data.recent_sources.slice(0, 4).map(s => (
              <SourceCard key={s.id} source={s} />
            ))}
          </div>
        </div>

        {/* Right column */}
        <div>
          {/* Opponent activity */}
          <div style={{ marginBottom: '1.5rem' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '0.75rem' }}>
              <div className="section-title" style={{ marginBottom: 0 }}>Opponent Activity</div>
              <Link to="/opponents" style={{ fontSize: '0.7rem', color: 'var(--accent)' }}>Full tracker →</Link>
            </div>
            {data.opponent_activity.map(act => (
              <div key={act.id} className="card" style={{ marginBottom: 8, borderLeft: '3px solid rgba(239,68,68,0.4)' }}>
                {act.attack && (
                  <div style={{ marginBottom: 6 }}>
                    <div style={{ fontSize: '0.6rem', fontFamily: 'JetBrains Mono', color: '#f87171', letterSpacing: '0.06em', marginBottom: 3 }}>ATTACK</div>
                    <p style={{ margin: 0, fontSize: '0.78rem', color: 'var(--text-primary)', lineHeight: 1.4 }}>"{act.attack}"</p>
                  </div>
                )}
                {act.claim && !act.attack && (
                  <div style={{ marginBottom: 6 }}>
                    <div style={{ fontSize: '0.6rem', fontFamily: 'JetBrains Mono', color: '#fbbf24', letterSpacing: '0.06em', marginBottom: 3 }}>CLAIM</div>
                    <p style={{ margin: 0, fontSize: '0.78rem', color: 'var(--text-primary)', lineHeight: 1.4 }}>"{act.claim}"</p>
                  </div>
                )}
                {act.contradiction_note && (
                  <p style={{ margin: '4px 0 0', fontSize: '0.72rem', color: 'var(--text-secondary)', lineHeight: 1.4, borderTop: '1px solid var(--border)', paddingTop: 6 }}>
                    {act.contradiction_note}
                  </p>
                )}
                {act.repeated_theme && (
                  <span className="badge badge-ghost" style={{ marginTop: 6, fontSize: '0.6rem' }}>
                    {act.repeated_theme}
                  </span>
                )}
              </div>
            ))}
          </div>

          {/* Canvassing summary */}
          {data.canvassing_summary && (
            <div style={{ marginBottom: '1.5rem' }}>
              <div className="section-title">Canvassing Summary</div>
              <div className="card" style={{ borderLeft: '3px solid rgba(167,139,250,0.5)' }}>
                <p style={{ margin: 0, fontSize: '0.8rem', color: 'var(--text-secondary)', lineHeight: 1.6 }}>
                  {data.canvassing_summary}
                </p>
                <Link to="/canvassing" style={{ display: 'inline-block', marginTop: 10, fontSize: '0.72rem', color: 'var(--accent)' }}>
                  View precinct breakdown →
                </Link>
              </div>
            </div>
          )}

          {/* What changed in last 24h */}
          {changes && changes.changes.length > 0 && (
            <div>
              <div className="section-title">Last 24 Hours</div>
              <div className="card" style={{ padding: '0.75rem 1rem' }}>
                <div style={{ fontSize: '0.65rem', fontFamily: 'JetBrains Mono', color: 'var(--text-muted)', marginBottom: 8 }}>
                  {changes.new_source_count} new source{changes.new_source_count !== 1 ? 's' : ''}
                  {changes.new_attack_count > 0 && ` · ${changes.new_attack_count} attack${changes.new_attack_count !== 1 ? 's' : ''}`}
                </div>
                {changes.changes.slice(0, 6).map((c, i) => (
                  <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'flex-start', marginBottom: 6 }}>
                    <span style={{
                      fontSize: '0.6rem', fontFamily: 'JetBrains Mono', flexShrink: 0, marginTop: 2,
                      color: c.type === 'new_attack' ? '#f87171' : 'var(--text-muted)',
                    }}>
                      {c.type === 'new_attack' ? '⚠' : '·'}
                    </span>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{
                        fontSize: '0.75rem', color: c.type === 'new_attack' ? '#fca5a5' : 'var(--text-secondary)',
                        lineHeight: 1.3,
                        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                      }}>
                        {c.title}
                      </div>
                    </div>
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
