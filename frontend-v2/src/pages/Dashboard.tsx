import { ChevronDown, X, Zap } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import { Link } from 'react-router-dom'
import { api } from '@/api/client'
import { awaitBriefing, getDashboardCache, prefetchDashboard } from '@/api/dashboardCache'
import type { MorningBriefing, NarrativeFrame, OwnerType, SourceItem, Spike } from '@/api/types'
import { useAuth } from '@/auth/AuthContext'
import { InfoTooltip } from '@/components/InfoTooltip'
import { RaceSentimentCard } from '@/components/RaceSentimentCard'
import { ActivityThisWeek } from '@/components/briefing/ActivityThisWeek'
import { NeedsResponse } from '@/components/briefing/NeedsResponse'
import { OvernightChanges } from '@/components/briefing/OvernightChanges'
import { RaceSituation } from '@/components/briefing/RaceSituation'
import {
  selectFeatured,
  sparklinePath,
  surfaceReason,
} from '@/lib/featuredFrame'
import { formatArticleDate } from '@/lib/formatDate'

// Plain-English explanations of jargon shown in the UI.
const STAGE_HELP: Record<string, string> = {
  mainstream: 'Mainstream — the story is everywhere. Multiple national or regional outlets are covering it this week.',
  spreading: 'Spreading — coverage is picking up steam. New outlets are starting to pick up the story.',
  resurfacing: 'Resurfacing — an old narrative is being talked about again after a quiet period.',
  active: 'Active — getting steady coverage, but not surging.',
  emerging: 'Emerging — only a handful of outlets have picked it up so far, but it could grow.',
  fading: 'Fading — coverage is dropping off compared to last week.',
  dormant: 'Dormant — no recent activity. Kept around for historical context.',
}

const OWNER_HELP: Record<OwnerType, string> = {
  candidate: 'Candidate — narratives that help our side (e.g. your accomplishments, your message).',
  opponent: 'Opponent — narratives the other side is pushing, usually attacks on us.',
  media: 'Media — narratives the press is driving on their own (not pushed by either campaign).',
}

// All colors come from CSS variables so the dark/light toggle works.
// See src/index.css for the palette definitions per theme.
const C = {
  bg1: 'var(--bg-1)', bg2: 'var(--bg-2)', bg3: 'var(--bg-3)', bg4: 'var(--bg-4)',
  border: 'var(--border)', borderBright: 'var(--border-bright)',
  text1: 'var(--text-1)', text2: 'var(--text-2)', text3: 'var(--text-3)',
  candidate: 'var(--candidate)', opponent: 'var(--opponent)', media: 'var(--media)',
  accent: 'var(--accent)',
  green: 'var(--green)', red: 'var(--red)',
}

// Relevance is shown as a coarse bucket badge (critical/high/medium/low),
// not a 0–100 number — the precise number invited "why 73 and not 81?"
// scrutiny that eroded trust in a ranking that's actually correct.
const REL_BADGE_STYLE: Record<string, { color: string; bg: string; border: string }> = {
  critical: { color: '#f87171', bg: 'rgba(215,25,19,0.08)', border: 'rgba(215,25,19,0.25)' },
  high: { color: '#fb923c', bg: 'rgba(234,88,12,0.08)', border: 'rgba(234,88,12,0.25)' },
  medium: { color: '#fbbf24', bg: 'rgba(202,138,4,0.08)', border: 'rgba(202,138,4,0.25)' },
  low: { color: '#a1a1a1', bg: 'rgba(161,161,161,0.08)', border: 'rgba(161,161,161,0.2)' },
  irrelevant: { color: '#555', bg: 'rgba(85,85,85,0.08)', border: 'rgba(85,85,85,0.2)' },
}

// NOTE: featured-card ranking now lives in lib/featuredFrame.ts as a
// multi-objective score (urgency + acceleration + novelty + propagation +
// persistence + momentum) with soft owner/stage diversity caps applied in
// selectFeatured(). The old importanceScore() used WoW mention delta,
// which is noisy at small N and conflates real spread with wire
// syndication. Kept this comment as a breadcrumb in case a future session
// wonders why the sort moved.

