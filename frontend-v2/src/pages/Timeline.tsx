/**
 * Timeline view — how the race responds to narrative events.
 *
 * The chart's primary axis is market sentiment (Kalshi) over time —
 * sentiment IS the chart, not a backdrop. Events appear as pins ON the
 * line at the moment they happened, color-coded and sized by how much
 * the market moved in the 48h that followed.
 *
 * Below the chart, an impact-ranked list shows which events actually
 * moved the race the most. Each row carries a mini sparkline of the
 * local sentiment shape so you can see the swing in context.
 *
 * Why markets (not forecaster ratings): for "did this event move the
 * race," markets react within hours; forecaster ratings update monthly.
 * Markets are the right tool for event causation. For the structural
 * "is the race actually competitive" question, see the Dashboard
 * Race Sentiment card.
 */
import {
  Calendar, ChevronRight, FileText, Filter, Sparkles,
  TrendingDown, TrendingUp, X,
} from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '@/api/client'
import { QuadrantPalette, quadrantKey, quadrantNamedLabel } from '@/lib/quadrantColor'
import type { OwnerType, QuadrantKey } from '@/lib/quadrantColor'

/** Surname extraction shared with Landscape.tsx — see lastName() there.
 *  Used to swap "Pro-us" → "Pro-Cognetti" / "Anti-them" → "Anti-Bresnahan"
 *  in impact-row metadata and the side-panel header. */
function lastName(raw?: string): string {
  if (!raw) return ''
  const t = raw.trim()
  const last = (t.includes(',') ? t.split(',')[0] : t.split(/\s+/).pop() || '').trim()
  return last ? last[0].toUpperCase() + last.slice(1).toLowerCase() : ''
}
import type {
  NarrativeLifecycleEvent,
  RaceSentimentSnapshot,
  TimelineEvent as ApiTimelineEvent,
} from '@/api/types'

// ── Event types ──────────────────────────────────────────────────────────

type Category = 'emerged' | 'peaked' | 'faded' | 'top_articles'

const CATEGORY_LABEL: Record<Category, string> = {
  emerged: 'Narrative Emerged',
  peaked: 'Peak Activity',
  faded: 'Narrative Faded',
  top_articles: 'Top Articles',
}

const CATEGORY_ICON: Record<Category, typeof Calendar> = {
  emerged: Sparkles,
  peaked: TrendingUp,
  faded: TrendingDown,
  top_articles: FileText,
}

const CATEGORY_COLOR: Record<Category, string> = {
  emerged: 'var(--green)',
  peaked: 'var(--red)',
  faded: 'var(--text-3)',
  top_articles: '#3b82f6',
}

interface TimelineEvent {
  id: string
  category: Category
  date: Date
  title: string
  detail: string
  weight: number
  navigateUrl?: string
  // Kalshi % point move in the 48h after the event (null = not computable).
  delta48h: number | null
  // 4-quadrant color key derived from (owner_type, subject_type). Pin fill
  // and impact-list icon background use this. See lib/quadrantColor.ts.
  quadrant: QuadrantKey
}

const TIME_RANGES = [
  { key: '7',   label: '7d',   days: 7   },
  { key: '30',  label: '30d',  days: 30  },
  { key: '60',  label: '60d',  days: 60 },
  { key: '90',  label: '90d',  days: 90 },
  { key: '365', label: '1 y',  days: 365 },
] as const

// ── Helpers ──────────────────────────────────────────────────────────────

function parseUtcIso(iso: string): Date {
  const hasTz = /[zZ]|[+-]\d{2}:?\d{2}$/.test(iso)
  return hasTz ? new Date(iso) : new Date(iso + 'Z')
}

/** Closest snapshot to target time within maxDistMs (ignores null pcts). */
function closestSnap(
  snaps: RaceSentimentSnapshot[], targetMs: number, maxDistMs: number,
): RaceSentimentSnapshot | null {
  let best: RaceSentimentSnapshot | null = null
  let bestDist = Infinity
  for (const s of snaps) {
    if (s.candidate_pct === null) continue
    const d = Math.abs(parseUtcIso(s.captured_at).getTime() - targetMs)
    if (d > maxDistMs) continue
    if (d < bestDist) { bestDist = d; best = s }
  }
  return best
}

/** Kalshi % point move in the 48h after the event. Null if data is thin. */
function postEventDelta(
  eventDate: Date, snaps: RaceSentimentSnapshot[],
): number | null {
  if (snaps.length === 0) return null
  const HOUR = 3600_000
  const t = eventDate.getTime()
  const before = closestSnap(snaps, t, 18 * HOUR)
  const after = closestSnap(snaps, t + 48 * HOUR, 24 * HOUR)
  if (!before || !after) return null
  if (before.candidate_pct === null || after.candidate_pct === null) return null
  return after.candidate_pct - before.candidate_pct
}

/** Linear-interpolated market value at a given timestamp. Used to pin
 *  events ON the line. Returns null if the timestamp is outside the
 *  snapshot range with no data to interpolate from. */
