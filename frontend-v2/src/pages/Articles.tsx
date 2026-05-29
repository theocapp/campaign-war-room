import { useEffect, useState } from 'react'
import type { CSSProperties } from 'react'
import { Link } from 'react-router-dom'
import { api } from '@/api/client'
import { InfoTooltip } from '@/components/InfoTooltip'
import type { SourceItem } from '@/api/types'
import { formatArticleDate } from '@/lib/formatDate'

const C = {
  bg1: 'var(--bg-1)', bg2: 'var(--bg-2)', bg3: 'var(--bg-3)',
  border: 'var(--border)', borderBright: 'var(--border-bright)',
  text1: 'var(--text-1)', text2: 'var(--text-2)', text3: 'var(--text-3)',
  accent: 'var(--accent)',
}

function ArticleListRow({ item }: { item: SourceItem }) {
  const score = item.race_relevance_score ?? 0
  const scoreColor = score >= 80 ? C.accent : score >= 50 ? C.text2 : C.text3
  const [hovered, setHovered] = useState(false)
  const [showDupes, setShowDupes] = useState(false)
  const duplicates = item.duplicates ?? []
  const hasDupes = duplicates.length > 0

  return (
    <div style={{ marginBottom: 6 }}>
      <Link
        to={`/articles/${item.id}`}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        style={{
          display: 'grid', gridTemplateColumns: '1fr 60px 100px',
          gap: 16, padding: '14px 16px',
          background: hovered ? C.bg3 : C.bg2,
          border: `1px solid ${hovered ? C.borderBright : C.border}`,
          borderRadius: hasDupes && showDupes ? '8px 8px 0 0' : 8,
          color: 'inherit', textDecoration: 'none',
          transition: 'background 0.1s ease, border-color 0.1s ease',
        } as CSSProperties}
      >
        <div style={{ minWidth: 0 }}>
          <div style={{
            fontSize: 14, color: C.text1, fontWeight: 500, lineHeight: 1.4,
            marginBottom: 4,
          }}>
            {item.title}
          </div>
          {item.summary && (
            <div style={{
              fontSize: 12, color: C.text2, lineHeight: 1.5, marginBottom: 6,
              overflow: 'hidden', display: '-webkit-box',
              WebkitLineClamp: 2, WebkitBoxOrient: 'vertical',
            } as CSSProperties}>
              {item.summary}
            </div>
          )}
          <div style={{ fontSize: 11, color: C.text3, display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            {item.source_name && <span>{item.source_name}</span>}
            {item.source_type && <><span>·</span><span style={{ textTransform: 'uppercase', letterSpacing: '0.05em' }}>{item.source_type}</span></>}
            {hasDupes && (
              <>
                <span>·</span>
                <button
                  type="button"
                  onClick={(e) => { e.preventDefault(); e.stopPropagation(); setShowDupes(s => !s) }}
                  style={{
                    background: 'transparent', border: `1px solid ${C.border}`,
                    color: C.accent, fontSize: 11, fontFamily: 'inherit',
                    padding: '2px 8px', borderRadius: 999, cursor: 'pointer',
                  }}
                  title={duplicates.map(d => d.source_name || 'Unknown source').join(', ')}
                >
                  {showDupes ? 'Hide' : 'Also in'} {duplicates.length} other outlet{duplicates.length === 1 ? '' : 's'}
                </button>
              </>
            )}
          </div>
        </div>
        <div style={{ textAlign: 'right' }}>
          <div style={{ fontSize: 16, fontWeight: 700, color: scoreColor }}>
            {score > 0 ? score : '—'}
          </div>
          <div style={{ fontSize: 10, color: C.text3, letterSpacing: '0.05em', textTransform: 'uppercase' }}>
            Relevance
          </div>
        </div>
        <div style={{ textAlign: 'right', fontSize: 11, color: C.text3 }}>
          {formatArticleDate(item.published_at ?? item.created_at)}
        </div>
      </Link>

      {hasDupes && showDupes && (
        <div style={{
          background: C.bg1,
          border: `1px solid ${C.border}`,
          borderTop: 'none',
          borderRadius: '0 0 8px 8px',
          padding: '8px 16px 10px',
        }}>
          <div style={{
            fontSize: 10, color: C.text3, textTransform: 'uppercase',
            letterSpacing: '0.05em', marginBottom: 6,
          }}>
            Same story, other outlets
          </div>
          {duplicates.map(dupe => (
            <Link
              key={dupe.id}
              to={`/articles/${dupe.id}`}
              style={{
                display: 'flex', justifyContent: 'space-between', gap: 12,
                padding: '6px 0', fontSize: 12, color: C.text2,
                textDecoration: 'none', borderTop: `1px dashed ${C.border}`,
              }}
            >
              <span style={{
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>
                {dupe.source_name || 'Unknown source'}
              </span>
              <span style={{ color: C.text3, flexShrink: 0 }}>
                {formatArticleDate(dupe.published_at)}
              </span>
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}

/**
 * Articles list page — shows the same confirmed-relevant feed as the
 * Dashboard right rail but with more space, summary previews, and direct
 * navigation to per-article detail.
 */
export function Articles() {
  const [items, setItems] = useState<SourceItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [limit, setLimit] = useState(50)

  useEffect(() => {
    let cancelled = false
    setLoading(true); setError(null)
    api.recentArticles(limit)
      .then(d => { if (!cancelled) setItems(d) })
      .catch(e => { if (!cancelled) setError(e.message || String(e)) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [limit])

  return (
    <div style={{ padding: '24px 28px', maxWidth: 1000, margin: '0 auto' }}>
      <div style={{ marginBottom: 18 }}>
        <h1 style={{
          fontSize: 24, fontWeight: 800, margin: 0,
          color: C.text1, letterSpacing: '-0.01em',
          display: 'inline-flex', alignItems: 'center',
        }}>
          Articles
          <InfoTooltip
            text={'Articles the AI has confirmed are relevant to your race. These have already cleared review (auto-approved or manually). Items still pending in the Review queue are not shown here.'}
            size={14}
          />
        </h1>
        <div style={{ fontSize: 12, color: C.text3, marginTop: 4 }}>
          {loading ? 'Loading…' : `${items.length} confirmed-relevant articles`}
        </div>
      </div>

      {error && (
        <div style={{
          padding: 16, border: `1px solid var(--red)`,
          background: 'rgba(239,68,68,0.08)', color: 'var(--red)',
          borderRadius: 8, marginBottom: 12, fontSize: 13,
        }}>
          Failed to load articles: {error}
        </div>
      )}

      {loading ? (
        Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="skeleton" style={{ height: 90, marginBottom: 6 }} />
        ))
      ) : items.length > 0 ? (
        <>
          {items.map(item => <ArticleListRow key={item.id} item={item} />)}
          {items.length >= limit && (
            <button
              type="button"
              onClick={() => setLimit(l => l + 50)}
              style={{
                marginTop: 12, width: '100%',
                padding: '10px 14px', borderRadius: 8,
                background: C.bg2, color: C.text2,
                border: `1px solid ${C.border}`,
                cursor: 'pointer', fontSize: 13, fontFamily: 'inherit',
              }}
            >
              Load more
            </button>
          )}
        </>
      ) : (
        <div style={{
          padding: '60px 20px', textAlign: 'center',
          color: C.text3, fontSize: 14,
        }}>
          No confirmed-relevant articles in the last week.
        </div>
      )}
    </div>
  )
}