function ownerColor(t: OwnerType): string {
  return t === 'candidate' ? C.candidate : t === 'opponent' ? C.opponent : C.media
}

// V13.21 — quadrant color (owner × subject). Falls back to ownerColor
// when subject_type isn't on the frame yet (older API responses).
import { quadrantColor as _qc } from '@/lib/quadrantColor'
function frameColor(f: { owner_type?: OwnerType; subject_type?: OwnerType }): string {
  if (f.subject_type) return _qc(f.owner_type ?? null, f.subject_type ?? null)
  return ownerColor(f.owner_type ?? 'media')
}

function stageLabel(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1)
}

// FeaturedCard — Phase 1 redesign.
//
// Card answers two questions an operator glancing at the panel needs:
//
//   1. What is it?              — owner dot + name (+ memo marker)
//   2. Why is it featured now?  — surface-reason line + sparkline
//                                  ("Going viral", "Accelerating", …)
//
// Every signal comes from existing fields on NarrativeFrame; no backend
// work was needed. See lib/featuredFrame.ts for the detector rules and
// scoring components.
function FeaturedCard({ frame, inMemo = false }: { frame: NarrativeFrame; inMemo?: boolean }) {
  const oc = frameColor(frame)
  const reason = surfaceReason(frame)
  const sparkD = sparklinePath(frame.activity_30d, 80, 18)
  const [hovered, setHovered] = useState(false)

  return (
    <Link
      to={`/narratives/${frame.id}`}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        background: hovered ? C.bg3 : C.bg2,
        border: `1px solid ${hovered ? C.borderBright : C.border}`,
        borderRadius: '0.625rem',
        padding: '10px 12px',
        cursor: 'pointer',
        transition: 'background 0.12s ease, border-color 0.12s ease',
        textDecoration: 'none',
        color: 'inherit',
        display: 'flex',
        flexDirection: 'column',
        gap: 6,
        minHeight: 96,
      } as CSSProperties}
    >
      {/* Row 1: owner dot + name + (memo marker) */}
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 6 }}>
        <span style={{
          width: 10, height: 10, borderRadius: '50%', background: oc,
          flexShrink: 0, marginTop: 3,
        }} />
        <span style={{
          flex: 1,
          fontSize: 13, fontWeight: 600, color: C.text1, lineHeight: 1.3,
          overflow: 'hidden', display: '-webkit-box',
          WebkitLineClamp: 2, WebkitBoxOrient: 'vertical',
        } as CSSProperties}>
          {frame.name}
        </span>
        {inMemo && (
          <span
            title="Cited in today's briefing memo"
            style={{
              flexShrink: 0,
              fontSize: 10, fontWeight: 600, letterSpacing: '0.02em',
              color: C.accent,
              background: 'rgba(255,191,0,0.10)',
              padding: '2px 6px', borderRadius: 4,
              whiteSpace: 'nowrap',
              cursor: 'help',
            }}
          >
            In memo
          </span>
        )}
      </div>

      {/* Row 2: surface reason + sparkline. Stage label fills in when
          no reason fires, so the card never feels empty. */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        gap: 8, marginTop: 'auto',
      }}>
        <span style={{
          fontSize: 11, fontWeight: 600,
          color: reason ? C.text1 : oc,
          letterSpacing: reason ? 0 : '0.02em',
        }}>
          {reason ? reason.label : stageLabel(frame.stage)}
        </span>
        {sparkD && (
          <svg width={80} height={18} style={{ flexShrink: 0, opacity: 0.7 }}>
            <path d={sparkD} fill="none" stroke={oc} strokeWidth={1.2} strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        )}
      </div>

    </Link>
  )
}

