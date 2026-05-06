import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { DashboardData, SetupStatus } from '../api/types'

function fmtDate(s: string | null) {
  if (!s) return '—'
  return new Date(s).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function priorityAccent(p: string) {
  if (p === 'urgent') return { bar: 'var(--urgent)',  bg: 'var(--urgent-bg)',  text: 'var(--opponent-light)' }
  if (p === 'high')   return { bar: 'var(--warning)', bg: 'var(--warning-bg)', text: 'var(--warning-light)' }
  return                     { bar: 'var(--accent)',  bg: 'var(--accent-dim)', text: 'var(--accent-light)' }
}

function narrativeLabel(narrativeType: string, attributionType: string) {
  if (narrativeType === 'opponent_attack') return 'Attack'
  if (narrativeType === 'possible_attack') return 'Possible attack'
  if (attributionType === 'media_frame' || narrativeType === 'media_frame') return 'Media frame'
  if (narrativeType === 'candidate_self_definition') return 'Candidate frame'
  if (narrativeType === 'policy_frame') return 'Policy frame'
  return narrativeType.replace(/_/g, ' ')
}

function statusColor(status: string) {
  if (status === 'rising')  return 'var(--opponent)'
  if (status === 'fading')  return 'var(--text-muted)'
  if (status === 'stable')  return 'var(--text-secondary)'
  return 'var(--accent-light)'
}

function sourceLink(id: number | null | undefined) {
  return id ? `/sources?source_id=${id}` : '/sources'
}

function narrativeLink(n: { narrative_id?: number; source_item_id?: number | null; owner_type: string }) {
  if (n.narrative_id) return `/narratives/${n.narrative_id}`
  if (n.source_item_id) return sourceLink(n.source_item_id)
  return n.owner_type === 'candidate' ? '/message-library' : '/sources'
}

function SetupBanner({ status }: { status: SetupStatus }) {
  const done = status.items.filter(i => i.complete).length
  const total = status.items.length
  const pct = Math.round((done / total) * 100)
  const incomplete = status.items.filter(i => !i.complete).slice(0, 3)
  if (status.complete) return null
  return (
    <div className="card" style={{ marginBottom: '1.5rem', borderLeft: '3px solid var(--accent)', background: 'var(--accent-dim)' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
        <div style={{ fontSize: '0.82rem', fontWeight: 600, color: 'var(--accent-light)' }}>
          Setup {pct}% complete
        </div>
        <div style={{
          fontFamily: 'JetBrains Mono', fontSize: '0.65rem', color: 'var(--text-muted)',
        }}>{done}/{total}</div>
      </div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        {incomplete.map(item => (
          <Link key={item.id} to={item.action_path} style={{
            padding: '0.3rem 0.75rem',
            borderRadius: 99,
            fontSize: '0.74rem',
            background: 'var(--surface-3)',
            border: '1px solid var(--accent-border)',
            color: 'var(--accent-light)',
          }}>
            {item.label} →
          </Link>
        ))}
      </div>
    </div>
  )
}

export default function Dashboard() {
  const [data, setData] = useState<DashboardData | null>(null)
  const [setup, setSetup] = useState<SetupStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([api.getDashboard(), api.getSetupStatus()])
      .then(([d, s]) => { setData(d); setSetup(s) })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <div className="loading-text">Loading briefing…</div>
  if (error || !data) return <div className="loading-text" style={{ color: 'var(--opponent)' }}>Error: {error}</div>

  const header = data.race_header
  const review = data.review_snapshot
  const readiness = data.coverage_readiness
  const opponent = data.opponent_watch

  return (
    <div className="page">
      {/* Race header */}
      <Link to="/campaign" style={{ textDecoration: 'none', display: 'block', marginBottom: '1.5rem' }}>
        <div className="card card-hover" style={{ padding: '1.25rem 1.5rem' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 16 }}>
            <div>
              <div className="label" style={{ marginBottom: 6 }}>Campaign Briefing</div>
              <h1 style={{ margin: 0, fontSize: '1.45rem', fontWeight: 700, letterSpacing: '-0.02em', lineHeight: 1.1 }}>
                {header?.candidate_name || data.candidate_name}
              </h1>
              <div style={{ fontSize: '0.82rem', color: 'var(--text-secondary)', marginTop: 4 }}>
                {header?.office || data.race}
                {header?.district ? <span style={{ color: 'var(--text-muted)' }}> · {header.district}</span> : ''}
                {header?.election_type ? <span style={{ color: 'var(--text-muted)' }}> · {header.election_type}</span> : ''}
              </div>
              <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 10 }}>
                <span className="badge badge-ghost">{header?.source_coverage_strength || data.source_coverage?.source_coverage_strength} coverage</span>
                {header?.opponents?.length
                  ? header.opponents.map((o: string) => <span key={o} className="badge badge-ghost">{o}</span>)
                  : <span className="badge badge-ghost">No opponents added</span>}
              </div>
            </div>
            <div style={{ textAlign: 'right', fontFamily: 'JetBrains Mono', fontSize: '0.64rem', color: 'var(--text-muted)', flexShrink: 0 }}>
              Updated<br />
              <span style={{ color: 'var(--text-secondary)' }}>{fmtDate(data.last_updated)}</span>
            </div>
          </div>
        </div>
      </Link>

      {setup && !setup.complete && <SetupBanner status={setup} />}

      {/* Attention Now */}
      {data.attention_now.length > 0 && (
        <section style={{ marginBottom: '2rem' }}>
          <div className="section-title">Needs Attention</div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: 10 }}>
            {data.attention_now.map((card, i) => {
              const acc = priorityAccent(card.priority)
              const body = (
                <div className="card card-hover" style={{
                  height: '100%',
                  borderLeft: `3px solid ${acc.bar}`,
                  background: acc.bg,
                }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8, marginBottom: 8 }}>
                    <div className="line-clamp-2" style={{ fontWeight: 600, fontSize: '0.87rem', lineHeight: 1.35, color: 'var(--text-primary)' }}>
                      {card.title}
                    </div>
                    <span className="badge badge-ghost" style={{ fontSize: '0.58rem', color: acc.text, flexShrink: 0 }}>
                      {card.action_label}
                    </span>
                  </div>
                  <div className="line-clamp-3" style={{ fontSize: '0.76rem', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                    {card.explanation}
                  </div>
                  <div style={{ fontSize: '0.7rem', color: 'var(--accent-light)', marginTop: 10 }}>View →</div>
                </div>
              )
              return card.destination
                ? <Link key={i} to={card.destination} className="card-link">{body}</Link>
                : <div key={i}>{body}</div>
            })}
          </div>
        </section>
      )}

      {/* Narrative Briefing */}
      <section style={{ marginBottom: '2rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: '0.75rem' }}>
          <div className="section-title" style={{ marginBottom: 0 }}>Narrative Briefing</div>
          <span style={{ fontSize: '0.68rem', color: 'var(--text-muted)' }}>Message traction, not raw articles</span>
        </div>
        {data.narrative_briefing.length === 0 ? (
          <div className="empty-state">
            <div className="empty-state-icon">◈</div>
            <div className="empty-state-title">No narrative signal yet</div>
            <div className="empty-state-body">Add opponent statements or local coverage to start tracking message traction.</div>
          </div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 10 }}>
            {data.narrative_briefing.map(n => (
              <Link key={n.narrative_id} to={narrativeLink(n)} className="card-link">
                <div className="card card-hover" style={{
                  borderLeft: n.owner_type === 'opponent'
                    ? '3px solid var(--opponent-border)'
                    : '3px solid var(--accent-border)',
                }}>
                  {/* Status + traction */}
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                    <div style={{ display: 'flex', gap: 5, alignItems: 'center' }}>
                      <span style={{
                        width: 6, height: 6, borderRadius: '50%',
                        background: statusColor(n.status),
                        display: 'inline-block', flexShrink: 0,
                      }} />
                      <span style={{ fontSize: '0.65rem', color: statusColor(n.status), fontFamily: 'JetBrains Mono', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                        {n.status}
                      </span>
                    </div>
                    <div style={{ display: 'flex', gap: 5, alignItems: 'center' }}>
                      <span className="badge badge-ghost" style={{ fontSize: '0.58rem' }}>
                        {narrativeLabel(n.narrative_type, n.attribution_type)}
                      </span>
                      <span style={{ fontFamily: 'JetBrains Mono', fontSize: '0.62rem', color: 'var(--text-muted)' }}>
                        {n.traction_score}
                      </span>
                    </div>
                  </div>

                  <div className="line-clamp-2" style={{ fontWeight: 600, fontSize: '0.87rem', lineHeight: 1.35, marginBottom: 8 }}>
                    {n.short_label}
                  </div>

                  {n.why_it_matters && (
                    <div className="line-clamp-2" style={{ fontSize: '0.76rem', color: 'var(--text-secondary)', lineHeight: 1.5, marginBottom: 8 }}>
                      {n.why_it_matters}
                    </div>
                  )}

                  {n.risk_or_opportunity && (
                    <div style={{ fontSize: '0.72rem', color: 'var(--accent-light)', lineHeight: 1.4, marginBottom: 6 }}>
                      {n.risk_or_opportunity}
                    </div>
                  )}

                  <div style={{ display: 'flex', gap: 6, color: 'var(--text-muted)', fontSize: '0.66rem', fontFamily: 'JetBrains Mono', marginTop: 6 }}>
                    <span>{n.source_cluster_count} cluster{n.source_cluster_count !== 1 ? 's' : ''}</span>
                    <span>·</span>
                    <span>{n.messenger_diversity_count} messenger{n.messenger_diversity_count !== 1 ? 's' : ''}</span>
                    <span>·</span>
                    <span>{n.evidence_strength}</span>
                  </div>
                </div>
              </Link>
            ))}
          </div>
        )}
      </section>

      {/* Narrative Comparison */}
      {data.narrative_comparison && (
        <section style={{ marginBottom: '2rem' }}>
          <div className="section-title">Candidate vs Opponent Traction</div>
          <div className="card">
            {data.narrative_comparison.summary && (
              <p style={{ margin: '0 0 1rem', fontSize: '0.82rem', color: 'var(--text-secondary)', lineHeight: 1.55 }}>
                {data.narrative_comparison.summary}
              </p>
            )}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 12 }}>
              {[
                { title: 'Opponent Signals', items: data.narrative_comparison.needs_response, to: '/opponents' },
                { title: 'Candidate Spread', items: data.narrative_comparison.ready_to_amplify, to: '/message-library' },
                { title: 'Owned-Only Frames', items: data.narrative_comparison.candidate_owned_only, to: '/message-library' },
              ].map(({ title, items, to }) => (
                <div key={title}>
                  <div className="label" style={{ marginBottom: 8 }}>{title}</div>
                  {items.length === 0 ? (
                    <p style={{ fontSize: '0.74rem', color: 'var(--text-muted)', margin: 0 }}>No signal yet.</p>
                  ) : items.slice(0, 2).map(item => (
                    <Link key={item.narrative_id} to={to} style={{ display: 'block', textDecoration: 'none', marginBottom: 6 }}>
                      <div className="card card-hover card-sm">
                        <div className="line-clamp-1" style={{ fontSize: '0.8rem', fontWeight: 600, color: 'var(--text-primary)', marginBottom: 2 }}>
                          {item.short_label}
                        </div>
                        <div className="line-clamp-2" style={{ fontSize: '0.73rem', color: 'var(--text-secondary)', lineHeight: 1.4 }}>
                          {item.practical_read}
                        </div>
                      </div>
                    </Link>
                  ))}
                </div>
              ))}
            </div>
          </div>
        </section>
      )}

      {/* Signals */}
      {data.suggested_actions.length > 0 && (
        <section style={{ marginBottom: '2rem' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: '0.75rem' }}>
            <div className="section-title" style={{ marginBottom: 0 }}>Signals</div>
            <span style={{ fontSize: '0.68rem', color: 'var(--text-muted)' }}>What the data suggests</span>
          </div>
          <div className="card">
            {data.suggested_actions.map((signal, i) => {
              const acc = priorityAccent(signal.priority)
              return (
                <div key={i} style={{
                  display: 'grid', gridTemplateColumns: 'auto 1fr', gap: 12, alignItems: 'start',
                  paddingBlock: i === 0 ? '0 0.9rem' : '0.9rem',
                  borderTop: i > 0 ? '1px solid var(--border)' : 'none',
                }}>
                  <div style={{
                    width: 8, height: 8, borderRadius: '50%',
                    background: acc.bar, flexShrink: 0, marginTop: 6,
                  }} />
                  <div>
                    <div style={{ fontSize: '0.83rem', color: 'var(--text-primary)', lineHeight: 1.45, marginBottom: 3 }}>
                      {signal.action}
                    </div>
                    <div style={{ fontSize: '0.73rem', color: 'var(--text-muted)', lineHeight: 1.4 }}>
                      {signal.rationale}
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        </section>
      )}

      {/* Bottom two-col */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 340px', gap: '1.25rem', alignItems: 'start' }}>
        {/* Review Queue Snapshot */}
        <section>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: '0.75rem' }}>
            <div className="section-title" style={{ marginBottom: 0 }}>Review Queue</div>
            <Link to="/review" style={{ fontSize: '0.72rem', color: 'var(--accent-light)' }}>Open queue →</Link>
          </div>
          <div className="card" style={{ padding: '0.75rem 1.25rem' }}>
            <div style={{ display: 'flex', gap: 10, marginBottom: 12 }}>
              <span className="badge badge-ghost">{review?.review_worthy_count || 0} review-worthy</span>
              <span className="badge badge-ghost">{review?.respond_now_count || 0} respond-now</span>
            </div>
            {(!review?.top_items || review.top_items.length === 0) ? (
              <p style={{ fontSize: '0.78rem', color: 'var(--text-muted)', margin: 0 }}>Queue is clear.</p>
            ) : review.top_items.map((item, idx) => (
              <Link key={item.source_id} to={sourceLink(item.source_id)} style={{ textDecoration: 'none', display: 'block' }}>
                <div style={{
                  padding: '0.7rem 0',
                  borderTop: idx > 0 ? '1px solid var(--border)' : 'none',
                }}>
                  <div className="line-clamp-1" style={{ fontSize: '0.83rem', fontWeight: 600, color: 'var(--text-primary)', marginBottom: 3 }}>
                    {item.title}
                  </div>
                  <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>
                    {item.issue || 'No issue'} · {item.source_type.replace(/_/g, ' ')} · {item.actionability_label}
                  </div>
                </div>
              </Link>
            ))}
          </div>
        </section>

        {/* Right column */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>
          {/* Opponent Watch */}
          <section>
            <div className="section-title">Opponent Watch</div>
            <Link to={opponent?.source_item_id ? sourceLink(opponent.source_item_id) : '/opponents'} style={{ textDecoration: 'none', display: 'block' }}>
              <div className="card card-hover" style={{
                borderLeft: opponent?.latest_attack ? '3px solid var(--opponent-border)' : undefined,
              }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
                  <span className="badge badge-ghost" style={{ fontSize: '0.62rem' }}>
                    {opponent?.response_status || 'quiet'}
                  </span>
                  <span style={{ fontSize: '0.68rem', color: 'var(--accent-light)' }}>Details →</span>
                </div>
                {opponent?.latest_attack && (
                  <blockquote style={{
                    margin: '0 0 10px',
                    padding: '0.6rem 0.75rem',
                    borderLeft: '2px solid var(--opponent-border)',
                    borderRadius: '0 var(--radius-sm) var(--radius-sm) 0',
                    background: 'var(--opponent-bg)',
                    fontSize: '0.8rem',
                    color: 'var(--opponent-light)',
                    lineHeight: 1.5,
                    fontStyle: 'italic',
                  }}>
                    "{opponent.latest_attack}"
                  </blockquote>
                )}
                <div className="line-clamp-3" style={{ fontSize: '0.78rem', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                  {opponent?.summary}
                </div>
                {opponent?.repeated_themes.length ? (
                  <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', marginTop: 10 }}>
                    {opponent.repeated_themes.map(t => <span key={t} className="badge badge-ghost" style={{ fontSize: '0.58rem' }}>{t}</span>)}
                  </div>
                ) : null}
              </div>
            </Link>
          </section>

          {/* Coverage */}
          <section>
            <div className="section-title">Evidence Coverage</div>
            <Link to="/monitors" style={{ textDecoration: 'none', display: 'block' }}>
              <div className="card card-hover">
                <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 10 }}>
                  <span className="badge badge-ghost">{readiness?.coverage_strength} coverage</span>
                  <span className="badge badge-ghost">{readiness?.manual_source_dependence} manual</span>
                </div>
                {readiness?.ready_to_message_issues?.length ? (
                  <div style={{ marginBottom: 8 }}>
                    <div className="label" style={{ marginBottom: 3, color: 'var(--ok-light)' }}>Stronger Evidence</div>
                    <div style={{ fontSize: '0.77rem', color: 'var(--text-secondary)' }}>
                      {readiness.ready_to_message_issues.join(', ')}
                    </div>
                  </div>
                ) : null}
                {readiness?.thin_evidence_issues?.length ? (
                  <div style={{ marginBottom: 8 }}>
                    <div className="label" style={{ marginBottom: 3, color: 'var(--warning-light)' }}>Verify First</div>
                    <div style={{ fontSize: '0.77rem', color: 'var(--text-secondary)' }}>
                      {readiness.thin_evidence_issues.join(', ')}
                    </div>
                  </div>
                ) : null}
                {readiness?.issue_gaps?.length ? (
                  <div>
                    <div className="label" style={{ marginBottom: 3 }}>Issue Gaps</div>
                    <div style={{ fontSize: '0.77rem', color: 'var(--text-secondary)' }}>
                      {readiness.issue_gaps.join(', ')}
                    </div>
                  </div>
                ) : null}
                <div style={{ marginTop: 10, fontSize: '0.7rem', color: 'var(--accent-light)' }}>Improve coverage →</div>
              </div>
            </Link>
          </section>
        </div>
      </div>
    </div>
  )
}
