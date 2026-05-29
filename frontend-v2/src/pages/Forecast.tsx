import { Activity, FileText, Layers, TrendingUp } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import type { CSSProperties } from 'react'
import { Link } from 'react-router-dom'
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { api } from '@/api/client'
import type {
  RaceSentimentSnapshot,
  TimelineEvent,
  TimelineEventType,
} from '@/api/types'
import { InfoTooltip } from '@/components/InfoTooltip'

// ─────────────────────────────────────────────────────────────────────────────
// Forecast page
//
// Plots prediction-market implied probabilities (Polymarket + Kalshi) for
// the candidate and opponent over time, and overlays narrative events
// (frame promotions, stage transitions, top articles) as vertical markers.
// Polymarket renders as solid lines, Kalshi as dashed — divergence between
// the two is itself signal (thin-market noise vs. genuine disagreement).
//
// Below the chart, each recent event gets a card showing what Polymarket
// did in the 24h / 72h / 7d after it. Cards stay Polymarket-only for now
// because Kalshi history is much sparser; revisit once snapshot count grows.
//
// IMPORTANT: the cards report descriptive temporal context, NOT causal
// claims. Correlation isn't causation — markets move for many reasons.
// ─────────────────────────────────────────────────────────────────────────────

const C = {
  bg1: 'var(--bg-1)', bg2: 'var(--bg-2)', bg3: 'var(--bg-3)',
  border: 'var(--border)', borderBright: 'var(--border-bright)',
  text1: 'var(--text-1)', text2: 'var(--text-2)', text3: 'var(--text-3)',
  candidate: 'var(--candidate)', opponent: 'var(--opponent)',
  accent: 'var(--accent)', green: 'var(--green)', red: 'var(--red)',
}

// Per-type styling. The shapes mirror the icons used in the legend so
// the user can match a marker to a category at a glance.
const EVENT_STYLE: Record<TimelineEventType, {
  color: string
  label: string
  Icon: typeof Layers
}> = {
  frame_created:      { color: '#0ea5e9', label: 'Narrative promoted',   Icon: Layers },
  frame_stage_change: { color: '#f59e0b', label: 'Stage change',          Icon: TrendingUp },
  top_article:        { color: '#a78bfa', label: 'Top article',           Icon: FileText },
}

const HEADER_HELP =
  'Implied win probabilities for both candidates over the last 30 days, from two ' +
  'prediction markets: Polymarket (solid lines) and Kalshi (dashed). Divergence ' +
  'between the markets is itself signal — thin liquidity, different trader pools, ' +
  'or genuine disagreement on where the race is.\n\n' +
  'Vertical markers flag narrative events (frames promoted, stages changing, top ' +
  'articles). The cards below describe what Polymarket did in the 24h / 72h / 7d ' +
  'after each event — descriptively, not causally. Correlation isn\'t causation.\n\n' +
  'Reminder: PA-08\'s Polymarket has ~$2K total liquidity, so single trades can move the ' +
  'price several points. Treat short-window swings with appropriate skepticism.'

// ─── Helpers ────────────────────────────────────────────────────────────────

function parseUtcIso(iso: string): Date {
  const hasTz = /[zZ]|[+-]\d{2}:?\d{2}$/.test(iso)
  return hasTz ? new Date(iso) : new Date(iso + 'Z')
}

function formatRelativeTime(iso: string): string {
  const t = parseUtcIso(iso).getTime()
  const diffMin = Math.round((Date.now() - t) / 60000)
  if (diffMin < 1) return 'just now'
  if (diffMin < 60) return `${diffMin}m ago`
  const diffH = Math.round(diffMin / 60)
  if (diffH < 24) return `${diffH}h ago`
  const diffD = Math.round(diffH / 24)
  return `${diffD}d ago`
}

function dayBucket(iso: string): string {
  return parseUtcIso(iso).toISOString().slice(0, 10)
}

// ─── Window math: descriptive temporal context, NOT causation ───────────────

interface EventWindow {
  event: TimelineEvent
  atEvent: number | null      // candidate_pct closest to the event timestamp
  after24h: number | null     // candidate_pct ~24h after
  after72h: number | null     // candidate_pct ~72h after
  after7d: number | null
}

