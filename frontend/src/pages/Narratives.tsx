import { useState, useMemo, useEffect } from 'react'
import { Link } from 'react-router-dom'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend,
} from 'recharts'
import { api } from '../api/client'
import type { NarrativeFrameWithCounts } from '../api/types'
import { computeUrgency, compareFrames, type SortBy, type UrgencyLevel } from './narrativeUrgency'

// ── Helpers ──────────────────────────────────────────────────────────────────

function fmtReach(n: number): string {
  if (n === 0) return '—'
  if (n >= 1_000_000) return `~${(n / 1_000_000).toFixed(1)}M`
  if (n >= 1_000) return `~${Math.round(n / 1_000)}K`
  return `~${Math.round(n)}`
}

const mono: React.CSSProperties = { fontFamily: "'JetBrains Mono', monospace" }

const STAGE_COLORS: Record<string, string> = {
  emerging:   '#4a90d9',
  spreading:  '#0ea5e9',
  mainstream: '#a1a1a1',
  active:     '#6b6b6b',
  fading:     '#d97706',
  dormant:    '#3f3f3f',
}
const STAGE_LABELS: Record<string, string> = {
  emerging: 'Emerging', spreading: 'Growing', mainstream: 'Established',
  active: 'Active', fading: 'Declining', dormant: 'Dormant',
}
const URGENCY_COLORS: Record<UrgencyLevel, string> = {
  critical: '#d71913', high: '#d97706', medium: '#4a90d9', low: '#3f3f3f',
}

function analyticalSentence(frame: NarrativeFrameWithCounts): string {
  const { stage, mentions_this_week: tw, mentions_last_week: lw, mentions_total: total,
    unique_outlets_this_week: uow, unique_outlets_last_week: uolw, days_active_last_7: days } = frame
  const o = (n: number) => n === 1 ? 'outlet' : 'outlets'
  const s = (n: number) => n === 1 ? 'story' : 'stories'
  const hasOutlets = uow > 0 || uolw > 0
  switch (stage) {
    case 'dormant':    return 'No coverage in the last 2 weeks.'
    case 'emerging':
      return hasOutlets ? `Just appearing — ${uow} ${o(uow)}, ${days} of last 7 days.` : `${total} ${s(total)} so far.`
    case 'spreading':
      return hasOutlets ? `Growing — ${uow} ${o(uow)} this week vs ${uolw} last week.` : `Up from ${lw} to ${tw} ${s(tw)} this week.`
    case 'fading':
      return hasOutlets ? `Fading — down to ${uow} ${o(uow)} from ${uolw}.` : `Declining — ${tw} ${s(tw)} vs ${lw} last week.`
    case 'mainstream':
      return hasOutlets ? `Sustained — ${uow} ${o(uow)}, active ${days} of last 7 days.` : `Established — ${tw} ${s(tw)} this week.`
    default:
      return hasOutlets ? `${uow} ${o(uow)} this week, active ${days} of last 7 days.` : `${tw} ${s(tw)} this week.`
  }
}

// ── Tension Bar ───────────────────────────────────────────────────────────────