function interpolateMarket(
  targetMs: number, snaps: RaceSentimentSnapshot[],
): number | null {
  if (snaps.length === 0) return null
  const sorted = [...snaps]
    .filter(s => s.candidate_pct !== null)
    .map(s => ({ t: parseUtcIso(s.captured_at).getTime(), v: s.candidate_pct as number }))
    .sort((a, b) => a.t - b.t)
  if (sorted.length === 0) return null
  if (targetMs <= sorted[0].t) return sorted[0].v
  if (targetMs >= sorted[sorted.length - 1].t) return sorted[sorted.length - 1].v
  for (let i = 0; i < sorted.length - 1; i++) {
    const a = sorted[i], b = sorted[i + 1]
    if (a.t <= targetMs && targetMs <= b.t) {
      const f = b.t > a.t ? (targetMs - a.t) / (b.t - a.t) : 0
      return a.v + f * (b.v - a.v)
    }
  }
  return null
}

/** Format a signed delta for display, e.g. "+2.3" / "−1.7" / "flat". */
function fmtDelta(d: number | null): string {
  if (d === null) return '—'
  if (Math.abs(d) < 0.2) return 'flat'
  const sign = d > 0 ? '+' : '−'
  return `${sign}${Math.abs(d).toFixed(1)}`
}

/** Color a delta by direction (green = candidate helped, red = hurt). */
function deltaColor(d: number | null): string {
  if (d === null) return 'var(--text-3)'
  if (Math.abs(d) < 0.2) return 'var(--text-3)'
  return d > 0 ? 'var(--green)' : 'var(--red)'
}

// ── Main component ───────────────────────────────────────────────────────