function closestSnapshot(
  snaps: RaceSentimentSnapshot[],
  targetMs: number,
  maxDistMs: number,
): RaceSentimentSnapshot | null {
  let best: RaceSentimentSnapshot | null = null
  let bestDist = Infinity
  for (const s of snaps) {
    if (s.candidate_pct === null) continue
    const d = Math.abs(parseUtcIso(s.captured_at).getTime() - targetMs)
    if (d > maxDistMs) continue
    if (d < bestDist) {
      bestDist = d
      best = s
    }
  }
  return best
}

function computeEventWindow(
  event: TimelineEvent,
  snaps: RaceSentimentSnapshot[],
): EventWindow {
  const t = parseUtcIso(event.timestamp).getTime()
  const HOUR = 3600_000
  return {
    event,
    atEvent:   closestSnapshot(snaps, t,             12 * HOUR)?.candidate_pct ?? null,
    after24h:  closestSnapshot(snaps, t + 24 * HOUR, 18 * HOUR)?.candidate_pct ?? null,
    after72h:  closestSnapshot(snaps, t + 72 * HOUR, 24 * HOUR)?.candidate_pct ?? null,
    after7d:   closestSnapshot(snaps, t + 7 * 24 * HOUR, 36 * HOUR)?.candidate_pct ?? null,
  }
}

// ─── Chart data shape ───────────────────────────────────────────────────────

interface ChartPoint {
  ts: number            // ms since epoch — used for x-axis numeric scale
  date: string          // pretty date label
  polyCandidate: number | null
  polyOpponent: number | null
  kalshiCandidate: number | null
  kalshiOpponent: number | null
}

// Bucket snapshots by UTC day. Multiple intra-day snapshots get averaged
// so the chart shows one point per market per day. This keeps Polymarket
// and Kalshi aligned even when their capture cadences differ.
function bucketByDay(snaps: RaceSentimentSnapshot[]): Map<string, { cand: number | null; opp: number | null }> {
  const sumByDay = new Map<string, { candSum: number; candN: number; oppSum: number; oppN: number }>()
  for (const s of snaps) {
    const day = parseUtcIso(s.captured_at).toISOString().slice(0, 10)
    if (!sumByDay.has(day)) sumByDay.set(day, { candSum: 0, candN: 0, oppSum: 0, oppN: 0 })
    const b = sumByDay.get(day)!
    if (s.candidate_pct !== null) { b.candSum += s.candidate_pct; b.candN += 1 }
    if (s.opponent_pct !== null) { b.oppSum += s.opponent_pct; b.oppN += 1 }
  }
  const out = new Map<string, { cand: number | null; opp: number | null }>()
  for (const [day, b] of sumByDay) {
    out.set(day, {
      cand: b.candN > 0 ? b.candSum / b.candN : null,
      opp: b.oppN > 0 ? b.oppSum / b.oppN : null,
    })
  }
  return out
}

function buildChartData(
  polySnaps: RaceSentimentSnapshot[],
  kalshiSnaps: RaceSentimentSnapshot[],
): ChartPoint[] {
  const poly = bucketByDay(polySnaps)
  const kalshi = bucketByDay(kalshiSnaps)
  const allDays = new Set<string>([...poly.keys(), ...kalshi.keys()])
  const sortedDays = Array.from(allDays).sort()
  return sortedDays.map(day => {
    const ts = new Date(day + 'T12:00:00Z').getTime()
    const p = poly.get(day) ?? { cand: null, opp: null }
    const k = kalshi.get(day) ?? { cand: null, opp: null }
    return {
      ts,
      date: new Date(ts).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
      polyCandidate: p.cand,
      polyOpponent: p.opp,
      kalshiCandidate: k.cand,
      kalshiOpponent: k.opp,
    }
  })
}

// Group events by day so we can render the same-day markers as a single
// vertical line (clicking it expands the day's events). This stops the
// chart from being a wall of vertical lines when 5 things happen on
// 2026-05-26.
interface EventDay {
  dayMs: number
  date: string
  events: TimelineEvent[]
}

function groupEventsByDay(events: TimelineEvent[]): EventDay[] {
  const byDay = new Map<string, EventDay>()
  for (const ev of events) {
    const d = parseUtcIso(ev.timestamp)
    const dayKey = d.toISOString().slice(0, 10)
    const dayMs = new Date(dayKey + 'T12:00:00Z').getTime()
    if (!byDay.has(dayKey)) {
      byDay.set(dayKey, {
        dayMs,
        date: d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
        events: [],
      })
    }
    byDay.get(dayKey)!.events.push(ev)
  }
  return Array.from(byDay.values()).sort((a, b) => a.dayMs - b.dayMs)
}

