import { ChevronDown, X, Zap } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import { Link } from 'react-router-dom'
import { Area, AreaChart, ResponsiveContainer } from 'recharts'
import { api } from '@/api/client'
import { getDashboardCache, prefetchDashboard } from '@/api/dashboardCache'
import type { NarrativeFrame, OwnerType, SourceItem, Spike, TimeseriesPoint } from '@/api/types'
import { InfoTooltip } from '@/components/InfoTooltip'
import { RaceSentimentCard } from '@/components/RaceSentimentCard'
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

// Order must include every stage the backend can emit; see
// _narrative_stage() in backend/app/services/narrative_frames.py.
// `active` and `resurfacing` were previously missing from the UI, which
// silently hid frames in those stages from the by-stage filter and made
// the chip counts not sum to "All".
const STAGE_ORDER = ['mainstream', 'spreading', 'resurfacing', 'active', 'emerging', 'fading', 'dormant']

// Composite "importance" score for the Featured Narratives section.
// Higher = more worth your attention right now. Components, roughly in
// order of weight:
//
//   • Strategic urgency  (the AI's "act now" call)             up to +40
//   • Strategic posture  (defensive > offensive > amplify…)    up to +20
//   • Momentum signal    (viral / amplified / missing / elite)  up to +25
//   • Stage              (spreading > mainstream > emerging…)  up to +20
//   • This-week volume   (capped — megavolume doesn't drown)   up to +30
//   • Week-over-week Δ   (growing stories matter more)         positive boost
//   • Outlet diversity   (broad coverage = bigger story)       up to +24
//
// Caps prevent a single noisy frame (e.g. 200 mentions this week from a
// repeated tweet) from drowning out a 5-outlet, defensive, high-urgency
// opponent attack — the second is the one the campaign actually needs to
// react to today.
function importanceScore(f: NarrativeFrame): number {
  let score = 0

  const urgency = f.strategic_lens?.urgency
  if (urgency === 'high') score += 40
  else if (urgency === 'medium') score += 20
  else if (urgency === 'low') score += 10

  const posture = f.strategic_lens?.posture
  if (posture === 'defensive') score += 20
  else if (posture === 'offensive') score += 15
  else if (posture === 'amplify') score += 12
  else if (posture === 'monitor') score += 5

  if (f.momentum_signal === 'viral') score += 25
  else if (f.momentum_signal === 'amplified') score += 18
  else if (f.momentum_signal === 'missing_coverage') score += 20
  else if (f.momentum_signal === 'elite_only') score += 10

  const stagePts: Record<string, number> = {
    spreading: 20, resurfacing: 18, mainstream: 15, emerging: 12,
    active: 10, fading: 3, dormant: 0,
  }
  score += stagePts[f.stage] ?? 0

  score += Math.min(f.mentions_this_week * 2, 30)

  const delta = f.mentions_this_week - f.mentions_last_week
  score += delta * 1.5

  score += Math.min(f.unique_outlets_this_week * 3, 24)

  return score
}

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

// Visual treatment for the momentum signal + strategic lens, fused into
// one chip. The LABEL comes from the momentum signal (what's happening).
// The COLOR comes from the strategic posture (what to do). The TOOLTIP
// combines momentum data with the strategic action recommendation.
//
// Why fused not split: a separate strategic chip would add a 4th element
// to an already-dense card row. The signal alone is read-only diagnosis;
// the posture is the decision. Combining them puts the same information
// in less space.
//
// We render only when posture is "actionable" (amplify, offensive,
// defensive, monitor with action). "ignore" + signals without owner type
// produce no chip.
type StrategicLens = { posture: 'amplify' | 'offensive' | 'defensive' | 'monitor' | 'ignore'; action: string | null; urgency: 'high' | 'medium' | 'low' }
type MomentumBadge = { label: string; color: string; bg: string; tooltip: string; urgencyBorder?: string }

// Posture color palette. Chosen to match standard semantic conventions:
// red for opposition response needed, blue for our-side offense, green
// for amplification, yellow for monitoring, neutral for ignore.
const POSTURE_COLORS: Record<string, { color: string; bg: string }> = {
  amplify:   { color: '#22c55e', bg: 'rgba(34, 197, 94, 0.14)' },   // green — our message + receptive audience
  offensive: { color: '#0ea5e9', bg: 'rgba(14, 165, 233, 0.14)' },   // cyan — content opportunity
  defensive: { color: '#ef4444', bg: 'rgba(239, 68, 68, 0.14)' },    // red — opposition attack territory
  monitor:   { color: '#a78bfa', bg: 'rgba(167, 139, 250, 0.14)' },  // purple — watch but don't engage
  ignore:    { color: '#666',    bg: 'rgba(102, 102, 102, 0.10)' },  // gray — not worth attention
}