export function Timeline() {
  const [rangeKey, setRangeKey] = useState<typeof TIME_RANGES[number]['key']>('60')
  const [enabled, setEnabled] = useState<Record<Category, boolean>>({
    emerged: true, peaked: true, faded: true, top_articles: true,
  })
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [hoveredId, setHoveredId] = useState<string | null>(null)
  const [topArticles, setTopArticles] = useState<ApiTimelineEvent[]>([])
  const [lifecycle, setLifecycle] = useState<NarrativeLifecycleEvent[]>([])
  const [polySnaps, setPolySnaps] = useState<RaceSentimentSnapshot[]>([])
  const [kalshiSnaps, setKalshiSnaps] = useState<RaceSentimentSnapshot[]>([])
  // Surnames for quadrantNamedLabel ("Pro-Cognetti" / "Anti-Bresnahan"
  // instead of "Pro-us" / "Anti-them"). Fire-and-forget; if either fetch
  // fails the label falls back to the generic form so nothing breaks.
  const [candidateName, setCandidateName] = useState('')
  const [opponentName, setOpponentName] = useState('')

  const range = TIME_RANGES.find(r => r.key === rangeKey)!

  useEffect(() => {
    api.narrativeLifecycle(30).then(setLifecycle).catch(() => setLifecycle([]))
    api.campaign().then(c => setCandidateName(lastName(c.candidate_name))).catch(() => {})
    api.opponents().then(o => { if (o[0]) setOpponentName(lastName(o[0].name)) }).catch(() => {})
  }, [])

  useEffect(() => {
    api.raceSentimentEvents(range.days).then(setTopArticles).catch(() => setTopArticles([]))
    api.raceSentimentHistory('polymarket', range.days).then(setPolySnaps).catch(() => setPolySnaps([]))
    api.raceSentimentHistory('kalshi', range.days).then(setKalshiSnaps).catch(() => setKalshiSnaps([]))
  }, [range.days])

  // Build raw event list (impact computed in next memo).
  const rawEvents = useMemo<Omit<TimelineEvent, 'delta48h'>[]>(() => {
    const out: Omit<TimelineEvent, 'delta48h'>[] = []

    lifecycle.forEach(e => {
      const date = e.timestamp ? new Date(e.timestamp) : null
      if (!date || Number.isNaN(date.getTime())) return
      const quadrant = quadrantKey(
        (e.owner_type ?? null) as OwnerType | null,
        (e.subject_type ?? null) as OwnerType | null,
      )
      // Pin SIZE encodes narrative magnitude (NOT market impact).
      //   emerged / faded → frame's lifetime mention count
      //   peaked          → peak-day article count
      // For peaked we use peak_count specifically because that moment IS
      // about that day's surge; for emerged/faded the meaningful scale is
      // the narrative's full reach. The impact-ranked list below is where
      // market-impact magnitude is read.
      const totalMentions = e.total_mentions ?? 1
      if (e.type === 'narrative_emerged') {
        out.push({
          id: `emg-${e.frame_id}`,
          category: 'emerged',
          date,
          title: `${e.label} first appeared`,
          detail: `Earliest article match · narrative grew to ${totalMentions} lifetime mention${totalMentions === 1 ? '' : 's'}`,
          weight: totalMentions,
          navigateUrl: `/narratives/${e.frame_id}`,
          quadrant,
        })
      } else if (e.type === 'narrative_peaked') {
        const peak = e.peak_count ?? 1
        out.push({
          id: `pk-${e.frame_id}`,
          category: 'peaked',
          date,
          title: `${e.label} peaked`,
          detail: `Highest-activity day · ${peak} matched article${peak === 1 ? '' : 's'} (of ${totalMentions} lifetime)`,
          weight: peak,
          navigateUrl: `/narratives/${e.frame_id}`,
          quadrant,
        })
      } else if (e.type === 'narrative_faded') {
        out.push({
          id: `fd-${e.frame_id}`,
          category: 'faded',
          date,
          title: `${e.label} went quiet`,
          detail: `Last matched article · narrative ran for ${totalMentions} total mention${totalMentions === 1 ? '' : 's'} before going dormant`,
          weight: totalMentions,
          navigateUrl: `/narratives/${e.frame_id}`,
          quadrant,
        })
      }
    })

    // Top articles — cap to top 2 per ISO week by relevance score so dense
    // windows don't smear into a continuous blue stripe.
    const articleEvents = topArticles.filter(e => e.type === 'top_article' && e.timestamp)
    function isoWeekKey(d: Date): string {
      const tmp = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()))
      const dayOfWeek = tmp.getUTCDay() || 7
      tmp.setUTCDate(tmp.getUTCDate() + 4 - dayOfWeek)
      const yearStart = new Date(Date.UTC(tmp.getUTCFullYear(), 0, 1))
      const weekNo = Math.ceil(((tmp.getTime() - yearStart.getTime()) / 86400000 + 1) / 7)
      return `${tmp.getUTCFullYear()}-W${String(weekNo).padStart(2, '0')}`
    }
    const byWeek = new Map<string, ApiTimelineEvent[]>()
    for (const a of articleEvents) {
      const wk = isoWeekKey(new Date(a.timestamp!))
      if (!byWeek.has(wk)) byWeek.set(wk, [])
      byWeek.get(wk)!.push(a)
    }
    for (const arts of byWeek.values()) {
      arts.sort((a, b) => (b.score ?? 0) - (a.score ?? 0))
      arts.slice(0, 2).forEach(e => {
        const date = new Date(e.timestamp!)
        if (Number.isNaN(date.getTime())) return
        // Pin size scales with the article's relevance score (0-100). High
        // relevance = bigger pin. Divided by 4 so the scale lands in the
        // same ballpark as narrative mention counts (typical scores 70-95
        // become weight ~18-24).
        const score = e.score ?? 50
        // Quadrant comes from the backend's per-article cascade (highest-
        // confidence frame match → source_owner_type → perspective →
        // media). See backend/app/services/article_quadrant.py. A Cognetti
        // tweet attacking Bresnahan lands in `our_offense` because its
        // top frame match is "Bresnahan's Stock Trades" (owner=candidate,
        // subject=opponent) — not as gray "Neutral".
        const quadrant = quadrantKey(
          (e.owner_type ?? null) as OwnerType | null,
          (e.subject_type ?? null) as OwnerType | null,
        )
        out.push({
          id: `art-${e.article_id}-${e.timestamp}`,
          category: 'top_articles',
          date,
          title: e.label,
          detail: `Top relevance${e.source_name ? ` · ${e.source_name}` : ''}`,
          weight: Math.max(1, Math.round(score / 4)),
          navigateUrl: e.article_id ? `/articles/${e.article_id}` : undefined,
          quadrant,
        })
      })
    }

    return out
  }, [lifecycle, topArticles])

  // Compute post-event impact (Kalshi 48h delta) for each event.
  const allEvents = useMemo<TimelineEvent[]>(
    () => rawEvents.map(e => ({ ...e, delta48h: postEventDelta(e.date, kalshiSnaps) })),
    [rawEvents, kalshiSnaps],
  )

  // Date range
  const now = new Date()
  const startDate = new Date(now.getTime() - range.days * 24 * 3600 * 1000)

  // Visible: filter to enabled categories and date range
  const visible = useMemo(
    () => allEvents
      .filter(e => enabled[e.category])
      .filter(e => e.date >= startDate && e.date <= now)
      .sort((a, b) => a.date.getTime() - b.date.getTime()),
    [allEvents, enabled, startDate, now],
  )

  // Impact-ranked: only events with computable delta, sorted by magnitude
  const impactRanked = useMemo(
    () => visible.filter(e => e.delta48h !== null && Math.abs(e.delta48h) >= 0.2)
      .sort((a, b) => Math.abs(b.delta48h!) - Math.abs(a.delta48h!)),
    [visible],
  )

  const selected = selectedId ? visible.find(e => e.id === selectedId) : null

  return (
    <div style={{ height: 'calc(100vh - 48px)', display: 'flex', flexDirection: 'column', background: 'var(--bg-1)', color: 'var(--text-1)' }}>
      <HeaderBar
        rangeKey={rangeKey} setRangeKey={setRangeKey}
        enabled={enabled} setEnabled={setEnabled}
        eventCount={visible.length}
      />

      <SentimentSummary polySnaps={polySnaps} kalshiSnaps={kalshiSnaps} />

      <div style={{ flex: 1, minHeight: 0, display: 'flex' }}>
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>
          {/* Chart — sentiment as spine, events as pins on the line */}
          <div style={{
            flex: '1 1 55%', minHeight: 280, overflow: 'hidden',
            background: 'var(--bg-sidebar)', borderBottom: '1px solid var(--bg-4)',
          }}>
            <SentimentChart
              kalshiSnaps={kalshiSnaps}
              polySnaps={polySnaps}
              events={visible}
              startDate={startDate}
              now={now}
              range={range}
              selectedId={selectedId}
              hoveredId={hoveredId}
              setSelectedId={setSelectedId}
              setHoveredId={setHoveredId}
            />
          </div>
          {/* Impact-ranked list */}
          <div style={{ flex: '1 1 45%', minHeight: 0, overflowY: 'auto' }}>
            <ImpactList
              events={impactRanked}
              kalshiSnaps={kalshiSnaps}
              selectedId={selectedId}
              setSelectedId={setSelectedId}
              candidateName={candidateName}
              opponentName={opponentName}
            />
          </div>
        </div>

        {selected && (
          <SidePanel
            event={selected}
            onClose={() => setSelectedId(null)}
            candidateName={candidateName}
            opponentName={opponentName}
          />
        )}
      </div>
    </div>
  )
}