function ArticleRow({ item }: { item: SourceItem }) {
  const [hovered, setHovered] = useState(false)
  const { user } = useAuth()
  // Per-article relevance bucket is admin-only — non-admins still see
  // the article ordering, just not the raw confidence label.
  const relStyle = user?.isAdmin
    ? (REL_BADGE_STYLE[item.race_relevance_label ?? ''] ?? null)
    : null

  return (
    <Link
      to={`/articles/${item.id}`}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        display: 'flex', alignItems: 'flex-start', gap: 9, width: '100%',
        padding: '10px 6px',
        borderBottom: `1px solid ${C.bg3}`,
        background: hovered ? 'var(--bg-3)' : 'transparent',
        color: 'inherit', textDecoration: 'none', textAlign: 'left',
        transition: 'background 0.1s ease',
        borderRadius: hovered ? 4 : 0,
      } as CSSProperties}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          fontSize: 13, color: C.text1, fontWeight: 500, lineHeight: 1.35,
          overflow: 'hidden', display: '-webkit-box',
          WebkitLineClamp: 2, WebkitBoxOrient: 'vertical',
        } as CSSProperties}>
          {item.title}
        </div>
        {item.source_name && (
          <div style={{ fontSize: 11, color: C.text3, marginTop: 3 }}>
            {item.source_name}
          </div>
        )}
      </div>
      <div style={{ textAlign: 'right', flexShrink: 0, display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4 }}>
        {relStyle && item.race_relevance_label && (
          <span style={{
            fontSize: 9, fontWeight: 700, letterSpacing: '0.07em',
            color: relStyle.color, background: relStyle.bg,
            border: `1px solid ${relStyle.border}`,
            padding: '1px 6px', borderRadius: 4,
          }}>
            {item.race_relevance_label.toUpperCase()}
          </span>
        )}
        <div style={{ fontSize: 11, color: C.text3 }}>
          {formatArticleDate(item.published_at ?? item.created_at)}
        </div>
      </div>
    </Link>
  )
}

type FilterKey = 'all' | OwnerType | 'mainstream' | 'spreading' | 'resurfacing' | 'active' | 'emerging' | 'fading' | 'dormant'

interface FilterPillProps {
  label: string
  filterKey: FilterKey
  count: number
  active: boolean
  onClick: () => void
  tooltip?: string
}

/**
 * Horizontal filter chip — used in the filter header bar at the top of the
 * dashboard. Active state is a yellow border + bolder text. Hover lifts the
 * background to bg-3.
 */
function FilterPill({ label, filterKey: _filterKey, count, active, onClick, tooltip }: FilterPillProps) {
  const [hovered, setHovered] = useState(false)
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        display: 'inline-flex', alignItems: 'center', gap: 6,
        padding: '5px 11px',
        borderRadius: 999,
        background: active ? 'var(--bg-3)' : hovered ? 'var(--bg-2)' : 'transparent',
        border: `1px solid ${active ? C.accent : C.border}`,
        color: active ? C.text1 : C.text2,
        fontWeight: active ? 600 : 400,
        fontSize: 13,
        cursor: 'pointer', whiteSpace: 'nowrap',
        transition: 'all 0.1s ease',
        fontFamily: 'inherit',
      } as CSSProperties}
    >
      <span>{label}</span>
      {count > 0 && (
        <span style={{
          fontSize: 11, color: active ? C.text2 : C.text3,
          fontWeight: 500,
        }}>
          {count}
        </span>
      )}
      {tooltip && <InfoTooltip text={tooltip} placement="bottom" />}
    </button>
  )
}

interface DropdownOption {
  key: FilterKey
  label: string
  count: number
  tooltip?: string
}

interface FilterDropdownProps {
  /** Label shown when no option in this group is selected (e.g. "Owner"). */
  label: string
  /** Tooltip for the group label itself. */
  groupTooltip?: string
  /** Options inside the dropdown. */
  options: DropdownOption[]
  /** Currently active filter (page-wide). Used to detect which option in
   *  this group, if any, is the active one. */
  activeFilter: FilterKey
  /** Called when an option is picked. Pass `'all'` to clear the active
   *  filter (only fired when the same active option is clicked again). */
  onSelect: (key: FilterKey) => void
}