function TensionBar({ frames }: { frames: NarrativeFrameWithCounts[] }) {
  const candOutlets = frames.filter(f => f.owner_type === 'candidate').reduce((s, f) => s + (f.unique_outlets_this_week ?? 0), 0)
  const oppOutlets  = frames.filter(f => f.owner_type === 'opponent').reduce((s, f) => s + (f.unique_outlets_this_week ?? 0), 0)
  const total = candOutlets + oppOutlets
  const candPct = total > 0 ? Math.round((candOutlets / total) * 100) : 50
  const oppPct  = 100 - candPct

  const ahead = candOutlets > oppOutlets
  const even  = candOutlets === oppOutlets || total === 0

  return (
    <div style={{ marginBottom: 28 }}>
      <div style={{ fontSize: 11, fontWeight: 500, color: 'var(--text-muted)', marginBottom: 12 }}>
        Outlets this week
      </div>

      {/* Labels */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
          <span style={{ ...mono, fontSize: 15, fontWeight: 700, color: '#0059c2' }}>{candOutlets}</span>
          <span style={{ ...mono, fontSize: 10, color: '#0059c266' }}>campaign</span>
        </div>
        <div style={{ textAlign: 'center' }}>
          {even ? (
            <span style={{ ...mono, fontSize: 10, color: 'var(--text-muted)' }}>even</span>
          ) : (
            <span style={{ ...mono, fontSize: 10, color: ahead ? '#0059c2' : '#d71913' }}>
              {ahead ? '↑ we lead' : '↓ they lead'} by {Math.abs(candOutlets - oppOutlets)}
            </span>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 6 }}>
          <span style={{ ...mono, fontSize: 10, color: '#d7191366' }}>opponent</span>
          <span style={{ ...mono, fontSize: 15, fontWeight: 700, color: '#d71913' }}>{oppOutlets}</span>
        </div>
      </div>

      {/* Bar */}
      <div style={{ height: 6, borderRadius: 99, background: 'var(--surface-3)', overflow: 'hidden', display: 'flex' }}>
        <div style={{ width: `${candPct}%`, background: '#0059c2', borderRadius: '99px 0 0 99px', transition: 'width 0.6s ease' }} />
        <div style={{ width: `${oppPct}%`, background: '#d71913', borderRadius: '0 99px 99px 0', transition: 'width 0.6s ease' }} />
      </div>
    </div>
  )
}

// ── Scoreboard Chart ─────────────────────────────────────────────────────────

const TOOLTIP_STYLE = {
  background: 'var(--surface-1)', border: '1px solid var(--border)', borderRadius: 4,
  fontSize: 11, fontFamily: "'JetBrains Mono', monospace",
}

const TIER_COLORS: Record<string, string> = {
  national: '#6366f1',
  regional: '#4a90d9',
  local:    '#22c55e',
  blog:     '#d97706',
  social:   '#6b6b6b',
}

function ScoreboardChart({ frames }: { frames: NarrativeFrameWithCounts[] }) {
  // Outlet counts (this week / last week) grouped by side for the bar chart
  const barData = useMemo(() => {
    const outlets = (type: string, key: 'unique_outlets_this_week' | 'unique_outlets_last_week') =>
      frames.filter(f => f.owner_type === type).reduce((s, f) => s + (f[key] ?? 0), 0)
    return [
      {
        label: 'Last week',
        candidate: outlets('candidate', 'unique_outlets_last_week'),
        opponent:  outlets('opponent',  'unique_outlets_last_week'),
        media:     outlets('media',     'unique_outlets_last_week'),
      },
      {
        label: 'This week',
        candidate: outlets('candidate', 'unique_outlets_this_week'),
        opponent:  outlets('opponent',  'unique_outlets_this_week'),
        media:     outlets('media',     'unique_outlets_this_week'),
      },
    ]
  }, [frames])

  // Tier breakdown — aggregate outlet_tiers per side for the summary row
  const tierData = useMemo(() => {
    const tiers = (type: string) => {
      const blank = { national: 0, regional: 0, local: 0, blog: 0, social: 0 }
      return frames
        .filter(f => f.owner_type === type)
        .reduce((acc, f) => {
          const t = f.outlet_tiers ?? {}
          acc.national += t.national ?? 0
          acc.regional += t.regional ?? 0
          acc.local    += t.local    ?? 0
          acc.blog     += t.blog     ?? 0
          acc.social   += t.social   ?? 0
          return acc
        }, blank)
    }
    return { candidate: tiers('candidate'), opponent: tiers('opponent') }
  }, [frames])

  const totalThis = barData[1].candidate + barData[1].opponent + barData[1].media
  const totalLast = barData[0].candidate + barData[0].opponent + barData[0].media
  const pctChange = totalLast > 0 ? Math.round(((totalThis - totalLast) / totalLast) * 100) : null
  const changeLabel = pctChange === null ? null : pctChange === 0 ? 'flat vs last week' : pctChange > 0 ? `↑ ${pctChange}% vs last week` : `↓ ${Math.abs(pctChange)}% vs last week`
  const changeColor = pctChange === null ? '#6b6b6b' : pctChange > 0 ? '#22c55e' : pctChange < 0 ? '#d71913' : '#6b6b6b'

  if (totalThis === 0 && totalLast === 0) return null

  const TierPill = ({ label, count, color }: { label: string; count: number; color: string }) => (
    count > 0 ? (
      <span style={{ ...mono, fontSize: 9, color, marginRight: 8 }}>
        {count} {label}
      </span>
    ) : null
  )

  return (
    <div style={{ marginBottom: 28 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12 }}>
        <span style={{ fontSize: 11, fontWeight: 500, color: 'var(--text-muted)' }}>
          Coverage by side
        </span>
        <div style={{ flex: 1, height: 1, background: 'var(--border)' }} />
        <span style={{ ...mono, fontSize: 10, color: 'var(--text-secondary)', fontWeight: 500 }}>
          {totalThis} outlet{totalThis !== 1 ? 's' : ''} this week
        </span>
        {changeLabel && <span style={{ ...mono, fontSize: 10, color: changeColor }}>{changeLabel}</span>}
      </div>

      <ResponsiveContainer width="100%" height={110}>
        <BarChart data={barData} barCategoryGap="35%" barGap={2} margin={{ top: 4, right: 0, left: 0, bottom: 0 }}>
          <XAxis dataKey="label" tick={{ fill: '#6b6b6b', fontSize: 10, fontFamily: "'JetBrains Mono', monospace" }} axisLine={false} tickLine={false} />
          <YAxis hide allowDecimals={false} />
          <Tooltip
            contentStyle={TOOLTIP_STYLE}
            labelStyle={{ color: 'var(--text-secondary)', marginBottom: 4 }}
            itemStyle={{ color: '#fff' }}
            formatter={(val: unknown) => { const n = Number(val); return `${n} outlet${n !== 1 ? 's' : ''}` }}
          />
          <Legend
            iconType="square" iconSize={8}
            wrapperStyle={{ ...mono, fontSize: 9, paddingTop: 4 }}
            formatter={(value: string) => ({ candidate: 'Campaign', opponent: 'Opposition', media: 'Media' }[value] ?? value)}
          />
          <Bar dataKey="candidate" stackId="a" fill="#0059c2" radius={[0, 0, 0, 0]} />
          <Bar dataKey="opponent"  stackId="a" fill="#d71913" radius={[0, 0, 0, 0]} />
          <Bar dataKey="media"     stackId="a" fill="#475569" radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>

      {/* Tier breakdown summary row */}
      <div style={{ display: 'flex', gap: 24, marginTop: 8, paddingTop: 8, borderTop: '1px solid var(--border)' }}>
        <div style={{ flex: 1 }}>
          <div style={{ ...mono, fontSize: 9, color: '#0059c2', fontWeight: 700, marginBottom: 4 }}>CAMPAIGN</div>
          <TierPill label="national" count={tierData.candidate.national} color={TIER_COLORS.national} />
          <TierPill label="regional" count={tierData.candidate.regional} color={TIER_COLORS.regional} />
          <TierPill label="local"    count={tierData.candidate.local}    color={TIER_COLORS.local} />
          <TierPill label="blog"     count={tierData.candidate.blog}     color={TIER_COLORS.blog} />
          <TierPill label="social"   count={tierData.candidate.social}   color={TIER_COLORS.social} />
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ ...mono, fontSize: 9, color: '#d71913', fontWeight: 700, marginBottom: 4 }}>OPPONENT</div>
          <TierPill label="national" count={tierData.opponent.national} color={TIER_COLORS.national} />
          <TierPill label="regional" count={tierData.opponent.regional} color={TIER_COLORS.regional} />
          <TierPill label="local"    count={tierData.opponent.local}    color={TIER_COLORS.local} />
          <TierPill label="blog"     count={tierData.opponent.blog}     color={TIER_COLORS.blog} />
          <TierPill label="social"   count={tierData.opponent.social}   color={TIER_COLORS.social} />
        </div>
      </div>
    </div>
  )
}