// ── Header bar ────────────────────────────────────────────────────────────

function HeaderBar({
  rangeKey, setRangeKey, enabled, setEnabled, eventCount,
}: {
  rangeKey: typeof TIME_RANGES[number]['key']
  setRangeKey: (k: typeof TIME_RANGES[number]['key']) => void
  enabled: Record<Category, boolean>
  setEnabled: React.Dispatch<React.SetStateAction<Record<Category, boolean>>>
  eventCount: number
}) {
  const categories: Category[] = ['emerged', 'peaked', 'faded', 'top_articles']
  return (
    <div style={{
      flexShrink: 0, padding: '14px 20px', borderBottom: '1px solid #2f2f2f',
      display: 'flex', alignItems: 'center', gap: 20,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <Calendar size={18} color="#a78bfa" />
        <div>
          <div style={{ fontSize: 16, fontWeight: 700 }}>Timeline</div>
          <div style={{ fontSize: 11, color: 'var(--text-2)', marginTop: 1 }}>
            Which events moved the race · {eventCount} events
          </div>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 4, marginLeft: 20 }}>
        {TIME_RANGES.map(r => {
          const active = rangeKey === r.key
          return (
            <button
              key={r.key} onClick={() => setRangeKey(r.key)}
              style={{
                padding: '5px 11px', borderRadius: 6,
                border: '1px solid ' + (active ? 'var(--accent)' : 'var(--bg-4)'),
                background: active ? 'rgba(255, 191, 0, 0.12)' : 'var(--bg-2)',
                color: active ? 'var(--accent)' : 'var(--text-2)',
                cursor: 'pointer', fontSize: 12, fontWeight: 500, fontFamily: 'inherit',
              }}
            >
              {r.label}
            </button>
          )
        })}
      </div>

      <div style={{ display: 'flex', gap: 5, alignItems: 'center', marginLeft: 'auto' }}>
        <Filter size={12} color="var(--text-3)" />
        {categories.map(cat => {
          const Icon = CATEGORY_ICON[cat]
          const active = enabled[cat]
          const color = CATEGORY_COLOR[cat]
          return (
            <button
              key={cat}
              onClick={() => setEnabled(e => ({ ...e, [cat]: !e[cat] }))}
              style={{
                display: 'flex', alignItems: 'center', gap: 5,
                padding: '5px 10px', borderRadius: 6,
                border: '1px solid ' + (active ? color : 'var(--bg-4)'),
                background: active ? `${color}1f` : 'var(--bg-2)',
                color: active ? color : 'var(--text-3)',
                cursor: 'pointer', fontSize: 12, fontWeight: 500, fontFamily: 'inherit',
              }}
            >
              <Icon size={11} />
              {CATEGORY_LABEL[cat]}
            </button>
          )
        })}
      </div>
    </div>
  )
}

// ── Sentiment summary strip (current Kalshi + Polymarket values) ──────────

function latestPct(snaps: RaceSentimentSnapshot[]): { pct: number; at: Date } | null {
  let best: { pct: number; at: Date } | null = null
  for (const s of snaps) {
    if (s.candidate_pct === null) continue
    const at = parseUtcIso(s.captured_at)
    if (Number.isNaN(at.getTime())) continue
    if (!best || at.getTime() > best.at.getTime()) {
      best = { pct: s.candidate_pct, at }
    }
  }
  return best
}

