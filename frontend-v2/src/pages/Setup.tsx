import { Bell, CheckCircle, ChevronDown, ChevronRight, Circle, Facebook, Globe, Instagram, Loader, Lock, MapPin, MessageSquare, RotateCcw, Search, Users, X, Youtube } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { api } from '@/api/client'
import { useAuth } from '@/auth/AuthContext'
import type {
  CampaignConfig,
  DiscoveredHandle,
  DiscoveredThirdPartyAccount,
  HandleDiscoveryResult,
  Opponent,
  RaceCandidate,
  RaceDirectory,
  SetupChecklistItem,
  SetupStatus,
  ThirdPartyDiscoveryResult,
  ThirdPartyPlatform,
  TrackedThirdPartyAccount,
} from '@/api/types'
import { NotificationSettings } from '@/components/NotificationSettings'

const PLATFORM_LABELS: Record<ThirdPartyPlatform, string> = {
  instagram: 'Instagram',
  facebook: 'Facebook',
  bluesky: 'Bluesky',
  reddit_subreddit: 'Reddit (subreddits)',
  reddit_user: 'Reddit (users)',
  youtube: 'YouTube',
}

// Display order: ingestable-today platforms first so the user's eye lands
// on the immediately-useful results. IG/FB at the end so their "paused"
// state is read in context.
const PLATFORM_ORDER: ThirdPartyPlatform[] = [
  'reddit_subreddit', 'reddit_user', 'bluesky', 'youtube', 'facebook', 'instagram',
]

const PLATFORM_ICONS: Record<ThirdPartyPlatform, typeof Globe> = {
  instagram: Instagram,
  facebook: Facebook,
  bluesky: MessageSquare,
  reddit_subreddit: Users,
  reddit_user: Users,
  youtube: Youtube,
}

// Platforms whose RSS ingestion is currently paused (see Phase 1.5 notes).
const PAUSED_PLATFORMS: ReadonlySet<ThirdPartyPlatform> = new Set(['instagram', 'facebook'])

/** Sticky top nav for the Setup page. Lets the user jump between the
 *  four major sections (campaign profile, notifications, social handles,
 *  third-party accounts) without scroll-hunting. Pill lights up via two
 *  signals: (1) URL hash for deep-links and the initial render, (2) an
 *  IntersectionObserver scrollspy that tracks the topmost section
 *  currently in view so scrolling between sections updates the pill
 *  automatically.
 */