function signalLabel(signal: string): string {
  switch (signal) {
    case 'viral': return 'Viral'
    case 'amplified': return 'Amplified'
    case 'missing_coverage': return 'Missing'
    case 'elite_only': return 'Elite only'
    case 'stable': return 'Stable'
    case 'no_trend_signal': return 'No signal'
    default: return signal
  }
}

function momentumBadge(
  signal: string | null | undefined,
  data: Record<string, unknown> | null | undefined,
  lens: StrategicLens | null | undefined,
): MomentumBadge | null {
  if (!signal) return null
  // Hide pure-monitoring with no action (nothing to show the user).
  if (lens && lens.posture === 'ignore') return null
  if (!lens && (signal === 'stable' || signal === 'no_trend_signal')) return null

  const palette = lens ? POSTURE_COLORS[lens.posture] : POSTURE_COLORS.monitor
  const ov = data?.outlet_velocity as number | undefined
  const cv = data?.cluster_velocity as number | undefined
  const tv = data?.trend_velocity as number | undefined

  // Tooltip combines the underlying momentum data with the strategic action.
  // Reads "Signal: <what's happening>. Action: <what to do>. Urgency: X."
  const signalDesc =
    signal === 'viral' ? `Outlets ${ov ? `${ov.toFixed(1)}×` : 'spiking'} AND voter search ${tv ? `${tv.toFixed(1)}×` : 'spiking'} vs baseline` :
    signal === 'amplified' ? `Outlets ${ov ? `${ov.toFixed(1)}×` : 'spiking'} (broad press pickup) but voter search flat` :
    signal === 'missing_coverage' ? `Voter search ${tv ? `${tv.toFixed(1)}×` : 'spiking'} but press flat` :
    signal === 'elite_only' ? `Angles ${cv ? `${cv.toFixed(1)}×` : 'spiking'} but few outlets — narrow press` :
    signal

  const parts = [signalDesc]
  if (lens?.action) parts.push(`→ ${lens.action}`)
  if (lens?.urgency) parts.push(`Urgency: ${lens.urgency}`)

  return {
    label: signalLabel(signal),
    color: palette.color,
    bg: palette.bg,
    tooltip: parts.join('\n'),
    urgencyBorder: lens?.urgency === 'high' ? palette.color : undefined,
  }
}

function TrendArrow({ delta }: { delta: number }) {
  if (delta > 0) return <span style={{ color: C.green, fontSize: 13 }}>↑</span>
  if (delta < 0) return <span style={{ color: C.red, fontSize: 13 }}>↓</span>
  return <span style={{ color: C.text3, fontSize: 13 }}>—</span>
}

function FeaturedCard({ frame }: { frame: NarrativeFrame }) {
  const oc = frameColor(frame)
  const delta = frame.mentions_this_week - frame.mentions_last_week
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
        padding: '12px 14px',
        cursor: 'pointer',
        transition: 'background 0.12s ease, border-color 0.12s ease',
        textDecoration: 'none',
        color: 'inherit',
        display: 'block',
      } as CSSProperties}
    >
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 8, marginBottom: 8 }}>
        <span style={{
          width: 10, height: 10, borderRadius: '50%', background: oc,
          flexShrink: 0, marginTop: 3,
        }} />
        <span style={{
          fontSize: 13, fontWeight: 600, color: C.text1, lineHeight: 1.3,
          overflow: 'hidden', display: '-webkit-box',
          WebkitLineClamp: 2, WebkitBoxOrient: 'vertical',
        } as CSSProperties}>
          {frame.name}
        </span>
      </div>
      <div style={{ fontSize: 12, color: oc, fontWeight: 600 }}>
        {stageLabel(frame.stage)}
        {delta !== 0 && (
          <span style={{ color: C.text2, fontWeight: 400, marginLeft: 6 }}>
            {delta > 0 ? `+${delta}` : delta} this week
          </span>
        )}
      </div>
    </Link>
  )
}