function SentimentSummary({
  polySnaps, kalshiSnaps,
}: {
  polySnaps: RaceSentimentSnapshot[]
  kalshiSnaps: RaceSentimentSnapshot[]
}) {
  const poly = latestPct(polySnaps)
  const kalshi = latestPct(kalshiSnaps)
  const fmtAge = (d: Date) => {
    const hrs = (Date.now() - d.getTime()) / 3600_000
    if (hrs < 1) return 'just now'
    if (hrs < 24) return `${Math.round(hrs)}h ago`
    return `${Math.round(hrs / 24)}d ago`
  }
  const Item = ({ name, color, snap }: { name: string; color: string; snap: { pct: number; at: Date } | null }) => (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 8,
      padding: '4px 10px', borderRadius: 6,
      background: 'var(--bg-2)', border: '1px solid var(--bg-4)',
    }}>
      <span style={{ width: 8, height: 8, borderRadius: '50%', background: color, display: 'inline-block' }} />
      <span style={{ fontSize: 11, color: 'var(--text-2)', fontWeight: 600 }}>{name}</span>
      <span style={{ fontSize: 13, color: 'var(--text-1)', fontWeight: 700 }}>
        {snap ? `${snap.pct.toFixed(0)}%` : '—'}
      </span>
      {snap && (
        <span style={{ fontSize: 10, color: 'var(--text-3)' }}>{fmtAge(snap.at)}</span>
      )}
    </div>
  )
  return (
    <div style={{
      flexShrink: 0, padding: '8px 20px',
      borderBottom: '1px solid #2f2f2f',
      display: 'flex', alignItems: 'center', gap: 10,
      background: 'var(--bg-1)',
    }}>
      <span style={{
        fontSize: 11, color: 'var(--text-3)',
        textTransform: 'uppercase', letterSpacing: '0.06em', fontWeight: 700,
      }}>
        Market Reaction · Cognetti win %
      </span>
      <Item name="Kalshi" color="#22d3ee" snap={kalshi} />
      <Item name="Polymarket" color="#a78bfa" snap={poly} />
      <span style={{ fontSize: 10, color: 'var(--text-3)', marginLeft: 'auto' }}>
        Markets react fast to news. Forecaster ratings (Cook / Sabato / IE) live on the Dashboard.
      </span>
    </div>
  )
}

// ── Sentiment chart (the spine) ───────────────────────────────────────────