function SetupSectionNav() {
  const SECTIONS: Array<{ id: string; label: string }> = [
    { id: 'campaign-profile',      label: 'Campaign profile' },
    { id: 'notifications',         label: 'Notifications' },
    { id: 'social-handles',        label: 'Social handles' },
    { id: 'third-party-accounts',  label: 'Other accounts' },
  ]
  // Initialize from URL hash, default to first section
  const initial = window.location.hash.slice(1) || SECTIONS[0].id
  const [active, setActive] = useState(initial)

  // Keep `active` in sync with hash changes (browser back, in-page anchor clicks)
  useEffect(() => {
    const onHash = () => setActive(window.location.hash.slice(1) || SECTIONS[0].id)
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Scrollspy. The rootMargin shifts the "in view" zone: -90px from the top
  // skips the sticky nav itself so the pill flips when a section's heading
  // crosses just below the nav, and -55% from the bottom keeps the trigger
  // zone narrow so two sections don't both claim "active" at once. Among
  // currently-intersecting sections, pick the topmost — that's the one the
  // user is reading.
  //
  // CRITICAL: Layout wraps the page in a flex column where <main> has its
  // own overflow-y scroll (the document never scrolls). Passing the <main>
  // element as `root` makes the observer track intersections inside that
  // scroller; with the default (viewport root) the callback would only
  // fire on initial mount because the document scroll position never
  // changes.
  //
  // Section divs are mounted by the Setup component, which loads data async,
  // so the observer setup waits for the elements to exist before attaching.
  useEffect(() => {
    let observer: IntersectionObserver | null = null
    const attach = () => {
      const targets = SECTIONS.map(s => document.getElementById(s.id)).filter(Boolean) as HTMLElement[]
      if (targets.length === 0) return false
      // Find the nearest scrolling ancestor — that's the IntersectionObserver
      // root we want. Looking up from a known section is more robust than
      // assuming a specific Layout structure. Only check overflow-y style,
      // NOT current scrollHeight vs clientHeight — at first mount content
      // may still be expanding and the scrollHeight check would reject the
      // real scroller, leaving the observer rooted at the viewport (which
      // doesn't actually scroll in this layout).
      const findScroller = (el: HTMLElement | null): HTMLElement | null => {
        while (el) {
          const oy = getComputedStyle(el).overflowY
          if (oy === 'auto' || oy === 'scroll') return el
          el = el.parentElement
        }
        return null
      }
      const root = findScroller(targets[0]) ?? null  // null = viewport, valid fallback
      observer = new IntersectionObserver(
        entries => {
          const visible = entries
            .filter(e => e.isIntersecting)
            .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)
          if (visible.length > 0) {
            setActive(visible[0].target.id)
          }
        },
        { root, rootMargin: '-90px 0px -55% 0px', threshold: 0 },
      )
      for (const el of targets) observer.observe(el)
      return true
    }
    // Try immediately; if sections aren't mounted yet, poll briefly via rAF
    // until they appear. Stops as soon as attach succeeds or after ~30 frames
    // (~500ms at 60fps) to avoid a runaway loop if the user navigates away.
    if (!attach()) {
      let tries = 0
      const tick = () => {
        if (tries++ > 30 || attach()) return
        requestAnimationFrame(tick)
      }
      requestAnimationFrame(tick)
    }
    return () => observer?.disconnect()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div style={{
      position: 'sticky', top: 0, zIndex: 10,
      background: 'var(--bg-1)',
      paddingTop: 12, paddingBottom: 12,
      marginBottom: 18,
      borderBottom: '1px solid var(--bg-3)',
      display: 'flex', flexWrap: 'wrap', gap: 6,
    }}>
      {SECTIONS.map(s => {
        const isOn = active === s.id
        return (
          <a
            key={s.id}
            href={`#${s.id}`}
            onClick={() => setActive(s.id)}
            style={{
              padding: '5px 14px', borderRadius: 999, fontSize: 12,
              background: isOn ? 'var(--accent)' : 'transparent',
              color: isOn ? 'var(--bg-1)' : 'var(--text-2)',
              border: `1px solid ${isOn ? 'var(--accent)' : 'var(--bg-3)'}`,
              cursor: 'pointer', textDecoration: 'none',
              fontWeight: isOn ? 600 : 500,
              letterSpacing: '0.02em',
              transition: 'background 0.12s ease, color 0.12s ease, border-color 0.12s ease',
            }}
          >
            {s.label}
          </a>
        )
      })}
    </div>
  )
}


/** Small toggle-pill used in the anchor filter row. */
function FilterChip({
  label, active, onClick, title,
}: {
  label: string
  active: boolean
  onClick: () => void
  title?: string
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={title}
      style={{
        padding: '3px 10px', borderRadius: 999, fontSize: 11,
        background: active ? 'var(--accent)' : 'transparent',
        color: active ? 'var(--bg-1)' : 'var(--text-2)',
        border: `1px solid ${active ? 'var(--accent)' : 'var(--bg-3)'}`,
        cursor: 'pointer', fontFamily: 'inherit',
        fontWeight: active ? 600 : 500,
        letterSpacing: '0.02em',
      }}
    >
      {label}
    </button>
  )
}

/**
 * Multi-handle row for one platform (IG or FB) on one actor. Renders the
 * currently-saved handles as removable chips, plus a "Discover" button
 * that surfaces ranked candidates with checkboxes — the user multi-selects
 * and clicks "Add selected" to extend the list. "Enter manually" handles
 * the case where discovery missed an account.
 *
 * The wire format is full-list replacement: we maintain the local list,
 * pass it as `instagram_handles` / `facebook_pages` to saveHandles, and
 * trust the backend to canonicalize (de-dup, strip @, trim).
 */
function HandleRow({
  platform, actor, currentHandles, location, onSave, canEdit = true,
}: {
  platform: 'instagram' | 'facebook'
  actor: { name: string; kind: 'candidate' | 'opponent'; opponent_id?: number }
  currentHandles: string[]
  location?: string
  onSave: (handles: string[]) => Promise<void>
  /** False = disable discover/save/remove buttons for read-only viewers. */
  canEdit?: boolean
}) {
  const [candidates, setCandidates] = useState<DiscoveredHandle[] | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [discovering, setDiscovering] = useState(false)
  const [discoverError, setDiscoverError] = useState<string | null>(null)
  const [manualValue, setManualValue] = useState('')
  const [showManual, setShowManual] = useState(false)
  const [saving, setSaving] = useState(false)

  const Icon = platform === 'instagram' ? Instagram : Facebook
  const accent = platform === 'instagram' ? '#e1306c' : '#1877f2'
  const baseUrl = platform === 'instagram' ? 'https://www.instagram.com/' : 'https://www.facebook.com/'
  const platformLabel = platform === 'instagram' ? 'Instagram' : 'Facebook'

  const discover = useCallback(async () => {
    setDiscovering(true); setDiscoverError(null); setSelected(new Set())
    try {
      const r: HandleDiscoveryResult = await api.discoverHandles(actor.name, location, 6)
      setCandidates(r[platform])
      if (r[platform].length === 0) {
        setDiscoverError('No candidates found. Try entering a handle manually.')
      }
    } catch (e: unknown) {
      setDiscoverError(e instanceof Error ? e.message : 'Discovery failed')
    } finally {
      setDiscovering(false)
    }
  }, [actor.name, location, platform])

  const saveList = useCallback(async (next: string[]) => {
    setSaving(true)
    try {
      await onSave(next)
    } finally {
      setSaving(false)
    }
  }, [onSave])

  const addSelected = useCallback(async () => {
    if (selected.size === 0) return
    const next = [...currentHandles]
    for (const h of selected) {
      if (!next.includes(h)) next.push(h)
    }
    await saveList(next)
    setSelected(new Set())
    setCandidates(null)
  }, [selected, currentHandles, saveList])

  const addManual = useCallback(async () => {
    const v = manualValue.trim().replace(/^@/, '')
    if (!v || currentHandles.includes(v)) return
    await saveList([...currentHandles, v])
    setManualValue('')
    setShowManual(false)
  }, [manualValue, currentHandles, saveList])

  const remove = useCallback(async (handle: string) => {
    await saveList(currentHandles.filter(h => h !== handle))
  }, [currentHandles, saveList])

  // Hide candidates that are already saved — once added they appear in
  // the chips above and don't need to be re-offered.
  const visibleCandidates = (candidates ?? []).filter(c => !currentHandles.includes(c.handle))

  return (
    <div style={{
      padding: '12px 14px',
      background: 'var(--bg-2)',
      border: '1px solid var(--bg-3)',
      borderRadius: 6,
      marginBottom: 8,
    }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
        <Icon size={16} style={{ color: accent, flexShrink: 0, marginTop: 2 }} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 12, color: 'var(--text-3)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
            {platformLabel}
            <span style={{ marginLeft: 8, color: 'var(--text-2)' }}>
              {currentHandles.length === 0 ? 'no handles set' : `${currentHandles.length} tracked`}
            </span>
          </div>
          {currentHandles.length > 0 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 6 }}>
              {currentHandles.map(h => (
                <span key={h} style={{
                  display: 'inline-flex', alignItems: 'center', gap: 6,
                  padding: '3px 4px 3px 10px',
                  background: 'var(--bg-1)',
                  border: '1px solid var(--bg-3)',
                  borderRadius: 999,
                  fontSize: 12,
                }}>
                  <a href={`${baseUrl}${h}`} target="_blank" rel="noreferrer"
                     style={{ color: 'var(--text-1)', textDecoration: 'none', fontWeight: 500 }}>
                    @{h}
                  </a>
                  <button type="button" onClick={() => remove(h)} disabled={!canEdit || saving}
                    style={{
                      background: 'transparent', border: 'none',
                      cursor: canEdit ? 'pointer' : 'not-allowed',
                      padding: 2, color: 'var(--text-3)', display: 'inline-flex',
                      opacity: canEdit ? 1 : 0.4,
                    }}
                    title={canEdit ? `Stop tracking @${h}` : 'Admin only'}
                  >
                    <X size={12} />
                  </button>
                </span>
              ))}
            </div>
          )}
        </div>
        <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
          <button type="button" onClick={discover} disabled={!canEdit || discovering || saving}
                  className="btn btn-secondary"
                  title={canEdit ? undefined : 'Admin only — discovery hits a paid web search'}
                  style={{ padding: '5px 10px', fontSize: 11, opacity: canEdit ? 1 : 0.55 }}>
            {discovering
              ? <><Loader size={11} style={{ animation: 'spin 1s linear infinite' }} /> Searching…</>
              : <><Search size={11} /> Discover</>
            }
          </button>
          <button type="button" onClick={() => setShowManual(v => !v)} disabled={!canEdit || saving}
                  className="btn btn-secondary"
                  title={canEdit ? undefined : 'Admin only'}
                  style={{ padding: '5px 10px', fontSize: 11, opacity: canEdit ? 1 : 0.55 }}>
            Enter manually
          </button>
        </div>
      </div>

      {showManual && (
        <div style={{ display: 'flex', gap: 8, marginTop: 10, alignItems: 'center' }}>
          <span style={{ fontSize: 12, color: 'var(--text-3)' }}>
            {baseUrl}
          </span>
          <input
            className="input"
            value={manualValue}
            onChange={e => setManualValue(e.target.value)}
            placeholder="handle"
            disabled={!canEdit}
            style={{ flex: 1, fontSize: 12, opacity: canEdit ? 1 : 0.55 }}
            onKeyDown={e => { if (e.key === 'Enter' && canEdit) addManual() }}
          />
          <button type="button" onClick={addManual}
                  disabled={!canEdit || saving || !manualValue.trim()}
                  className="btn btn-primary"
                  title={canEdit ? undefined : 'Admin only'}
                  style={{ padding: '6px 12px', fontSize: 12, opacity: canEdit ? 1 : 0.55 }}>
            Add
          </button>
        </div>
      )}

      {discoverError && (
        <div style={{ marginTop: 8, fontSize: 11, color: 'var(--text-3)' }}>
          {discoverError}
        </div>
      )}

      {visibleCandidates.length > 0 && (
        <div style={{ marginTop: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
            <div className="section-label">
              Candidates — pick which to track
            </div>
            <button type="button" onClick={addSelected}
                    disabled={!canEdit || saving || selected.size === 0}
                    className="btn btn-primary"
                    title={canEdit ? undefined : 'Admin only'}
                    style={{ padding: '5px 12px', fontSize: 11, opacity: canEdit ? 1 : 0.55 }}>
              Add {selected.size > 0 ? `${selected.size} selected` : ''}
            </button>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {visibleCandidates.map(c => {
              const isOn = selected.has(c.handle)
              return (
                <label
                  key={c.handle}
                  style={{
                    display: 'flex', alignItems: 'flex-start', gap: 10,
                    padding: '8px 10px',
                    background: isOn ? 'rgba(255,191,0,0.08)' : 'var(--bg-1)',
                    border: `1px solid ${isOn ? 'var(--accent)' : 'var(--bg-3)'}`,
                    borderRadius: 4,
                    fontSize: 12,
                    cursor: canEdit ? 'pointer' : 'not-allowed',
                    opacity: canEdit ? 1 : 0.6,
                  }}
                  title={canEdit ? undefined : 'Admin only'}
                >
                  <input
                    type="checkbox"
                    checked={isOn}
                    disabled={!canEdit}
                    onChange={e => {
                      setSelected(prev => {
                        const next = new Set(prev)
                        if (e.target.checked) next.add(c.handle); else next.delete(c.handle)
                        return next
                      })
                    }}
                    style={{ marginTop: 2 }}
                  />
                  <span style={{
                    padding: '1px 6px', borderRadius: 3, fontSize: 9, letterSpacing: '0.05em', textTransform: 'uppercase',
                    fontWeight: 700, flexShrink: 0, marginTop: 1,
                    background: c.confidence === 'high' ? 'rgba(45,184,102,0.15)'
                             : c.confidence === 'medium' ? 'rgba(255,191,0,0.15)'
                             : 'var(--bg-3)',
                    color: c.confidence === 'high' ? '#2db866'
                         : c.confidence === 'medium' ? '#ffbf00'
                         : 'var(--text-3)',
                  }}>{c.confidence}</span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontWeight: 600, color: 'var(--text-1)' }}>@{c.handle}</div>
                    {c.snippet && (
                      <div style={{
                        fontSize: 11, color: 'var(--text-3)', marginTop: 2,
                        overflow: 'hidden', display: '-webkit-box',
                        WebkitLineClamp: 2, WebkitBoxOrient: 'vertical',
                      } as React.CSSProperties}>
                        {c.snippet}
                      </div>
                    )}
                  </div>
                </label>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}

/** A panel of two HandleRows (IG + FB) for a single actor. */
function ActorHandlePanel({
  title, subtitle, kind, opponentId, name, location, instagramHandles, facebookPages, onChange,
  canEdit = true,
}: {
  title: string
  subtitle?: string
  kind: 'candidate' | 'opponent'
  opponentId?: number
  name: string
  location?: string
  instagramHandles: string[]
  facebookPages: string[]
  onChange: (next: { instagram_handles: string[]; facebook_pages: string[] }) => void
  /** False = read-only preview: disable discover/save/remove buttons. */
  canEdit?: boolean
}) {
  const saveList = useCallback(async (platform: 'instagram' | 'facebook', list: string[]) => {
    const body = {
      target: kind,
      opponent_id: opponentId,
      instagram_handles: platform === 'instagram' ? list : undefined,
      facebook_pages: platform === 'facebook' ? list : undefined,
    } as Parameters<typeof api.saveHandles>[0]
    const r = await api.saveHandles(body)
    onChange({ instagram_handles: r.instagram_handles, facebook_pages: r.facebook_pages })
  }, [kind, opponentId, onChange])

  return (
    <div style={{ marginBottom: 18 }}>
      <div style={{
        fontSize: 14, fontWeight: 700, letterSpacing: '0.04em',
        color: 'var(--text-1)', marginBottom: 2,
      }}>
        {title}
      </div>
      {subtitle && (
        <div style={{ fontSize: 11, color: 'var(--text-3)', marginBottom: 8 }}>{subtitle}</div>
      )}
      <HandleRow
        platform="instagram"
        actor={{ name, kind, opponent_id: opponentId }}
        currentHandles={instagramHandles}
        location={location}
        onSave={(list) => saveList('instagram', list)}
        canEdit={canEdit}
      />
      <HandleRow
        platform="facebook"
        actor={{ name, kind, opponent_id: opponentId }}
        currentHandles={facebookPages}
        location={location}
        onSave={(list) => saveList('facebook', list)}
        canEdit={canEdit}
      />
    </div>
  )
}

/** One chip in the horizontal setup checklist. Renders from the backend's
 *  SetupChecklistItem shape — label inline, helper text moved to the hover
 *  tooltip. Each unchecked chip links to its action_path so the checklist
 *  doubles as quick navigation while the campaign is being set up. The
 *  whole checklist hides itself once every step is complete, so this
 *  component only matters during onboarding.
 */
function CheckChip({ item }: { item: SetupChecklistItem }) {
  const { complete: done, label, helper_text: desc, action_path } = item
  const inner = (
    <div
      title={done ? desc : `${desc} — click to set up`}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        padding: '5px 10px',
        background: done ? 'rgba(14,124,58,0.07)' : 'var(--bg-2)',
        border: `1px solid ${done ? 'rgba(14,124,58,0.25)' : 'var(--bg-3)'}`,
        borderRadius: 999,
        fontSize: 11,
        textDecoration: 'none',
        color: 'inherit',
        whiteSpace: 'nowrap',
      }}>
      {done
        ? <CheckCircle size={12} style={{ color: '#2db866', flexShrink: 0 }} />
        : <Circle size={12} style={{ color: 'var(--text-3)', flexShrink: 0 }} />
      }
      <span style={{
        fontWeight: 600,
        color: done ? '#2db866' : 'var(--text-1)',
        letterSpacing: '0.02em',
      }}>
        {label}
      </span>
      {!done && <ChevronRight size={11} style={{ color: 'var(--text-3)', flexShrink: 0 }} />}
    </div>
  )
  if (done || !action_path) return inner
  return <Link to={action_path} style={{ textDecoration: 'none', color: 'inherit' }}>{inner}</Link>
}

/**
 * Phase 2: Other accounts tracking this race.
 *
 * Lists currently-tracked third-party accounts (local news, county
 * committees, PACs, statewide subreddits, journalists, etc.) as removable
 * chips grouped by platform, and offers a Discover button that surfaces
 * ranked candidates with checkbox confirmation.
 *
 * Distinct from the per-actor "Social handles" section above — those are
 * the candidate's and opponents' OWN accounts. These are anyone ELSE the
 * user wants ingested.
 */
function ThirdPartyAccountsPanel({ canEdit = true }: { canEdit?: boolean }) {
  const [tracked, setTracked] = useState<TrackedThirdPartyAccount[]>([])
  const [loading, setLoading] = useState(true)
  const [discovery, setDiscovery] = useState<ThirdPartyDiscoveryResult | null>(null)
  const [discovering, setDiscovering] = useState(false)
  const [discoverError, setDiscoverError] = useState<string | null>(null)
  // Map of `${platform}:${identifier}` → true for picked candidates.
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [saving, setSaving] = useState(false)
  // Anchor filter — null = show all results. When set to a name like
  // "Rob Bresnahan", only show results that surfaced via that anchor's
  // search. Lets the user quickly drill into opponent-only signal that
  // would otherwise blend into the candidate-anchored results.
  const [anchorFilter, setAnchorFilter] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api.listTrackedAccounts()
      .then(rows => { if (!cancelled) setTracked(rows) })
      .catch(() => { /* surfaced below via empty state */ })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  const runDiscovery = useCallback(async () => {
    setDiscovering(true); setDiscoverError(null); setSelected(new Set())
    setAnchorFilter(null)  // reset filter on fresh run
    try {
      const r = await api.discoverThirdParty()
      setDiscovery(r)
    } catch (e: unknown) {
      setDiscoverError(e instanceof Error ? e.message : 'Discovery failed')
    } finally {
      setDiscovering(false)
    }
  }, [])

  const toggleSelection = useCallback((key: string) => {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key); else next.add(key)
      return next
    })
  }, [])

  const addSelected = useCallback(async () => {
    if (selected.size === 0 || !discovery) return
    setSaving(true)
    try {
      const payload: Array<Omit<TrackedThirdPartyAccount, 'id' | 'added_at'>> = []
      for (const key of selected) {
        const [platform, identifier] = key.split(':')
        const list = discovery.accounts_by_platform[platform as ThirdPartyPlatform] ?? []
        const match = list.find(a => a.identifier === identifier)
        if (!match) continue
        payload.push({
          platform: match.platform,
          identifier: match.identifier,
          display_name: match.display_name ?? null,
          url: match.url,
          inferred_role: match.inferred_role,
          snippet: match.snippet ?? null,
          rss_url: match.rss_url ?? null,
          notes: null,
        })
      }
      const saved = await api.saveTrackedAccounts(payload)
      // Merge into local tracked list; new rows + idempotent re-saves both
      // come back as full rows we can splice in by id.
      setTracked(prev => {
        const byId = new Map(prev.map(r => [r.id, r]))
        for (const row of saved) byId.set(row.id, row)
        return Array.from(byId.values()).sort(
          (a, b) => new Date(b.added_at).getTime() - new Date(a.added_at).getTime()
        )
      })
      setSelected(new Set())
    } finally {
      setSaving(false)
    }
  }, [selected, discovery])

  const removeTracked = useCallback(async (id: number) => {
    await api.deleteTrackedAccount(id)
    setTracked(prev => prev.filter(r => r.id !== id))
  }, [])

  // Group tracked rows by platform for display.
  const trackedByPlatform: Partial<Record<ThirdPartyPlatform, TrackedThirdPartyAccount[]>> = {}
  for (const row of tracked) {
    (trackedByPlatform[row.platform] ??= []).push(row)
  }

  const trackedKeys = new Set(tracked.map(r => `${r.platform}:${r.identifier}`))

  if (loading) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, color: 'var(--text-3)', padding: '12px 0' }}>
        <Loader size={14} style={{ animation: 'spin 1s linear infinite' }} />
        <span style={{ fontSize: 12 }}>Loading tracked accounts…</span>
      </div>
    )
  }

  return (
    <div>
      {/* Tracked accounts — render only platforms that have any */}
      {tracked.length === 0 ? (
        <div style={{
          fontSize: 12, color: 'var(--text-3)', fontStyle: 'italic',
          padding: '10px 12px', marginBottom: 12,
          background: 'var(--bg-2)', border: '1px solid var(--bg-3)',
          borderRadius: 4,
        }}>
          No third-party accounts tracked yet. Click Discover to find local news
          outlets, committees, PACs, and subreddits that mention this race.
        </div>
      ) : (
        <div style={{ marginBottom: 16 }}>
          <div className="section-label" style={{ marginBottom: 8 }}>
            Currently tracked ({tracked.length})
          </div>
          {PLATFORM_ORDER.filter(p => (trackedByPlatform[p] ?? []).length > 0).map(platform => {
            const rows = trackedByPlatform[platform] ?? []
            const Icon = PLATFORM_ICONS[platform]
            const paused = PAUSED_PLATFORMS.has(platform)
            return (
              <div key={platform} style={{ marginBottom: 10 }}>
                <div style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  fontSize: 12, color: 'var(--text-2)', marginBottom: 6,
                  fontWeight: 600,
                }}>
                  <Icon size={14} />
                  {PLATFORM_LABELS[platform]}
                  {paused && (
                    <span style={{
                      fontSize: 9, padding: '1px 6px', borderRadius: 3,
                      background: 'rgba(255,191,0,0.12)', color: 'var(--accent)',
                      textTransform: 'uppercase', letterSpacing: '0.05em',
                    }}>
                      ingestion paused
                    </span>
                  )}
                </div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                  {rows.map(row => (
                    <span key={row.id} style={{
                      display: 'inline-flex', alignItems: 'center', gap: 6,
                      padding: '3px 4px 3px 10px',
                      background: 'var(--bg-1)', border: '1px solid var(--bg-3)',
                      borderRadius: 999, fontSize: 12,
                    }}>
                      <a href={row.url} target="_blank" rel="noreferrer"
                         style={{ color: 'var(--text-1)', textDecoration: 'none', fontWeight: 500 }}
                         title={row.snippet || row.inferred_role || row.identifier}>
                        {row.display_name || row.identifier}
                      </a>
                      {row.inferred_role && row.inferred_role !== 'unknown' && (
                        <span style={{
                          fontSize: 9, color: 'var(--text-3)',
                          textTransform: 'uppercase', letterSpacing: '0.04em',
                        }}>
                          {row.inferred_role}
                        </span>
                      )}
                      <button type="button" onClick={() => removeTracked(row.id)}
                        disabled={!canEdit}
                        style={{
                          background: 'transparent', border: 'none',
                          cursor: canEdit ? 'pointer' : 'not-allowed',
                          padding: 2, color: 'var(--text-3)', display: 'inline-flex',
                          opacity: canEdit ? 1 : 0.4,
                        }}
                        title={canEdit ? `Stop tracking ${row.display_name || row.identifier}` : 'Admin only'}
                      >
                        <X size={12} />
                      </button>
                    </span>
                  ))}
                </div>
              </div>
            )
          })}
        </div>
      )}

      {/* Discover action */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
        <button type="button" onClick={runDiscovery} disabled={!canEdit || discovering || saving}
                className="btn btn-secondary"
                title={canEdit ? undefined : 'Admin only — discovery hits a paid web search'}
                style={{ padding: '6px 12px', fontSize: 12, opacity: canEdit ? 1 : 0.55 }}>
          {discovering
            ? <><Loader size={12} style={{ animation: 'spin 1s linear infinite' }} /> Searching…</>
            : <><Search size={12} /> Discover accounts</>
          }
        </button>
        {discoverError && (
          <span style={{ fontSize: 11, color: 'var(--red, #ef4444)' }}>{discoverError}</span>
        )}
      </div>

      {/* Discovery results */}
      {discovery && (() => {
        // Filter out already-tracked candidates so we don't ask the user to
        // re-confirm something they've already saved.
        const platformsWithResults = PLATFORM_ORDER.filter(p => {
          const accts = discovery.accounts_by_platform[p] ?? []
          return accts.some(a => !trackedKeys.has(`${p}:${a.identifier}`))
        })
        if (platformsWithResults.length === 0) {
          return (
            <div style={{ fontSize: 12, color: 'var(--text-3)', fontStyle: 'italic' }}>
              Discovery returned no new accounts beyond what's already tracked.
            </div>
          )
        }

        // Build the union of anchors that surfaced any result. Only worth
        // showing the filter row when there are 2+ — with one anchor every
        // chip would be a no-op.
        const anchorUnion = new Set<string>()
        for (const accts of Object.values(discovery.accounts_by_platform)) {
          for (const a of accts) {
            for (const anchor of (a.matched_anchors ?? [])) {
              anchorUnion.add(anchor)
            }
          }
        }
        const anchors = Array.from(anchorUnion)
        const matchesFilter = (a: DiscoveredThirdPartyAccount) =>
          !anchorFilter || (a.matched_anchors ?? []).includes(anchorFilter)

        return (
          <div>
            <div style={{
              display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8,
            }}>
              <div className="section-label">Discovery results — pick accounts to track</div>
              <button type="button" onClick={addSelected}
                      disabled={!canEdit || saving || selected.size === 0}
                      className="btn btn-primary"
                      title={canEdit ? undefined : 'Admin only'}
                      style={{ padding: '5px 12px', fontSize: 11, opacity: canEdit ? 1 : 0.55 }}>
                {selected.size === 0 ? 'Add selected' : `Add ${selected.size} selected`}
              </button>
            </div>
            {anchors.length >= 2 && (
              <div style={{
                display: 'flex', alignItems: 'center', gap: 6, marginBottom: 12,
                flexWrap: 'wrap',
              }}>
                <span style={{ fontSize: 10, color: 'var(--text-3)', textTransform: 'uppercase', letterSpacing: '0.05em', marginRight: 4 }}>
                  Show:
                </span>
                <FilterChip
                  label="All"
                  active={anchorFilter === null}
                  onClick={() => setAnchorFilter(null)}
                />
                {anchors.map(name => {
                  const last = name.split(' ').slice(-1)[0]
                  return (
                    <FilterChip
                      key={name}
                      label={`via ${last}`}
                      active={anchorFilter === name}
                      onClick={() => setAnchorFilter(name)}
                      title={`Show only results surfaced by the ${name} search`}
                    />
                  )
                })}
              </div>
            )}
            {platformsWithResults.map(platform => {
              const accts = (discovery.accounts_by_platform[platform] ?? [])
                .filter(a => !trackedKeys.has(`${platform}:${a.identifier}`))
                .filter(matchesFilter)
              if (accts.length === 0) return null
              const Icon = PLATFORM_ICONS[platform]
              const paused = PAUSED_PLATFORMS.has(platform)
              return (
                <div key={platform} style={{ marginBottom: 14 }}>
                  <div style={{
                    display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6,
                    fontSize: 12, color: 'var(--text-2)', fontWeight: 600,
                  }}>
                    <Icon size={14} />
                    {PLATFORM_LABELS[platform]}
                    {paused && (
                      <span style={{
                        fontSize: 9, padding: '1px 6px', borderRadius: 3,
                        background: 'rgba(255,191,0,0.12)', color: 'var(--accent)',
                        textTransform: 'uppercase', letterSpacing: '0.05em',
                      }}>
                        ingestion paused
                      </span>
                    )}
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
                    {accts.map(a => {
                      const key = `${platform}:${a.identifier}`
                      const isOn = selected.has(key)
                      return (
                        <label key={key} style={{
                          display: 'flex', alignItems: 'flex-start', gap: 10,
                          padding: '8px 10px',
                          background: isOn ? 'rgba(255,191,0,0.08)' : 'var(--bg-1)',
                          border: `1px solid ${isOn ? 'var(--accent)' : 'var(--bg-3)'}`,
                          borderRadius: 4, fontSize: 12,
                          cursor: canEdit ? 'pointer' : 'not-allowed',
                          opacity: canEdit ? 1 : 0.6,
                        }} title={canEdit ? undefined : 'Admin only'}>
                          <input type="checkbox" checked={isOn}
                                 disabled={!canEdit}
                                 onChange={() => toggleSelection(key)}
                                 style={{ marginTop: 2 }} />
                          <span style={{
                            padding: '1px 6px', borderRadius: 3, fontSize: 9,
                            letterSpacing: '0.05em', textTransform: 'uppercase',
                            fontWeight: 700, flexShrink: 0, marginTop: 1,
                            background: a.confidence === 'high' ? 'rgba(45,184,102,0.15)'
                                     : a.confidence === 'medium' ? 'rgba(255,191,0,0.15)'
                                     : 'var(--bg-3)',
                            color: a.confidence === 'high' ? '#2db866'
                                 : a.confidence === 'medium' ? '#ffbf00'
                                 : 'var(--text-3)',
                          }}>{a.confidence}</span>
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <div style={{
                              fontWeight: 600, color: 'var(--text-1)',
                              display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 6,
                            }}>
                              <span>{a.display_name || a.identifier}</span>
                              {a.inferred_role && a.inferred_role !== 'unknown' && (
                                <span style={{
                                  fontSize: 9, color: 'var(--text-3)',
                                  textTransform: 'uppercase', letterSpacing: '0.05em',
                                  fontWeight: 500,
                                }}>
                                  {a.inferred_role}
                                </span>
                              )}
                              {(a.matched_anchors ?? []).map(name => {
                                const last = name.split(' ').slice(-1)[0]
                                return (
                                  <span key={name} style={{
                                    fontSize: 9, padding: '1px 6px', borderRadius: 3,
                                    background: 'var(--bg-3)', color: 'var(--text-2)',
                                    fontWeight: 500, letterSpacing: '0.04em',
                                  }} title={`Surfaced by the ${name} search`}>
                                    via {last}
                                  </span>
                                )
                              })}
                            </div>
                            {a.snippet && (
                              <div style={{
                                fontSize: 11, color: 'var(--text-3)', marginTop: 2,
                                overflow: 'hidden', display: '-webkit-box',
                                WebkitLineClamp: 2, WebkitBoxOrient: 'vertical',
                              } as React.CSSProperties}>
                                {a.snippet}
                              </div>
                            )}
                          </div>
                        </label>
                      )
                    })}
                  </div>
                </div>
              )
            })}
          </div>
        )
      })()}
    </div>
  )
}

