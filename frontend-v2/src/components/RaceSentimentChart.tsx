import { Activity, FileText, Layers, TrendingUp } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
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
import { InfoTooltip } from './InfoTooltip'

// ─────────────────────────────────────────────────────────────────────────────
// Race Sentiment chart
//
// Polymarket + Kalshi implied win-probability over time with narrative-event
// markers overlaid as vertical reference lines. Polymarket = solid, Kalshi =
// dashed; divergence between the two is itself signal (thin-market noise vs.
// genuine disagreement).
//
// Lifted out of the (now-removed) /forecast page on 2026-05-29 to live as
// a card inside /analytics. The event-window cards that used to render
// below the chart were dropped — the Timeline page covers event/market
// impact in a richer way (pins on the line + impact-ranked list).
// ─────────────────────────────────────────────────────────────────────────────

const C = {
  bg2: 'var(--bg-2)', bg3: 'var(--bg-3)',
  border: 'var(--border)',
  text1: 'var(--text-1)', text2: 'var(--text-2)', text3: 'var(--text-3)',
  candidate: 'var(--candidate)', opponent: 'var(--opponent)',
}

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
  'Implied win probabilities for both candidates, from two prediction markets: ' +
  'Polymarket (solid lines) and Kalshi (dashed). Divergence between the markets ' +
  'is itself signal — thin liquidity, different trader pools, or genuine ' +
  'disagreement on where the race is.\n\n' +
  'Vertical markers flag narrative events (frames promoted, stages changing, top ' +
  'articles) — hover a day to see what happened.\n\n' +
  'Reminder: PA-08\'s Polymarket has ~$2K total liquidity, so single trades can move ' +
  'the price several points. Treat short-window swings with appropriate skepticism.'

// ─── Helpers ────────────────────────────────────────────────────────────────

function parseUtcIso(iso: string): Date {
  const hasTz = /[zZ]|[+-]\d{2}:?\d{2}$/.test(iso)
  return hasTz ? new Date(iso) : new Date(iso + 'Z')
}

function dayBucket(iso: string): string {
  return parseUtcIso(iso).toISOString().slice(0, 10)
}

// ─── Chart data shape ───────────────────────────────────────────────────────

interface ChartPoint {
  ts: number
  date: string
  polyCandidate: number | null
  polyOpponent: number | null
  kalshiCandidate: number | null
  kalshiOpponent: number | null
}

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

interface EventDay {
  dayMs: number
  events: TimelineEvent[]
}

function groupEventsByDay(events: TimelineEvent[]): EventDay[] {
  const byDay = new Map<string, EventDay>()
  for (const ev of events) {
    const dayKey = dayBucket(ev.timestamp)
    const dayMs = new Date(dayKey + 'T12:00:00Z').getTime()
    if (!byDay.has(dayKey)) byDay.set(dayKey, { dayMs, events: [] })
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

function ChartTooltip({ active, payload, eventsByDay }: {
  active?: boolean
  payload?: Array<{ value: number | null; dataKey: string; payload: ChartPoint }>
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

// ─────────────────────────────────────────────────────────────────────────────
// Main component
// ─────────────────────────────────────────────────────────────────────────────

interface Props {
  days?: number
}

export function RaceSentimentChart({ days = 30 }: Props) {
  const [polySnaps, setPolySnaps] = useState<RaceSentimentSnapshot[]>([])
  const [kalshiSnaps, setKalshiSnaps] = useState<RaceSentimentSnapshot[]>([])
  const [events, setEvents] = useState<TimelineEvent[]>([])
  const [loading, setLoading] = useState(true)

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

  const latestPoly = useMemo(() => [...chartData].reverse().find(d => d.polyCandidate !== null), [chartData])
  const latestKalshi = useMemo(() => [...chartData].reverse().find(d => d.kalshiCandidate !== null), [chartData])

  const hasData = chartData.length > 0

  return (
    <div style={{
      background: C.bg2, border: `1px solid ${C.border}`,
      borderRadius: 12, padding: '18px 20px',
    }}>
      {/* ── Title row ── */}
      <div style={{
        display: 'flex', alignItems: 'baseline', gap: 16, flexWrap: 'wrap',
        marginBottom: 14, paddingBottom: 8,
        borderBottom: `1px solid ${C.bg3}`,
      }}>
        <div style={{
          fontSize: 14, fontWeight: 700, letterSpacing: '0.1em',
          color: C.text2, textTransform: 'uppercase',
          display: 'inline-flex', alignItems: 'center',
        }}>
          Race Sentiment — last {days} days
          <InfoTooltip text={HEADER_HELP} maxWidth={400} />
        </div>
        {latestPoly && (
          <div style={{ fontSize: 12, color: C.text2 }}>
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
          <div style={{ fontSize: 12, color: C.text2 }}>
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

      {/* ── Chart body ── */}
      {loading ? (
        <div className="skeleton" style={{ height: 340, borderRadius: 12 }} />
      ) : (
        <>
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

                {/* One reference line per event day, colored by the day's
                    dominant event type. */}
                {eventDays.map((day, i) => {
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

                {/* Polymarket — solid */}
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
                {/* Kalshi — dashed */}
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
        </>
      )}
    </div>
  )
}