function SentimentChart({
  kalshiSnaps, polySnaps, events, startDate, now, range,
  selectedId, hoveredId, setSelectedId, setHoveredId,
}: {
  kalshiSnaps: RaceSentimentSnapshot[]
  polySnaps: RaceSentimentSnapshot[]
  events: TimelineEvent[]
  startDate: Date
  now: Date
  range: typeof TIME_RANGES[number]
  selectedId: string | null
  hoveredId: string | null
  setSelectedId: (id: string | null) => void
  setHoveredId: (id: string | null) => void
}) {
  // Match the SVG to the actual container box. The previous code derived
  // width/height from `window.innerWidth` and `window.innerHeight * 0.34`,
  // which drifted from the parent's real flex-allotted size — the chart
  // ended up shorter/narrower than its container and left blank gutters
  // below and to the right. ResizeObserver on a wrapping div keeps the
  // SVG flush with whatever space the layout actually gives it.
  const containerRef = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)
  const [height, setHeight] = useState(0)

  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const ro = new ResizeObserver(entries => {
      const r = entries[0].contentRect
      setWidth(r.width)
      setHeight(r.height)
    })
    ro.observe(el)
    const r = el.getBoundingClientRect()
    setWidth(r.width)
    setHeight(r.height)
    return () => ro.disconnect()
  }, [])

  // LEFT_PAD must clear half the first tick label's width (e.g. "Mar 30" is
  // ~37px wide, centered → needs ~20px of padding so the label doesn't get
  // clipped at the left edge of the SVG).
  const LEFT_PAD = 24
  const RIGHT_PAD = 56   // room for Y-axis labels on the right
  const TOP_PAD = 20
  const BOTTOM_PAD = 36

  // Y-axis range: auto-fit to Kalshi data with padding, but always include 50%
  // and a reasonable spread so the line isn't squished to one edge.
  const allPcts = [
    ...kalshiSnaps.filter(s => s.candidate_pct !== null).map(s => s.candidate_pct!),
    ...polySnaps.filter(s => s.candidate_pct !== null).map(s => s.candidate_pct!),
  ]
  let yMin: number, yMax: number
  if (allPcts.length === 0) {
    yMin = 30; yMax = 70
  } else {
    const dataMin = Math.min(...allPcts)
    const dataMax = Math.max(...allPcts)
    yMin = Math.min(dataMin - 4, 48)   // ensure 50% line is in frame
    yMax = Math.max(dataMax + 4, 52)
    // Clamp so axis stays sane
    yMin = Math.max(0, Math.floor(yMin / 5) * 5)
    yMax = Math.min(100, Math.ceil(yMax / 5) * 5)
  }
  const yRange = yMax - yMin

  function xFor(date: Date): number {
    const total = now.getTime() - startDate.getTime()
    const since = date.getTime() - startDate.getTime()
    return LEFT_PAD + (since / total) * (width - LEFT_PAD - RIGHT_PAD)
  }
  function yFor(pct: number): number {
    const clamped = Math.max(yMin, Math.min(yMax, pct))
    return TOP_PAD + (1 - (clamped - yMin) / yRange) * (height - TOP_PAD - BOTTOM_PAD)
  }

  function snapsToPath(snaps: RaceSentimentSnapshot[]): string {
    const pts = snaps
      .filter(s => s.candidate_pct !== null)
      .map(s => ({ t: parseUtcIso(s.captured_at).getTime(), v: s.candidate_pct as number }))
      .sort((a, b) => a.t - b.t)
    if (pts.length === 0) return ''
    return pts.map((p, i) => `${i === 0 ? 'M' : 'L'} ${xFor(new Date(p.t)).toFixed(1)},${yFor(p.v).toFixed(1)}`).join(' ')
  }
  const kalshiPath = snapsToPath(kalshiSnaps)
  const polyPath = snapsToPath(polySnaps)
  const hasSentiment = kalshiPath !== '' || polyPath !== ''

  // Time axis ticks
  function timeTicks() {
    const ticks: { date: Date; label: string }[] = []
    const totalDays = range.days
    const spacing =
      totalDays <= 30 ? 5
        : totalDays <= 90 ? 14
        : totalDays <= 180 ? 30
        : 60
    for (let d = 0; d <= totalDays; d += spacing) {
      const date = new Date(now.getTime() - (totalDays - d) * 24 * 3600 * 1000)
      const month = date.toLocaleString('en', { month: 'short' })
      const day = date.getDate()
      ticks.push({ date, label: `${month} ${day}` })
    }
    return ticks
  }

  // Y-axis ticks every 5 percentage points
  const yTicks: number[] = []
  for (let p = Math.ceil(yMin / 5) * 5; p <= yMax; p += 5) yTicks.push(p)

  // Pin radius from narrative magnitude (NOT market impact). Bigger
  // weight = larger narrative. Scale: 1 → 5, 5 → 7, 20 → 10, 80 → 13, 120+ → 14
  function pinRadius(weight: number): number {
    return Math.max(4, Math.min(14, 4 + Math.sqrt(Math.max(1, weight)) * 1.0))
  }

  // Pin fill = quadrant color (who/what the event is for/against). Impact
  // direction (+/-) is shown via the size, tooltip, and the impact list's
  // colored delta number — color encoding stays one-axis-per-channel.
  function pinColor(quadrant: QuadrantKey): string {
    return QuadrantPalette[quadrant]
  }

  return (
    <div ref={containerRef} style={{ width: '100%', height: '100%' }}>
    <svg
      width={width} height={height}
      style={{ display: 'block' }}
      onClick={() => setSelectedId(null)}
    >
      {/* Y-axis gridlines + labels (right side) */}
      {yTicks.map(p => {
        const y = yFor(p)
        const is50 = p === 50
        return (
          <g key={p}>
            <line
              x1={LEFT_PAD} y1={y} x2={width - RIGHT_PAD} y2={y}
              stroke={is50 ? 'rgba(255,255,255,0.12)' : 'rgba(255,255,255,0.04)'}
              strokeWidth={is50 ? 1 : 0.5}
              strokeDasharray={is50 ? '4 3' : undefined}
            />
            <text
              x={width - RIGHT_PAD + 6} y={y + 3}
              fill="var(--text-3)" fontSize={10}
            >
              {p}%
            </text>
          </g>
        )
      })}

      {/* Time-axis ticks */}
      {timeTicks().map(t => {
        const x = xFor(t.date)
        return (
          <g key={t.label}>
            <line
              x1={x} y1={TOP_PAD} x2={x} y2={height - BOTTOM_PAD}
              stroke="rgba(255,255,255,0.03)" strokeWidth={0.5}
            />
            <text
              x={x} y={height - BOTTOM_PAD + 16}
              textAnchor="middle" fill="var(--text-2)" fontSize={11}
            >
              {t.label}
            </text>
          </g>
        )
      })}

      {/* TODAY marker */}
      <g>
        <line
          x1={xFor(now)} y1={TOP_PAD - 4} x2={xFor(now)} y2={height - BOTTOM_PAD}
          stroke="var(--accent)" strokeWidth={1.5} strokeDasharray="2 3" opacity={0.6}
        />
        <text
          x={xFor(now)} y={TOP_PAD - 8}
          textAnchor="middle" fill="var(--accent)" fontSize={10} fontWeight={700}
        >
          TODAY
        </text>
      </g>

      {/* Polymarket line (secondary, faint) */}
      {polyPath && (
        <path
          d={polyPath} stroke="#a78bfa" strokeOpacity={0.35}
          strokeWidth={1.5} fill="none"
          strokeLinejoin="round" strokeLinecap="round"
        />
      )}
      {/* Kalshi line (primary) */}
      {kalshiPath && (
        <path
          d={kalshiPath} stroke="#22d3ee" strokeOpacity={0.85}
          strokeWidth={2.25} fill="none"
          strokeLinejoin="round" strokeLinecap="round"
        />
      )}

      {/* Event pins — ON the kalshi line, color = post-event delta sign */}
      {events.map(e => {
        const marketAt = interpolateMarket(e.date.getTime(), kalshiSnaps)
        if (marketAt === null) return null
        const cx = xFor(e.date)
        const cy = yFor(marketAt)
        const r = pinRadius(e.weight)
        const fill = pinColor(e.quadrant)
        const isSel = selectedId === e.id
        const isHov = hoveredId === e.id
        return (
          <g
            key={e.id}
            onClick={ev => { ev.stopPropagation(); setSelectedId(e.id) }}
            onMouseEnter={() => setHoveredId(e.id)}
            onMouseLeave={() => setHoveredId(null)}
            style={{ cursor: 'pointer' }}
          >
            {(isSel || isHov) && (
              <circle
                cx={cx} cy={cy} r={r + 4} fill="none"
                stroke={isSel ? 'var(--accent)' : 'rgba(255,255,255,0.45)'}
                strokeWidth={2}
              />
            )}
            <circle
              cx={cx} cy={cy} r={r}
              fill={fill} fillOpacity={0.92}
              stroke="rgba(0,0,0,0.55)" strokeWidth={1}
            />
            {isHov && (
              <g style={{ pointerEvents: 'none' }}>
                <rect
                  x={cx - 130} y={cy - r - 50}
                  width={260} height={42} rx={6}
                  fill="var(--bg-2)" stroke="var(--border)" strokeWidth={1}
                />
                <text
                  x={cx} y={cy - r - 33}
                  textAnchor="middle" fill="var(--text-1)" fontSize={11} fontWeight={600}
                >
                  {e.title.length > 38 ? e.title.slice(0, 36) + '…' : e.title}
                </text>
                <text
                  x={cx} y={cy - r - 18}
                  textAnchor="middle" fill={deltaColor(e.delta48h)} fontSize={11} fontWeight={700}
                >
                  Kalshi {fmtDelta(e.delta48h)}{e.delta48h !== null ? 'pt in 48h' : ''}
                </text>
              </g>
            )}
          </g>
        )
      })}

      {!hasSentiment && (
        <text
          x={width / 2} y={height / 2}
          textAnchor="middle" fill="var(--text-3)" fontSize={13}
        >
          No market data in this range — pick a shorter window.
        </text>
      )}
    </svg>
    </div>
  )
}