// ─── Subcomponents ──────────────────────────────────────────────────────────

const SERIES_LABELS: Record<string, { label: string; color: string }> = {
  polyCandidate:   { label: 'Polymarket · Candidate', color: C.candidate },
  polyOpponent:    { label: 'Polymarket · Opponent',  color: C.opponent },
  kalshiCandidate: { label: 'Kalshi · Candidate',     color: C.candidate },
  kalshiOpponent:  { label: 'Kalshi · Opponent',      color: C.opponent },
}

function ChartTooltip({ active, payload, label, eventsByDay }: {
  active?: boolean
  payload?: Array<{ value: number | null; dataKey: string; payload: ChartPoint }>
  label?: string | number
  eventsByDay: Map<string, TimelineEvent[]>
}) {
  if (!active || !payload || payload.length === 0) return null
  const point = payload[0].payload
  const dayKey = new Date(point.ts).toISOString().slice(0, 10)
  const dayEvents = eventsByDay.get(dayKey) || []
  return (
    <div style={{
      background: C.bg2, border: `1px solid ${C.border}`,
      borderRadius: 6, padding: '8px 10px',
      fontSize: 12, color: C.text1, maxWidth: 320,
    }}>
      <div style={{ fontWeight: 600, marginBottom: 4 }}>{point.date}</div>
      {payload.map(p => {
        if (p.value === null || p.value === undefined) return null
        const meta = SERIES_LABELS[p.dataKey]
        if (!meta) return null
        return (
          <div key={p.dataKey} style={{ color: meta.color }}>
            {meta.label}: {p.value.toFixed(1)}%
          </div>
        )
      })}
      {dayEvents.length > 0 && (
        <div style={{ marginTop: 6, paddingTop: 6, borderTop: `1px solid ${C.bg3}` }}>
          <div style={{ color: C.text3, fontSize: 10, letterSpacing: '0.08em', marginBottom: 3 }}>
            EVENTS THIS DAY
          </div>
          {dayEvents.slice(0, 5).map((e, i) => {
            const s = EVENT_STYLE[e.type]
            return (
              <div key={i} style={{ color: s.color, fontSize: 11, lineHeight: 1.35 }}>
                • {e.label.length > 60 ? e.label.slice(0, 60) + '…' : e.label}
              </div>
            )
          })}
          {dayEvents.length > 5 && (
            <div style={{ color: C.text3, fontSize: 11 }}>+ {dayEvents.length - 5} more</div>
          )}
        </div>
      )}
    </div>
  )
}

// Inline SVG for legend swatches — needed so we can render a dashed
// stroke identically to the chart's dashed Kalshi lines.
function LegendSwatch({ color, dashed }: { color: string; dashed?: boolean }) {
  return (
    <svg width={18} height={6} style={{ flexShrink: 0 }}>
      <line
        x1={0} y1={3} x2={18} y2={3}
        stroke={color} strokeWidth={2}
        strokeDasharray={dashed ? '3 3' : undefined}
      />
    </svg>
  )
}

function LegendRow() {
  return (
    <div style={{
      display: 'flex', gap: 18, flexWrap: 'wrap',
      alignItems: 'center',
      fontSize: 12, color: C.text2,
      marginBottom: 12,
    }}>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
        <LegendSwatch color={C.candidate} /> Polymarket · Candidate
      </span>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
        <LegendSwatch color={C.opponent} /> Polymarket · Opponent
      </span>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
        <LegendSwatch color={C.candidate} dashed /> Kalshi · Candidate
      </span>
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
        <LegendSwatch color={C.opponent} dashed /> Kalshi · Opponent
      </span>
      <span style={{ color: C.text3, marginLeft: 'auto', display: 'inline-flex', gap: 14 }}>
        {(Object.keys(EVENT_STYLE) as TimelineEventType[]).map(t => {
          const s = EVENT_STYLE[t]
          return (
            <span key={t} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, color: s.color }}>
              <s.Icon size={11} />
              {s.label}
            </span>
          )
        })}
      </span>
    </div>
  )
}

function fmtPct(v: number | null): string {
  return v === null ? '—' : `${v.toFixed(1)}%`
}

