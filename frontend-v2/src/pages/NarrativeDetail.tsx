import { ArrowLeft, ExternalLink, MessageSquareQuote, Edit2 } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { Area, AreaChart, Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api } from '@/api/client'
import type { ActivityPoint, NarrativeFrameDetail, NarrativeFrameTimeline, OwnerType } from '@/api/types'

type TierKey = 'national' | 'regional' | 'local' | 'blog' | 'social' | 'unknown'
const TIER_ORDER: readonly TierKey[] = ['national', 'regional', 'local', 'blog', 'social', 'unknown']
const TIER_COLORS: Record<TierKey, string> = {
  national: '#d71913',
  regional: '#ea580c',
  local: '#ca8a04',
  blog: '#a1a1a1',
  social: '#4f8ef7',
  unknown: '#555',
}
const TIER_LABELS: Record<TierKey, string> = {
  national: 'National', regional: 'Regional', local: 'Local',
  blog: 'Blog', social: 'Social', unknown: 'Unknown',
}

type Timeframe = '7' | '30' | '90' | 'all'
const TIMEFRAMES: ReadonlyArray<readonly [Timeframe, string]> = [
  ['7', '7D'], ['30', '30D'], ['90', '90D'], ['all', 'ALL'],
]

// Variant chart timeframes — separate from the tier-activity chart so users
// can zoom each independently. 'all' fetches 365-day window.
type VariantWindow = '30' | '90' | '365'
const VARIANT_WINDOWS: ReadonlyArray<readonly [VariantWindow, string]> = [
  ['30', '30D'], ['90', '90D'], ['365', '1Y'],
]

// Palette for top variants. Picked for dark-mode legibility + distinguishability
// when stacked. Order matters — higher-volume variants get the brightest colors.
const VARIANT_COLORS = [
  '#ffbf00',  // golden yellow — top variant (your brand accent)
  '#4f8ef7',  // sky blue
  '#22c55e',  // emerald
  '#f97316',  // orange
  '#a855f7',  // purple
  '#ec4899',  // pink
  '#14b8a6',  // teal
]
const OTHER_COLOR = '#555'
const TOP_VARIANT_COUNT = 6

const C = {
  bg1: '#121212', bg2: '#171717', bg3: '#262626',
  border: '#434343', text1: '#fff', text2: '#a1a1a1', text3: '#666',
  candidate: '#0059c2', opponent: '#d71913', media: '#a1a1a1',
}

function ownerColor(t: OwnerType) {
  return t === 'candidate' ? C.candidate : t === 'opponent' ? C.opponent : C.media
}

function ownerLabel(t: OwnerType, candidateName?: string, opponentName?: string) {
  if (t === 'candidate') return candidateName ? `Favors ${candidateName}` : 'Favors candidate'
  if (t === 'opponent') return opponentName ? `Favors ${opponentName}` : 'Favors opponent'
  return 'Media'
}

function lastName(raw?: string): string {
  if (!raw) return ''
  const t = raw.trim()
  const last = (t.includes(',') ? t.split(',')[0] : t.split(/\s+/).pop() || '').trim()
  return last ? last[0].toUpperCase() + last.slice(1).toLowerCase() : ''
}

function formatDate(iso?: string): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

function relativeDays(iso?: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  const days = Math.floor((Date.now() - d.getTime()) / (1000 * 60 * 60 * 24))
  if (days === 0) return 'today'
  if (days === 1) return 'yesterday'
  if (days < 30) return `${days}d ago`
  if (days < 365) return `${Math.floor(days / 30)}mo ago`
  return `${Math.floor(days / 365)}y ago`
}

