/**
 * Universal search bar — global header.
 *
 * Empty/focused state: shows trending narrative spikes (real signal).
 * Typing state: live results from the FTS5 backend (debounced 300ms),
 *   plus client-side narrative frame matches.
 * Enter: full results page.
 */
import { Search, TrendingUp, Layers, Inbox, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '@/api/client'
import type { NarrativeFrame, SourceItem, Spike } from '@/api/types'

export function SearchBar() {
  const [q, setQ] = useState('')
  const [focused, setFocused] = useState(false)
  const [frames, setFrames] = useState<NarrativeFrame[]>([])
  const [spikes, setSpikes] = useState<Spike[]>([])
  const [liveArticles, setLiveArticles] = useState<SourceItem[]>([])
  const [searching, setSearching] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)
  const dropdownRef = useRef<HTMLDivElement>(null)
  const navigate = useNavigate()

  // Load narrative frames + spikes once on mount (small datasets, fast)
  useEffect(() => {
    api.narrativeFrames().then(setFrames).catch(() => {})
    api.spikes().then(setSpikes).catch(() => {})
  }, [])

  // Debounced live search — calls real FTS5 backend 300ms after typing stops
  useEffect(() => {
    const term = q.trim()
    if (!term) {
      setLiveArticles([])
      return
    }
    setSearching(true)
    const timer = setTimeout(() => {
      api.search(term, 5)
        .then(setLiveArticles)
        .catch(() => setLiveArticles([]))
        .finally(() => setSearching(false))
    }, 300)
    return () => clearTimeout(timer)
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
  const matchFrames = term
    ? frames.filter(f => f.name.toLowerCase().includes(term.toLowerCase())).slice(0, 3)
    : []

  function submit() {
    if (!term) return
    navigate(`/search?q=${encodeURIComponent(term)}`)
    setFocused(false)
  }

  const showDropdown = focused
  const hasTypingResults = liveArticles.length > 0 || matchFrames.length > 0

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
          placeholder="Search articles, narratives…"
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
            // ── Empty state: trending spikes only ──────────────────────
            spikes.length > 0 ? (
              <Section label="Trending now" icon={<TrendingUp size={11} />}>
                {spikes.slice(0, 4).map(s => (
                  <ResultRow
                    key={s.frame_id}
                    primary={s.frame_name}
                    secondary={`${s.ratio.toFixed(1)}× normal · ${s.reach_24h.toLocaleString()} reach`}
                    onClick={() => { navigate(`/narratives/${s.frame_id}`); setFocused(false) }}
                  />
                ))}
              </Section>
            ) : (
              <div style={{ padding: '16px', fontSize: 12, color: 'var(--text-3)', textAlign: 'center' }}>
                Type to search across all articles
              </div>
            )
          ) : (
            // ── Typing state: live FTS5 + narrative matches ─────────────
            <>
              {searching && !hasTypingResults && (
                <div style={{ padding: '16px', fontSize: 12, color: 'var(--text-3)', textAlign: 'center' }}>
                  Searching…
                </div>
              )}

              {liveArticles.length > 0 && (
                <Section label="Articles" icon={<Inbox size={11} />}>
                  {liveArticles.map(a => (
                    <ResultRow
                      key={a.id}
                      primary={a.title || 'Untitled'}
                      secondary={a.source_name || ''}
                      onClick={() => { navigate(`/articles/${a.id}`); setFocused(false) }}
                    />
                  ))}
                </Section>
              )}

              {matchFrames.length > 0 && (
                <Section label="Narratives" icon={<Layers size={11} />}>
                  {matchFrames.map(f => (
                    <ResultRow
                      key={f.id}
                      primary={f.name}
                      secondary={f.owner_type ?? ''}
                      onClick={() => { navigate(`/narratives/${f.id}`); setFocused(false) }}
                    />
                  ))}
                </Section>
              )}

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

function Section({ label, icon, children }: { label: string; icon: React.ReactNode; children: React.ReactNode }) {
  return (
    <div style={{ borderBottom: '1px solid var(--border)' }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 6,
        padding: '10px 16px 6px',
        fontSize: 10, color: 'var(--text-3)', textTransform: 'uppercase',
        fontWeight: 700, letterSpacing: '0.08em',
      }}>
        {icon}{label}
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