// ── Impact-ranked list ────────────────────────────────────────────────────

function ImpactList({
  events, kalshiSnaps, selectedId, setSelectedId, candidateName, opponentName,
}: {
  events: TimelineEvent[]
  kalshiSnaps: RaceSentimentSnapshot[]
  selectedId: string | null
  setSelectedId: (id: string | null) => void
  candidateName: string
  opponentName: string
}) {
  if (events.length === 0) {
    return (
      <div style={{ padding: 24, color: 'var(--text-3)', fontSize: 13, textAlign: 'center' }}>
        No events have a measurable market impact in this range yet.
        <div style={{ marginTop: 6, fontSize: 11 }}>
          (Need at least one Kalshi snapshot in the 18h before the event and 24h around event + 48h.)
        </div>
      </div>
    )
  }
  return (
    <div>
      <div style={{
        position: 'sticky', top: 0, zIndex: 1,
        padding: '10px 20px 8px',
        background: 'var(--bg-1)', borderBottom: '1px solid var(--bg-4)',
        fontSize: 11, color: 'var(--text-3)', textTransform: 'uppercase',
        letterSpacing: '0.08em', fontWeight: 700,
      }}>
        Top moments by market impact · {events.length}
      </div>
      {events.map(e => (
        <ImpactRow
          key={e.id} event={e}
          kalshiSnaps={kalshiSnaps}
          selected={selectedId === e.id}
          onSelect={() => setSelectedId(e.id)}
          candidateName={candidateName}
          opponentName={opponentName}
        />
      ))}
    </div>
  )
}