/**
 * Compact dropdown for the filter header. Click to open a panel of options.
 * When one of this group's options is the active page filter, the trigger
 * shows "Label · OptionName" and gets the accent border, and a tiny ✕
 * button lets you clear it without opening the menu.
 *
 * Click-outside / Escape close the panel. The panel is absolutely positioned
 * — caller must put the trigger in a relatively-positioned parent (we do
 * that here via `position: 'relative'`).
 */
function FilterDropdown({ label, groupTooltip, options, activeFilter, onSelect }: FilterDropdownProps) {
  const [open, setOpen] = useState(false)
  const wrapRef = useRef<HTMLDivElement | null>(null)
  const activeOpt = options.find(o => o.key === activeFilter) ?? null
  const isActive = !!activeOpt

  // Close on outside click or Escape.
  useEffect(() => {
    if (!open) return
    function onDocClick(e: MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false)
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDocClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div ref={wrapRef} style={{ position: 'relative', display: 'inline-flex', alignItems: 'center' }}>
      <button
        onClick={() => setOpen(o => !o)}
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 6,
          padding: '5px 8px 5px 11px',
          borderRadius: 999,
          background: isActive || open ? 'var(--bg-3)' : 'transparent',
          border: `1px solid ${isActive ? C.accent : C.border}`,
          color: isActive ? C.text1 : C.text2,
          fontWeight: isActive ? 600 : 400,
          fontSize: 13,
          cursor: 'pointer', whiteSpace: 'nowrap',
          transition: 'all 0.1s ease',
          fontFamily: 'inherit',
        } as CSSProperties}
      >
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
          {label}
          {isActive && (
            <>
              <span style={{ color: C.text3, fontWeight: 400 }}>·</span>
              <span>{activeOpt!.label}</span>
            </>
          )}
        </span>
        {groupTooltip && !isActive && <InfoTooltip text={groupTooltip} placement="bottom" />}
        {isActive ? (
          <span
            role="button"
            aria-label={`Clear ${label} filter`}
            onClick={e => { e.stopPropagation(); onSelect('all') }}
            style={{
              display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
              padding: 2, borderRadius: 4,
              color: C.text2, cursor: 'pointer',
            }}
            onMouseDown={e => e.stopPropagation()}
          >
            <X size={12} />
          </span>
        ) : (
          <ChevronDown size={13} style={{ color: C.text3, transition: 'transform 0.1s ease', transform: open ? 'rotate(180deg)' : 'none' }} />
        )}
      </button>

      {open && (
        <div
          role="menu"
          style={{
            position: 'absolute', top: 'calc(100% + 6px)', left: 0,
            background: 'var(--bg-2)',
            border: `1px solid ${C.border}`,
            borderRadius: 8,
            padding: 4,
            minWidth: 200,
            boxShadow: 'var(--shadow-elev)',
            zIndex: 50,
            display: 'flex', flexDirection: 'column', gap: 1,
          }}
        >
          {options.map(opt => {
            const selected = opt.key === activeFilter
            return (
              <button
                key={opt.key}
                role="menuitemradio"
                aria-checked={selected}
                onClick={() => {
                  // Same option clicked → clear (toggle off). Otherwise switch.
                  onSelect(selected ? 'all' : opt.key)
                  setOpen(false)
                }}
                style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  padding: '7px 10px',
                  borderRadius: 5,
                  background: selected ? 'var(--bg-3)' : 'transparent',
                  border: 'none',
                  color: selected ? C.text1 : C.text2,
                  fontWeight: selected ? 600 : 400,
                  fontSize: 13, textAlign: 'left',
                  cursor: 'pointer', fontFamily: 'inherit',
                  transition: 'background 0.1s ease',
                }}
                onMouseEnter={e => { if (!selected) e.currentTarget.style.background = 'var(--bg-3)' }}
                onMouseLeave={e => { if (!selected) e.currentTarget.style.background = 'transparent' }}
              >
                <span style={{ flex: 1 }}>{opt.label}</span>
                {opt.count > 0 && (
                  <span style={{ fontSize: 11, color: C.text3, fontVariantNumeric: 'tabular-nums' }}>
                    {opt.count}
                  </span>
                )}
                {opt.tooltip && <InfoTooltip text={opt.tooltip} placement="left" />}
              </button>
            )
          })}
        </div>
      )}
    </div>
  )
}