/** Inline race picker — search by candidate name, district, or state and
 *  pick the candidate within the chosen race. Selecting calls
 *  /api/races/{id}/select on the backend, which auto-fills party, district,
 *  office, location, election_date, geography_keywords on the campaign
 *  and creates Opponent rows for everyone else in the race.
 *
 *  The component manages its own search/results state but bubbles the
 *  result up via onSelected so the parent can refresh campaign config.
 */
function RacePicker({
  currentRaceLabel,
  onSelected,
  onCancel,
  canEdit = true,
}: {
  currentRaceLabel?: string | null
  onSelected: () => Promise<void> | void
  onCancel?: () => void
  /** False = read-only preview: search runs but "Use this →" is disabled. */
  canEdit?: boolean
}) {
  const [q, setQ] = useState('')
  const [results, setResults] = useState<RaceDirectory[]>([])
  const [searching, setSearching] = useState(false)
  const [expandedRaceId, setExpandedRaceId] = useState<number | null>(null)
  const [committingCandidateId, setCommittingCandidateId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  // Debounced search. <2 chars = clear results to avoid huge result sets.
  useEffect(() => {
    if (q.trim().length < 2) {
      setResults([])
      setSearching(false)
      return
    }
    let cancelled = false
    setSearching(true)
    const t = window.setTimeout(async () => {
      try {
        const r = await api.searchRaces(q.trim(), 15)
        if (!cancelled) setResults(r)
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Search failed')
      } finally {
        if (!cancelled) setSearching(false)
      }
    }, 250)
    return () => { cancelled = true; window.clearTimeout(t) }
  }, [q])

  async function pick(race: RaceDirectory, candidate: RaceCandidate) {
    setCommittingCandidateId(candidate.id)
    setError(null)
    try {
      await api.selectRace(race.id, candidate.id)
      await onSelected()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not select race')
    } finally {
      setCommittingCandidateId(null)
    }
  }

  return (
    <div style={{
      padding: 16,
      background: 'var(--bg-2)',
      border: '1px solid var(--bg-3)',
      borderRadius: 6,
      marginBottom: 16,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-1)' }}>
          {currentRaceLabel ? 'Change race' : 'Pick your race'}
        </div>
        {onCancel && (
          <button type="button" onClick={onCancel} className="btn btn-secondary"
                  style={{ padding: '4px 10px', fontSize: 11 }}>
            Cancel
          </button>
        )}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
        <Search size={14} style={{ color: 'var(--text-3)', flexShrink: 0 }} />
        <input
          className="input"
          autoFocus
          value={q}
          onChange={e => setQ(e.target.value)}
          placeholder="Search by candidate name, state, or district (e.g. 'Cognetti', 'PA-08', 'Ohio')"
          style={{ flex: 1, fontSize: 13 }}
        />
        {searching && <Loader size={14} style={{ animation: 'spin 1s linear infinite', color: 'var(--text-3)' }} />}
      </div>
      {error && (
        <div style={{ fontSize: 11, color: '#f05050', marginBottom: 8 }}>{error}</div>
      )}
      {q.trim().length < 2 && (
        <div style={{ fontSize: 11, color: 'var(--text-3)', fontStyle: 'italic' }}>
          Type at least two characters. Results come from the FEC 2026 candidate filings.
        </div>
      )}
      {q.trim().length >= 2 && !searching && results.length === 0 && (
        <div style={{ fontSize: 11, color: 'var(--text-3)', fontStyle: 'italic' }}>
          No races matched. Try a candidate name, "PA-08" style district code, or a state abbreviation.
        </div>
      )}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, marginTop: 6 }}>
        {results.map(race => {
          const expanded = expandedRaceId === race.id
          const candidates = race.candidates ?? []
          return (
            <div key={race.id} style={{
              background: 'var(--bg-1)',
              border: '1px solid var(--bg-3)',
              borderRadius: 4,
              overflow: 'hidden',
            }}>
              <button
                type="button"
                onClick={() => setExpandedRaceId(expanded ? null : race.id)}
                style={{
                  width: '100%', background: 'transparent', border: 'none',
                  padding: '10px 12px', cursor: 'pointer',
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  fontFamily: 'inherit', textAlign: 'left',
                }}
              >
                <div>
                  <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-1)' }}>
                    {race.race_name}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--text-3)', marginTop: 2 }}>
                    {[race.office_name, race.district_label, race.state]
                      .filter(Boolean).join(' · ')} · {candidates.length} candidate{candidates.length === 1 ? '' : 's'}
                  </div>
                </div>
                {expanded
                  ? <ChevronDown size={14} style={{ color: 'var(--text-3)' }} />
                  : <ChevronRight size={14} style={{ color: 'var(--text-3)' }} />
                }
              </button>
              {expanded && (
                <div style={{ borderTop: '1px solid var(--bg-3)', padding: '6px 8px 8px' }}>
                  <div style={{ fontSize: 10, textTransform: 'uppercase', letterSpacing: '0.05em', color: 'var(--text-3)', padding: '4px 4px 6px' }}>
                    Pick your candidate
                  </div>
                  {candidates.map(c => (
                    <button
                      key={c.id}
                      type="button"
                      disabled={!canEdit || committingCandidateId !== null}
                      onClick={() => pick(race, c)}
                      title={canEdit ? undefined : 'Only campaign admins can change the linked race'}
                      style={{
                        width: '100%', textAlign: 'left',
                        padding: '8px 10px', marginBottom: 4,
                        background: 'var(--bg-2)',
                        border: '1px solid var(--bg-3)',
                        borderRadius: 4,
                        cursor: canEdit ? 'pointer' : 'not-allowed',
                        fontFamily: 'inherit',
                        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                        opacity: !canEdit ? 0.55 : committingCandidateId !== null && committingCandidateId !== c.id ? 0.5 : 1,
                      }}
                    >
                      <div>
                        <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-1)' }}>
                          {c.candidate_name}
                        </div>
                        <div style={{ fontSize: 11, color: 'var(--text-3)', marginTop: 2 }}>
                          {[c.party, c.is_incumbent ? 'Incumbent' : null].filter(Boolean).join(' · ') || 'Party unknown'}
                        </div>
                      </div>
                      {committingCandidateId === c.id
                        ? <Loader size={14} style={{ animation: 'spin 1s linear infinite', color: 'var(--text-3)' }} />
                        : <span style={{ fontSize: 11, color: canEdit ? 'var(--accent)' : 'var(--text-3)', fontWeight: 600 }}>
                            {canEdit ? 'Use this →' : 'Admin only'}
                          </span>
                      }
                    </button>
                  ))}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

/** One cell of the compact campaign-profile summary card. Label on top,
 *  value on the second line — keeps the steady-state header dense but
 *  readable. */
function SummaryField({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="section-label" style={{ marginBottom: 2 }}>{label}</div>
      <div style={{
        fontSize: 13, color: 'var(--text-1)', fontWeight: 600,
        whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
      }} title={value}>
        {value}
      </div>
    </div>
  )
}

/** Reset-to-FEC button shown next to auto-filled fields when the current
 *  value differs from the directory default. One click reverts that single
 *  field. Hidden when the field already matches the default. */
function ResetToFecButton({
  visible, onReset, title,
}: { visible: boolean; onReset: () => void; title: string }) {
  if (!visible) return null
  return (
    <button
      type="button"
      onClick={onReset}
      title={title}
      style={{
        background: 'transparent',
        border: '1px solid var(--bg-3)',
        borderRadius: 3,
        cursor: 'pointer',
        padding: '2px 6px',
        color: 'var(--text-3)',
        fontSize: 10,
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        letterSpacing: '0.04em',
        textTransform: 'uppercase',
      }}
    >
      <RotateCcw size={10} />
      reset
    </button>
  )
}

export function Setup() {
  const { user } = useAuth()
  const isAdmin = !!user?.isAdmin
  const [config, setConfig] = useState<CampaignConfig | null>(null)
  const [status, setStatus] = useState<SetupStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState('')
  const location = useLocation()

  // Scroll to hash anchor (e.g. /setup#notifications) once the page has
  // rendered. React Router's pushState doesn't trigger native hash
  // scrolling, so we do it ourselves after data finishes loading.
  useEffect(() => {
    if (loading || !location.hash) return
    const id = location.hash.slice(1)
    // requestAnimationFrame ensures the target element exists in DOM
    // after the conditional render flips.
    requestAnimationFrame(() => {
      document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
  }, [loading, location.hash])

  // Form fields. `party` was added when the race-picker flow started
  // auto-filling it — previous Setup didn't expose it as an editable input.
  const [candidateName, setCandidateName] = useState('')
  const [party, setParty] = useState('')
  const [office, setOffice] = useState('')
  const [district, setDistrict] = useState('')
  const [state, setState] = useState('')
  const [electionDate, setElectionDate] = useState('')
  const [campaignMessage, setCampaignMessage] = useState('')
  const [keywords, setKeywords] = useState('')
  const [priorities, setPriorities] = useState('')
  const [opponents, setOpponents] = useState<Opponent[]>([])

  // Race-picker UI state.
  const [directoryRace, setDirectoryRace] = useState<RaceDirectory | null>(null)
  const [showRacePicker, setShowRacePicker] = useState(false)
  const [showEditDetails, setShowEditDetails] = useState(false)

  function hydrateFromConfig(d: CampaignConfig) {
    setConfig(d)
    setCandidateName(d.candidate_name ?? '')
    setParty(d.party ?? '')
    setOffice(d.office ?? '')
    setDistrict(d.district ?? '')
    setState(d.state ?? '')
    setElectionDate(d.election_date ? d.election_date.slice(0, 10) : '')
    setCampaignMessage(d.campaign_message ?? '')
    // Server stores these as key_priorities / relevance_keywords; the legacy
    // `keywords` / `priorities` aliases are still in the type for back-compat
    // but the canonical fields are what the backend actually returns.
    setKeywords((d.relevance_keywords ?? d.keywords ?? []).join(', '))
    setPriorities((d.key_priorities ?? d.priorities ?? []).join('\n'))
  }

  useEffect(() => {
    Promise.allSettled([api.campaign(), api.setupStatus(), api.opponents()]).then(([c, s, o]) => {
      if (c.status === 'fulfilled') {
        hydrateFromConfig(c.value)
      }
      if (s.status === 'fulfilled') setStatus(s.value)
      if (o.status === 'fulfilled') setOpponents(o.value)
    }).finally(() => setLoading(false))
  }, [])

  // Fetch the linked FEC race entry when the campaign points to one. Powers
  // the per-field "reset to FEC default" affordance and the compact summary
  // header. Runs whenever the linked race id changes.
  useEffect(() => {
    const raceId = config?.directory_race_id
    if (!raceId) {
      setDirectoryRace(null)
      return
    }
    let cancelled = false
    api.getRace(raceId)
      .then(r => { if (!cancelled) setDirectoryRace(r) })
      .catch(() => { if (!cancelled) setDirectoryRace(null) })
    return () => { cancelled = true }
  }, [config?.directory_race_id])

  // Selected RaceCandidate row inside the linked race — the one whose data
  // got copied onto the campaign config. Identified by name match; that's
  // what select_directory_race uses too.
  const linkedCandidate: RaceCandidate | null = useMemo(() => {
    if (!directoryRace || !candidateName) return null
    const normalize = (s: string) => s.trim().toLowerCase()
    const target = normalize(candidateName)
    return directoryRace.candidates.find(c => normalize(c.candidate_name) === target) ?? null
  }, [directoryRace, candidateName])

  // FEC-derived defaults the reset buttons compare against. Undefined when
  // no race is linked (resets become no-ops and the buttons hide).
  const fecDefaults = useMemo(() => {
    if (!directoryRace) return null
    return {
      party: linkedCandidate?.party ?? '',
      office: directoryRace.office_name ?? '',
      district: directoryRace.district_label ?? '',
      state: directoryRace.state ?? '',
      election_date: directoryRace.election_date ? directoryRace.election_date.slice(0, 10) : '',
    }
  }, [directoryRace, linkedCandidate])

  async function refreshAfterRacePick() {
    setShowRacePicker(false)
    // After /races/{id}/select, both the campaign and the opponents list
    // need to be refetched — selecting also created/updated Opponent rows.
    const [c, s, o] = await Promise.allSettled([api.campaign(), api.setupStatus(), api.opponents()])
    if (c.status === 'fulfilled') hydrateFromConfig(c.value)
    if (s.status === 'fulfilled') setStatus(s.value)
    if (o.status === 'fulfilled') setOpponents(o.value)
  }

  async function save(e: React.FormEvent) {
    e.preventDefault()
    setSaving(true)
    setError('')
    setSaved(false)
    try {
      const updated = await api.updateCampaign({
        candidate_name: candidateName.trim(),
        party: party.trim() || undefined,
        office: office.trim() || undefined,
        district: district.trim() || undefined,
        state: state.trim() || undefined,
        election_date: electionDate || undefined,
        campaign_message: campaignMessage.trim() || undefined,
        // Backend canonical names. The legacy aliases stay in the type so
        // older code reading config.keywords / config.priorities still works.
        relevance_keywords: keywords.split(',').map(k => k.trim()).filter(Boolean),
        key_priorities: priorities.split('\n').map(p => p.trim()).filter(Boolean),
      })
      setConfig(updated)
      setSaved(true)
      setTimeout(() => setSaved(false), 3000)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to save')
    } finally {
      setSaving(false)
    }
  }

  const checklistItems: SetupChecklistItem[] = status?.items ?? []
  const setupDone = checklistItems.filter(i => i.complete).length
  const setupTotal = checklistItems.length || 4

  return (
    <div style={{ minHeight: '100vh' }}>
      <div style={{ padding: '24px 28px', maxWidth: 800, margin: '0 auto' }}>
        {loading && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, color: 'var(--text-3)', padding: '40px 0' }}>
            <Loader size={20} style={{ animation: 'spin 1s linear infinite' }} />
            <span style={{ fontSize: 12 }}>LOADING CONFIGURATION...</span>
          </div>
        )}

        {!loading && (
          <>
          {/* Sticky section nav — Setup has grown to 4+ sections; this lets
              the user jump rather than scroll-hunt. Active section is
              tracked from the URL hash (set by clicking a chip or any
              other in-page anchor), with "campaign-profile" as the
              default when no hash is set. */}
          <SetupSectionNav />

          {/* Admin-only banner for non-admins. The page itself is open so
              viewers can see how the campaign is configured and adjust
              their own notification preferences — but every campaign
              setting is disabled, and the backend require_admin gate is
              the real authority if someone bypasses the disable client-
              side. Hidden for admins so it doesn't add noise. */}
          {!isAdmin && (
            <div style={{
              display: 'flex', alignItems: 'flex-start', gap: 10,
              padding: '12px 14px', marginBottom: 18,
              background: 'rgba(255,191,0,0.07)',
              border: '1px solid rgba(255,191,0,0.25)',
              borderRadius: 6,
            }}>
              <Lock size={14} style={{ color: 'var(--accent)', flexShrink: 0, marginTop: 2 }} />
              <div style={{ flex: 1, minWidth: 0, fontSize: 12, color: 'var(--text-2)', lineHeight: 1.5 }}>
                <strong style={{ color: 'var(--text-1)' }}>Read-only view.</strong>{' '}
                Only campaign admins can change these settings. You can still
                adjust your personal notification preferences below.
              </div>
            </div>
          )}

          {/* Setup checklist — only renders while there are unfinished steps.
              Goes ABOVE the campaign profile as a compact horizontal strip
              so the profile gets full width below. Once every step is
              complete the whole block disappears — onboarding is a
              transient state, not a permanent fixture. */}
          {checklistItems.length > 0 && setupDone < setupTotal && (
            <div style={{
              padding: '10px 14px',
              background: 'var(--bg-2)',
              border: '1px solid var(--bg-3)',
              borderRadius: 6,
              marginBottom: 18,
            }}>
              <div style={{
                display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                marginBottom: 8,
              }}>
                <span style={{
                  fontSize: 12, fontWeight: 700, letterSpacing: '0.06em',
                  color: 'var(--text-2)', textTransform: 'uppercase',
                }}>
                  Setup Checklist
                </span>
                <span style={{ fontSize: 11, color: 'var(--text-2)', fontWeight: 600 }}>
                  {setupDone}/{setupTotal} · click a step to set it up
                </span>
              </div>
              <div style={{
                height: 3, background: 'var(--bg-3)', borderRadius: 2,
                overflow: 'hidden', marginBottom: 10,
              }}>
                <div style={{
                  height: '100%',
                  width: `${(setupDone / setupTotal) * 100}%`,
                  background: 'linear-gradient(90deg, #1d6ae5, #4f8ef7)',
                  borderRadius: 2,
                  transition: 'width 0.5s ease',
                }} />
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {checklistItems.map(item => (
                  <CheckChip key={item.id} item={item} />
                ))}
              </div>
            </div>
          )}

          <div id="campaign-profile" style={{ scrollMarginTop: 70 }}>
            {/* Campaign config — race picker + compact summary + collapsible edit form. */}
            <div>
              {/* Section header with the Edit-details toggle pinned to the
                  same row. The toggle used to sit under the summary card —
                  moving it here keeps the steady-state view tighter and
                  lets users open the form without scrolling past the
                  summary every time. */}
              <div style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: 12,
                marginBottom: 16,
                paddingBottom: 8,
                borderBottom: '1px solid var(--bg-3)',
              }}>
                <div style={{
                  fontSize: 16,
                  fontWeight: 700,
                  letterSpacing: '0.06em',
                  color: 'var(--text-2)',
                  textTransform: 'uppercase',
                }}>
                  Campaign Profile
                </div>
                <button
                  type="button"
                  onClick={() => setShowEditDetails(v => !v)}
                  style={{
                    background: 'transparent',
                    border: '1px solid var(--bg-3)',
                    borderRadius: 4,
                    padding: '5px 10px',
                    fontSize: 11,
                    color: 'var(--text-2)',
                    cursor: 'pointer',
                    fontFamily: 'inherit',
                    display: 'inline-flex',
                    alignItems: 'center',
                    gap: 5,
                    flexShrink: 0,
                  }}
                >
                  {showEditDetails ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                  {showEditDetails ? 'Hide details' : 'Edit details'}
                </button>
              </div>

              {/* Race-picker UI — three states:
                  1. picker open (showRacePicker = true): search + results list
                  2. linked race (directoryRace) and picker closed: compact "Linked
                     to {race}" card with a "Change race" affordance
                  3. unlinked campaign (no directoryRace) and picker closed: prompt
                     to pick a race so per-field FEC defaults work */}
              {showRacePicker ? (
                <RacePicker
                  currentRaceLabel={directoryRace?.race_name}
                  onSelected={refreshAfterRacePick}
                  onCancel={() => setShowRacePicker(false)}
                  canEdit={isAdmin}
                />
              ) : directoryRace ? (
                <div style={{
                  display: 'flex', alignItems: 'flex-start', gap: 12,
                  padding: '12px 14px',
                  background: 'var(--bg-2)',
                  border: '1px solid var(--bg-3)',
                  borderRadius: 6,
                  marginBottom: 16,
                }}>
                  <MapPin size={14} style={{ color: 'var(--accent)', flexShrink: 0, marginTop: 3 }} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="section-label" style={{ marginBottom: 4 }}>Linked race</div>
                    <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-1)' }}>
                      {directoryRace.race_name}
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--text-3)', marginTop: 2 }}>
                      Source: {directoryRace.data_source.toUpperCase()} · auto-fills party,
                      district, office, election date, and opponents from the FEC filing.
                    </div>
                  </div>
                  <button type="button" onClick={() => setShowRacePicker(true)}
                          disabled={!isAdmin}
                          className="btn btn-secondary"
                          title={isAdmin ? undefined : 'Admin only'}
                          style={{ padding: '5px 12px', fontSize: 11, flexShrink: 0, opacity: isAdmin ? 1 : 0.55 }}>
                    Change race
                  </button>
                </div>
              ) : (
                <div style={{
                  display: 'flex', alignItems: 'flex-start', gap: 12,
                  padding: '12px 14px',
                  background: 'rgba(255,191,0,0.07)',
                  border: '1px solid rgba(255,191,0,0.25)',
                  borderRadius: 6,
                  marginBottom: 16,
                }}>
                  <Search size={14} style={{ color: 'var(--accent)', flexShrink: 0, marginTop: 3 }} />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-1)' }}>
                      Pick your race
                    </div>
                    <div style={{ fontSize: 11, color: 'var(--text-2)', marginTop: 2, lineHeight: 1.5 }}>
                      Choose your race from the FEC 2026 candidate filings and we'll fill in
                      party, district, office, election date, and opponents automatically.
                      You can still edit anything below.
                    </div>
                  </div>
                  <button type="button" onClick={() => setShowRacePicker(true)}
                          disabled={!isAdmin}
                          className="btn btn-primary"
                          title={isAdmin ? undefined : 'Admin only'}
                          style={{ padding: '5px 12px', fontSize: 11, flexShrink: 0, opacity: isAdmin ? 1 : 0.55 }}>
                    Pick a race
                  </button>
                </div>
              )}

              {/* Compact summary card — always visible once a candidate is set. */}
              {candidateName && (
                <div style={{
                  padding: '12px 14px',
                  background: 'var(--bg-2)',
                  border: '1px solid var(--bg-3)',
                  borderRadius: 6,
                  marginBottom: 12,
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fit, minmax(120px, 1fr))',
                  gap: 12,
                }}>
                  <SummaryField label="Candidate" value={candidateName} />
                  <SummaryField label="Party" value={party || '—'} />
                  <SummaryField label="Office" value={office || '—'} />
                  <SummaryField label="District" value={district || state || '—'} />
                  <SummaryField
                    label="Election"
                    value={electionDate
                      ? new Date(electionDate).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })
                      : '—'
                    }
                  />
                </div>
              )}

              {showEditDetails && (
              <form onSubmit={save} style={{
                padding: 16,
                background: 'var(--bg-2)',
                border: '1px solid var(--bg-3)',
                borderRadius: 6,
                opacity: isAdmin ? 1 : 0.85,
              }}>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginBottom: 14 }}>
                  <div>
                    <label className="section-label" style={{ display: 'block', marginBottom: 6 }}>CANDIDATE NAME *</label>
                    <input
                      className="input"
                      value={candidateName}
                      onChange={e => setCandidateName(e.target.value)}
                      placeholder="Paige Cognetti"
                      required
                      disabled={!isAdmin}
                    />
                  </div>
                  <div>
                    <label className="section-label" style={{
                      display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6,
                    }}>
                      <span>PARTY</span>
                      <ResetToFecButton
                        visible={isAdmin && !!fecDefaults && (party ?? '') !== (fecDefaults.party ?? '')}
                        onReset={() => setParty(fecDefaults?.party ?? '')}
                        title={`Reset to FEC default: ${fecDefaults?.party || '(blank)'}`}
                      />
                    </label>
                    <input
                      className="input"
                      value={party}
                      onChange={e => setParty(e.target.value)}
                      placeholder="Democrat"
                      disabled={!isAdmin}
                    />
                  </div>
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginBottom: 14 }}>
                  <div>
                    <label className="section-label" style={{
                      display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6,
                    }}>
                      <span>OFFICE</span>
                      <ResetToFecButton
                        visible={isAdmin && !!fecDefaults && (office ?? '') !== (fecDefaults.office ?? '')}
                        onReset={() => setOffice(fecDefaults?.office ?? '')}
                        title={`Reset to FEC default: ${fecDefaults?.office || '(blank)'}`}
                      />
                    </label>
                    <input
                      className="input"
                      value={office}
                      onChange={e => setOffice(e.target.value)}
                      placeholder="U.S. House of Representatives"
                      disabled={!isAdmin}
                    />
                  </div>
                  <div>
                    <label className="section-label" style={{
                      display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6,
                    }}>
                      <span>ELECTION DATE</span>
                      <ResetToFecButton
                        visible={isAdmin && !!fecDefaults && (electionDate ?? '') !== (fecDefaults.election_date ?? '')}
                        onReset={() => setElectionDate(fecDefaults?.election_date ?? '')}
                        title={`Reset to FEC default: ${fecDefaults?.election_date || '(blank)'}`}
                      />
                    </label>
                    <input
                      className="input"
                      type="date"
                      value={electionDate}
                      onChange={e => setElectionDate(e.target.value)}
                      disabled={!isAdmin}
                    />
                  </div>
                </div>

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginBottom: 14 }}>
                  <div>
                    <label className="section-label" style={{
                      display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6,
                    }}>
                      <span>DISTRICT</span>
                      <ResetToFecButton
                        visible={isAdmin && !!fecDefaults && (district ?? '') !== (fecDefaults.district ?? '')}
                        onReset={() => setDistrict(fecDefaults?.district ?? '')}
                        title={`Reset to FEC default: ${fecDefaults?.district || '(blank)'}`}
                      />
                    </label>
                    <input
                      className="input"
                      value={district}
                      onChange={e => setDistrict(e.target.value)}
                      placeholder="PA-08"
                      disabled={!isAdmin}
                    />
                  </div>
                  <div>
                    <label className="section-label" style={{
                      display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6,
                    }}>
                      <span>STATE</span>
                      <ResetToFecButton
                        visible={isAdmin && !!fecDefaults && (state ?? '') !== (fecDefaults.state ?? '')}
                        onReset={() => setState(fecDefaults?.state ?? '')}
                        title={`Reset to FEC default: ${fecDefaults?.state || '(blank)'}`}
                      />
                    </label>
                    <input
                      className="input"
                      value={state}
                      onChange={e => setState(e.target.value)}
                      placeholder="PA"
                      maxLength={2}
                      disabled={!isAdmin}
                    />
                  </div>
                </div>

                <div style={{ marginBottom: 14 }}>
                  <label className="section-label" style={{ display: 'block', marginBottom: 6 }}>CAMPAIGN MESSAGE</label>
                  <textarea
                    className="input"
                    value={campaignMessage}
                    onChange={e => setCampaignMessage(e.target.value)}
                    placeholder="Core campaign message or contrast with opponent..."
                    rows={3}
                    style={{ resize: 'vertical' }}
                    disabled={!isAdmin}
                  />
                </div>

                <div style={{ marginBottom: 14 }}>
                  <label className="section-label" style={{ display: 'block', marginBottom: 6 }}>
                    TRACKING KEYWORDS
                    <span style={{ color: 'var(--text-3)', marginLeft: 8, fontWeight: 400 }}>comma-separated</span>
                  </label>
                  <input
                    className="input"
                    value={keywords}
                    onChange={e => setKeywords(e.target.value)}
                    placeholder="Paige Cognetti, PA-08, Lackawanna County, Scranton..."
                    disabled={!isAdmin}
                  />
                </div>

                <div style={{ marginBottom: 20 }}>
                  <label className="section-label" style={{ display: 'block', marginBottom: 6 }}>
                    CAMPAIGN PRIORITIES
                    <span style={{ color: 'var(--text-3)', marginLeft: 8, fontWeight: 400 }}>one per line</span>
                  </label>
                  <textarea
                    className="input"
                    value={priorities}
                    onChange={e => setPriorities(e.target.value)}
                    placeholder={"healthcare\neconomy\ninfrastructure\neducation"}
                    rows={4}
                    style={{ resize: 'vertical', fontSize: 12 }}
                    disabled={!isAdmin}
                  />
                </div>

                {error && (
                  <div style={{
                    color: '#f05050',
                    fontSize: 12,
                    marginBottom: 12,
                    padding: '8px 12px',
                    background: 'rgba(201,28,28,0.08)',
                    border: '1px solid rgba(201,28,28,0.2)',
                    borderRadius: 3,
                  }}>
                    {error}
                  </div>
                )}

                <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                  <button type="submit" disabled={!isAdmin || saving} className="btn btn-primary"
                          title={isAdmin ? undefined : 'Admin only'}
                          style={{ opacity: isAdmin ? 1 : 0.55 }}>
                    {saving ? (
                      <>
                        <Loader size={13} style={{ animation: 'spin 1s linear infinite' }} />
                        Saving...
                      </>
                    ) : 'Save Configuration'}
                  </button>
                  {!isAdmin && (
                    <span style={{ fontSize: 11, color: 'var(--text-3)' }}>
                      Read-only — only admins can save changes.
                    </span>
                  )}
                  {saved && (
                    <span style={{
                      fontSize: 11,
                      color: '#2db866',
                      letterSpacing: '0.08em',
                    }}>
                      ✓ SAVED
                    </span>
                  )}
                </div>
              </form>
              )}
            </div>

          </div>

          {/* Notification preferences — full-width section below the
              campaign config grid. The id="notifications" anchor lets
              the bell page link directly here (/setup#notifications). */}
          <div id="notifications" style={{ marginTop: 36, scrollMarginTop: 70 }}>
            <div style={{
              fontSize: 16,
              fontWeight: 700,
              letterSpacing: '0.06em',
              color: 'var(--text-2)',
              textTransform: 'uppercase',
              marginBottom: 4,
              paddingBottom: 8,
              borderBottom: '1px solid var(--bg-3)',
              display: 'flex', alignItems: 'center', gap: 8,
            }}>
              <Bell size={14} />
              Notifications
            </div>
            <div style={{ fontSize: 12, color: 'var(--text-3)', marginTop: 8, marginBottom: 18 }}>
              Pick which events should generate alerts and where to send them.
            </div>
            <NotificationSettings />
          </div>

          {/* Social handles — discover and confirm IG/FB handles for the
              candidate and each opponent. Confirmed handles flow into
              source_discovery so the next monitor sync creates RSSHub
              feeds for them. */}
          <div id="social-handles" style={{ marginTop: 36, scrollMarginTop: 70 }}>
            <div style={{
              fontSize: 16, fontWeight: 700, letterSpacing: '0.06em',
              color: 'var(--text-2)', textTransform: 'uppercase',
              marginBottom: 4, paddingBottom: 8,
              borderBottom: '1px solid var(--bg-3)',
              display: 'flex', alignItems: 'center', gap: 8,
            }}>
              <Instagram size={14} />
              <Facebook size={14} />
              Social handles
            </div>
            <div style={{ fontSize: 12, color: 'var(--text-3)', marginTop: 8, marginBottom: 12 }}>
              Confirm Instagram and Facebook handles for the candidate and each
              opponent. Use "Discover" to search for likely handles, or enter
              one manually. Saved handles persist so we can turn ingestion on
              later without redoing the work.
            </div>
            <div style={{
              fontSize: 11, color: 'var(--text-2)', lineHeight: 1.5,
              padding: '10px 12px', marginBottom: 18,
              background: 'rgba(255,191,0,0.07)',
              border: '1px solid rgba(255,191,0,0.25)',
              borderRadius: 4,
            }}>
              <strong style={{ color: 'var(--accent)' }}>Ingestion paused.</strong>{' '}
              Instagram and Facebook posts are not being pulled right now. The
              public RSSHub mirror blocks anonymous IG/FB access, and
              self-hosted RSSHub still needs throwaway-account credentials for
              those platforms specifically. To turn ingestion on later, either
              wire up a self-hosted RSSHub with <code>IGUSERID</code>/
              <code>IGPASSWORD</code> + <code>FBCOOKIE</code> env vars and an
              Apify adapter, then set{' '}
              <code>SOCIAL_HANDLE_MONITORS_ENABLED=true</code> in the backend
              env.
            </div>

            {config && (
              <ActorHandlePanel
                title={config.candidate_name}
                subtitle="Candidate"
                kind="candidate"
                name={config.candidate_name}
                location={config.state || config.district || config.location}
                instagramHandles={config.instagram_handles ?? []}
                facebookPages={config.facebook_pages ?? []}
                onChange={(next) => setConfig(c => c ? { ...c, ...next } : c)}
                canEdit={isAdmin}
              />
            )}

            {opponents.map(opp => (
              <ActorHandlePanel
                key={opp.id}
                title={opp.name}
                subtitle="Opponent"
                kind="opponent"
                opponentId={opp.id}
                name={opp.name}
                location={config?.state || config?.district || config?.location}
                instagramHandles={opp.instagram_handles ?? []}
                facebookPages={opp.facebook_pages ?? []}
                onChange={(next) => setOpponents(list =>
                  list.map(o => o.id === opp.id ? { ...o, ...next } : o)
                )}
                canEdit={isAdmin}
              />
            ))}

            {opponents.length === 0 && (
              <div style={{ fontSize: 12, color: 'var(--text-3)', fontStyle: 'italic' }}>
                Add opponents on the Opponents page to track their social posts here.
              </div>
            )}
          </div>

          {/* Phase 2: third-party accounts that talk about this race. Local
              news, county committees, PACs, subreddits, journalists. The
              candidate's and opponents' OWN accounts live in the Social
              handles section above. */}
          <div id="third-party-accounts" style={{ marginTop: 36, scrollMarginTop: 70 }}>
            <div style={{
              fontSize: 16, fontWeight: 700, letterSpacing: '0.06em',
              color: 'var(--text-2)', textTransform: 'uppercase',
              marginBottom: 4, paddingBottom: 8,
              borderBottom: '1px solid var(--bg-3)',
              display: 'flex', alignItems: 'center', gap: 8,
            }}>
              <Globe size={14} />
              Other accounts tracking this race
            </div>
            <div style={{ fontSize: 12, color: 'var(--text-3)', marginTop: 8, marginBottom: 18 }}>
              Find local news outlets, county committees, PACs, statewide
              subreddits, and journalists who post about the race. Bluesky,
              Reddit, and YouTube accounts start ingesting immediately. IG and
              FB accounts save here but stay paused until the fetcher is wired
              up (same as the Social handles section above).
            </div>
            <ThirdPartyAccountsPanel canEdit={isAdmin} />
          </div>
          </>
        )}
      </div>
    </div>
  )
}