function ImpactRow({
  event, kalshiSnaps, selected, onSelect, candidateName, opponentName,
}: {
  event: TimelineEvent
  kalshiSnaps: RaceSentimentSnapshot[]
  selected: boolean
  onSelect: () => void
  candidateName: string
  opponentName: string
}) {
  const delta = event.delta48h
  const dColor = deltaColor(delta)
  const dateStr = event.date.toLocaleDateString('en', { month: 'short', day: 'numeric' })
  return (
    <div
      onClick={onSelect}
      style={{
        display: 'grid',
        gridTemplateColumns: '1fr 110px 96px 16px',
        gap: 12, alignItems: 'center',
        padding: '10px 20px',
        cursor: 'pointer',
        borderBottom: '1px solid var(--bg-3)',
        background: selected ? 'rgba(255,191,0,0.06)' : 'transparent',
      }}
      onMouseEnter={e => { if (!selected) e.currentTarget.style.background = 'rgba(255,255,255,0.02)' }}
      onMouseLeave={e => { if (!selected) e.currentTarget.style.background = 'transparent' }}
    >
      <div style={{ minWidth: 0 }}>
        <div style={{
          fontSize: 13, color: 'var(--text-1)', fontWeight: 500,
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}>
          {event.title}
        </div>
        <div style={{ fontSize: 11, color: 'var(--text-3)' }}>
          {quadrantNamedLabel(event.quadrant, candidateName, opponentName)} · {CATEGORY_LABEL[event.category]} · {dateStr}
        </div>
      </div>
      <Sparkline event={event} kalshiSnaps={kalshiSnaps} />
      <div style={{ textAlign: 'right' }}>
        <div style={{ color: dColor, fontSize: 18, fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>
          {fmtDelta(delta)}{delta !== null && Math.abs(delta) >= 0.2 ? 'pt' : ''}
        </div>
        <div style={{ fontSize: 10, color: 'var(--text-3)' }}>Kalshi · 48h</div>
      </div>
      <ChevronRight size={14} color="var(--text-3)" />
    </div>
  )
}

// ── Mini sparkline: Kalshi ±3 days around an event ────────────────────────

function Sparkline({
  event, kalshiSnaps,
}: {
  event: TimelineEvent
  kalshiSnaps: RaceSentimentSnapshot[]
}) {
  const W = 96, H = 28
  const eventMs = event.date.getTime()
  const HOUR = 3600_000
  const winStart = eventMs - 3 * 24 * HOUR
  const winEnd = eventMs + 3 * 24 * HOUR

  const pts = kalshiSnaps
    .filter(s => s.candidate_pct !== null)
    .map(s => ({ t: parseUtcIso(s.captured_at).getTime(), v: s.candidate_pct as number }))
    .filter(p => p.t >= winStart && p.t <= winEnd)
    .sort((a, b) => a.t - b.t)

  if (pts.length < 2) {
    return <div style={{ width: W, height: H, color: 'var(--text-3)', fontSize: 10 }}>—</div>
  }

  const vMin = Math.min(...pts.map(p => p.v)) - 0.5
  const vMax = Math.max(...pts.map(p => p.v)) + 0.5
  const vRange = Math.max(0.001, vMax - vMin)

  function x(t: number) { return ((t - winStart) / (winEnd - winStart)) * W }
  function y(v: number) { return H - 2 - ((v - vMin) / vRange) * (H - 4) }

  const path = pts.map((p, i) => `${i === 0 ? 'M' : 'L'} ${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join(' ')
  const evX = x(eventMs)
  return (
    <svg width={W} height={H} style={{ display: 'block' }}>
      {/* Event marker — vertical guide at t=0 */}
      <line x1={evX} y1={0} x2={evX} y2={H} stroke="rgba(255,255,255,0.18)" strokeWidth={1} strokeDasharray="2 2" />
      <path d={path} stroke="#22d3ee" strokeWidth={1.5} fill="none" strokeLinejoin="round" strokeLinecap="round" />
      {/* Event point */}
      {(() => {
        // value at event time, interpolated
        let val: number | null = null
        if (pts[0].t >= eventMs) val = pts[0].v
        else if (pts[pts.length - 1].t <= eventMs) val = pts[pts.length - 1].v
        else {
          for (let i = 0; i < pts.length - 1; i++) {
            if (pts[i].t <= eventMs && eventMs <= pts[i + 1].t) {
              const f = (eventMs - pts[i].t) / (pts[i + 1].t - pts[i].t)
              val = pts[i].v + f * (pts[i + 1].v - pts[i].v)
              break
            }
          }
        }
        if (val === null) return null
        return <circle cx={evX} cy={y(val)} r={2.5} fill="var(--accent)" />
      })()}
    </svg>
  )
}

// ── Side panel for selected event ────────────────────────────────────────

function SidePanel({
  event, onClose, candidateName, opponentName,
}: {
  event: TimelineEvent
  onClose: () => void
  candidateName: string
  opponentName: string
}) {
  const dateStr = event.date.toLocaleDateString('en', { month: 'short', day: 'numeric', year: 'numeric' })
  const Icon = CATEGORY_ICON[event.category]
  const qColor = QuadrantPalette[event.quadrant]
  const dColor = deltaColor(event.delta48h)
  return (
    <div style={{
      width: 360, flexShrink: 0, borderLeft: '1px solid #2f2f2f',
      background: 'var(--bg-2)', overflowY: 'auto',
    }}>
      <div style={{ padding: '18px 20px', borderBottom: '1px solid #2f2f2f' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
          <Icon size={14} color={qColor} />
          <span style={{
            fontSize: 10, color: 'var(--text-2)', textTransform: 'uppercase',
            fontWeight: 700, letterSpacing: '0.08em',
          }}>
            {quadrantNamedLabel(event.quadrant, candidateName, opponentName)} · {CATEGORY_LABEL[event.category]}
          </span>
          <button
            onClick={onClose}
            style={{
              marginLeft: 'auto', background: 'transparent', border: 'none',
              color: 'var(--text-3)', cursor: 'pointer', padding: 2,
            }}
          >
            <X size={14} />
          </button>
        </div>
        <div style={{ fontSize: 18, fontWeight: 700, lineHeight: 1.3, marginBottom: 8 }}>
          {event.title}
        </div>
        <div style={{ fontSize: 13, color: 'var(--text-2)', lineHeight: 1.5, marginBottom: 14 }}>
          {event.detail}
        </div>
        <div style={{ display: 'flex', gap: 14, fontSize: 11, color: 'var(--text-3)' }}>
          <div>
            <strong style={{ color: 'var(--text-1)' }}>{dateStr}</strong>
          </div>
          <div>·</div>
          <div>
            Market impact:{' '}
            <strong style={{ color: dColor }}>
              Kalshi {fmtDelta(event.delta48h)}{event.delta48h !== null && Math.abs(event.delta48h) >= 0.2 ? 'pt / 48h' : ''}
            </strong>
          </div>
        </div>
      </div>
      {event.navigateUrl && (
        <div style={{ padding: '12px 20px' }}>
          <a
            href={event.navigateUrl}
            style={{
              fontSize: 13, color: 'var(--accent)',
              textDecoration: 'none', fontWeight: 600,
            }}
          >
            Open detail →
          </a>
        </div>
      )}
    </div>
  )
}
