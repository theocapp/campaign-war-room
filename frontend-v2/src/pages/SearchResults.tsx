/**
 * Search results page — full-text search across all articles via /api/search
 * (SQLite FTS5, 16k+ indexed rows). Narratives and entities remain client-side
 * filtered since they're small datasets.
 */
import { Building2, Calendar, FileText, Inbox, Layers, MapPin, Search as SearchIcon, Sparkles, User } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { api } from '@/api/client'
import { entities as MOCK_ENTITIES, type Entity, type EntityType } from '@/data/entityNetworkMock'
import type { NarrativeFrame, SourceItem } from '@/api/types'
import { formatArticleDate } from '@/lib/formatDate'

const TYPE_ICONS: Record<EntityType, typeof User> = {
  person: User,
  organization: Building2,
  bill: FileText,
  event: Calendar,
  location: MapPin,
}

type Tab = 'all' | 'articles' | 'narratives' | 'entities'

export function SearchResults() {
  const [params] = useSearchParams()
  const q = (params.get('q') || '').trim()
  const [tab, setTab] = useState<Tab>('all')
  const [frames, setFrames] = useState<NarrativeFrame[]>([])
  const [articles, setArticles] = useState<SourceItem[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    setLoading(true)
    setArticles([])
    Promise.all([
      api.narrativeFrames().then(setFrames).catch(() => setFrames([])),
      q
        ? api.search(q).then(setArticles).catch(() => setArticles([]))
        : Promise.resolve(),
    ]).finally(() => setLoading(false))
  }, [q])

  const results = useMemo(() => {
    const term = q.toLowerCase()
    if (!term) return { articles: [], frames: [], entities: [] as Entity[] }
    return {
      articles,
      frames: frames.filter(f => f.name.toLowerCase().includes(term)),
      entities: MOCK_ENTITIES.filter(e =>
        e.name.toLowerCase().includes(term) ||
        e.description.toLowerCase().includes(term)
      ),
    }
  }, [q, articles, frames])

  const total = results.articles.length + results.frames.length + results.entities.length
  const visible = {
    articles: tab === 'all' || tab === 'articles' ? results.articles : [],
    frames: tab === 'all' || tab === 'narratives' ? results.frames : [],
    entities: tab === 'all' || tab === 'entities' ? results.entities : [],
  }

  return (
    <div style={{ maxWidth: 960, margin: '0 auto', padding: '24px 24px 60px' }}>
      <div style={{ marginBottom: 24 }}>
        <div style={{ fontSize: 11, color: 'var(--text-3)', textTransform: 'uppercase', fontWeight: 700, letterSpacing: '0.08em', marginBottom: 6 }}>
          Search results
        </div>
        <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--text-1)' }}>
          {q ? (
            <>
              <SearchIcon size={20} style={{ marginRight: 8, verticalAlign: '-3px', color: 'var(--text-3)' }} />
              "{q}"
            </>
          ) : (
            'Enter a query to search'
          )}
        </div>
        {q && !loading && (
          <div style={{ fontSize: 13, color: 'var(--text-2)', marginTop: 6 }}>
            {total} {total === 1 ? 'result' : 'results'} across articles, narratives, and entities
          </div>
        )}
      </div>

      {/* Tabs */}
      {q && (
        <div style={{ display: 'flex', gap: 6, marginBottom: 24, borderBottom: '1px solid var(--border)' }}>
          {([
            { v: 'all', label: 'All', count: total },
            { v: 'articles', label: 'Articles', count: results.articles.length },
            { v: 'narratives', label: 'Narratives', count: results.frames.length },
            { v: 'entities', label: 'Entities', count: results.entities.length },
          ] as const).map(t => {
            const active = tab === t.v
            return (
              <button
                key={t.v}
                onClick={() => setTab(t.v)}
                style={{
                  padding: '8px 14px', background: 'transparent', border: 'none',
                  borderBottom: '2px solid ' + (active ? 'var(--accent)' : 'transparent'),
                  color: active ? 'var(--text-1)' : 'var(--text-3)',
                  fontSize: 13, fontWeight: active ? 600 : 500, fontFamily: 'inherit',
                  cursor: 'pointer', marginBottom: -1,
                }}
              >
                {t.label} <span style={{ color: 'var(--text-3)', fontWeight: 400 }}>({t.count})</span>
              </button>
            )
          })}
        </div>
      )}

      {q && !loading && total === 0 && (
        <div style={{ textAlign: 'center', padding: '60px 24px', color: 'var(--text-3)' }}>
          <SearchIcon size={32} style={{ opacity: 0.4, marginBottom: 12 }} />
          <div style={{ fontSize: 15, color: 'var(--text-2)', marginBottom: 8 }}>
            No matches for "{q}"
          </div>
          <div style={{ fontSize: 13 }}>
            Try a shorter query, or check spelling.
          </div>
        </div>
      )}

      {visible.articles.length > 0 && (
        <ResultSection icon={<Inbox size={14} />} label="Articles" count={visible.articles.length}>
          {visible.articles.slice(0, 50).map(a => (
            <Link
              key={a.id}
              to={`/articles/${a.id}`}
              style={resultRowStyle}
              onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = 'var(--bg-2)' }}
              onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = 'transparent' }}
            >
              <div style={{ fontSize: 14, color: 'var(--text-1)', fontWeight: 500, marginBottom: 4 }}>
                {a.title || 'Untitled'}
              </div>
              <div style={{ fontSize: 12, color: 'var(--text-3)' }}>
                {a.source_name} · {formatArticleDate(a.published_at) || 'unknown date'}
              </div>
              {a.summary && (
                <div style={{ fontSize: 13, color: 'var(--text-2)', marginTop: 6, lineHeight: 1.4 }}>
                  {a.summary.slice(0, 200)}{a.summary.length > 200 ? '…' : ''}
                </div>
              )}
            </Link>
          ))}
          {visible.articles.length > 50 && (
            <div style={{ padding: '12px 16px', fontSize: 12, color: 'var(--text-3)', fontStyle: 'italic' }}>
              + {visible.articles.length - 50} more
            </div>
          )}
        </ResultSection>
      )}

      {visible.frames.length > 0 && (
        <ResultSection icon={<Layers size={14} />} label="Narratives" count={visible.frames.length}>
          {visible.frames.map(f => (
            <Link
              key={f.id}
              to={`/narratives/${f.id}`}
              style={resultRowStyle}
              onMouseEnter={e => { (e.currentTarget as HTMLElement).style.background = 'var(--bg-2)' }}
              onMouseLeave={e => { (e.currentTarget as HTMLElement).style.background = 'transparent' }}
            >
              <div style={{ fontSize: 14, color: 'var(--text-1)', fontWeight: 500 }}>
                {f.name}
              </div>
              {f.description && (
                <div style={{ fontSize: 12, color: 'var(--text-2)', marginTop: 4 }}>
                  {f.description.slice(0, 160)}
                </div>
              )}
            </Link>
          ))}
        </ResultSection>
      )}

      {visible.entities.length > 0 && (
        <ResultSection icon={<Sparkles size={14} />} label="Entities" count={visible.entities.length}>
          {visible.entities.map(e => {
            const Icon = TYPE_ICONS[e.type]
            return (
              <Link
                key={e.id}
                to={`/entity-network?focus=${e.id}`}
                style={resultRowStyle}
                onMouseEnter={ev => { (ev.currentTarget as HTMLElement).style.background = 'var(--bg-2)' }}
                onMouseLeave={ev => { (ev.currentTarget as HTMLElement).style.background = 'transparent' }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <Icon size={14} style={{ color: 'var(--text-3)' }} />
                  <span style={{ fontSize: 14, color: 'var(--text-1)', fontWeight: 500 }}>
                    {e.name}
                  </span>
                  <span style={{ fontSize: 11, color: 'var(--text-3)', textTransform: 'uppercase', fontWeight: 600 }}>
                    {e.type}
                  </span>
                </div>
                <div style={{ fontSize: 13, color: 'var(--text-2)', marginTop: 4 }}>
                  {e.description}
                </div>
              </Link>
            )
          })}
        </ResultSection>
      )}
    </div>
  )
}

const resultRowStyle: React.CSSProperties = {
  display: 'block',
  padding: '14px 16px',
  borderRadius: 8,
  textDecoration: 'none',
  marginBottom: 6,
  transition: 'background 0.08s ease',
}

function ResultSection({ icon, label, count, children }: { icon: React.ReactNode; label: string; count: number; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 32 }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8,
        fontSize: 11, color: 'var(--text-3)', textTransform: 'uppercase',
        fontWeight: 700, letterSpacing: '0.08em',
      }}>
        {icon} {label} <span style={{ color: 'var(--text-3)', fontWeight: 500 }}>({count})</span>
      </div>
      <div>{children}</div>
    </div>
  )
}