function fmtDelta(from: number | null, to: number | null): { text: string; color: string } {
  if (from === null || to === null) return { text: '—', color: C.text3 }
  const d = to - from
  if (Math.abs(d) < 0.5) return { text: 'flat', color: C.text3 }
  const sign = d > 0 ? '+' : ''
  const color = d > 0 ? C.green : C.red
  return { text: `${sign}${d.toFixed(1)}pt`, color }
}

function EventCard({ window: w }: { window: EventWindow }) {
  const s = EVENT_STYLE[w.event.type]
  const d24 = fmtDelta(w.atEvent, w.after24h)
  const d72 = fmtDelta(w.atEvent, w.after72h)
  const d7d = fmtDelta(w.atEvent, w.after7d)

  const linkTarget = w.event.frame_id
    ? `/narratives/${w.event.frame_id}`
    : w.event.article_id
      ? `/articles/${w.event.article_id}`
      : null

  const inner = (
    <div style={{
      background: C.bg2, border: `1px solid ${C.border}`,
      borderRadius: 8, padding: '12px 14px',
      transition: 'border-color 0.12s ease',
    }}>
      <div style={{
        display: 'flex', alignItems: 'flex-start', gap: 8,
        marginBottom: 10,
      }}>
        <s.Icon size={14} style={{ color: s.color, marginTop: 2, flexShrink: 0 }} />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{
            fontSize: 10, color: s.color, letterSpacing: '0.08em',
            fontWeight: 600, textTransform: 'uppercase',
          }}>
            {s.label}
          </div>
          <div style={{
            fontSize: 13, fontWeight: 600, color: C.text1, lineHeight: 1.35,
            marginTop: 2,
            overflow: 'hidden', display: '-webkit-box',
            WebkitLineClamp: 2, WebkitBoxOrient: 'vertical',
          } as CSSProperties}>
            {w.event.label}
          </div>
          <div style={{ fontSize: 11, color: C.text3, marginTop: 2 }}>
            {formatRelativeTime(w.event.timestamp)}
          </div>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'auto 1fr auto', gap: 4, fontSize: 12 }}>
        <div style={{ color: C.text3 }}>At event</div>
        <div />
        <div style={{ color: C.text2, fontWeight: 600, fontVariantNumeric: 'tabular-nums', textAlign: 'right' }}>
          {fmtPct(w.atEvent)}
        </div>

        <div style={{ color: C.text3 }}>+24h</div>
        <div style={{ color: d24.color, fontSize: 11, textAlign: 'right', paddingRight: 4 }}>{d24.text}</div>
        <div style={{ color: C.text2, fontVariantNumeric: 'tabular-nums', textAlign: 'right' }}>
          {fmtPct(w.after24h)}
        </div>

        <div style={{ color: C.text3 }}>+72h</div>
        <div style={{ color: d72.color, fontSize: 11, textAlign: 'right', paddingRight: 4 }}>{d72.text}</div>
        <div style={{ color: C.text2, fontVariantNumeric: 'tabular-nums', textAlign: 'right' }}>
          {fmtPct(w.after72h)}
        </div>

        <div style={{ color: C.text3 }}>+7d</div>
        <div style={{ color: d7d.color, fontSize: 11, textAlign: 'right', paddingRight: 4 }}>{d7d.text}</div>
        <div style={{ color: C.text2, fontVariantNumeric: 'tabular-nums', textAlign: 'right' }}>
          {fmtPct(w.after7d)}
        </div>
      </div>
    </div>
  )

  if (linkTarget) {
    return (
      <Link to={linkTarget} style={{ textDecoration: 'none', display: 'block' }}
        onMouseEnter={e => {
          const el = e.currentTarget.firstChild as HTMLElement
          if (el) el.style.borderColor = 'var(--border-bright)'
        }}
        onMouseLeave={e => {
          const el = e.currentTarget.firstChild as HTMLElement
          if (el) el.style.borderColor = 'var(--border)'
        }}
      >
        {inner}
      </Link>
    )
  }
  return inner
}

// ─────────────────────────────────────────────────────────────────────────────
// Page
// ─────────────────────────────────────────────────────────────────────────────