export function NarrativeDetail() {
  const { id } = useParams<{ id: string }>()
  const frameId = Number(id)
  const [detail, setDetail] = useState<NarrativeFrameDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [timeframe, setTimeframe] = useState<Timeframe>('30')
  const [error, setError] = useState<string | null>(null)
  const [candidateName, setCandidateName] = useState('')
  const [opponentName, setOpponentName] = useState('')

  // Variant timeline — separate state so the variant chart can render
  // independently of the existing activity chart. Errors are non-fatal
  // (the rest of the page renders) — if the timeline endpoint is missing
  // (e.g. backend not yet restarted), we just hide the variant section.
  const [timeline, setTimeline] = useState<NarrativeFrameTimeline | null>(null)
  const [timelineWindow, setTimelineWindow] = useState<VariantWindow>('90')

  useEffect(() => {
    if (!frameId) return
    setLoading(true)
    setError(null)
    api.narrativeFrameDetail(frameId)
      .then(setDetail)
      .catch(e => setError(String(e)))
      .finally(() => setLoading(false))
    api.campaign().then(c => setCandidateName(lastName(c.candidate_name))).catch(() => {})
    api.opponents().then(o => { if (o[0]) setOpponentName(lastName(o[0].name)) }).catch(() => {})
  }, [frameId])

  // Fetch the variant timeline whenever the frame or the requested window
  // changes. Failures are silent — the section just disappears (lets the page
  // still render if the endpoint is unavailable, e.g. backend not restarted).
  useEffect(() => {
    if (!frameId) return
    const days = parseInt(timelineWindow, 10)
    api.narrativeFrameTimeline(frameId, days)
      .then(setTimeline)
      .catch(() => setTimeline(null))
  }, [frameId, timelineWindow])

  // === HOOKS MUST RUN BEFORE ANY EARLY RETURN ===
  // Filtering the activity by timeframe and computing which tiers appear are
  // both useMemo — they have to run on every render in the same order, even
  // before `detail` arrives. Use empty fallbacks so they're safe to compute
  // pre-fetch.
  const filteredActivity: ActivityPoint[] = useMemo(() => {
    const all = detail?.activity ?? []
    if (timeframe === 'all') return all
    const days = parseInt(timeframe, 10)
    const cutoffISO = new Date(Date.now() - days * 86400000).toISOString().slice(0, 10)
    return all.filter(p => p.date >= cutoffISO)
  }, [detail, timeframe])

  const tiersInUse = useMemo(
    () => TIER_ORDER.filter(t => filteredActivity.some(p => (p[t] ?? 0) > 0)),
    [filteredActivity],
  )

  // Variant chart data preparation.
  // Recharts AreaChart wants: [{date, variantKey1: N, variantKey2: N, ...}].
  // We pick the top N variants by mention_count (these get distinct colors),
  // and bucket the rest into a single "Other" series. Dormant variants
  // (no daily_counts in window) are excluded from the chart but counted
  // separately so the legend can mention them.
  const variantChart = useMemo(() => {
    if (!timeline || timeline.variants.length === 0) {
      return { data: [], series: [], dormantCount: 0, hasData: false }
    }
    // Variants with at least one mention in the requested window.
    const active = timeline.variants.filter(v => v.daily_counts.length > 0)
    const dormant = timeline.variants.length - active.length

    // Top N by mention_count get distinct colors; everything else folded into "Other".
    const sorted = [...active].sort((a, b) => b.mention_count - a.mention_count)
    const topN = sorted.slice(0, TOP_VARIANT_COUNT)
    const others = sorted.slice(TOP_VARIANT_COUNT)

    // Build the per-day data map: date → { date, variantKey: count, ... }
    // Stored as `Record<string, any>` so we can accumulate numeric keys
    // alongside the literal `date` string without fighting TypeScript.
    type DayRow = Record<string, number | string>
    const dayMap = new Map<string, DayRow>()
    const ensureDay = (date: string): DayRow => {
      let entry = dayMap.get(date)
      if (!entry) {
        entry = { date }
        dayMap.set(date, entry)
      }
      return entry
    }

    const series: Array<{ key: string; name: string; color: string; mentions: number }> = []
    topN.forEach((v, i) => {
      const key = `v${v.id}`
      series.push({
        key,
        name: v.name,
        color: VARIANT_COLORS[i % VARIANT_COLORS.length],
        mentions: v.mention_count,
      })
      v.daily_counts.forEach(d => {
        const entry = ensureDay(d.date)
        const cur = (typeof entry[key] === 'number' ? entry[key] as number : 0)
        entry[key] = cur + d.count
      })
    })

    if (others.length > 0) {
      const otherMentions = others.reduce((s, v) => s + v.mention_count, 0)
      series.push({
        key: '__other__',
        name: `Other (${others.length} variant${others.length === 1 ? '' : 's'})`,
        color: OTHER_COLOR,
        mentions: otherMentions,
      })
      others.forEach(v => {
        v.daily_counts.forEach(d => {
          const entry = ensureDay(d.date)
          const cur = (typeof entry.__other__ === 'number' ? entry.__other__ as number : 0)
          entry.__other__ = cur + d.count
        })
      })
    }

    // Fill gaps: for the entire window, create a continuous date axis so the
    // chart doesn't render with collapsed time gaps. Each missing day gets
    // zeros for all series.
    const days = parseInt(timelineWindow, 10)
    const today = new Date()
    const filled: DayRow[] = []
    for (let i = days - 1; i >= 0; i--) {
      const d = new Date(today)
      d.setDate(d.getDate() - i)
      const key = d.toISOString().slice(0, 10)
      const entry: DayRow = dayMap.get(key) ?? { date: key }
      // Fill missing variant keys with 0 so stacking works
      series.forEach(s => { if (entry[s.key] == null) entry[s.key] = 0 })
      filled.push(entry)
    }

    return { data: filled, series, dormantCount: dormant, hasData: true }
  }, [timeline, timelineWindow])

  if (loading) {
    return (
      <div style={{ padding: '24px 28px', maxWidth: 1100, margin: '0 auto' }}>
        <div className="skeleton" style={{ height: 28, width: 200, marginBottom: 16, borderRadius: 4 }} />
        <div className="skeleton" style={{ height: 80, marginBottom: 16, borderRadius: 6 }} />
        <div className="skeleton" style={{ height: 220, marginBottom: 16, borderRadius: 6 }} />
        <div className="skeleton" style={{ height: 400, borderRadius: 6 }} />
      </div>
    )
  }

  if (error || !detail) {
    return (
      <div style={{ padding: '24px 28px', maxWidth: 1100, margin: '0 auto' }}>
        <Link to="/narratives" style={{ color: C.text2, fontSize: 13, textDecoration: 'none', display: 'inline-flex', alignItems: 'center', gap: 6, marginBottom: 16 }}>
          <ArrowLeft size={14} /> Back to narratives
        </Link>
        <div style={{ padding: 40, textAlign: 'center', color: C.text3, fontSize: 14 }}>
          {error ? `Failed to load: ${error}` : "Narrative not found."}
        </div>
      </div>
    )
  }

  const oc = ownerColor(detail.owner_type)
  const tiers = detail.outlet_tiers
  const tierEntries = (['national', 'regional', 'local', 'blog', 'social'] as const)
    .map(k => [k, tiers[k]] as const)
    .filter(([, n]) => n > 0)

  return (
    <div style={{ padding: '20px 28px 40px', maxWidth: 1100, margin: '0 auto' }}>
      {/* Back link */}
      <Link
        to="/narratives"
        style={{
          color: C.text2, fontSize: 13, textDecoration: 'none',
          display: 'inline-flex', alignItems: 'center', gap: 6, marginBottom: 16,
        }}
      >
        <ArrowLeft size={14} /> Back to narratives
      </Link>

      {/* Header */}
      <div style={{ marginBottom: 24 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
          <span style={{ width: 9, height: 9, borderRadius: '50%', background: oc, display: 'inline-block' }} />
          <span className="section-label" style={{ color: oc }}>
            {ownerLabel(detail.owner_type, candidateName, opponentName)}
          </span>
        </div>
        <h1 style={{ fontSize: 28, fontWeight: 800, color: C.text1, margin: 0, lineHeight: 1.2 }}>
          {detail.name}
        </h1>
        {detail.description && (
          <p style={{ color: C.text2, fontSize: 14, lineHeight: 1.5, marginTop: 8, maxWidth: 800 }}>
            {detail.description}
          </p>
        )}
      </div>

      {/* Stat row */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10, marginBottom: 24 }}>
        <Stat label="ARTICLES TOTAL" value={detail.articles_total} color={oc} />
        <Stat label="THIS WEEK" value={detail.articles_this_week} color={C.text1} />
        <Stat label="FIRST SEEN" value={formatDate(detail.first_seen_at)} valueSize={15} color={C.text2} />
        <Stat label="LAST SEEN" value={`${formatDate(detail.last_seen_at)} (${relativeDays(detail.last_seen_at)})`} valueSize={15} color={C.text2} />
      </div>

      {/* Activity chart — stacked by outlet tier, with a timeframe selector. */}
      <section style={{ marginBottom: 24 }}>
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          marginBottom: 10, gap: 8, flexWrap: 'wrap',
        }}>
          <div className="section-label">Activity — by outlet tier</div>
          <div style={{ display: 'flex', gap: 4 }}>
            {TIMEFRAMES.map(([val, label]) => {
              const active = timeframe === val
              return (
                <button
                  key={val}
                  onClick={() => setTimeframe(val)}
                  style={{
                    fontFamily: "'IBM Plex Mono', monospace",
                    fontSize: 10, letterSpacing: '0.05em',
                    padding: '4px 10px', borderRadius: 3, cursor: 'pointer',
                    border: `1px solid ${active ? oc : '#1c2a3f'}`,
                    background: active ? 'rgba(255,255,255,0.05)' : 'transparent',
                    color: active ? C.text1 : C.text3,
                  }}
                >
                  {label}
                </button>
              )
            })}
          </div>
        </div>
        <div style={{
          background: C.bg2, border: `1px solid ${C.bg3}`,
          borderRadius: 6, padding: '14px 16px',
        }}>
          {filteredActivity.length === 0 ? (
            <EmptyBlock label="No activity in this window." />
          ) : (
            <>
              <ResponsiveContainer width="100%" height={180}>
                <BarChart data={filteredActivity} margin={{ top: 8, right: 12, left: -20, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="2 4" stroke="#1c2a3f" />
                  <XAxis
                    dataKey="date"
                    tick={{ fontSize: 9, fill: C.text3, fontFamily: "'IBM Plex Mono', monospace" }}
                    tickFormatter={v => v.slice(5)}
                    minTickGap={28}
                    axisLine={false}
                    tickLine={false}
                  />
                  <YAxis
                    tick={{ fontSize: 9, fill: C.text3, fontFamily: "'IBM Plex Mono', monospace" }}
                    axisLine={false}
                    tickLine={false}
                    allowDecimals={false}
                  />
                  <Tooltip
                    contentStyle={{
                      background: '#0e1422', border: `1px solid ${C.border}`,
                      borderRadius: 3, fontSize: 12, color: C.text1,
                    }}
                    labelStyle={{ color: C.text3 }}
                    cursor={{ fill: 'rgba(255,255,255,0.05)' }}
                    formatter={(value: number, name: string) => [
                      value, TIER_LABELS[name as TierKey] ?? name,
                    ]}
                  />
                  {TIER_ORDER.map(tier => (
                    <Bar
                      key={tier}
                      dataKey={tier}
                      stackId="a"
                      fill={TIER_COLORS[tier]}
                    />
                  ))}
                </BarChart>
              </ResponsiveContainer>

              {/* Legend — only swatches for tiers that actually appear */}
              {tiersInUse.length > 0 && (
                <div style={{
                  display: 'flex', gap: 14, marginTop: 10,
                  flexWrap: 'wrap', fontSize: 11,
                }}>
                  {tiersInUse.map(tier => (
                    <div key={tier} style={{ display: 'flex', alignItems: 'center', gap: 5 }}>
                      <span style={{
                        width: 10, height: 10, borderRadius: 2,
                        background: TIER_COLORS[tier], display: 'inline-block',
                      }} />
                      <span style={{ color: C.text2 }}>{TIER_LABELS[tier]}</span>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      </section>

      {/* Variant timeline — how messaging is evolving over time */}
      {timeline && variantChart.hasData && (
        <section style={{ marginBottom: 24 }}>
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            marginBottom: 4, gap: 8, flexWrap: 'wrap',
          }}>
            <div className="section-label">Variant evolution — how the messaging is changing</div>
            <div style={{ display: 'flex', gap: 4 }}>
              {VARIANT_WINDOWS.map(([val, label]) => {
                const active = timelineWindow === val
                return (
                  <button
                    key={val}
                    onClick={() => setTimelineWindow(val)}
                    style={{
                      fontFamily: "'IBM Plex Mono', monospace",
                      fontSize: 10, letterSpacing: '0.05em',
                      padding: '4px 10px', borderRadius: 3, cursor: 'pointer',
                      border: `1px solid ${active ? oc : '#1c2a3f'}`,
                      background: active ? 'rgba(255,255,255,0.05)' : 'transparent',
                      color: active ? C.text1 : C.text3,
                    }}
                  >
                    {label}
                  </button>
                )
              })}
            </div>
          </div>
          <div style={{ fontSize: 11, color: C.text3, marginBottom: 8 }}>
            Each color is a distinct messaging variant within this frame. Heights stack to show
            total mentions per day. New variants appearing on the right side = emerging framings.
          </div>
          <div style={{
            background: C.bg2, border: `1px solid ${C.bg3}`,
            borderRadius: 6, padding: '14px 16px',
          }}>
            <ResponsiveContainer width="100%" height={220}>
              <AreaChart data={variantChart.data} margin={{ top: 8, right: 12, left: -20, bottom: 0 }}>
                <CartesianGrid strokeDasharray="2 4" stroke="#1c2a3f" />
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 9, fill: C.text3, fontFamily: "'IBM Plex Mono', monospace" }}
                  tickFormatter={v => {
                    const d = new Date(v + 'T00:00:00')
                    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
                  }}
                  minTickGap={40}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  tick={{ fontSize: 9, fill: C.text3, fontFamily: "'IBM Plex Mono', monospace" }}
                  axisLine={false}
                  tickLine={false}
                  allowDecimals={false}
                />
                <Tooltip
                  contentStyle={{
                    background: '#0e1422', border: `1px solid ${C.border}`,
                    borderRadius: 3, fontSize: 11, color: C.text1, maxWidth: 320,
                  }}
                  labelStyle={{ color: C.text3, fontSize: 10 }}
                  labelFormatter={(v: string) => {
                    const d = new Date(v + 'T00:00:00')
                    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
                  }}
                  cursor={{ stroke: '#1c2a3f', strokeWidth: 1 }}
                  // Show only variants with >0 in tooltip
                  formatter={(value: number, name: string) => {
                    if (value === 0) return ['', '']
                    const s = variantChart.series.find(x => x.key === name)
                    return [value, s?.name || name]
                  }}
                />
                {variantChart.series.map(s => (
                  <Area
                    key={s.key}
                    type="monotone"
                    dataKey={s.key}
                    stackId="variants"
                    stroke={s.color}
                    fill={s.color}
                    fillOpacity={0.65}
                    strokeWidth={1}
                  />
                ))}
              </AreaChart>
            </ResponsiveContainer>

            {/* Custom legend — variant names + mention counts, color-keyed */}
            <div style={{
              display: 'flex', gap: 12, marginTop: 12,
              flexWrap: 'wrap', fontSize: 11,
            }}>
              {variantChart.series.map(s => (
                <div key={s.key} style={{ display: 'flex', alignItems: 'center', gap: 6, maxWidth: 240 }}>
                  <span style={{
                    width: 10, height: 10, borderRadius: 2,
                    background: s.color, display: 'inline-block', flexShrink: 0,
                  }} />
                  <span style={{
                    color: C.text2,
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    flex: 1,
                  }}>
                    {s.name}
                  </span>
                  <span style={{
                    color: C.text3, fontFamily: "'IBM Plex Mono', monospace",
                    fontSize: 10, flexShrink: 0,
                  }}>
                    {s.mentions}
                  </span>
                </div>
              ))}
            </div>

            {variantChart.dormantCount > 0 && (
              <div style={{ marginTop: 6, fontSize: 10, color: C.text3, fontStyle: 'italic' }}>
                + {variantChart.dormantCount} dormant variant{variantChart.dormantCount === 1 ? '' : 's'} (no mentions in window)
              </div>
            )}
          </div>
        </section>
      )}

      {/* Two columns: Quotes + Outlet mix */}
      <div style={{ display: 'grid', gridTemplateColumns: '1.7fr 1fr', gap: 16, marginBottom: 24 }}>
        <Section title="Notable quotes" icon={<MessageSquareQuote size={14} color={C.text3} />}>
          {detail.quotes.length === 0 ? (
            <EmptyBlock label="No quotes available — articles still being summarized." />
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              {detail.quotes.map((q, i) => (
                <div key={i} style={{
                  padding: 12, background: C.bg2, borderRadius: 6,
                  borderLeft: `3px solid ${oc}`,
                }}>
                  <div style={{ color: C.text1, fontSize: 13, lineHeight: 1.5 }}>
                    "{q.text}"
                  </div>
                  <div style={{ marginTop: 8, fontSize: 11, color: C.text3, display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span>{q.outlet_name || q.source_name || 'Unknown source'}</span>
                    <span>·</span>
                    <span>{formatDate(q.published_at)}</span>
                    {q.source_url && (
                      <a href={q.source_url} target="_blank" rel="noreferrer" style={{ color: C.text2, marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                        Read <ExternalLink size={10} />
                      </a>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </Section>

        <Section title="Coverage by outlet tier">
          {tierEntries.length === 0 ? (
            <EmptyBlock label="No tier data." />
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {tierEntries.map(([tier, n]) => (
                <div key={tier} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span className="section-label" style={{ flex: 1, textTransform: 'uppercase' }}>
                    {tier}
                  </span>
                  <span style={{ fontFamily: "'IBM Plex Mono', monospace", color: C.text1, fontWeight: 600 }}>
                    {n}
                  </span>
                </div>
              ))}
            </div>
          )}
        </Section>
      </div>

      {/* All articles */}
      <Section title={`All articles (${detail.articles.length})`}>
        {detail.articles.length === 0 ? (
          <EmptyBlock label="No articles linked yet." />
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column' }}>
            {detail.articles.map((a, i) => (
              <a
                key={a.id}
                href={a.source_url || '#'}
                target={a.source_url ? '_blank' : undefined}
                rel="noreferrer"
                style={{
                  display: 'grid',
                  gridTemplateColumns: '80px 1fr 140px',
                  gap: 12,
                  alignItems: 'center',
                  padding: '10px 4px',
                  borderTop: i === 0 ? 'none' : `1px solid ${C.bg3}`,
                  color: 'inherit', textDecoration: 'none',
                }}
                onMouseEnter={e => (e.currentTarget.style.background = C.bg2)}
                onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
              >
                <div style={{ fontSize: 11, color: C.text3, fontFamily: "'IBM Plex Mono', monospace" }}>
                  {a.published_at ? formatDate(a.published_at) : '—'}
                </div>
                <div style={{ minWidth: 0 }}>
                  <div style={{
                    fontSize: 13, color: C.text1, fontWeight: 500,
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  }}>
                    {a.title}
                  </div>
                  {a.summary && (
                    <div style={{
                      fontSize: 12, color: C.text3, marginTop: 2,
                      overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                    }}>
                      {a.summary}
                    </div>
                  )}
                </div>
                <div style={{ fontSize: 11, color: C.text2, textAlign: 'right', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {a.outlet_name || a.source_name || '—'}
                </div>
              </a>
            ))}
          </div>
        )}
      </Section>
    </div>
  )
}

function Stat({ label, value, color, valueSize = 22 }: {
  label: string; value: string | number; color?: string; valueSize?: number
}) {
  return (
    <div style={{ padding: '12px 14px', background: C.bg2, borderRadius: 6, border: `1px solid ${C.bg3}` }}>
      <div style={{ fontSize: valueSize, fontWeight: 700, color: color || C.text1, lineHeight: 1.1 }}>
        {value}
      </div>
      <div className="section-label" style={{ marginTop: 4 }}>{label}</div>
    </div>
  )
}

function Section({ title, icon, children }: {
  title: string; icon?: React.ReactNode; children: React.ReactNode
}) {
  return (
    <section style={{ marginBottom: 24 }}>
      <div className="section-label" style={{
        marginBottom: 10, display: 'flex', alignItems: 'center', gap: 6,
      }}>
        {icon}{title}
      </div>
      <div style={{
        background: C.bg2, border: `1px solid ${C.bg3}`,
        borderRadius: 6, padding: '14px 16px',
      }}>
        {children}
      </div>
    </section>
  )
}

function EmptyBlock({ label }: { label: string }) {
  return (
    <div style={{
      padding: '24px 0', textAlign: 'center', color: C.text3, fontSize: 13,
    }}>
      {label}
    </div>
  )
}
