import { useEffect, useMemo, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import { Link } from 'react-router-dom'
import { api } from '@/api/client'
import { useAuth } from '@/auth/AuthContext'
import type { SourceItem } from '@/api/types'
import { formatArticleDate } from '@/lib/formatDate'

const C = {
  bg1: 'var(--bg-1)', bg2: 'var(--bg-2)', bg3: 'var(--bg-3)',
  border: 'var(--border)', borderBright: 'var(--border-bright)',
  text1: 'var(--text-1)', text2: 'var(--text-2)', text3: 'var(--text-3)',
  accent: 'var(--accent)',
}

// Relevance is shown as a coarse bucket badge (critical/high/medium/low),
// not a 0–100 number — see comment in Dashboard.tsx for rationale.
const REL_BADGE_STYLE: Record<string, { color: string; bg: string; border: string }> = {
  critical: { color: '#f87171', bg: 'rgba(215,25,19,0.08)', border: 'rgba(215,25,19,0.25)' },
  high: { color: '#fb923c', bg: 'rgba(234,88,12,0.08)', border: 'rgba(234,88,12,0.25)' },
  medium: { color: '#fbbf24', bg: 'rgba(202,138,4,0.08)', border: 'rgba(202,138,4,0.25)' },
  low: { color: '#a1a1a1', bg: 'rgba(161,161,161,0.08)', border: 'rgba(161,161,161,0.2)' },
  irrelevant: { color: '#555', bg: 'rgba(85,85,85,0.08)', border: 'rgba(85,85,85,0.2)' },
}

const RELEVANCE_ORDER: Record<string, number> = {
  critical: 4, high: 3, medium: 2, low: 1, irrelevant: 0,
}

type SortKey =
  | 'newest'
  | 'oldest'
  | 'most_relevant'
  | 'source_az'
  | 'most_duplicated'

const SORT_OPTIONS: { value: SortKey; label: string }[] = [
  { value: 'newest', label: 'Newest first' },
  { value: 'oldest', label: 'Oldest first' },
  { value: 'most_relevant', label: 'Most relevant' },
  { value: 'source_az', label: 'Source A → Z' },
  { value: 'most_duplicated', label: 'Most duplicated' },
]

type DateRange = 'all' | 'today' | '3d' | '7d'

const DATE_OPTIONS: { value: DateRange; label: string; days: number | null }[] = [
  { value: 'all', label: 'Last 7 days', days: null },
  { value: 'today', label: 'Today', days: 1 },
  { value: '3d', label: 'Last 3 days', days: 3 },
  { value: '7d', label: 'Last 7 days', days: 7 },
]

const SENTIMENT_OPTIONS = ['positive', 'neutral', 'negative', 'mixed']

function ArticleListRow({ item, isAdmin }: { item: SourceItem; isAdmin: boolean }) {
  // Non-admins see no relevance bucket — the column is dropped from the
  // grid entirely so the title gets the extra space rather than leaving
  // an awkward gap.
  const relStyle = isAdmin
    ? (REL_BADGE_STYLE[item.race_relevance_label ?? ''] ?? null)
    : null
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
          display: 'grid',
          gridTemplateColumns: isAdmin ? '1fr 90px 100px' : '1fr 100px',
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
        {isAdmin && (
          <div style={{ textAlign: 'right', display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4 }}>
            {relStyle && item.race_relevance_label ? (
              <span style={{
                fontSize: 10, fontWeight: 700, letterSpacing: '0.07em',
                color: relStyle.color, background: relStyle.bg,
                border: `1px solid ${relStyle.border}`,
                padding: '2px 7px', borderRadius: 4,
              }}>
                {item.race_relevance_label.toUpperCase()}
              </span>
            ) : (
              <span style={{ fontSize: 10, color: C.text3 }}>—</span>
            )}
            <div style={{ fontSize: 10, color: C.text3, letterSpacing: '0.05em', textTransform: 'uppercase' }}>
              Relevance
            </div>
          </div>
        )}
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
 * Multi-select dropdown styled to match the toolbar. Closes on outside click.
 * Uses Set<string> for the selected values to keep parent state simple.
 */
function FilterDropdown({
  label, options, selected, onChange, formatLabel,
}: {
  label: string
  options: string[]
  selected: Set<string>
  onChange: (next: Set<string>) => void
  formatLabel?: (value: string) => string
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDocClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [open])

  const toggle = (value: string) => {
    const next = new Set(selected)
    if (next.has(value)) next.delete(value)
    else next.add(value)
    onChange(next)
  }

  const count = selected.size
  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 6,
          padding: '7px 12px', borderRadius: 8,
          background: count > 0 ? C.bg3 : C.bg2,
          color: count > 0 ? C.text1 : C.text2,
          border: `1px solid ${count > 0 ? C.borderBright : C.border}`,
          fontSize: 13, fontFamily: 'inherit', cursor: 'pointer',
          whiteSpace: 'nowrap',
        }}
      >
        {label}
        {count > 0 && (
          <span style={{
            background: C.accent, color: '#000',
            fontSize: 10, fontWeight: 700, borderRadius: 999,
            padding: '1px 6px', minWidth: 16, textAlign: 'center',
          }}>{count}</span>
        )}
        <span style={{ fontSize: 9, color: C.text3 }}>▼</span>
      </button>
      {open && (
        <div style={{
          position: 'absolute', top: 'calc(100% + 4px)', left: 0, zIndex: 20,
          background: C.bg2, border: `1px solid ${C.borderBright}`,
          borderRadius: 8, padding: 4, minWidth: 200, maxHeight: 320,
          overflowY: 'auto',
          boxShadow: '0 8px 24px rgba(0,0,0,0.4)',
        }}>
          {options.length === 0 ? (
            <div style={{ padding: '8px 10px', fontSize: 12, color: C.text3 }}>
              No options
            </div>
          ) : options.map(opt => {
            const isSelected = selected.has(opt)
            return (
              <button
                key={opt}
                type="button"
                onClick={() => toggle(opt)}
                style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  width: '100%', padding: '6px 10px',
                  background: isSelected ? C.bg3 : 'transparent',
                  border: 'none', borderRadius: 6,
                  color: C.text1, fontSize: 13, fontFamily: 'inherit',
                  cursor: 'pointer', textAlign: 'left',
                }}
              >
                <span style={{
                  width: 14, height: 14, flexShrink: 0,
                  borderRadius: 3,
                  border: `1px solid ${isSelected ? C.accent : C.border}`,
                  background: isSelected ? C.accent : 'transparent',
                  display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: 10, color: '#000', fontWeight: 900,
                }}>{isSelected ? '✓' : ''}</span>
                {formatLabel ? formatLabel(opt) : opt}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}

/**
 * Single-select dropdown (used for sort + date). Same visual style as
 * FilterDropdown so the toolbar reads as one row.
 */
function SingleSelect<T extends string>({
  label, value, options, onChange,
}: {
  label: string
  value: T
  options: { value: T; label: string }[]
  onChange: (value: T) => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const current = options.find(o => o.value === value)

  useEffect(() => {
    if (!open) return
    const onDocClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [open])

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 6,
          padding: '7px 12px', borderRadius: 8,
          background: C.bg2, color: C.text2,
          border: `1px solid ${C.border}`,
          fontSize: 13, fontFamily: 'inherit', cursor: 'pointer',
          whiteSpace: 'nowrap',
        }}
      >
        <span style={{ color: C.text3 }}>{label}:</span>
        <span style={{ color: C.text1 }}>{current?.label ?? value}</span>
        <span style={{ fontSize: 9, color: C.text3 }}>▼</span>
      </button>
      {open && (
        <div style={{
          position: 'absolute', top: 'calc(100% + 4px)', left: 0, zIndex: 20,
          background: C.bg2, border: `1px solid ${C.borderBright}`,
          borderRadius: 8, padding: 4, minWidth: 180,
          boxShadow: '0 8px 24px rgba(0,0,0,0.4)',
        }}>
          {options.map(opt => (
            <button
              key={opt.value}
              type="button"
              onClick={() => { onChange(opt.value); setOpen(false) }}
              style={{
                display: 'block', width: '100%', padding: '6px 10px',
                background: opt.value === value ? C.bg3 : 'transparent',
                border: 'none', borderRadius: 6,
                color: C.text1, fontSize: 13, fontFamily: 'inherit',
                cursor: 'pointer', textAlign: 'left',
              }}
            >
              {opt.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function FilterChip({ label, onRemove }: { label: string; onRemove: () => void }) {
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 6,
      padding: '3px 4px 3px 10px', borderRadius: 999,
      background: C.bg3, border: `1px solid ${C.border}`,
      fontSize: 11, color: C.text2,
    }}>
      {label}
      <button
        type="button"
        onClick={onRemove}
        aria-label={`Remove ${label}`}
        style={{
          width: 16, height: 16, borderRadius: 999,
          background: 'transparent', border: 'none',
          color: C.text3, cursor: 'pointer', fontSize: 14,
          display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
          padding: 0,
        }}
      >×</button>
    </span>
  )
}

/**
 * Articles list page — confirmed-relevant feed with client-side sort,
 * multi-filter, and keyword search over the most recent ~200 articles.
 *
 * All filtering is client-side: the backend `/articles/recent` endpoint
 * stays single-purpose (reviewed + race-relevant + last 7 days), and we
 * fetch a generous batch and narrow in the browser for instant feedback.
 */
export function Articles() {
  const { user } = useAuth()
  const isAdmin = !!user?.isAdmin
  const [items, setItems] = useState<SourceItem[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [limit, setLimit] = useState(200)

  // Filter + sort state
  const [sort, setSort] = useState<SortKey>('newest')
  const [dateRange, setDateRange] = useState<DateRange>('all')
  const [search, setSearch] = useState('')
  const [sources, setSources] = useState<Set<string>>(new Set())
  const [relevance, setRelevance] = useState<Set<string>>(new Set())
  const [sentiments, setSentiments] = useState<Set<string>>(new Set())
  const [sourceTypes, setSourceTypes] = useState<Set<string>>(new Set())
  const [frames, setFrames] = useState<Set<string>>(new Set())

  useEffect(() => {
    let cancelled = false
    setLoading(true); setError(null)
    api.recentArticles(limit)
      .then(d => { if (!cancelled) setItems(d) })
      .catch(e => { if (!cancelled) setError(e.message || String(e)) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [limit])

  // Derive filter dropdown options from what we actually have, so we don't
  // show a "Reddit" filter when there are no Reddit articles in scope.
  const sourceOptions = useMemo(() => {
    const set = new Set<string>()
    for (const it of items) if (it.source_name) set.add(it.source_name)
    return Array.from(set).sort((a, b) => a.localeCompare(b))
  }, [items])

  const sourceTypeOptions = useMemo(() => {
    const set = new Set<string>()
    for (const it of items) if (it.source_type) set.add(it.source_type)
    return Array.from(set).sort()
  }, [items])

  const sentimentOptions = useMemo(() => {
    const set = new Set<string>()
    for (const it of items) if (it.sentiment) set.add(it.sentiment)
    // Always offer the canonical four — but only if we have data backing them.
    return SENTIMENT_OPTIONS.filter(s => set.has(s))
  }, [items])

  const relevanceOptions = useMemo(() => {
    const set = new Set<string>()
    for (const it of items) if (it.race_relevance_label) set.add(it.race_relevance_label)
    // Display in severity order.
    return ['critical', 'high', 'medium', 'low', 'irrelevant'].filter(r => set.has(r))
  }, [items])

  const frameOptions = useMemo(() => {
    const set = new Set<string>()
    for (const it of items) for (const f of it.frames ?? []) set.add(f.name)
    return Array.from(set).sort((a, b) => a.localeCompare(b))
  }, [items])

  const filtered = useMemo(() => {
    const dateOption = DATE_OPTIONS.find(d => d.value === dateRange)
    const cutoffMs = dateOption?.days != null
      ? Date.now() - dateOption.days * 24 * 60 * 60 * 1000
      : null
    const q = search.trim().toLowerCase()

    const out = items.filter(it => {
      if (sources.size > 0 && !(it.source_name && sources.has(it.source_name))) return false
      if (sourceTypes.size > 0 && !(it.source_type && sourceTypes.has(it.source_type))) return false
      if (sentiments.size > 0 && !(it.sentiment && sentiments.has(it.sentiment))) return false
      if (relevance.size > 0 && !(it.race_relevance_label && relevance.has(it.race_relevance_label))) return false
      if (frames.size > 0) {
        const names = it.frames?.map(f => f.name) ?? []
        if (!names.some(n => frames.has(n))) return false
      }
      if (cutoffMs != null) {
        const ts = it.published_at ?? it.created_at
        const t = ts ? new Date(ts).getTime() : 0
        if (!t || t < cutoffMs) return false
      }
      if (q) {
        const hay = `${it.title ?? ''} ${it.summary ?? ''}`.toLowerCase()
        if (!hay.includes(q)) return false
      }
      return true
    })

    const ts = (it: SourceItem) => {
      const v = it.published_at ?? it.created_at
      return v ? new Date(v).getTime() : 0
    }

    switch (sort) {
      case 'newest': out.sort((a, b) => ts(b) - ts(a)); break
      case 'oldest': out.sort((a, b) => ts(a) - ts(b)); break
      case 'most_relevant':
        out.sort((a, b) => {
          const ra = RELEVANCE_ORDER[a.race_relevance_label ?? ''] ?? -1
          const rb = RELEVANCE_ORDER[b.race_relevance_label ?? ''] ?? -1
          if (ra !== rb) return rb - ra
          return ts(b) - ts(a)
        })
        break
      case 'source_az':
        out.sort((a, b) => (a.source_name ?? '').localeCompare(b.source_name ?? '') || ts(b) - ts(a))
        break
      case 'most_duplicated':
        out.sort((a, b) => (b.duplicates?.length ?? 0) - (a.duplicates?.length ?? 0) || ts(b) - ts(a))
        break
    }
    return out
  }, [items, sources, sourceTypes, sentiments, relevance, frames, dateRange, search, sort])

  const removeFromSet = (set: Set<string>, setter: (s: Set<string>) => void, value: string) => {
    const next = new Set(set)
    next.delete(value)
    setter(next)
  }

  const clearAll = () => {
    setSources(new Set()); setRelevance(new Set())
    setSentiments(new Set()); setSourceTypes(new Set())
    setFrames(new Set())
    setDateRange('all'); setSearch('')
  }

  const activeFilterCount =
    sources.size + relevance.size + sentiments.size + sourceTypes.size + frames.size
    + (dateRange !== 'all' ? 1 : 0) + (search.trim() ? 1 : 0)

  return (
    <div style={{ padding: '24px 28px' }}>
      <div style={{ marginBottom: 18 }}>
        <h1 style={{
          fontSize: 24, fontWeight: 800, margin: 0,
          color: C.text1, letterSpacing: '-0.01em',
          display: 'inline-flex', alignItems: 'center',
        }}>
          Articles
        </h1>
        <div style={{ fontSize: 12, color: C.text3, marginTop: 4 }}>
          {loading
            ? 'Loading…'
            : activeFilterCount > 0
              ? `${filtered.length} of ${items.length} articles`
              : `${items.length} confirmed-relevant articles`}
        </div>
      </div>

      {/* Toolbar */}
      <div style={{ marginBottom: 12 }}>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <input
            type="text"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Search title or summary…"
            className="input"
            style={{ flex: '1 1 240px', minWidth: 200, fontSize: 13, padding: '7px 12px' }}
          />
          <SingleSelect
            label="Sort"
            value={sort}
            options={SORT_OPTIONS}
            onChange={setSort}
          />
          <SingleSelect
            label="Date"
            value={dateRange}
            options={DATE_OPTIONS.map(d => ({ value: d.value, label: d.label }))}
            onChange={setDateRange}
          />
          <FilterDropdown
            label="Source"
            options={sourceOptions}
            selected={sources}
            onChange={setSources}
          />
          {isAdmin && relevanceOptions.length > 0 && (
            <FilterDropdown
              label="Relevance"
              options={relevanceOptions}
              selected={relevance}
              onChange={setRelevance}
              formatLabel={v => v.charAt(0).toUpperCase() + v.slice(1)}
            />
          )}
          {sentimentOptions.length > 0 && (
            <FilterDropdown
              label="Sentiment"
              options={sentimentOptions}
              selected={sentiments}
              onChange={setSentiments}
              formatLabel={v => v.charAt(0).toUpperCase() + v.slice(1)}
            />
          )}
          {sourceTypeOptions.length > 0 && (
            <FilterDropdown
              label="Type"
              options={sourceTypeOptions}
              selected={sourceTypes}
              onChange={setSourceTypes}
              formatLabel={v => v.toUpperCase()}
            />
          )}
          {frameOptions.length > 0 && (
            <FilterDropdown
              label="Frame"
              options={frameOptions}
              selected={frames}
              onChange={setFrames}
            />
          )}
        </div>
        {activeFilterCount > 0 && (
          <div style={{
            display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center',
            marginTop: 10,
          }}>
            {search.trim() && (
              <FilterChip label={`"${search.trim()}"`} onRemove={() => setSearch('')} />
            )}
            {dateRange !== 'all' && (
              <FilterChip
                label={DATE_OPTIONS.find(d => d.value === dateRange)?.label ?? dateRange}
                onRemove={() => setDateRange('all')}
              />
            )}
            {Array.from(sources).map(s => (
              <FilterChip key={`src-${s}`} label={s} onRemove={() => removeFromSet(sources, setSources, s)} />
            ))}
            {Array.from(relevance).map(s => (
              <FilterChip key={`rel-${s}`} label={s.charAt(0).toUpperCase() + s.slice(1)} onRemove={() => removeFromSet(relevance, setRelevance, s)} />
            ))}
            {Array.from(sentiments).map(s => (
              <FilterChip key={`sen-${s}`} label={s.charAt(0).toUpperCase() + s.slice(1)} onRemove={() => removeFromSet(sentiments, setSentiments, s)} />
            ))}
            {Array.from(sourceTypes).map(s => (
              <FilterChip key={`type-${s}`} label={s.toUpperCase()} onRemove={() => removeFromSet(sourceTypes, setSourceTypes, s)} />
            ))}
            {Array.from(frames).map(s => (
              <FilterChip key={`frame-${s}`} label={s} onRemove={() => removeFromSet(frames, setFrames, s)} />
            ))}
            <button
              type="button"
              onClick={clearAll}
              style={{
                background: 'transparent', border: 'none',
                color: C.text3, fontSize: 11, fontFamily: 'inherit',
                cursor: 'pointer', padding: '3px 6px',
                textDecoration: 'underline',
              }}
            >Clear all</button>
          </div>
        )}
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
      ) : filtered.length > 0 ? (
        <>
          {filtered.map(item => <ArticleListRow key={item.id} item={item} isAdmin={isAdmin} />)}
          {items.length >= limit && (
            <button
              type="button"
              onClick={() => setLimit(l => l + 200)}
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
      ) : activeFilterCount > 0 ? (
        <div style={{
          padding: '60px 20px', textAlign: 'center',
          color: C.text3, fontSize: 14,
        }}>
          No articles match your filters.
          <div style={{ marginTop: 10 }}>
            <button
              type="button"
              onClick={clearAll}
              style={{
                background: 'transparent', border: `1px solid ${C.border}`,
                color: C.text2, fontSize: 12, fontFamily: 'inherit',
                padding: '6px 12px', borderRadius: 6, cursor: 'pointer',
              }}
            >Clear all filters</button>
          </div>
        </div>
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