export function Forecast() {
  const [polySnaps, setPolySnaps] = useState<RaceSentimentSnapshot[]>([])
  const [kalshiSnaps, setKalshiSnaps] = useState<RaceSentimentSnapshot[]>([])
  const [events, setEvents] = useState<TimelineEvent[]>([])
  const [loading, setLoading] = useState(true)
  const [days] = useState(30)

  useEffect(() => {
    Promise.all([
      api.raceSentimentHistory('polymarket', days),
      api.raceSentimentHistory('kalshi', days),
      api.raceSentimentEvents(days),
    ])
      .then(([p, k, e]) => { setPolySnaps(p); setKalshiSnaps(k); setEvents(e) })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [days])

  const chartData = useMemo(() => buildChartData(polySnaps, kalshiSnaps), [polySnaps, kalshiSnaps])
  const eventDays = useMemo(() => groupEventsByDay(events), [events])
  const eventsByDay = useMemo(() => {
    const m = new Map<string, TimelineEvent[]>()
    for (const ev of events) {
      const k = dayBucket(ev.timestamp)
      if (!m.has(k)) m.set(k, [])
      m.get(k)!.push(ev)
    }
    return m
  }, [events])

  // Event windows — sort newest first, limit to a manageable card count.
  // Computed against Polymarket only because it has far more snapshots;
  // Kalshi history is too sparse to ground a +24h/+72h delta yet.
  const eventWindows = useMemo(() => {
    return events
      .map(e => computeEventWindow(e, polySnaps))
      .sort((a, b) => parseUtcIso(b.event.timestamp).getTime() - parseUtcIso(a.event.timestamp).getTime())
      .slice(0, 12)
  }, [events, polySnaps])

  // Latest non-null reading per market, for the header subtitle.
  const latestPoly = useMemo(() => [...chartData].reverse().find(d => d.polyCandidate !== null), [chartData])
  const latestKalshi = useMemo(() => [...chartData].reverse().find(d => d.kalshiCandidate !== null), [chartData])

  if (loading) {
    return (
      <div style={{ background: C.bg1, minHeight: '100%', padding: 24 }}>
        <div style={{ color: C.text3 }}>Loading forecast…</div>
      </div>
    )
  }

  const hasData = chartData.length > 0

  return (
    <div style={{ background: C.bg1, minHeight: '100%', padding: '20px 24px' }}>
      {/* ── Header ── */}
      <div style={{ marginBottom: 18 }}>
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8,
          fontSize: 11, color: C.text3, letterSpacing: '0.12em',
          fontWeight: 600, textTransform: 'uppercase',
          marginBottom: 6,
        }}>
          Forecast
          <InfoTooltip text={HEADER_HELP} maxWidth={400} />
        </div>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 20, flexWrap: 'wrap' }}>
          <h1 style={{
            fontSize: 22, fontWeight: 700, color: C.text1, margin: 0,
          }}>
            Race Sentiment — last {days} days
          </h1>
          {latestPoly && (
            <div style={{ fontSize: 13, color: C.text2 }}>
              <span style={{ color: C.text3, marginRight: 4 }}>Polymarket:</span>
              <span style={{ color: C.candidate, fontWeight: 600 }}>
                {latestPoly.polyCandidate!.toFixed(1)}%
              </span>
              {' / '}
              <span style={{ color: C.opponent, fontWeight: 600 }}>
                {latestPoly.polyOpponent?.toFixed(1) ?? '—'}%
              </span>
            </div>
          )}
          {latestKalshi && (
            <div style={{ fontSize: 13, color: C.text2 }}>
              <span style={{ color: C.text3, marginRight: 4 }}>Kalshi:</span>
              <span style={{ color: C.candidate, fontWeight: 600 }}>
                {latestKalshi.kalshiCandidate!.toFixed(1)}%
              </span>
              {' / '}
              <span style={{ color: C.opponent, fontWeight: 600 }}>
                {latestKalshi.kalshiOpponent?.toFixed(1) ?? '—'}%
              </span>
            </div>
          )}
        </div>
      </div>

      {/* ── Chart ── */}
      <div style={{
        background: C.bg2, border: `1px solid ${C.border}`,
        borderRadius: 10, padding: 16, marginBottom: 24,
      }}>
        <LegendRow />
        {!hasData ? (
          <div style={{ color: C.text3, padding: '40px 0', textAlign: 'center' }}>
            No snapshots yet. Run the daily sync or backfill the markets from the Dashboard.
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={340}>
            <LineChart
              data={chartData}
              margin={{ top: 8, right: 16, left: 0, bottom: 8 }}
            >
              <CartesianGrid stroke={C.bg3} vertical={false} />
              <XAxis
                dataKey="ts"
                type="number"
                domain={['dataMin', 'dataMax']}
                scale="time"
                tickFormatter={(v) => new Date(v).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}
                stroke={C.text3} fontSize={11}
              />
              <YAxis
                domain={[20, 80]}
                tickFormatter={(v) => `${v}%`}
                stroke={C.text3} fontSize={11}
                width={42}
              />
              <Tooltip content={<ChartTooltip eventsByDay={eventsByDay} />} />

              {/* Reference lines for event days. One line per day,
                  colored by the FIRST event of the day (or accent if mixed). */}
              {eventDays.map((day, i) => {
                // Pick a color from the dominant event type that day.
                const counts: Record<string, number> = {}
                for (const e of day.events) counts[e.type] = (counts[e.type] || 0) + 1
                const dominant = Object.entries(counts).sort((a, b) => b[1] - a[1])[0][0] as TimelineEventType
                const color = EVENT_STYLE[dominant].color
                return (
                  <ReferenceLine
                    key={i} x={day.dayMs} stroke={color}
                    strokeOpacity={0.5} strokeDasharray="3 3"
                  />
                )
              })}

              {/* Polymarket — solid lines */}
              <Line
                type="monotone" dataKey="polyCandidate"
                stroke={C.candidate} strokeWidth={2.5}
                dot={{ r: 2, fill: C.candidate }}
                activeDot={{ r: 5 }}
                isAnimationActive={false}
                connectNulls
              />
              <Line
                type="monotone" dataKey="polyOpponent"
                stroke={C.opponent} strokeWidth={2}
                strokeOpacity={0.6}
                dot={{ r: 2, fill: C.opponent }}
                activeDot={{ r: 5 }}
                isAnimationActive={false}
                connectNulls
              />
              {/* Kalshi — dashed lines, same color scheme */}
              <Line
                type="monotone" dataKey="kalshiCandidate"
                stroke={C.candidate} strokeWidth={2}
                strokeDasharray="5 4"
                dot={{ r: 2, fill: C.candidate }}
                activeDot={{ r: 5 }}
                isAnimationActive={false}
                connectNulls
              />
              <Line
                type="monotone" dataKey="kalshiOpponent"
                stroke={C.opponent} strokeWidth={2}
                strokeOpacity={0.6}
                strokeDasharray="5 4"
                dot={{ r: 2, fill: C.opponent }}
                activeDot={{ r: 5 }}
                isAnimationActive={false}
                connectNulls
              />
            </LineChart>
          </ResponsiveContainer>
        )}

        {/* Thin-market caveat */}
        <div style={{
          marginTop: 12, paddingTop: 12, borderTop: `1px solid ${C.bg3}`,
          fontSize: 11, color: C.text3, display: 'flex', alignItems: 'center', gap: 6,
        }}>
          <Activity size={11} />
          Both markets have thin PA-08 liquidity (Polymarket ~$2K). Single trades can move the price several points — treat short-window swings with skepticism, and watch the gap between the two markets as a noise check.
        </div>
      </div>

      {/* ── Event window cards ── */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12,
      }}>
        <div style={{
          fontSize: 11, color: C.text3, letterSpacing: '0.12em',
          fontWeight: 600, textTransform: 'uppercase',
        }}>
          Recent events ({events.length})
        </div>
        <InfoTooltip
          text={
            'For each event, the table shows where Polymarket was at the event time, ' +
            'and where it moved 24 hours / 3 days / 7 days later. Polymarket only — ' +
            'Kalshi history is currently too sparse to ground these deltas.\n\n' +
            'This is descriptive — what happened in the window — not causal. ' +
            'A frame going viral the same week the market moved doesn\'t mean the frame caused the move; ' +
            'something else may have driven both, or the move may be noise from the market\'s thin liquidity.'
          }
          maxWidth={380}
        />
        <span style={{ color: C.text3, fontSize: 11, marginLeft: 'auto' }}>
          showing {eventWindows.length} most recent
        </span>
      </div>

      {eventWindows.length === 0 ? (
        <div style={{ color: C.text3, fontSize: 13, padding: 12 }}>
          No events recorded in the window yet.
        </div>
      ) : (
        <div style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
          gap: 12,
        }}>
          {eventWindows.map((w, i) => (
            <EventCard key={i} window={w} />
          ))}
        </div>
      )}
    </div>
  )
}