export function Dashboard() {
  // Hydrate from the Layout-warmed cache so navigating to Home from another
  // page renders instantly. Skeletons only show on the first-ever visit
  // (before Layout's initial prefetch resolves).
  const cached = getDashboardCache()
  const { user } = useAuth()
  const [frames, setFrames] = useState<NarrativeFrame[]>(cached.frames ?? [])
  const [spikes, setSpikes] = useState<Spike[]>(cached.spikes ?? [])
  const [recent, setRecent] = useState<SourceItem[]>(cached.recent ?? [])
  const [briefing, setBriefing] = useState<MorningBriefing | null>(cached.briefing)
  const [loading, setLoading] = useState(!cached.frames)
  const [briefingLoading, setBriefingLoading] = useState(!cached.briefing)
  const [activeFilter, setActiveFilter] = useState<FilterKey>('all')
  // The right rail loads 15 articles by default; Load more bumps this in
  // 15-row chunks. A ref mirrors the state so the 60s refresh poll always
  // reads the user's currently-expanded limit even though the polling
  // useEffect captures the initial closure.
  const [articleLimit, setArticleLimit] = useState(15)
  const articleLimitRef = useRef(articleLimit)
  articleLimitRef.current = articleLimit
  const [loadingMoreArticles, setLoadingMoreArticles] = useState(false)

  const refresh = async () => {
    // Includes briefing — refresh is on a 60s timer, so a slow briefing
    // call here doesn't gate first paint (it ran independently on mount).
    const [fr, sp, ra, br] = await Promise.allSettled([
      api.narrativeFrames(), api.spikes(),
      api.recentArticles(articleLimitRef.current),
      api.morningBriefing(2),
    ])
    if (fr.status === 'fulfilled') setFrames(fr.value)
    if (sp.status === 'fulfilled') setSpikes(sp.value)
    if (ra.status === 'fulfilled') setRecent(ra.value)
    if (br.status === 'fulfilled') setBriefing(br.value)
  }

  const onLoadMoreArticles = async () => {
    const next = articleLimit + 15
    setArticleLimit(next)
    articleLimitRef.current = next
    setLoadingMoreArticles(true)
    try {
      const more = await api.recentArticles(next)
      setRecent(more)
    } finally {
      setLoadingMoreArticles(false)
    }
  }

  useEffect(() => {
    if (!cached.frames) {
      // First-ever visit: kick the prefetch and surface its result.
      prefetchDashboard().then(() => {
        const c = getDashboardCache()
        if (c.frames) setFrames(c.frames)
        if (c.spikes) setSpikes(c.spikes)
        if (c.recent) setRecent(c.recent)
        setLoading(false)
      })
    } else {
      // Background refresh on mount so stale cache catches up immediately.
      refresh()
    }
    // Briefing arrives on its own clock — the LLM synthesis can take several
    // seconds, so we don't let it gate first paint. The briefing section
    // skeleton shows while we wait; the rest of the dashboard renders ASAP.
    awaitBriefing().then(b => {
      if (b) setBriefing(b)
      setBriefingLoading(false)
    })
    const id = setInterval(refresh, 60_000)
    return () => clearInterval(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const filteredFrames = [...frames]
    .filter(f => {
      if (activeFilter === 'all') return true
      if (['candidate', 'opponent', 'media'].includes(activeFilter)) return f.owner_type === activeFilter
      return f.stage === activeFilter
    })

  // Frames cited in today's grounded memo are pinned into Featured — the
  // editorial memo and the algorithmic panel should never disagree about
  // what matters today. See lib/featuredFrame.ts:selectFeatured for the
  // pinning + diversity-cap interaction. Empty set when briefing hasn't
  // loaded yet or the memo has no citations.
  const pinnedFrameIds = new Set<number>(
    briefing?.cited_frames?.map(f => f.frame_id) ?? [],
  )

  // Multi-objective selection: ranks by urgency + acceleration + novelty +
  // propagation + persistence + momentum, then applies soft per-owner and
  // per-stage diversity caps so the panel isn't 8 versions of the same
  // flavor. See lib/featuredFrame.ts for component weights.
  const featuredFrames = selectFeatured(filteredFrames, 8, pinnedFrameIds)

  // Log featured-card appearances once per page load. The backend upsert
  // is idempotent on (frame_id, today), so reload spam is harmless. We
  // only fire when frames have actually loaded (avoids logging the
  // skeleton state). The count rendered yesterday gets credited yesterday;
  // today's count only gets credited the first time the user opens the
  // dashboard today. Ref guards against double-logging on filter changes.
  const appearanceLoggedRef = useRef(false)
  useEffect(() => {
    if (appearanceLoggedRef.current) return
    if (loading || featuredFrames.length === 0) return
    appearanceLoggedRef.current = true
    api.logFeaturedAppearance(featuredFrames.map(f => f.id)).catch(() => {
      // Best-effort telemetry — if the POST fails (offline, backend down),
      // we don't surface an error. Next load will write today's row.
      appearanceLoggedRef.current = false
    })
  }, [loading, featuredFrames])

  const counts = {
    all: frames.length,
    candidate: frames.filter(f => f.owner_type === 'candidate').length,
    opponent: frames.filter(f => f.owner_type === 'opponent').length,
    media: frames.filter(f => f.owner_type === 'media').length,
    mainstream: frames.filter(f => f.stage === 'mainstream').length,
    spreading: frames.filter(f => f.stage === 'spreading').length,
    resurfacing: frames.filter(f => f.stage === 'resurfacing').length,
    active: frames.filter(f => f.stage === 'active').length,
    emerging: frames.filter(f => f.stage === 'emerging').length,
    fading: frames.filter(f => f.stage === 'fading').length,
    dormant: frames.filter(f => f.stage === 'dormant').length,
  }

  return (
    <div style={{ background: C.bg1, minHeight: '100%' }}>
      {/* ── Body: featured + detail + spikes | recent articles ── */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 280px', minHeight: '100%' }}>

        {/* ── Center: Featured cards + detail panels ── */}
        {/* Top padding is 0 so the RaceSentiment banner sits flush against
            the app header. Left/right padding (24px) and bottom padding
            (16px) match the prior look for the rest of the column.
            minWidth: 0 prevents intrinsic-content-sized children (e.g.
            the briefing headline's white-space: nowrap auto-fit text)
            from blowing the grid 1fr column past the right rail. */}
        <div style={{ padding: '0 24px 16px', borderRight: `1px solid ${C.border}`, minWidth: 0 }}>
          {/* Race Sentiment — prominent peer card above the narrative cards.
              Markets + forecaster ratings shown separately (no blended number).
              Phase 1: manual values entered via the edit modal. Phase 2 will
              swap in scraped/API values without touching this component. */}
          <RaceSentimentCard />

          {/* Briefing sections — Race Situation memo, Needs Response,
              What Changed in the Race. Pulled out of the frames-loading
              conditional so they render independently of narrative-frames
              data. Briefing has its own loading state (LLM-slow). */}
          {briefingLoading && !briefing ? (
            <section style={{ marginBottom: 32 }}>
              <div className="skeleton" style={{ height: 16, width: 140, marginBottom: 12 }} />
              <div className="skeleton" style={{ height: 140, borderRadius: 12 }} />
            </section>
          ) : briefing ? (
            <>
              <RaceSituation memo={briefing.race_memo} onRequestRefresh={refresh} />
              <NeedsResponse items={briefing.needs_response} />
              <OvernightChanges claims={briefing.overnight_changes} />
            </>
          ) : null}

          {loading ? (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10, marginBottom: 24 }}>
              {Array.from({ length: 8 }).map((_, i) => (
                <div key={i} className="skeleton" style={{ height: 80 }} />
              ))}
            </div>
          ) : filteredFrames.length === 0 ? (
            <div style={{ textAlign: 'center', padding: '60px 20px', color: C.text3 }}>
              <Zap size={40} style={{ margin: '0 auto 16px', opacity: 0.3 }} />
              <div style={{ fontSize: 18, fontWeight: 600, color: C.text2, marginBottom: 8 }}>
                No narrative frames yet
              </div>
              <div style={{ fontSize: 13 }}>
                Go to{' '}
                <Link to="/narratives" style={{ color: C.accent }}>Narratives</Link>
                {' '}to create frames
              </div>
            </div>
          ) : (
            <>
              {/* Featured narrative cards. The section header doubles as the
                  filter row — label on the left, filter pills + dropdowns on
                  the right, all on a single line. */}
              {featuredFrames.length > 0 && (
                <div style={{ marginBottom: 28 }}>
                  <div style={{
                    display: 'flex', alignItems: 'center',
                    gap: 12, marginBottom: 12,
                    flexWrap: 'wrap',
                  }}>
                    <div style={{
                      fontSize: 11, color: C.text3, letterSpacing: '0.12em',
                      fontWeight: 600, textTransform: 'uppercase',
                      display: 'inline-flex', alignItems: 'center',
                    }}>
                      Featured Narratives
                      <InfoTooltip
                        text={'The narratives that matter most this week, ranked by urgency and coverage. Filter by Owner or Stage to re-rank within a subset.'}
                      />
                    </div>
                    <span style={{ flex: 1, height: 1, background: 'var(--bg-3)', display: 'block' }} />
                    <div style={{
                      display: 'inline-flex', alignItems: 'center', gap: 8,
                    }}>
                      <FilterDropdown
                        label="Owner"
                        groupTooltip="Who benefits from this narrative being out there. Candidate = helps us. Opponent = helps the other side. Media = the press is driving it on their own."
                        activeFilter={activeFilter}
                        onSelect={setActiveFilter}
                        options={[
                          { key: 'candidate', label: 'Candidate', count: counts.candidate, tooltip: OWNER_HELP.candidate },
                          { key: 'opponent',  label: 'Opponent',  count: counts.opponent,  tooltip: OWNER_HELP.opponent },
                          { key: 'media',     label: 'Media',     count: counts.media,     tooltip: OWNER_HELP.media },
                        ]}
                      />
                      <FilterDropdown
                        label="Stage"
                        groupTooltip={'How big the story is right now. Updated automatically based on how many outlets are covering it and how that\'s changing week-over-week.'}
                        activeFilter={activeFilter}
                        onSelect={setActiveFilter}
                        options={[
                          { key: 'mainstream',  label: 'Mainstream',  count: counts.mainstream,  tooltip: STAGE_HELP.mainstream },
                          { key: 'spreading',   label: 'Spreading',   count: counts.spreading,   tooltip: STAGE_HELP.spreading },
                          { key: 'resurfacing', label: 'Resurfacing', count: counts.resurfacing, tooltip: STAGE_HELP.resurfacing },
                          { key: 'active',      label: 'Active',      count: counts.active,      tooltip: STAGE_HELP.active },
                          { key: 'emerging',    label: 'Emerging',    count: counts.emerging,    tooltip: STAGE_HELP.emerging },
                          { key: 'fading',      label: 'Fading',      count: counts.fading,      tooltip: STAGE_HELP.fading },
                          { key: 'dormant',     label: 'Dormant',     count: counts.dormant,     tooltip: STAGE_HELP.dormant },
                        ]}
                      />
                    </div>
                  </div>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10 }}>
                    {featuredFrames.map(f => (
                      <FeaturedCard key={f.id} frame={f} inMemo={pinnedFrameIds.has(f.id)} />
                    ))}
                  </div>
                </div>
              )}

              {/* Activity This Week — top race-allowlist entities (briefing). */}
              {briefing && <ActivityThisWeek entities={briefing.top_entities} />}

              {/* Spikes — moved here from the right rail */}
              <div>
                <div style={{
                  fontSize: 11, color: C.text3, letterSpacing: '0.12em',
                  marginBottom: 12, fontWeight: 600, textTransform: 'uppercase',
                  display: 'flex', alignItems: 'center',
                }}>
                  24h Spikes {spikes.length > 0 ? `(${spikes.length})` : ''}
                  <InfoTooltip
                    text={'Narratives that got noticeably more coverage in the last 24 hours than usual. The "Nx surge" number is how many times more articles than the baseline. Worth a quick look — could be a real-time story breaking.'}
                  />
                </div>
                {spikes.length > 0 ? (
                  <div style={{
                    background: C.bg2, border: `1px solid ${C.border}`,
                    borderRadius: '0.625rem', padding: '4px 16px',
                  }}>
                    {spikes.map((s, i) => (
                      <div key={i} style={{
                        padding: '10px 0',
                        borderBottom: i < spikes.length - 1 ? `1px solid ${C.bg3}` : 'none',
                        display: 'flex', alignItems: 'center', gap: 10,
                      }}>
                        <Zap size={14} style={{ color: C.accent, flexShrink: 0 }} />
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{
                            fontSize: 13, color: C.text1, fontWeight: 500,
                            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                          }}>
                            {s.frame_name}
                          </div>
                          <div style={{ fontSize: 11, color: C.text3, marginTop: 2 }}>
                            {s.ratio.toFixed(1)}× surge · reach {s.reach_24h.toLocaleString()}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div style={{
                    background: C.bg2, border: `1px solid ${C.border}`,
                    borderRadius: '0.625rem', padding: '16px 18px',
                    fontSize: 13, color: C.text3,
                  }}>
                    No frames have surged in the last 24h.
                  </div>
                )}
              </div>
            </>
          )}
        </div>

        {/* ── Right: Recent Relevant Articles ── */}
        <div style={{
          padding: '16px',
          position: 'sticky', top: 0, alignSelf: 'start',
          maxHeight: 'calc(100vh - 76px)', overflowY: 'auto',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
            <span style={{ fontSize: 12, fontWeight: 700, color: C.text1, letterSpacing: '0.08em', display: 'inline-flex', alignItems: 'center' }}>
              RECENT ARTICLES
            </span>
            <span style={{ fontSize: 11, color: C.text3 }}>
              {loading ? '' : `${recent.length} new`}
            </span>
          </div>

          {loading ? (
            Array.from({ length: 5 }).map((_, i) => (
              <div key={i} className="skeleton" style={{ height: 52, marginBottom: 6 }} />
            ))
          ) : recent.length > 0 ? (
            <>
              {recent.map(item => <ArticleRow key={item.id} item={item} />)}
              {/* Load more — only render when the last fetch returned the
                  full requested page. If recent.length < articleLimit, the
                  backend has run out of articles and there's nothing more
                  to fetch, so hide the button to avoid a dead click. */}
              {recent.length >= articleLimit && (
                <button
                  type="button"
                  onClick={onLoadMoreArticles}
                  disabled={loadingMoreArticles}
                  style={{
                    marginTop: 8, width: '100%',
                    padding: '8px 12px', borderRadius: 8,
                    background: C.bg2, color: C.text2,
                    border: `1px solid ${C.border}`,
                    cursor: loadingMoreArticles ? 'wait' : 'pointer',
                    fontSize: 12, fontFamily: 'inherit',
                  } as CSSProperties}
                >
                  {loadingMoreArticles ? 'Loading…' : 'Load more'}
                </button>
              )}
            </>
          ) : (
            <div style={{ textAlign: 'center', padding: '24px 0', fontSize: 13, color: C.text3 }}>
              No relevant articles in the last week.
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