function DetailPanel({ frame }: { frame: NarrativeFrame }) {
  const [timeseries, setTimeseries] = useState<TimeseriesPoint[]>([])
  const [hovered, setHovered] = useState(false)
  const oc = frameColor(frame)

  useEffect(() => {
    api.frameTimeseries(frame.id).then(setTimeseries).catch(() => {})
  }, [frame.id])

  const articleDelta = frame.mentions_this_week - frame.mentions_last_week
  const outletDelta = frame.unique_outlets_this_week - frame.unique_outlets_last_week
  const reachDelta = frame.reach_this_week - frame.reach_last_week
  const reachFmt = (v: number) => v > 0 ? `${(v / 1000).toFixed(1)}K` : '—'

  // Total outlets covering this frame across all time, derived from the
  // outlet_tiers breakdown the API returns. The API doesn't ship a
  // `unique_outlets_total` field directly, so we sum the tier counts.
  const outletsTotal = frame.outlet_tiers
    ? Object.values(frame.outlet_tiers).reduce((a, b) => a + (b || 0), 0)
    : frame.unique_outlets_this_week

  const rows = [
    { label: 'Articles', total: frame.mentions_total, wk: frame.mentions_this_week, delta: articleDelta },
    { label: 'Outlets', total: outletsTotal, wk: frame.unique_outlets_this_week, delta: outletDelta },
    { label: 'Reach', total: reachFmt(frame.reach_total), wk: reachFmt(frame.reach_this_week), delta: reachDelta },
  ]

  return (
    <Link
      to={`/narratives/${frame.id}`}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        background: C.bg2,
        border: `1px solid ${hovered ? C.borderBright : C.border}`,
        borderRadius: '0.625rem', padding: 16, overflow: 'hidden',
        textDecoration: 'none', color: 'inherit', display: 'block',
        cursor: 'pointer',
        transition: 'border-color 0.12s ease',
      } as CSSProperties}
    >
      <div style={{ marginBottom: 10 }}>
        <div style={{ fontSize: 16, fontWeight: 700, color: C.text1, lineHeight: 1.25, marginBottom: 4 }}>
          {frame.name}
        </div>
        {frame.description && (
          <div style={{
            fontSize: 12, color: C.text2, lineHeight: 1.45,
            overflow: 'hidden', display: '-webkit-box',
            WebkitLineClamp: 2, WebkitBoxOrient: 'vertical',
          } as CSSProperties}>
            {frame.description}
          </div>
        )}
      </div>

      <div style={{ display: 'flex', gap: 6, marginBottom: 14, flexWrap: 'wrap', alignItems: 'center' }}>
        <span
          title={STAGE_HELP[frame.stage] ?? ''}
          style={{
            background: C.bg3, border: `1px solid ${C.border}`,
            borderRadius: 4, padding: '3px 8px', fontSize: 11, color: C.text2,
            cursor: 'help',
          }}
        >
          {stageLabel(frame.stage)}
        </span>
        <span
          title={OWNER_HELP[frame.owner_type] ?? ''}
          style={{
            background: C.bg3, border: `1px solid ${C.border}`,
            borderRadius: 4, padding: '3px 8px', fontSize: 11, color: oc, fontWeight: 600,
            cursor: 'help',
          }}
        >
          {frame.owner_type.charAt(0).toUpperCase() + frame.owner_type.slice(1)}
        </span>
        {(() => {
          const m = momentumBadge(frame.momentum_signal, frame.momentum_data, frame.strategic_lens)
          if (!m) return null
          // High urgency gets a slightly bolder border (2px) — subtle visual
          // weight to draw the eye toward truly time-sensitive items.
          const borderWidth = m.urgencyBorder ? 2 : 1
          return (
            <span
              title={m.tooltip}
              style={{
                background: m.bg, border: `${borderWidth}px solid ${m.color}`,
                borderRadius: 4, padding: '3px 8px', fontSize: 11,
                color: m.color, fontWeight: 600, cursor: 'help',
              }}
            >
              {m.label}
            </span>
          )
        })()}
      </div>

      <table style={{ width: '100%', borderCollapse: 'collapse', marginBottom: 14 }}>
        <thead>
          <tr>
            {['METRIC', 'TOTAL', '1W', ''].map((h, i) => (
              <th key={i} style={{
                textAlign: i === 0 ? 'left' : 'right',
                fontSize: 10, color: C.text3, padding: '4px 0 6px',
                letterSpacing: '0.1em', fontWeight: 600,
                borderBottom: `1px solid ${C.border}`,
              }}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(row => (
            <tr key={row.label} style={{ borderBottom: `1px solid ${C.bg3}` }}>
              <td style={{ fontSize: 13, color: C.text2, padding: '7px 0' }}>{row.label}</td>
              <td style={{ textAlign: 'right', fontSize: 14, fontWeight: 600, color: C.text1, padding: '7px 0' }}>
                {row.total}
              </td>
              <td style={{ textAlign: 'right', fontSize: 13, color: C.text2, padding: '7px 0' }}>
                {row.wk}
              </td>
              <td style={{ textAlign: 'right', padding: '7px 0' }}>
                <TrendArrow delta={row.delta} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {timeseries.length > 0 && (
        <div style={{ marginTop: 4 }}>
          <ResponsiveContainer width="100%" height={70}>
            <AreaChart data={timeseries} margin={{ top: 2, right: 0, bottom: 0, left: 0 }}>
              <defs>
                <linearGradient id={`grad-${frame.id}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={oc} stopOpacity={0.2} />
                  <stop offset="95%" stopColor={oc} stopOpacity={0} />
                </linearGradient>
              </defs>
              <Area
                type="monotone" dataKey="count"
                stroke={oc} strokeWidth={2}
                fill={`url(#grad-${frame.id})`}
                dot={false} activeDot={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </Link>
  )
}

function ArticleRow({ item }: { item: SourceItem }) {
  const score = item.race_relevance_score ?? 0
  const scoreColor = score >= 80 ? C.accent : score >= 50 ? C.text2 : C.text3
  const [hovered, setHovered] = useState(false)

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
      <div style={{ textAlign: 'right', flexShrink: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: scoreColor }}>
          {score > 0 ? score : '—'}
        </div>
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
  const [frames, setFrames] = useState<NarrativeFrame[]>(cached.frames ?? [])
  const [spikes, setSpikes] = useState<Spike[]>(cached.spikes ?? [])
  const [recent, setRecent] = useState<SourceItem[]>(cached.recent ?? [])
  const [loading, setLoading] = useState(!cached.frames)
  const [activeFilter, setActiveFilter] = useState<FilterKey>('all')

  const refresh = async () => {
    const [fr, sp, ra] = await Promise.allSettled([
      api.narrativeFrames(), api.spikes(), api.recentArticles(10),
    ])
    if (fr.status === 'fulfilled') setFrames(fr.value)
    if (sp.status === 'fulfilled') setSpikes(sp.value)
    if (ra.status === 'fulfilled') setRecent(ra.value)
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
    // Sort by composite importance score (urgency + posture + momentum +
    // stage + recent activity + growth + outlet diversity). This puts the
    // narratives that ACTUALLY matter today at the top of the Featured
    // Narratives section, instead of just "biggest historical pile."
    // Ties fall back to stage order, then total mentions, so the order is
    // deterministic even when scores collide.
    .sort((a, b) => {
      const ia = importanceScore(a), ib = importanceScore(b)
      if (ia !== ib) return ib - ia
      const sa = STAGE_ORDER.indexOf(a.stage), sb = STAGE_ORDER.indexOf(b.stage)
      if (sa !== sb) return sa - sb
      return b.mentions_total - a.mentions_total
    })

  const featuredFrames = filteredFrames.slice(0, 8)
  // Detail-panel candidates: any frame in an "actively moving" stage.
  // Previously only mainstream+spreading — meant a campaign with all its
  // frames in resurfacing/active showed an empty detail-panel grid.
  const topFrames = filteredFrames
    .filter(f => ['mainstream', 'spreading', 'resurfacing', 'active'].includes(f.stage))
    .slice(0, 2)

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
        <div style={{ padding: '16px 24px', borderRight: `1px solid ${C.border}` }}>
          {/* Race Sentiment — prominent peer card above the narrative cards.
              Markets + forecaster ratings shown separately (no blended number).
              Phase 1: manual values entered via the edit modal. Phase 2 will
              swap in scraped/API values without touching this component. */}
          <RaceSentimentCard />

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
                        text={'The eight most important narratives right now. Ranked by a combined score: how urgent the AI thinks it is, what action it calls for (defend / attack / amplify), whether it\'s viral or growing, where it sits in its lifecycle, and how many outlets are covering it this week. Filtering by Owner or Stage re-ranks within that subset.'}
                      />
                    </div>
                    <div style={{
                      display: 'inline-flex', alignItems: 'center', gap: 8,
                      marginLeft: 'auto',
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
                    {featuredFrames.map(f => <FeaturedCard key={f.id} frame={f} />)}
                  </div>
                </div>
              )}

              {/* Detail panels for top 2 active frames */}
              {topFrames.length > 0 && (
                <div style={{ marginBottom: 28 }}>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16 }}>
                    {topFrames.map(f => <DetailPanel key={f.id} frame={f} />)}
                  </div>
                </div>
              )}

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
              <InfoTooltip
                text={'The latest articles the system has pulled in that look race-relevant. The yellow number on the right is an AI-assigned relevance score from 0–100 — higher means more directly about your race.'}
                placement="left"
              />
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
            recent.map(item => <ArticleRow key={item.id} item={item} />)
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