// ── Media Tone Panel ─────────────────────────────────────────────────────────

function ToneSparkline({ series }: { series: { date: string; tone: number }[] }) {
  if (series.length < 2) return null
  const W = 120, H = 28
  const tones = series.map(p => p.tone)
  const minT = Math.min(...tones), maxT = Math.max(...tones)
  const range = maxT - minT || 1
  const pts = series.map((p, i) => {
    const x = (i / (series.length - 1)) * W
    const y = H - ((p.tone - minT) / range) * H
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
  const latest = tones[tones.length - 1]
  const strokeColor = latest > 1 ? '#0059c2' : latest < -1 ? '#d71913' : '#6b6b6b'
  return (
    <svg width={W} height={H} style={{ display: 'block', overflow: 'visible' }}>
      <polyline points={pts} fill="none" stroke={strokeColor} strokeWidth={1.5} strokeLinejoin="round" opacity={0.85} />
    </svg>
  )
}

function MediaTonePanel() {
  const [data, setData] = useState<{ label: string; entity_type: string; series: { date: string; tone: number }[] }[]>([])

  useEffect(() => {
    api.getToneHistory(30).then(r => setData(r.entities)).catch(() => {})
  }, [])

  if (data.length === 0) return null

  return (
    <div style={{ marginBottom: 24 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
        <span style={{ ...mono, fontSize: 9, fontWeight: 700, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'var(--text-muted)' }}>
          Media Tone — 30 days
        </span>
        <div style={{ flex: 1, height: 1, background: 'var(--border)' }} />
        <span style={{ ...mono, fontSize: 9, color: 'var(--text-xmuted)' }}>via GDELT</span>
      </div>
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        {data.map(entity => {
          const latest = entity.series.length > 0 ? entity.series[entity.series.length - 1].tone : null
          const entityColor = entity.entity_type === 'candidate' ? '#0059c2' : '#d71913'
          const toneColor = latest === null ? '#6b6b6b' : latest > 1 ? '#0059c2' : latest < -1 ? '#d71913' : '#a1a1a1'
          const toneLabel = latest === null ? '—' : latest > 1 ? 'positive' : latest < -1 ? 'negative' : 'neutral'
          return (
            <div key={entity.label} style={{
              background: 'var(--surface-1)',
              border: '1px solid var(--border)',
              borderLeft: `2px solid ${entityColor}`,
              borderRadius: 6,
              padding: '10px 14px',
              display: 'flex', alignItems: 'center', gap: 14,
              minWidth: 240,
            }}>
              <div>
                <div style={{ fontSize: 11, fontWeight: 600, color: entityColor, marginBottom: 2 }}>{entity.label}</div>
                <div style={{ ...mono, fontSize: 9, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
                  {entity.entity_type === 'candidate' ? 'Our candidate' : 'Opponent'}
                </div>
              </div>
              <ToneSparkline series={entity.series} />
              <div style={{ textAlign: 'right' }}>
                <div style={{ fontSize: 15, fontWeight: 700, color: toneColor, ...mono }}>
                  {latest !== null ? (latest > 0 ? '+' : '') + latest.toFixed(1) : '—'}
                </div>
                <div style={{ ...mono, fontSize: 9, color: toneColor, textTransform: 'uppercase', letterSpacing: '0.08em' }}>{toneLabel}</div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── Search Trends Panel ───────────────────────────────────────────────────────

function SearchTrendsPanel() {
  const [data, setData] = useState<{ term: string; series: { date: string; interest: number }[] }[]>([])

  useEffect(() => {
    api.getSearchTrends(90).then(r => setData(r.terms)).catch(() => {})
  }, [])

  if (data.length === 0) return null

  return (
    <div style={{ marginBottom: 28 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 10 }}>
        <span style={{ ...mono, fontSize: 9, fontWeight: 700, letterSpacing: '0.14em', textTransform: 'uppercase', color: 'var(--text-muted)' }}>
          Search Interest — 90 days
        </span>
        <div style={{ flex: 1, height: 1, background: 'var(--border)' }} />
        <span style={{ ...mono, fontSize: 9, color: 'var(--text-xmuted)' }}>via Google Trends · Pennsylvania</span>
      </div>
      <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
        {data.map(item => {
          const series = item.series.filter(p => p.interest > 0)
          if (series.length < 2) return null
          const latest = series[series.length - 1].interest
          const prev7 = series.slice(-8, -1)
          const avg7 = prev7.length > 0 ? Math.round(prev7.reduce((s, p) => s + p.interest, 0) / prev7.length) : 0
          const trend = latest > avg7 + 5 ? 'up' : latest < avg7 - 5 ? 'down' : 'flat'
          const trendGlyph = trend === 'up' ? '↑' : trend === 'down' ? '↓' : '→'
          const trendColor = trend === 'up' ? '#22c55e' : trend === 'down' ? '#d71913' : '#6b6b6b'

          const W = 100, H = 24
          const vals = series.map(p => p.interest)
          const maxV = Math.max(...vals, 1)
          const pts = series.map((p, i) => {
            const x = (i / (series.length - 1)) * W
            const y = H - (p.interest / maxV) * H
            return `${x.toFixed(1)},${y.toFixed(1)}`
          }).join(' ')

          return (
            <div key={item.term} style={{
              background: 'var(--surface-1)',
              border: '1px solid var(--border)',
              borderRadius: 6, padding: '8px 12px',
              display: 'flex', alignItems: 'center', gap: 12,
              minWidth: 220,
            }}>
              <div style={{ minWidth: 0, flex: 1 }}>
                <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {item.term}
                </div>
                <div style={{ ...mono, fontSize: 9, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                  search interest
                </div>
              </div>
              <svg width={W} height={H} style={{ display: 'block', flexShrink: 0 }}>
                <polyline points={pts} fill="none" stroke="#60a5fa" strokeWidth={1.5} strokeLinejoin="round" opacity={0.8} />
              </svg>
              <div style={{ textAlign: 'right', flexShrink: 0 }}>
                <div style={{ ...mono, fontSize: 15, fontWeight: 700, color: '#fff' }}>{latest}</div>
                <div style={{ ...mono, fontSize: 10, color: trendColor }}>{trendGlyph} {trend}</div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── Alerts Strip ──────────────────────────────────────────────────────────────

function AlertsStrip({ frames }: { frames: NarrativeFrameWithCounts[] }) {
  const alerts = useMemo(() => frames
    .map(f => ({ frame: f, urgency: computeUrgency(f) }))
    .filter(x => x.urgency.score >= 50)
    .sort((a, b) => b.urgency.score - a.urgency.score)
    .slice(0, 5), [frames])

  if (alerts.length === 0) return null

  return (
    <div style={{ marginBottom: 24 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 10, paddingBottom: 8, borderBottom: '1px solid var(--border)' }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: '#fff', letterSpacing: '-0.01em' }}>Needs attention</span>
        <span style={{ ...mono, fontSize: 10, color: 'var(--text-xmuted)' }}>{alerts.length}</span>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
        {alerts.map(({ frame, urgency }) => {
          const levelColor = URGENCY_COLORS[urgency.level]
          return (
            <Link key={frame.id} to={`/frames/${frame.id}`} style={{ textDecoration: 'none' }}>
              <div
                style={{
                  display: 'flex', alignItems: 'center', gap: 12,
                  padding: '9px 14px',
                  background: 'var(--surface-1)',
                  border: '1px solid var(--border)',
                  borderLeft: `2px solid ${levelColor}`,
                  borderRadius: 4,
                  transition: 'background 0.1s',
                }}
                onMouseEnter={e => { e.currentTarget.style.background = 'var(--surface-2)' }}
                onMouseLeave={e => { e.currentTarget.style.background = 'var(--surface-1)' }}
              >
                <span style={{ fontSize: 13, fontWeight: 600, color: '#fff', flex: 1 }}>{frame.name}</span>
                <span style={{ ...mono, fontSize: 10, color: 'var(--text-muted)', flexShrink: 0 }}>{urgency.reason}</span>
              </div>
            </Link>
          )
        })}
      </div>
    </div>
  )
}

// ── Card Sub-components ───────────────────────────────────────────────────────

function OutletSparkline({ lw, tw }: { lw: number; tw: number }) {
  const W = 40, H = 20, pad = 2
  const max = Math.max(lw, tw, 1)
  const lwH = Math.max(2, Math.round(((H - pad) * lw) / max))
  const twH = Math.max(2, Math.round(((H - pad) * tw) / max))
  const color = tw > lw ? '#0059c2' : tw < lw ? '#d71913' : '#6b6b6b'
  const bw = (W - 6) / 2
  return (
    <svg width={W} height={H} style={{ display: 'block', flexShrink: 0 }}>
      <title>{`Last week: ${lw} outlet${lw !== 1 ? 's' : ''} → This week: ${tw} outlet${tw !== 1 ? 's' : ''}`}</title>
      <rect x={0}       y={H - lwH} width={bw} height={lwH} rx={1} fill="#3f3f3f" />
      <rect x={bw + 6}  y={H - twH} width={bw} height={twH} rx={1} fill={color}   />
    </svg>
  )
}

function OutletTierBar({ tiers }: { tiers: NarrativeFrameWithCounts['outlet_tiers'] }) {
  const total = (tiers.national ?? 0) + (tiers.regional ?? 0) + (tiers.local ?? 0) + (tiers.blog ?? 0) + (tiers.social ?? 0)
  if (total === 0) return null
  const keys = ['national', 'regional', 'local', 'blog', 'social'] as const
  const tooltip = keys.filter(k => (tiers[k] ?? 0) > 0).map(k => `${k}: ${tiers[k]}`).join(' · ')
  return (
    <div title={tooltip} style={{ height: 5, borderRadius: 99, overflow: 'hidden', display: 'flex', width: '100%' }}>
      {keys.map(k => {
        const count = tiers[k] ?? 0
        if (count === 0) return null
        return (
          <div key={k} style={{ width: `${(count / total) * 100}%`, height: '100%', background: TIER_COLORS[k] }} />
        )
      })}
    </div>
  )
}

// ── Narrative Row (DDHQ race-row style) ──────────────────────────────────────

function NarrativeRow({ frame, onDelete, onEdit }: {
  frame: NarrativeFrameWithCounts
  onDelete: (id: number) => void
  onEdit: (f: NarrativeFrameWithCounts) => void
}) {
  const circleClass = frame.owner_type === 'candidate'
    ? 'party-circle party-circle-dem'
    : frame.owner_type === 'opponent'
    ? 'party-circle party-circle-rep'
    : 'party-circle party-circle-med'
  const circleLetter = frame.owner_type === 'candidate' ? 'C' : frame.owner_type === 'opponent' ? 'O' : 'M'
  const stageColor = STAGE_COLORS[frame.stage] || '#6b6b6b'
  const trendGlyph = frame.trend === 'up' ? '↑' : frame.trend === 'down' ? '↓' : '→'
  const trendColor = frame.trend === 'up' ? '#22c55e' : frame.trend === 'down' ? '#d71913' : '#6b6b6b'
  const urgency = computeUrgency(frame)
  const hasAlert = urgency.score >= 50

  return (
    <div
      className="race-row group/row"
      style={hasAlert ? { borderLeftWidth: 3, borderLeftColor: URGENCY_COLORS[urgency.level] } : {}}
    >
      <Link to={`/frames/${frame.id}`} style={{ display: 'flex', alignItems: 'center', gap: 12, flex: 1, minWidth: 0, textDecoration: 'none', color: 'inherit' }}>
        <div className={circleClass}>{circleLetter}</div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: '#fff', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {frame.name}
          </div>
          <div style={{ fontSize: 11, color: '#6b6b6b', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', marginTop: 1 }}>
            {analyticalSentence(frame)}
          </div>
        </div>
        <span className="stage-chip" style={{ background: stageColor + '22', color: stageColor, border: `1px solid ${stageColor}44` }}>
          {STAGE_LABELS[frame.stage] || frame.stage}
        </span>
        <span style={{ ...mono, fontSize: 12, color: '#a1a1a1', flexShrink: 0 }}>
          {frame.unique_outlets_this_week ?? 0} outlets
        </span>
        <span style={{ fontSize: 12, fontWeight: 700, color: trendColor, flexShrink: 0 }}>{trendGlyph}</span>
      </Link>
      <div className="flex gap-1 opacity-0 group-hover/row:opacity-100 transition-opacity" style={{ display: 'flex', gap: 4, flexShrink: 0 }}>
        <button
          onClick={e => { e.stopPropagation(); onEdit(frame) }}
          style={{ width: 24, height: 24, flexShrink: 0, background: 'transparent', border: '1px solid var(--border)', borderRadius: 4, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)', fontSize: 11, transition: 'border-color 0.1s, color 0.1s' }}
          onMouseEnter={e => { e.currentTarget.style.borderColor = '#666'; e.currentTarget.style.color = '#fff' }}
          onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.color = 'var(--text-muted)' }}
        >✎</button>
        <button
          onClick={e => { e.stopPropagation(); onDelete(frame.id) }}
          style={{ width: 24, height: 24, flexShrink: 0, background: 'transparent', border: '1px solid var(--border)', borderRadius: 4, cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)', fontSize: 11, transition: 'border-color 0.1s, color 0.1s' }}
          onMouseEnter={e => { e.currentTarget.style.borderColor = 'rgba(215,25,19,0.5)'; e.currentTarget.style.color = '#f04340' }}
          onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.color = 'var(--text-muted)' }}
        >×</button>
      </div>
    </div>
  )
}

// ── Modals ────────────────────────────────────────────────────────────────────

function AddFrameModal({ onClose, onSave }: { onClose: () => void; onSave: (name: string, description: string, owner_type: string) => Promise<void> }) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [ownerType, setOwnerType] = useState('candidate')
  const [saving, setSaving] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim()) return
    setSaving(true)
    await onSave(name.trim(), description.trim(), ownerType)
    setSaving(false)
  }

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 }}>
      <div style={{ background: 'var(--surface-1)', border: '1px solid var(--border)', borderRadius: 8, padding: 24, width: 400, maxWidth: '90vw' }}>
        <h3 style={{ margin: '0 0 16px', color: 'var(--text-primary)', fontSize: 15 }}>Add Narrative Frame</h3>
        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div>
            <label style={{ ...mono, fontSize: 10, color: 'var(--text-muted)', display: 'block', marginBottom: 4, textTransform: 'uppercase', letterSpacing: '0.08em' }}>Frame name *</label>
            <input value={name} onChange={e => setName(e.target.value)} placeholder="e.g. Healthcare Access" required />
          </div>
          <div>
            <label style={{ ...mono, fontSize: 10, color: 'var(--text-muted)', display: 'block', marginBottom: 4, textTransform: 'uppercase', letterSpacing: '0.08em' }}>Description</label>
            <textarea value={description} onChange={e => setDescription(e.target.value)} placeholder="One sentence: what this frame covers and why it matters." rows={2} />
          </div>
          <div>
            <label style={{ ...mono, fontSize: 10, color: 'var(--text-muted)', display: 'block', marginBottom: 4, textTransform: 'uppercase', letterSpacing: '0.08em' }}>Side</label>
            <select value={ownerType} onChange={e => setOwnerType(e.target.value as 'candidate' | 'opponent' | 'media')}>
              <option value="candidate">Campaign narrative</option>
              <option value="opponent">Opposition narrative</option>
            </select>
          </div>
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 4 }}>
            <button type="button" onClick={onClose} className="btn-ghost">Cancel</button>
            <button type="submit" disabled={saving || !name.trim()} className="btn-primary">
              {saving ? 'Saving…' : 'Add Frame'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

function EditFrameModal({ frame, onClose, onSave }: { frame: NarrativeFrameWithCounts; onClose: () => void; onSave: (id: number, name: string, description: string, owner_type: string) => Promise<void> }) {
  const [name, setName] = useState(frame.name)
  const [description, setDescription] = useState(frame.description || '')
  const [ownerType, setOwnerType] = useState(frame.owner_type)
  const [saving, setSaving] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setSaving(true)
    await onSave(frame.id, name.trim(), description.trim(), ownerType)
    setSaving(false)
  }

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 }}>
      <div style={{ background: 'var(--surface-1)', border: '1px solid var(--border)', borderRadius: 8, padding: 24, width: 400, maxWidth: '90vw' }}>
        <h3 style={{ margin: '0 0 16px', color: 'var(--text-primary)', fontSize: 15 }}>Edit Narrative Frame</h3>
        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div>
            <label style={{ ...mono, fontSize: 10, color: 'var(--text-muted)', display: 'block', marginBottom: 4, textTransform: 'uppercase', letterSpacing: '0.08em' }}>Frame name</label>
            <input value={name} onChange={e => setName(e.target.value)} required />
          </div>
          <div>
            <label style={{ ...mono, fontSize: 10, color: 'var(--text-muted)', display: 'block', marginBottom: 4, textTransform: 'uppercase', letterSpacing: '0.08em' }}>Description</label>
            <textarea value={description} onChange={e => setDescription(e.target.value)} rows={2} />
          </div>
          <div>
            <label style={{ ...mono, fontSize: 10, color: 'var(--text-muted)', display: 'block', marginBottom: 4, textTransform: 'uppercase', letterSpacing: '0.08em' }}>Side</label>
            <select value={ownerType} onChange={e => setOwnerType(e.target.value as 'candidate' | 'opponent' | 'media')}>
              <option value="candidate">Campaign narrative</option>
              <option value="opponent">Opposition narrative</option>
              <option value="media" disabled style={{ color: 'var(--text-muted)' }}>Media theme (legacy)</option>
            </select>
          </div>
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 4 }}>
            <button type="button" onClick={onClose} className="btn-ghost">Cancel</button>
            <button type="submit" disabled={saving} className="btn-primary">{saving ? 'Saving…' : 'Save'}</button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ── Main Component ────────────────────────────────────────────────────────────

export default function Narratives() {
  const [frames, setFrames] = useState<NarrativeFrameWithCounts[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showAdd, setShowAdd] = useState(false)
  const [editFrame, setEditFrame] = useState<NarrativeFrameWithCounts | null>(null)
  const [suggesting, setSuggesting] = useState(false)
  const [statusMsg, setStatusMsg] = useState<string | null>(null)
  const [stageFilter, setStageFilter] = useState('all')
  const [sortBy, setSortBy] = useState<SortBy>('urgency')
  const [view, setView] = useState<'overview' | 'list'>('overview')
  const [monitoringStart, setMonitoringStart] = useState<string | null>(null)

  function load() {
    return api.getNarrativeFrames()
      .then(setFrames)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])
  useEffect(() => {
    api.getMonitoringStartDate().then(r => setMonitoringStart(r.monitoring_start)).catch(() => {})
  }, [])

  // Auto-trigger historical backfill
  useEffect(() => {
    let cancelled = false
    let interval: ReturnType<typeof setInterval> | null = null
    api.ensureBackfill(180).then(r => {
      if (cancelled || r.status === 'already_done') return
      interval = setInterval(async () => {
        try {
          const s = await api.getBackfillStatus()
          if (cancelled) return
          if (s.done && !s.running) { if (interval) clearInterval(interval); load() }
        } catch { if (interval) clearInterval(interval) }
      }, 5000)
    }).catch(() => {})
    return () => { cancelled = true; if (interval) clearInterval(interval) }
  }, [])

  const monitoringDays = useMemo(() => {
    if (!monitoringStart) return null
    const ms = new Date(monitoringStart).getTime()
    if (Number.isNaN(ms)) return null
    return Math.floor((Date.now() - ms) / 86400000)
  }, [monitoringStart])

  async function handleAdd(name: string, description: string, owner_type: string) {
    await api.createNarrativeFrame({ name, description, owner_type })
    setShowAdd(false); load()
  }
  async function handleEdit(id: number, name: string, description: string, owner_type: string) {
    await api.updateNarrativeFrame(id, { name, description, owner_type })
    setEditFrame(null); load()
  }
  async function handleDelete(id: number) {
    if (!confirm('Remove this narrative frame?')) return
    await api.deleteNarrativeFrame(id); load()
  }
  async function handleSuggest() {
    setSuggesting(true); setStatusMsg(null)
    try {
      const result = await api.suggestNarrativeFrames(90)
      setStatusMsg(`Auto-suggested ${result.suggested} frame${result.suggested === 1 ? '' : 's'} from recent articles.`)
      load()
    } catch (e: any) { setStatusMsg('Auto-suggest failed: ' + e.message) }
    finally { setSuggesting(false) }
  }

  const sorted = useMemo(() =>
    [...frames].sort((a, b) => compareFrames(a, b, sortBy)),
    [frames, sortBy]
  )

  const filtered = useMemo(() =>
    sorted.filter(f => stageFilter === 'all' || f.stage === stageFilter),
    [sorted, stageFilter]
  )

  const candidateFrames = filtered.filter(f => f.owner_type === 'candidate')
  const opponentFrames  = filtered.filter(f => f.owner_type === 'opponent')
  const mediaFrames     = filtered.filter(f => f.owner_type === 'media')

  const stages = ['all', 'emerging', 'spreading', 'mainstream', 'fading', 'dormant']

  return (
    <div style={{ padding: '32px 40px 64px', maxWidth: 1200 }}>

      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 28, flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 26, fontWeight: 700, letterSpacing: '-0.02em', color: 'var(--text-primary)', lineHeight: 1.15 }}>
            Narrative Tracker
          </h1>
          <div style={{ ...mono, fontSize: 10, color: 'var(--text-muted)', marginTop: 6 }}>
            {frames.length} frames · monitoring since {monitoringStart ? new Date(monitoringStart).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : '—'}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <button onClick={handleSuggest} disabled={suggesting} className="btn-ghost" style={{ fontSize: 12 }}>
            {suggesting ? 'Analyzing…' : 'Auto-suggest'}
          </button>
          <button onClick={() => setShowAdd(true)} className="btn-primary" style={{ fontSize: 12 }}>
            + Add frame
          </button>
        </div>
      </div>

      {/* Early monitoring notice */}
      {monitoringDays !== null && monitoringDays < 14 && (
        <div style={{ ...mono, fontSize: 10, color: '#fbbf24', background: '#fbbf2411', border: '1px solid #fbbf2433', borderRadius: 4, padding: '8px 12px', marginBottom: 20 }}>
          ⚠ Early data — monitoring started {monitoringDays === 0 ? 'today' : `${monitoringDays}d ago`}. Week-over-week comparisons are unreliable until 2 weeks of coverage accumulate.
        </div>
      )}

      {loading && <div style={{ ...mono, fontSize: 11, color: 'var(--text-muted)' }}>Loading…</div>}
      {error   && <div style={{ fontSize: 12, color: '#d71913' }}>Error: {error}</div>}

      {frames.length === 0 && !loading && (
        <div style={{ textAlign: 'center', padding: '64px 24px', color: 'var(--text-muted)' }}>
          <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--text-primary)', marginBottom: 8 }}>No narrative frames yet</div>
          <div style={{ fontSize: 12 }}>Click <strong>Auto-suggest</strong> to generate frames from recent articles.</div>
        </div>
      )}

      {frames.length > 0 && (
        <>
          {/* Alerts — first so urgent items are above the fold */}
          <AlertsStrip frames={frames} />

          {/* Tension bar */}
          <TensionBar frames={filtered} />

          {/* Scoreboard: reach by side, last week vs this week */}
          <ScoreboardChart frames={filtered} />

          {/* GDELT media tone trend */}
          <MediaTonePanel />

          {/* Google search interest */}
          <SearchTrendsPanel />

          {/* Controls row */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 20, flexWrap: 'wrap', borderTop: '1px solid var(--border)', paddingTop: 16 }}>
            {/* Stage filter */}
            <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
              <span style={{ ...mono, fontSize: 9, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.1em', marginRight: 4 }}>Stage</span>
              {stages.map(s => (
                <button key={s} onClick={() => setStageFilter(s)} style={{
                  ...mono, fontSize: 10, padding: '3px 8px', borderRadius: 3, cursor: 'pointer',
                  background: stageFilter === s ? (s === 'all' ? '#2f2f2f' : STAGE_COLORS[s] + '33' || '#2f2f2f') : 'transparent',
                  color: stageFilter === s ? (s === 'all' ? '#fff' : STAGE_COLORS[s] || '#fff') : 'var(--text-muted)',
                  border: `1px solid ${stageFilter === s ? (s === 'all' ? '#525252' : STAGE_COLORS[s] + '66' || '#525252') : 'var(--border)'}`,
                  textTransform: 'capitalize',
                }}>{s === 'all' ? 'All' : STAGE_LABELS[s]}</button>
              ))}
            </div>

            <div style={{ flex: 1 }} />

            {/* Sort */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ ...mono, fontSize: 9, color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>Sort</span>
              <select value={sortBy} onChange={e => setSortBy(e.target.value as SortBy)}
                style={{ ...mono, width: 'auto', fontSize: 11, padding: '3px 8px', background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: 3, color: 'var(--text-primary)' }}>
                <option value="urgency">Urgency</option>
                <option value="outlets">Outlets</option>
                <option value="momentum">Momentum</option>
                <option value="recency">Recency</option>
              </select>
            </div>

            {/* View toggle */}
            <div style={{ display: 'flex', border: '1px solid var(--border)', borderRadius: 4, overflow: 'hidden' }}>
              {(['overview', 'list'] as const).map(v => (
                <button key={v} onClick={() => setView(v)} style={{
                  ...mono, fontSize: 10, padding: '4px 10px', cursor: 'pointer', border: 'none',
                  background: view === v ? 'var(--surface-3)' : 'transparent',
                  color: view === v ? 'var(--text-primary)' : 'var(--text-muted)',
                  textTransform: 'capitalize',
                }}>{v}</button>
              ))}
            </div>
          </div>

          {statusMsg && (
            <div style={{ ...mono, fontSize: 11, marginBottom: 16, padding: '8px 12px', background: 'var(--surface-1)', border: '1px solid var(--border)', borderRadius: 4, color: 'var(--text-secondary)' }}>
              {statusMsg}
            </div>
          )}

          {/* ── Battlefield view ── */}
          {view === 'overview' && (
            <>
              <div className="grid grid-cols-2 gap-5 mb-6">
                {/* Campaign */}
                <div>
                  <div className="flex items-baseline gap-2 mb-3.5 pb-2.5 border-b border-border">
                    <span className="text-[13px] font-semibold text-slate-100 tracking-tight">Campaign</span>
                    <span className="font-mono text-[10px]" style={{ color: '#0059c2' }}>{candidateFrames.length}</span>
                  </div>
                  <div className="flex flex-col gap-2">
                    {candidateFrames.length > 0
                      ? candidateFrames.map(f => <NarrativeRow key={f.id} frame={f} onDelete={handleDelete} onEdit={setEditFrame} />)
                      : <div className="font-mono text-[10px] text-slate-600 py-5">No frames in this category.</div>
                    }
                  </div>
                </div>

                {/* Opposition */}
                <div>
                  <div className="flex items-baseline gap-2 mb-3.5 pb-2.5 border-b border-border">
                    <span className="text-[13px] font-semibold text-slate-100 tracking-tight">Opposition</span>
                    <span className="font-mono text-[10px]" style={{ color: '#d71913' }}>{opponentFrames.length}</span>
                  </div>
                  <div className="flex flex-col gap-2">
                    {opponentFrames.length > 0
                      ? opponentFrames.map(f => <NarrativeRow key={f.id} frame={f} onDelete={handleDelete} onEdit={setEditFrame} />)
                      : <div className="font-mono text-[10px] text-slate-600 py-5">No frames in this category.</div>
                    }
                  </div>
                </div>
              </div>

              {/* Legacy media frames — prompt to reclassify */}
              {mediaFrames.length > 0 && (
                <div style={{
                  border: '1px solid #fbbf2433',
                  borderRadius: 6,
                  padding: '12px 16px',
                  background: '#fbbf2408',
                  marginBottom: 8,
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
                    <span style={{ ...mono, fontSize: 9, fontWeight: 700, letterSpacing: '0.12em', textTransform: 'uppercase', color: '#fbbf24' }}>
                      ⚠ Needs reclassification
                    </span>
                    <span style={{ ...mono, fontSize: 9, color: '#fbbf2466' }}>{mediaFrames.length} frames</span>
                    <div style={{ flex: 1, height: 1, background: '#fbbf2422' }} />
                  </div>
                  <p style={{ ...mono, fontSize: 10, color: 'var(--text-secondary)', margin: '0 0 10px', lineHeight: 1.6 }}>
                    These frames were previously tagged as "Media Theme." Click <strong>edit</strong> on each one and set it to either <strong>Campaign narrative</strong> or <strong>Opposition narrative</strong> so it appears in the right column.
                  </p>
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 8 }}>
                    {mediaFrames.map(f => <NarrativeRow key={f.id} frame={f} onDelete={handleDelete} onEdit={setEditFrame} />)}
                  </div>
                </div>
              )}
            </>
          )}

          {/* ── List view ── */}
          {view === 'list' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {filtered.map(f => <NarrativeRow key={f.id} frame={f} onDelete={handleDelete} onEdit={setEditFrame} />)}
            </div>
          )}
        </>
      )}

      {showAdd  && <AddFrameModal onClose={() => setShowAdd(false)} onSave={handleAdd} />}
      {editFrame && <EditFrameModal frame={editFrame} onClose={() => setEditFrame(null)} onSave={handleEdit} />}
    </div>
  )
}
