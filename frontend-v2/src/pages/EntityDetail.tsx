import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { api } from '@/api/client'
import type { EntityDetail as EntityDetailT } from '@/api/types'
import { formatArticleDate } from '@/lib/formatDate'

const C = {
  bg1: 'var(--bg-1)', bg2: 'var(--bg-2)', bg3: 'var(--bg-3)',
  border: 'var(--border)',
  text1: 'var(--text-1)', text2: 'var(--text-2)', text3: 'var(--text-3)',
  candidate: 'var(--candidate)', opponent: 'var(--opponent)', media: 'var(--media)',
  accent: 'var(--accent)',
  green: 'var(--green, #22c55e)', red: 'var(--red, #ef4444)',
}

function affiliationColor(a: string | null): string {
  if (a === 'D') return C.candidate
  if (a === 'R') return C.opponent
  return C.text2
}

function ownerColor(t: string): string {
  return t === 'candidate' ? C.candidate : t === 'opponent' ? C.opponent : C.media
}

export function EntityDetail() {
  const { id } = useParams<{ id: string }>()
  const canonicalId = decodeURIComponent(id || '')
  const [data, setData] = useState<EntityDetailT | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!canonicalId) return
    setLoading(true)
    setError(null)
    api.entity(canonicalId)
      .then(d => { setData(d); setLoading(false) })
      .catch(e => {
        const msg = e?.message || String(e)
        setError(msg.includes('404') ? 'Entity not found.' : 'Failed to load entity.')
        setLoading(false)
      })
  }, [canonicalId])

  if (!canonicalId) {
    return (
      <div style={{ padding: 32, color: C.text2 }}>
        Missing entity id.
      </div>
    )
  }

  if (loading) {
    return (
      <div style={{ padding: 32 }}>
        <div className="skeleton" style={{ height: 24, width: 120, marginBottom: 20 }} />
        <div className="skeleton" style={{ height: 48, width: 320, marginBottom: 12 }} />
        <div className="skeleton" style={{ height: 16, width: 480, marginBottom: 32 }} />
        <div className="skeleton" style={{ height: 200, borderRadius: 12 }} />
      </div>
    )
  }

  if (error || !data) {
    return (
      <div style={{ padding: 32 }}>
        <Link to="/" style={{
          display: 'inline-flex', alignItems: 'center', gap: 6,
          color: C.text2, fontSize: 13, textDecoration: 'none', marginBottom: 16,
        }}>
          <ArrowLeft size={14} /> Back
        </Link>
        <div style={{ color: C.text2, fontSize: 14 }}>
          {error || 'No data.'}
        </div>
      </div>
    )
  }

  const e = data.entity
  const s = data.stats
  const arrow = s.delta > 0 ? '↑' : s.delta < 0 ? '↓' : '→'
  const deltaColor = s.delta > 0 ? C.green : s.delta < 0 ? C.red : C.text3

  return (
    <div style={{ background: C.bg1, minHeight: '100%' }}>
      <div style={{ padding: '24px 24px 60px' }}>

        {/* Back link */}
        <Link to="/" style={{
          display: 'inline-flex', alignItems: 'center', gap: 6,
          color: C.text3, fontSize: 12, textDecoration: 'none', marginBottom: 16,
        }}>
          <ArrowLeft size={14} /> Back to dashboard
        </Link>

        {/* Header — name, type chip, affiliation chip, description */}
        <div style={{ marginBottom: 24 }}>
          <div style={{
            display: 'flex', alignItems: 'baseline', gap: 12,
            flexWrap: 'wrap', marginBottom: 8,
          }}>
            <h1 style={{
              margin: 0, fontSize: 28, fontWeight: 700,
              color: affiliationColor(e.affiliation),
              letterSpacing: '-0.01em',
            }}>
              {e.name}
            </h1>
            <span style={{
              fontSize: 10, color: C.text3,
              textTransform: 'uppercase', letterSpacing: '0.08em',
              fontWeight: 600,
            }}>
              {e.type}
            </span>
            {e.affiliation && (
              <span style={{
                fontSize: 10, fontWeight: 700,
                padding: '2px 6px', borderRadius: 4,
                color: 'white',
                background: affiliationColor(e.affiliation),
                letterSpacing: '0.05em',
              }}>
                {e.affiliation}
              </span>
            )}
          </div>
          {e.description && (
            <p style={{
              margin: 0, fontSize: 14, color: C.text2,
              lineHeight: 1.5, maxWidth: 760,
            }}>
              {e.description}
            </p>
          )}
        </div>

        {/* Stats strip */}
        <div className="card" style={{
          padding: '16px 20px', marginBottom: 28,
          display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)',
          gap: 16,
        }}>
          <StatCell
            label={`Mentions (last ${s.window_days}d)`}
            value={s.mentions_this_week.toLocaleString()}
            extra={
              <span style={{ fontSize: 11, color: deltaColor }}>
                {arrow}{Math.abs(s.delta)} vs prior {s.window_days}d ({s.mentions_last_week})
              </span>
            }
          />
          <StatCell label="Total articles" value={s.total_articles.toLocaleString()} />
          <StatCell label="Supporting quotes" value={s.total_quotes.toLocaleString()} />
          <StatCell
            label="Last seen"
            value={e.last_seen ? formatArticleDate(e.last_seen) : '—'}
          />
        </div>

        {/* Two-column body: left = articles, right = quotes + frames */}
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'minmax(0, 1.5fr) minmax(0, 1fr)',
          gap: 24,
        }}>

          {/* Left: recent articles */}
          <section>
            <SectionHeader>Recent articles</SectionHeader>
            {data.recent_articles.length === 0 ? (
              <EmptyHint>No articles mentioning this entity yet.</EmptyHint>
            ) : (
              <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
                {data.recent_articles.map(a => (
                  <li key={a.id} style={{ marginBottom: 10 }}>
                    <Link
                      to={`/articles/${a.id}`}
                      className="card"
                      style={{
                        display: 'block', padding: '12px 14px',
                        textDecoration: 'none', color: 'inherit',
                      }}
                    >
                      <div style={{
                        fontSize: 14, fontWeight: 600, color: C.text1,
                        lineHeight: 1.35, marginBottom: 4,
                      }}>
                        {a.title}
                      </div>
                      <div style={{
                        fontSize: 11, color: C.text3,
                        display: 'flex', gap: 8, alignItems: 'center',
                      }}>
                        <span>{a.source_name}</span>
                        <span>·</span>
                        <span>{formatArticleDate(a.published_at)}</span>
                      </div>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </section>

          {/* Right: quotes + frames */}
          <aside style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>

            <section>
              <SectionHeader>Supporting quotes</SectionHeader>
              {data.supporting_quotes.length === 0 ? (
                <EmptyHint>No quote-anchored claims mention this entity.</EmptyHint>
              ) : (
                <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
                  {data.supporting_quotes.map(q => (
                    <li key={q.id} className="card" style={{
                      padding: '10px 12px', marginBottom: 8,
                    }}>
                      <blockquote style={{
                        margin: 0, fontSize: 12, color: C.text1,
                        lineHeight: 1.45, fontStyle: 'italic',
                      }}>
                        "{q.evidence_span}"
                      </blockquote>
                      <div style={{
                        marginTop: 6, fontSize: 10, color: C.text3,
                        display: 'flex', gap: 6, alignItems: 'center',
                        flexWrap: 'wrap',
                      }}>
                        {q.label && (
                          <span style={{
                            textTransform: 'uppercase', letterSpacing: '0.06em',
                            fontWeight: 600,
                          }}>
                            {q.label}
                          </span>
                        )}
                        {q.label && q.article && <span>·</span>}
                        {q.article && (
                          <Link
                            to={`/articles/${q.article.id}`}
                            style={{ color: C.text3, textDecoration: 'underline' }}
                          >
                            {q.article.source_name}
                          </Link>
                        )}
                        {q.article?.published_at && (
                          <>
                            <span>·</span>
                            <span>{formatArticleDate(q.article.published_at)}</span>
                          </>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </section>

            <section>
              <SectionHeader>Narrative frames</SectionHeader>
              {data.narrative_frames.length === 0 ? (
                <EmptyHint>No active frames overlap with this entity's coverage.</EmptyHint>
              ) : (
                <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
                  {data.narrative_frames.map(f => (
                    <li key={f.id} style={{ marginBottom: 6 }}>
                      <Link
                        to={`/narratives/${f.id}`}
                        className="card"
                        style={{
                          display: 'flex', alignItems: 'center', gap: 10,
                          padding: '8px 12px', textDecoration: 'none',
                          color: 'inherit',
                        }}
                      >
                        <span style={{
                          width: 4, alignSelf: 'stretch',
                          background: ownerColor(f.owner_type), borderRadius: 2,
                        }} />
                        <span style={{
                          flex: 1, fontSize: 13, color: C.text1,
                          overflow: 'hidden', textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                        }}>
                          {f.name}
                        </span>
                        <span style={{
                          fontSize: 11, color: C.text3,
                          fontVariantNumeric: 'tabular-nums',
                        }}>
                          {f.article_count}
                        </span>
                      </Link>
                    </li>
                  ))}
                </ul>
              )}
            </section>

          </aside>
        </div>
      </div>
    </div>
  )
}

function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      fontSize: 13, fontWeight: 600, color: C.text2,
      marginBottom: 10, display: 'flex', alignItems: 'center', gap: 8,
    }}>
      {children}
      <span style={{ flex: 1, height: 1, background: C.bg3, display: 'block' }} />
    </div>
  )
}

function EmptyHint({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      fontSize: 12, color: C.text3, padding: '8px 0',
    }}>
      {children}
    </div>
  )
}

function StatCell({
  label, value, extra,
}: {
  label: string
  value: string
  extra?: React.ReactNode
}) {
  return (
    <div>
      <div style={{
        fontSize: 10, color: C.text3, textTransform: 'uppercase',
        letterSpacing: '0.06em', marginBottom: 4, fontWeight: 600,
      }}>
        {label}
      </div>
      <div style={{
        fontSize: 22, fontWeight: 700, color: C.text1,
        fontVariantNumeric: 'tabular-nums', lineHeight: 1.1,
      }}>
        {value}
      </div>
      {extra && (
        <div style={{ marginTop: 2 }}>
          {extra}
        </div>
      )}
    </div>
  )
}
