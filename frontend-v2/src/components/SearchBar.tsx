/**
 * Universal search bar — global header.
 *
 * Empty/focused state shows three discovery sections:
 *   - Recent searches    (last 8 queries from localStorage)
 *   - Trending now       (narrative spikes ranked by ratio)
 *   - Try searching      (top entity / outlet / narrative / quote — live
 *                         from /api/search/suggestions, ranked by 7-day
 *                         activity, teaches what's searchable)
 *
 * Typing state: live results from 4 parallel backends (debounced 300ms):
 *   - Articles  (Postgres FTS via /api/search)
 *   - Quotes    (verbatim claim_records via /api/search/quotes)
 *   - Entities  (canonical people/orgs/bills via /api/search/entities)
 *   - Outlets   (publishers via /api/search/outlets)
 *   plus client-side narrative-frame matches (small, already in memory).
 *
 * Section order is smart-ranked: an exact entity-name match floats
 * Entities to the top; an exact outlet match floats Outlets up; otherwise
 * Articles lead because they're the densest signal day-to-day.
 *
 * Enter: full results page.
 */
import {
  Clock, Inbox, Layers, Newspaper, Quote as QuoteIcon,
  Search, Sparkles, TrendingUp, User, X,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '@/api/client'
import { getRecentSearches, pushRecentSearch, clearRecentSearches } from '@/lib/recentSearches'
import type {
  EntitySearchHit,
  NarrativeFrame,
  OutletSearchHit,
  QuoteSearchHit,
  SearchSuggestions,
  SourceItem,
  Spike,
} from '@/api/types'

export function SearchBar() {
  const [q, setQ] = useState('')
  const [focused, setFocused] = useState(false)
  const [frames, setFrames] = useState<NarrativeFrame[]>([])
  const [spikes, setSpikes] = useState<Spike[]>([])
  const [liveArticles, setLiveArticles] = useState<SourceItem[]>([])
  const [liveEntities, setLiveEntities] = useState<EntitySearchHit[]>([])
  const [liveQuotes, setLiveQuotes] = useState<QuoteSearchHit[]>([])
  const [liveOutlets, setLiveOutlets] = useState<OutletSearchHit[]>([])
  const [searching, setSearching] = useState(false)
  const [suggestions, setSuggestions] = useState<SearchSuggestions | null>(null)
  const [recent, setRecent] = useState<string[]>(() => getRecentSearches())
  const inputRef = useRef<HTMLInputElement>(null)
  const dropdownRef = useRef<HTMLDivElement>(null)
  const navigate = useNavigate()

  // Load narrative frames + spikes + empty-state suggestions once on mount.
  useEffect(() => {
    api.narrativeFrames().then(setFrames).catch(() => {})
    api.spikes().then(setSpikes).catch(() => {})
    api.searchSuggestions(3).then(setSuggestions).catch(() => {})
  }, [])

  // Sync the in-memory recent-searches list with localStorage events.
  // Same tab dispatches `noctua:recent-searches-changed` after a push;
  // other tabs use the native `storage` event for cross-tab sync.
  useEffect(() => {
    function refresh() { setRecent(getRecentSearches()) }
    window.addEventListener('noctua:recent-searches-changed', refresh)
    window.addEventListener('storage', refresh)
    return () => {
      window.removeEventListener('noctua:recent-searches-changed', refresh)
      window.removeEventListener('storage', refresh)
    }
  }, [])

  // Debounced live search — fires all 4 backend lookups in parallel so
  // one slow query can't stall the others. We resolve them together so
  // the dropdown lights up in one paint, not four.
  useEffect(() => {
    const term = q.trim()
    if (!term) {
      setLiveArticles([])
      setLiveEntities([])
      setLiveQuotes([])
      setLiveOutlets([])
      return
    }
    let cancelled = false
    setSearching(true)
    const timer = setTimeout(() => {
      Promise.allSettled([
        api.search(term, 5),
        api.searchEntities(term, 5),
        api.searchQuotes(term, 5),
        api.searchOutlets(term, 5),
      ]).then(([articles, entities, quotes, outlets]) => {
        // A superseded keystroke's batch can resolve out of order; the
        // cleanup flag keeps it from overwriting the latest results.
        if (cancelled) return
        setLiveArticles(articles.status === 'fulfilled' ? articles.value : [])
        setLiveEntities(entities.status === 'fulfilled' ? entities.value : [])
        setLiveQuotes(quotes.status === 'fulfilled' ? quotes.value : [])
        setLiveOutlets(outlets.status === 'fulfilled' ? outlets.value : [])
      }).finally(() => { if (!cancelled) setSearching(false) })
    }, 300)
    return () => { cancelled = true; clearTimeout(timer) }
  }, [q])

  // Close dropdown on outside click
  useEffect(() => {
    if (!focused) return
    function onDown(e: MouseEvent) {
      if (
        !dropdownRef.current?.contains(e.target as Node) &&
        !inputRef.current?.contains(e.target as Node)
      ) {
        setFocused(false)
      }
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [focused])

  // Cmd/Ctrl+K shortcut
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        inputRef.current?.focus()
        setFocused(true)
      }
      if (e.key === 'Escape' && focused) {
        setFocused(false)
        inputRef.current?.blur()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [focused])

  const term = q.trim()
  const termLower = term.toLowerCase()
  const matchFrames = term
    ? frames.filter(f => f.name.toLowerCase().includes(termLower)).slice(0, 3)
    : []

  // Smart section ranking. Default order leads with Articles because that's
  // the densest signal, but if the query exact-matches a person/org or an
  // outlet, the user almost certainly wants THAT entity — float it up.
  const sectionOrder = useMemo<Array<'entities' | 'outlets' | 'articles' | 'quotes' | 'narratives'>>(() => {
    if (!term) return []
    const entityExact = liveEntities.some(e => e.name.toLowerCase() === termLower)
    const outletExact = liveOutlets.some(o =>
      o.name.toLowerCase() === termLower || o.domain.toLowerCase() === termLower
    )
    if (entityExact) return ['entities', 'articles', 'quotes', 'narratives', 'outlets']
    if (outletExact) return ['outlets', 'articles', 'quotes', 'narratives', 'entities']
    return ['articles', 'quotes', 'narratives', 'entities', 'outlets']
  }, [term, termLower, liveEntities, liveOutlets])

  function submit() {
    if (!term) return
    pushRecentSearch(term)
    navigate(`/search?q=${encodeURIComponent(term)}`)
    setFocused(false)
  }

  function close() { setFocused(false) }

  // Used by every dropdown row that lands on a search results page —
  // records the suggestion/term in recent before navigating so the user
  // can re-find it from the empty state next time.
  function goToSearch(termToRecord: string) {
    pushRecentSearch(termToRecord)
    navigate(`/search?q=${encodeURIComponent(termToRecord)}`)
    close()
  }

  const showDropdown = focused
  const hasTypingResults =
    liveArticles.length > 0 ||
    liveEntities.length > 0 ||
    liveQuotes.length > 0 ||
    liveOutlets.length > 0 ||
    matchFrames.length > 0

  // Render section by key — keeps the JSX below uncluttered and makes the
  // smart-ranking re-order a single map() call.
  function renderSection(key: typeof sectionOrder[number]) {
    if (key === 'articles' && liveArticles.length > 0) {
      return (
        <Section key="articles" label="Articles" icon={<Inbox size={11} />}>
          {liveArticles.map(a => (
            <ResultRow
              key={a.id}
              primary={a.title || 'Untitled'}
              secondary={a.source_name || ''}
              onClick={() => { navigate(`/articles/${a.id}`); close() }}
            />
          ))}
        </Section>
      )
    }
    if (key === 'entities' && liveEntities.length > 0) {
      return (
        <Section key="entities" label="People & organizations" icon={<User size={11} />}>
          {liveEntities.map(e => (
            <ResultRow
              key={e.id}
              primary={e.name}
              secondary={`${e.type}${e.affiliation ? ` · ${e.affiliation}` : ''} · ${e.source_count} article${e.source_count === 1 ? '' : 's'}`}
              onClick={() => goToSearch(e.name)}
            />
          ))}
        </Section>
      )
    }
    if (key === 'quotes' && liveQuotes.length > 0) {
      return (
        <Section key="quotes" label="Quotes" icon={<QuoteIcon size={11} />}>
          {liveQuotes.map(qt => (
            <ResultRow
              key={qt.id}
              primary={`"${truncate(qt.evidence_span, 140)}"`}
              secondary={`${qt.source_name}${qt.article_title ? ` · ${truncate(qt.article_title, 60)}` : ''}`}
              onClick={() => { navigate(`/articles/${qt.article_id}`); close() }}
            />
          ))}
        </Section>
      )
    }
    if (key === 'outlets' && liveOutlets.length > 0) {
      return (
        <Section key="outlets" label="Outlets" icon={<Newspaper size={11} />}>
          {liveOutlets.map(o => (
            <ResultRow
              key={o.id}
              primary={o.name}
              secondary={[o.outlet_type.replace(/_/g, ' '), [o.city, o.state].filter(Boolean).join(', ')].filter(Boolean).join(' · ') || o.domain}
              onClick={() => goToSearch(o.name)}
            />
          ))}
        </Section>
      )
    }
    if (key === 'narratives' && matchFrames.length > 0) {
      return (
        <Section key="narratives" label="Narratives" icon={<Layers size={11} />}>
          {matchFrames.map(f => (
            <ResultRow
              key={f.id}
              primary={f.name}
              secondary={f.owner_type ?? ''}
              onClick={() => { navigate(`/narratives/${f.id}`); close() }}
            />
          ))}
        </Section>
      )
    }
    return null
  }

  return (
    <div style={{ position: 'relative', flex: 1, maxWidth: 860, minWidth: 240 }}>
      {/* Input */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8,
        background: 'var(--bg-2)',
        border: '1px solid ' + (focused ? 'var(--accent)' : 'var(--border)'),
        borderRadius: 8,
        padding: '6px 12px',
        transition: 'border-color 0.1s ease',
      }}>
        <Search size={14} style={{ color: 'var(--text-3)', flexShrink: 0 }} />
        <input
          ref={inputRef}
          value={q}
          onChange={e => setQ(e.target.value)}
          onFocus={() => setFocused(true)}
          onKeyDown={e => { if (e.key === 'Enter') submit() }}
          placeholder="Search articles, quotes, people, outlets…"
          style={{
            background: 'transparent', border: 'none', outline: 'none',
            color: 'var(--text-1)', fontSize: 13, fontFamily: 'inherit',
            width: '100%',
          }}
        />
        {q && (
          <button
            onClick={() => { setQ(''); inputRef.current?.focus() }}
            style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-3)', padding: 0, display: 'flex' }}
            aria-label="Clear"
          ><X size={14} /></button>
        )}
        <span style={{
          fontSize: 10, color: 'var(--text-3)',
          border: '1px solid var(--border)', borderRadius: 4,
          padding: '1px 5px', fontFamily: 'monospace', flexShrink: 0,
        }}>⌘K</span>
      </div>

      {/* Dropdown */}
      {showDropdown && (
        <div
          ref={dropdownRef}
          style={{
            position: 'absolute', top: 'calc(100% + 6px)', left: 0, right: 0,
            background: 'var(--bg-2)',
            border: '1px solid var(--border)',
            borderRadius: 10,
            boxShadow: '0 10px 32px rgba(0,0,0,0.5)',
            maxHeight: '70vh', overflowY: 'auto',
            zIndex: 200,
          }}
        >
          {!term ? (
            // ── Empty state: discovery sections ────────────────────────
            <EmptyState
              recent={recent}
              spikes={spikes}
              suggestions={suggestions}
              onPickRecent={(t) => { setQ(t); inputRef.current?.focus() }}
              onClearRecent={() => { clearRecentSearches(); setRecent([]) }}
              onPickSpike={(frameId) => { navigate(`/narratives/${frameId}`); close() }}
              onPickEntity={(name) => goToSearch(name)}
              onPickOutlet={(name) => goToSearch(name)}
              onPickFrame={(frameId) => { navigate(`/narratives/${frameId}`); close() }}
              onPickQuote={(articleId) => { navigate(`/articles/${articleId}`); close() }}
            />
          ) : (
            // ── Typing state: smart-ranked sections ────────────────────
            <>
              {searching && !hasTypingResults && (
                <div style={{ padding: '16px', fontSize: 12, color: 'var(--text-3)', textAlign: 'center' }}>
                  Searching…
                </div>
              )}

              {sectionOrder.map(renderSection)}

              {!searching && !hasTypingResults && (
                <div style={{ padding: '16px', fontSize: 12, color: 'var(--text-3)', textAlign: 'center' }}>
                  No quick matches — press Enter to search full corpus
                </div>
              )}

              {hasTypingResults && (
                <button
                  onClick={submit}
                  style={{
                    display: 'block', width: '100%', textAlign: 'left',
                    padding: '10px 16px', background: 'transparent', border: 'none',
                    color: 'var(--accent)', fontSize: 12, fontWeight: 600,
                    cursor: 'pointer', borderTop: '1px solid var(--border)',
                  }}
                >
                  See all results for "{q}" →
                </button>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}

// ── Sub-components ─────────────────────────────────────────────────────────

function Section({ label, icon, children, action }: {
  label: string
  icon: React.ReactNode
  children: React.ReactNode
  action?: React.ReactNode  // optional trailing button (e.g. "Clear" on Recent)
}) {
  return (
    <div style={{ borderBottom: '1px solid var(--border)' }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 6,
        padding: '10px 16px 6px',
        fontSize: 10, color: 'var(--text-3)', textTransform: 'uppercase',
        fontWeight: 700, letterSpacing: '0.08em',
      }}>
        {icon}{label}
        {action && <div style={{ marginLeft: 'auto' }}>{action}</div>}
      </div>
      <div>{children}</div>
    </div>
  )
}

function ResultRow({ primary, secondary, onClick }: { primary: string; secondary: string; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      style={{
        display: 'flex', flexDirection: 'column', alignItems: 'flex-start',
        width: '100%', textAlign: 'left',
        padding: '8px 16px',
        background: 'transparent', border: 'none', cursor: 'pointer',
        fontFamily: 'inherit',
        transition: 'background 0.08s ease',
      }}
      onMouseEnter={e => { e.currentTarget.style.background = 'var(--bg-3)' }}
      onMouseLeave={e => { e.currentTarget.style.background = 'transparent' }}
    >
      <span style={{
        fontSize: 13, color: 'var(--text-1)', fontWeight: 500,
        whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
        maxWidth: '100%',
      }}>{primary}</span>
      {secondary && (
        <span style={{ fontSize: 11, color: 'var(--text-3)', marginTop: 1 }}>{secondary}</span>
      )}
    </button>
  )
}

function truncate(s: string, n: number): string {
  if (!s) return ''
  return s.length <= n ? s : s.slice(0, n - 1).trimEnd() + '…'
}

// Empty-state dropdown — three discovery sections. Hoisted out of the
// main component because the render logic is non-trivial and the inner
// component has no shared closure state worth keeping inline.
function EmptyState({
  recent, spikes, suggestions,
  onPickRecent, onClearRecent, onPickSpike,
  onPickEntity, onPickOutlet, onPickFrame, onPickQuote,
}: {
  recent: string[]
  spikes: Spike[]
  suggestions: SearchSuggestions | null
  onPickRecent: (term: string) => void
  onClearRecent: () => void
  onPickSpike: (frameId: number) => void
  onPickEntity: (name: string) => void
  onPickOutlet: (name: string) => void
  onPickFrame: (frameId: number) => void
  onPickQuote: (articleId: number) => void
}) {
  const hasRecent = recent.length > 0
  const hasSpikes = spikes.length > 0
  const hasSuggestions = !!suggestions && (
    suggestions.entities.length > 0 || suggestions.outlets.length > 0 ||
    suggestions.frames.length > 0 || suggestions.quotes.length > 0
  )

  // Fallback for the truly-empty case (fresh deployment, no data yet,
  // and the user has never searched).
  if (!hasRecent && !hasSpikes && !hasSuggestions) {
    return (
      <div style={{ padding: '16px', fontSize: 12, color: 'var(--text-3)', textAlign: 'center' }}>
        Type to search articles, quotes, people, outlets
      </div>
    )
  }

  return (
    <>
      {hasRecent && (
        <Section
          label="Recent searches"
          icon={<Clock size={11} />}
          action={(
            <button
              onClick={onClearRecent}
              style={{
                background: 'transparent', border: 'none', cursor: 'pointer',
                fontSize: 10, color: 'var(--text-3)', fontWeight: 600,
                textTransform: 'uppercase', letterSpacing: '0.08em',
                fontFamily: 'inherit',
              }}
            >Clear</button>
          )}
        >
          {recent.slice(0, 5).map(t => (
            <ResultRow
              key={t}
              primary={t}
              secondary=""
              onClick={() => onPickRecent(t)}
            />
          ))}
        </Section>
      )}

      {hasSpikes && (
        <Section label="Trending now" icon={<TrendingUp size={11} />}>
          {spikes.slice(0, 4).map(s => (
            <ResultRow
              key={s.frame_id}
              primary={s.frame_name}
              secondary={`${s.ratio.toFixed(1)}× normal · ${s.reach_24h.toLocaleString()} reach`}
              onClick={() => onPickSpike(s.frame_id)}
            />
          ))}
        </Section>
      )}

      {hasSuggestions && suggestions && (
        <Section label="Try searching" icon={<Sparkles size={11} />}>
          {suggestions.entities.slice(0, 1).map(e => (
            <ResultRow
              key={`e${e.id}`}
              primary={e.name}
              secondary={`${e.type}${e.affiliation ? ` · ${e.affiliation}` : ''} · ${e.mentions_this_week} article${e.mentions_this_week === 1 ? '' : 's'} this week`}
              onClick={() => onPickEntity(e.name)}
            />
          ))}
          {suggestions.outlets.slice(0, 1).map(o => (
            <ResultRow
              key={`o${o.id}`}
              primary={o.name}
              secondary={`outlet · ${o.articles_this_week} article${o.articles_this_week === 1 ? '' : 's'} this week`}
              onClick={() => onPickOutlet(o.name)}
            />
          ))}
          {suggestions.frames.slice(0, 1).map(f => (
            <ResultRow
              key={`f${f.id}`}
              primary={f.name}
              secondary={`narrative${f.owner_type ? ` · ${f.owner_type}` : ''} · ${f.mentions_this_week} mention${f.mentions_this_week === 1 ? '' : 's'} this week`}
              onClick={() => onPickFrame(f.id)}
            />
          ))}
          {suggestions.quotes.slice(0, 1).map(qt => (
            <ResultRow
              key={`q${qt.id}`}
              primary={`"${truncate(qt.evidence_span, 110)}"`}
              secondary={`quote · ${qt.source_name}`}
              onClick={() => onPickQuote(qt.article_id)}
            />
          ))}
        </Section>
      )}
    </>
  )
}
