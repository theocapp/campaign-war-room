import { ArrowLeft, Copy, ExternalLink, MessageSquareQuote, Edit2, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useLocation, useNavigate, useParams } from 'react-router-dom'
import { Area, AreaChart, Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api } from '@/api/client'
import type { ActivityPoint, DetailArticle, NarrativeFrameDetail, NarrativeFrameTimeline, OwnerType } from '@/api/types'
import { InfoTooltip } from '@/components/InfoTooltip'
import { formatArticleDate } from '@/lib/formatDate'

const OUTLET_TIER_HELP =
  'Where the story is showing up:\n• National — big outlets (NYT, WaPo, AP, cable news)\n• Regional — state-level papers and TV\n• Local — district-level outlets (very high signal for us)\n• Blog — opinion sites and small publishers\n• Social — Reddit, Bluesky, etc.\n\nLocal + regional means it\'s real on the ground; national means it\'s broken through.'

type TierKey = 'national' | 'regional' | 'local' | 'blog' | 'social' | 'unknown'
const TIER_ORDER: readonly TierKey[] = ['national', 'regional', 'local', 'blog', 'social', 'unknown']
// Cool gradient palette — distinct from the candidate-blue / opponent-red
// owner colors so tier ≠ "side". Each hue chosen to read clearly in dark mode
// and against the bg-2 panel. Local and Blog are pulled out of the
// pee-yellow / poo-gray hole the prior palette was in.
const TIER_COLORS: Record<TierKey, string> = {
  national: '#a855f7',  // purple — biggest media
  regional: '#6366f1',  // indigo
  local:    '#06b6d4',  // cyan — high signal, distinct
  blog:     '#14b8a6',  // teal
  social:   '#22c55e',  // emerald — organic / people
  unknown:  '#64748b',  // slate — proper neutral gray
}
const TIER_LABELS: Record<TierKey, string> = {
  national: 'National', regional: 'Regional', local: 'Local',
  blog: 'Blog', social: 'Social', unknown: 'Unknown',
}

// Maps a SourceItem.outlet_type to one of our 5 tier buckets — mirrors
// the backend rule in services/narrative_frames.py get_frame_detail().
// Used for client-side filtering of detail.articles when the user clicks
// a tier bar in the Activity chart.
function outletTypeToTier(outletType?: string): TierKey {
  if (!outletType) return 'unknown'
  if (outletType === 'national' || outletType === 'broadcast') return 'national'
  if (outletType === 'regional_news') return 'regional'
  if (outletType === 'local_news') return 'local'
  if (outletType === 'blog') return 'blog'
  if (outletType === 'social') return 'social'
  return 'unknown'
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

// Palette for top variants. Cool-gradient with two warm accents so adjacent
// variants are visually distinct without clashing with the candidate-blue /
// opponent-red owner colors. Order matters — higher-volume variants get the
// brightest, most prominent hue.
const VARIANT_COLORS = [
  '#a855f7',  // purple — top variant
  '#06b6d4',  // cyan
  '#f59e0b',  // amber — warm contrast
  '#10b981',  // emerald
  '#ec4899',  // pink
  '#8b5cf6',  // violet
  '#14b8a6',  // teal
]
const OTHER_COLOR = '#64748b'  // slate — neutral, reads as "other" not as data
const TOP_VARIANT_COUNT = 6

// Colors via CSS variables so dark/light toggle works. See index.css.
const C = {
  bg1: 'var(--bg-1)', bg2: 'var(--bg-2)', bg3: 'var(--bg-3)',
  border: 'var(--border)',
  text1: 'var(--text-1)', text2: 'var(--text-2)', text3: 'var(--text-3)',
  candidate: 'var(--candidate)', opponent: 'var(--opponent)', media: 'var(--media)',
  accent: 'var(--accent)', accentSoft: 'rgba(255,191,0,0.13)',
}

function ownerColor(t: OwnerType) {
  return t === 'candidate' ? C.candidate : t === 'opponent' ? C.opponent : C.media
}

// V13.21 — quadrant color (owner × subject). See lib/quadrantColor.ts.
import { quadrantColor as _qc } from '@/lib/quadrantColor'
function frameColor(f: { owner_type?: OwnerType; subject_type?: OwnerType }): string {
  if (f.subject_type) return _qc(f.owner_type ?? null, f.subject_type ?? null)
  return ownerColor(f.owner_type ?? 'media')
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
  const out = formatArticleDate(iso)
  return out || '—'
}

// Discriminated union describing the active drill-down filter on the All
// Articles list. Module-level so the ArticleList helper component can
// type-check against it.
type ArticleFilter =
  | { kind: 'tier'; tier: TierKey; tierLabel: string; date: string }
  | {
      kind: 'variant'; variantId: number; variantName: string;
      variantColor: string; date: string;
      articles: DetailArticle[] | null;  // null while loading
      loading: boolean;
    }

export function NarrativeDetail() {
  const { id } = useParams<{ id: string }>()
  const frameId = Number(id)
  const navigate = useNavigate()
  const location = useLocation()
  // React Router's initial entry has key='default'. A different key means
  // the user navigated here from another in-app page — go back to where
  // they came from. Direct hits (bookmark, pasted URL) fall back to the
  // narratives list.
  const hasInAppHistory = location.key !== 'default'
  const backLabel = hasInAppHistory ? 'Back' : 'Back to narratives'

  function handleBack() {
    if (hasInAppHistory) navigate(-1)
    else navigate('/narratives')
  }

  const backButtonStyle: React.CSSProperties = {
    background: 'transparent', border: 'none', padding: 0,
    font: 'inherit',
    color: C.text2, fontSize: 13, cursor: 'pointer',
    display: 'inline-flex', alignItems: 'center', gap: 6, marginBottom: 16,
    textAlign: 'left',
  }

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

  // Entity-graph join — entities and relations propagating this frame.
  const [frameGraph, setFrameGraph] = useState<Awaited<ReturnType<typeof api.frameGraph>> | null>(null)

  // v15.0 quote evidence — verbatim claim_record spans from articles matched
  // to this frame. Grouped by label in the UI.
  const [quoteEvidence, setQuoteEvidence] = useState<Awaited<ReturnType<typeof api.frameQuoteEvidence>> | null>(null)
  const [quoteLabelFilter, setQuoteLabelFilter] = useState<string | null>(null)

  // Click-to-drill-down filter for the All Articles list. Set by clicking
  // either a tier-bar in the Activity chart (kind='tier', client-side filter)
  // or a variant-area/dot in the Variant Evolution chart (kind='variant',
  // server-fetched via /variant-articles).
  const [articleFilter, setArticleFilter] = useState<ArticleFilter | null>(null)
  const articlesSectionRef = useRef<HTMLDivElement | null>(null)

  function scrollToArticles() {
    // requestAnimationFrame so the filter chip renders before we scroll —
    // otherwise the scroll target ends up too high.
    requestAnimationFrame(() => {
      articlesSectionRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' })
    })
  }

  function handleTierBarClick(tier: TierKey, date: string) {
    setArticleFilter({
      kind: 'tier', tier, tierLabel: TIER_LABELS[tier], date,
    })
    scrollToArticles()
  }

  function handleVariantClick(variantId: number, variantName: string, variantColor: string, date: string) {
    // Optimistic: render the chip immediately, fetch in the background.
    setArticleFilter({
      kind: 'variant', variantId, variantName, variantColor, date,
      articles: null, loading: true,
    })
    scrollToArticles()
    api.narrativeFrameVariantArticles(frameId, variantId, date)
      .then(arr => setArticleFilter(prev =>
        prev && prev.kind === 'variant' && prev.variantId === variantId && prev.date === date
          ? { ...prev, articles: arr, loading: false }
          : prev,
      ))
      .catch(() => setArticleFilter(prev =>
        prev && prev.kind === 'variant' && prev.variantId === variantId && prev.date === date
          ? { ...prev, articles: [], loading: false }
          : prev,
      ))
  }

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
    api.frameGraph(frameId, 25).then(setFrameGraph).catch(() => setFrameGraph(null))
    api.frameQuoteEvidence(frameId, 200).then(setQuoteEvidence).catch(() => setQuoteEvidence(null))
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
        <button onClick={handleBack} style={backButtonStyle}>
          <ArrowLeft size={14} /> {backLabel}
        </button>
        <div style={{ padding: 40, textAlign: 'center', color: C.text3, fontSize: 14 }}>
          {error ? `Failed to load: ${error}` : "Narrative not found."}
        </div>
      </div>
    )
  }

  const oc = frameColor(detail)
  const tiers = detail.outlet_tiers
  const tierEntries = (['national', 'regional', 'local', 'blog', 'social'] as const)
    .map(k => [k, tiers[k]] as const)
    .filter(([, n]) => n > 0)

  return (
    <div style={{ padding: '20px 28px 40px', maxWidth: 1100, margin: '0 auto' }}>
      {/* Back link */}
      <button onClick={handleBack} style={backButtonStyle}>
        <ArrowLeft size={14} /> {backLabel}
      </button>

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
        <Stat label="LAST SEEN" value={formatDate(detail.last_seen_at)} valueSize={15} color={C.text2} />
      </div>

      {/* Activity chart — stacked by outlet tier, with a timeframe selector. */}
      <section style={{ marginBottom: 24 }}>
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          marginBottom: 10, gap: 8, flexWrap: 'wrap',
        }}>
          <div className="section-label" style={{ display: 'inline-flex', alignItems: 'center' }}>
            Activity — by outlet tier
            <InfoTooltip text={OUTLET_TIER_HELP} />
          </div>
          <div style={{ display: 'flex', gap: 4 }}>
            {TIMEFRAMES.map(([val, label]) => {
              const active = timeframe === val
              return (
                <button
                  key={val}
                  onClick={() => setTimeframe(val)}
                  style={{
                    fontSize: 10, letterSpacing: '0.05em',
                    padding: '4px 10px', borderRadius: 3, cursor: 'pointer',
                    border: `1px solid ${active ? oc : 'var(--border)'}`,
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
                  <CartesianGrid strokeDasharray="2 4" stroke="var(--bg-3)" />
                  <XAxis
                    dataKey="date"
                    tick={{ fontSize: 9, fill: C.text3 }}
                    tickFormatter={v => v.slice(5)}
                    minTickGap={28}
                    axisLine={false}
                    tickLine={false}
                  />
                  <YAxis
                    tick={{ fontSize: 9, fill: C.text3 }}
                    axisLine={false}
                    tickLine={false}
                    allowDecimals={false}
                  />
                  <Tooltip
                    contentStyle={{
                      background: 'var(--bg-3)', border: `1px solid ${C.border}`,
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
                      cursor="pointer"
                      onClick={(entry) => {
                        // Recharts passes the row datum + index. We close over `tier`.
                        const row = (entry?.payload ?? entry) as ActivityPoint | undefined
                        const count = (row?.[tier] ?? 0) as number
                        if (row && count > 0) handleTierBarClick(tier, row.date)
                      }}
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
            <div className="section-label" style={{ display: 'inline-flex', alignItems: 'center' }}>
              Variant evolution — how the messaging is changing
              <InfoTooltip
                text={'Within a single narrative, there are usually multiple specific framings (e.g. "Bresnahan voted against rural hospitals" vs "Bresnahan opposed the healthcare bill"). Each color is one of those variants. Watch for new colors appearing on the right — that\'s a new angle being introduced.'}
              />
            </div>
            <div style={{ display: 'flex', gap: 4 }}>
              {VARIANT_WINDOWS.map(([val, label]) => {
                const active = timelineWindow === val
                return (
                  <button
                    key={val}
                    onClick={() => setTimelineWindow(val)}
                    style={{
                      fontSize: 10, letterSpacing: '0.05em',
                      padding: '4px 10px', borderRadius: 3, cursor: 'pointer',
                      border: `1px solid ${active ? oc : 'var(--border)'}`,
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
                <CartesianGrid strokeDasharray="2 4" stroke="var(--bg-3)" />
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 9, fill: C.text3 }}
                  tickFormatter={v => {
                    const d = new Date(v + 'T00:00:00')
                    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
                  }}
                  minTickGap={40}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  tick={{ fontSize: 9, fill: C.text3 }}
                  axisLine={false}
                  tickLine={false}
                  allowDecimals={false}
                />
                <Tooltip
                  cursor={{ stroke: 'var(--border)', strokeWidth: 1 }}
                  content={<VariantTooltip seriesMeta={variantChart.series} />}
                />
                {variantChart.series.map(s => {
                  // "__other__" is a synthetic bucket — clicking it can't drill
                  // down to a specific variant. Make it non-clickable.
                  const clickable = s.key.startsWith('v')
                  const variantId = clickable ? parseInt(s.key.slice(1), 10) : null
                  // Recharts' DotProps isn't usefully exported and the strict
                  // typed onClick blocks us from reading props.payload. Cast
                  // the activeDot config so TS accepts the data-aware handler.
                  // eslint-disable-next-line @typescript-eslint/no-explicit-any
                  const activeDot: any = clickable
                    ? {
                        r: 5, stroke: s.color, strokeWidth: 2,
                        fill: 'var(--bg-1)', cursor: 'pointer',
                        onClick: (props: { payload?: { date?: string } }) => {
                          const date = props?.payload?.date
                          if (variantId != null && date) {
                            handleVariantClick(variantId, s.name, s.color, date)
                          }
                        },
                      }
                    : { r: 0 }
                  return (
                    <Area
                      key={s.key}
                      type="monotone"
                      dataKey={s.key}
                      stackId="variants"
                      stroke={s.color}
                      fill={s.color}
                      fillOpacity={0.65}
                      strokeWidth={1}
                      style={{ cursor: clickable ? 'pointer' : 'default' }}
                      activeDot={activeDot}
                    />
                  )
                })}
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
                    color: C.text3,
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
        <Section
          title="Notable quotes"
          icon={<MessageSquareQuote size={14} color={C.text3} />}
        >
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

        <Section title="Coverage by outlet tier" tooltip={OUTLET_TIER_HELP}>
          {tierEntries.length === 0 ? (
            <EmptyBlock label="No tier data." />
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {tierEntries.map(([tier, n]) => (
                <div key={tier} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span className="section-label" style={{ flex: 1, textTransform: 'uppercase' }}>
                    {tier}
                  </span>
                  <span style={{ color: C.text1, fontWeight: 600 }}>
                    {n}
                  </span>
                </div>
              ))}
            </div>
          )}
        </Section>
      </div>

      {/* v15.0 supporting quotes — verbatim claim_record spans from articles
          matched to this frame. Use the label chips to filter; one-click copy
          for press release / rapid-response use. */}
      {quoteEvidence && quoteEvidence.total > 0 && (
        <SupportingQuotes
          data={quoteEvidence}
          activeLabel={quoteLabelFilter}
          onLabelChange={setQuoteLabelFilter}
        />
      )}

      {/* All articles — filterable by clicking a tier bar or variant spike above. */}
      <div ref={articlesSectionRef}>
        <ArticleList
          articles={detail.articles}
          articleFilter={articleFilter}
          onClearFilter={() => setArticleFilter(null)}
        />
      </div>

      {/* Entity-graph join — entities and relations propagating this frame.
          GKG principle: connecting the narrative-tracking layer (what story
          is being told) with the entity-graph (who-does-what-to-whom). */}
      {frameGraph && (frameGraph.entities.length > 0 || frameGraph.relations.length > 0) && (
        <Section
          title="Entities & relations propagating this narrative"
          tooltip="The entities most mentioned in this frame's articles, and the specific subject→predicate→object claims those articles produce. Bridges the narrative-tracking layer with the entity graph."
        >
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.4fr', gap: 16 }}>
            {/* Entities column */}
            <div>
              <div style={{ fontSize: 11, color: C.text3, textTransform: 'uppercase', fontWeight: 700, letterSpacing: '0.06em', marginBottom: 8 }}>
                Top entities ({frameGraph.entities.length})
              </div>
              {frameGraph.entities.slice(0, 12).map(e => (
                <div key={e.id} style={{ display: 'flex', alignItems: 'baseline', gap: 8, padding: '6px 0', borderBottom: `1px solid ${C.border}` }}>
                  <span style={{
                    width: 6, height: 6, borderRadius: '50%',
                    background: e.affiliation === 'D' ? '#0059c2' : e.affiliation === 'R' ? '#d71913' : C.text3,
                    flexShrink: 0,
                  }} />
                  <div style={{ fontSize: 12, color: C.text1, fontWeight: 500, flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {e.name}
                  </div>
                  <div style={{ fontSize: 11, color: C.text3, whiteSpace: 'nowrap' }}>
                    {e.mention_count_in_frame} / {e.overall_mention_count}
                  </div>
                </div>
              ))}
            </div>
            {/* Relations column */}
            <div>
              <div style={{ fontSize: 11, color: C.text3, textTransform: 'uppercase', fontWeight: 700, letterSpacing: '0.06em', marginBottom: 8 }}>
                Top relations ({frameGraph.relations.length})
              </div>
              {frameGraph.relations.slice(0, 15).map(r => (
                <div key={r.id} style={{ padding: '6px 0', borderBottom: `1px solid ${C.border}` }}>
                  <div style={{ fontSize: 12, color: C.text1 }}>
                    <span style={{ fontWeight: 600 }}>{r.source_name}</span>
                    <span style={{
                      margin: '0 6px', padding: '1px 6px', borderRadius: 3,
                      background: r.type.includes('attack') || r.type.includes('critic') || r.type === 'voted_against'
                        ? 'rgba(239, 68, 68, 0.12)'
                        : r.type.includes('endors') || r.type === 'co_sponsored' || r.type === 'voted_for'
                          ? 'rgba(34, 197, 94, 0.12)'
                          : 'rgba(115, 115, 115, 0.12)',
                      color: r.type.includes('attack') || r.type.includes('critic') || r.type === 'voted_against'
                        ? '#ef4444'
                        : r.type.includes('endors') || r.type === 'co_sponsored' || r.type === 'voted_for'
                          ? '#22c55e'
                          : C.text2,
                      fontSize: 10, fontWeight: 600,
                    }}>
                      {r.type.replace(/_/g, ' ')}
                    </span>
                    <span style={{ fontWeight: 600 }}>{r.target_name}</span>
                  </div>
                  <div style={{ fontSize: 11, color: C.text3, marginTop: 2 }}>
                    in this frame: {r.weight_in_frame}/{r.overall_weight} ({Math.round(r.in_frame_share * 100)}% of overall weight)
                  </div>
                </div>
              ))}
            </div>
          </div>
          <div style={{ fontSize: 11, color: C.text3, marginTop: 12 }}>
            Counts: <code>in-frame / overall</code>. A relation with <code>15/86</code> means
            15 of its 86 supporting articles also support this narrative frame.
          </div>
        </Section>
      )}
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

function Section({ title, icon, children, tooltip, action }: {
  title: string; icon?: React.ReactNode; children: React.ReactNode;
  tooltip?: string; action?: React.ReactNode
}) {
  return (
    <section style={{ marginBottom: 24 }}>
      <div className="section-label" style={{
        marginBottom: 10, display: 'flex', alignItems: 'center', gap: 6,
      }}>
        {icon}{title}
        {tooltip && <InfoTooltip text={tooltip} />}
        {action && (
          <span style={{ marginLeft: 'auto', textTransform: 'none', letterSpacing: 0 }}>
            {action}
          </span>
        )}
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

// Color per shallow-label, kept aligned with the v15.0 label taxonomy.
// Hex (not var()) so the `${color}22` alpha-suffix tints work — CSS vars
// don't support the hex-pair alpha trick.
const QUOTE_LABEL_COLOR: Record<string, string> = {
  attack: '#d71913',           // opponent red
  defense: '#0059c2',          // candidate blue
  endorsement: '#22c55e',
  vote: '#a78bfa',
  policy_position: '#fb923c',
  commitment: '#38bdf8',
  announcement: '#facc15',
  statement: '#8a8a8a',
  unlabeled: '#8a8a8a',
}

function formatQuoteDate(iso: string | null): string {
  if (!iso) return ''
  const d = new Date(iso)
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

function SupportingQuotes({
  data,
  activeLabel,
  onLabelChange,
}: {
  data: NonNullable<Awaited<ReturnType<typeof api.frameQuoteEvidence>>>
  activeLabel: string | null
  onLabelChange: (label: string | null) => void
}) {
  const labels = Object.entries(data.by_label).sort((a, b) => b[1] - a[1])
  const visible = activeLabel
    ? data.quotes.filter(q => (q.label ?? 'unlabeled') === activeLabel)
    : data.quotes
  const [copiedId, setCopiedId] = useState<number | null>(null)

  function copy(text: string, id: number) {
    navigator.clipboard?.writeText(text).then(() => {
      setCopiedId(id)
      setTimeout(() => setCopiedId(c => (c === id ? null : c)), 1400)
    }).catch(() => {})
  }

  return (
    <Section
      title={`Supporting quotes (${data.total})`}
      icon={<MessageSquareQuote size={14} />}
      tooltip="Verbatim quote spans from articles matched to this frame. Each quote is grounded in a single article — no paraphrase. Use the label chips to filter; one-click copy for press release / rapid-response."
      action={
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          <button
            onClick={() => onLabelChange(null)}
            style={{
              padding: '3px 8px', borderRadius: 4, fontSize: 11, fontWeight: 600,
              border: `1px solid ${activeLabel === null ? C.accent : C.bg3}`,
              background: activeLabel === null ? C.accentSoft : 'transparent',
              color: activeLabel === null ? C.accent : C.text2,
              cursor: 'pointer',
            }}
          >
            All
          </button>
          {labels.map(([label, count]) => {
            const isActive = activeLabel === label
            const color = QUOTE_LABEL_COLOR[label] ?? C.text3
            return (
              <button
                key={label}
                onClick={() => onLabelChange(isActive ? null : label)}
                style={{
                  padding: '3px 8px', borderRadius: 4, fontSize: 11, fontWeight: 600,
                  border: `1px solid ${isActive ? color : C.bg3}`,
                  background: isActive ? `${color}22` : 'transparent',
                  color: isActive ? color : C.text2,
                  cursor: 'pointer', textTransform: 'capitalize',
                }}
              >
                {label.replace(/_/g, ' ')} {count}
              </button>
            )
          })}
        </div>
      }
    >
      {visible.length === 0 && (
        <EmptyBlock label="No quotes match this filter." />
      )}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {visible.map(q => {
          const labelKey = q.label ?? 'unlabeled'
          const color = QUOTE_LABEL_COLOR[labelKey] ?? C.text3
          return (
            <div key={q.id} style={{
              borderLeft: `3px solid ${color}`,
              padding: '8px 12px',
              background: C.bg1,
              borderRadius: 4,
            }}>
              <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{
                    fontSize: 14, color: C.text1, lineHeight: 1.55,
                    fontStyle: 'italic',
                  }}>
                    &ldquo;{q.evidence_span}&rdquo;
                  </div>
                  <div style={{
                    display: 'flex', flexWrap: 'wrap', gap: 10, marginTop: 6,
                    fontSize: 11, color: C.text3, alignItems: 'center',
                  }}>
                    <span style={{
                      textTransform: 'capitalize', fontWeight: 600, color,
                    }}>
                      {labelKey.replace(/_/g, ' ')}
                    </span>
                    <span>·</span>
                    <span>{q.article.outlet_name ?? 'Unknown outlet'}</span>
                    {q.article.published_at && <><span>·</span><span>{formatQuoteDate(q.article.published_at)}</span></>}
                    {q.article.url && (
                      <a
                        href={q.article.url}
                        target="_blank"
                        rel="noreferrer"
                        style={{
                          display: 'inline-flex', alignItems: 'center', gap: 3,
                          color: C.text2, textDecoration: 'none',
                        }}
                      >
                        Source <ExternalLink size={10} />
                      </a>
                    )}
                    {q.entities.length > 0 && (
                      <>
                        <span>·</span>
                        <span>Entities: {q.entities.map(e => e.name).join(', ')}</span>
                      </>
                    )}
                  </div>
                </div>
                <button
                  onClick={() => copy(q.evidence_span, q.id)}
                  title="Copy verbatim text"
                  style={{
                    flexShrink: 0,
                    padding: '4px 8px', borderRadius: 4,
                    border: `1px solid ${C.bg3}`,
                    background: copiedId === q.id ? C.accentSoft : 'transparent',
                    color: copiedId === q.id ? C.accent : C.text2,
                    cursor: 'pointer', fontSize: 11,
                    display: 'inline-flex', alignItems: 'center', gap: 4,
                  }}
                >
                  <Copy size={11} />
                  {copiedId === q.id ? 'Copied' : 'Copy'}
                </button>
              </div>
            </div>
          )
        })}
      </div>
    </Section>
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

// Filterable article list. Shows a filter chip when articleFilter is set;
// otherwise renders every article passed in. For tier filters, applies the
// outlet_type → tier mapping client-side. For variant filters, uses the
// articles array fetched from /variant-articles (which is null while loading).
function ArticleList({
  articles, articleFilter, onClearFilter,
}: {
  articles: DetailArticle[]
  articleFilter: ArticleFilter | null
  onClearFilter: () => void
}) {
  let visible: DetailArticle[] = articles
  let titleSuffix = `(${articles.length})`
  let isLoading = false

  if (articleFilter?.kind === 'tier') {
    visible = articles.filter(a => {
      if (outletTypeToTier(a.outlet_type) !== articleFilter.tier) return false
      if (a.published_at && a.published_at.slice(0, 10) !== articleFilter.date) return false
      return true
    })
    titleSuffix = `(${visible.length})`
  } else if (articleFilter?.kind === 'variant') {
    isLoading = articleFilter.loading
    visible = articleFilter.articles ?? []
    titleSuffix = isLoading ? '(loading…)' : `(${visible.length})`
  }

  const chip = articleFilter && (
    <FilterChip filter={articleFilter} onClear={onClearFilter} />
  )

  return (
    <Section title={`All articles ${titleSuffix}`} action={chip}>
      {isLoading ? (
        <div style={{ padding: '20px 0', textAlign: 'center', color: C.text3, fontSize: 12 }}>
          Loading articles for this variant…
        </div>
      ) : visible.length === 0 ? (
        <EmptyBlock
          label={
            articleFilter
              ? 'No articles match this filter.'
              : 'No articles linked yet.'
          }
        />
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column' }}>
          {visible.map((a, i) => (
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
              <div style={{ fontSize: 11, color: C.text3 }}>
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
  )
}

function FilterChip({ filter, onClear }: { filter: ArticleFilter; onClear: () => void }) {
  const dateLabel = new Date(filter.date + 'T00:00:00').toLocaleDateString(
    'en-US', { month: 'short', day: 'numeric', year: 'numeric' },
  )
  const swatch = filter.kind === 'tier' ? TIER_COLORS[filter.tier] : filter.variantColor
  const label = filter.kind === 'tier'
    ? `${filter.tierLabel} outlets · ${dateLabel}`
    : `${filter.variantName} · ${dateLabel}`
  return (
    <div style={{
      display: 'inline-flex', alignItems: 'center', gap: 8,
      padding: '3px 4px 3px 10px',
      background: 'var(--bg-3)', border: `1px solid ${C.border}`,
      borderRadius: 999, fontSize: 11, color: C.text1,
      maxWidth: 360,
    }}>
      <span style={{ width: 8, height: 8, borderRadius: '50%', background: swatch, flexShrink: 0 }} />
      <span style={{
        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        flex: 1, minWidth: 0,
      }}>
        {label}
      </span>
      <button
        onClick={onClear}
        title="Clear filter"
        style={{
          background: 'none', border: 'none', cursor: 'pointer',
          color: C.text3, padding: 2, borderRadius: 999,
          display: 'inline-flex', alignItems: 'center', flexShrink: 0,
        }}
      >
        <X size={13} />
      </button>
    </div>
  )
}

// Custom tooltip for the Variant Evolution chart. Default Recharts tooltip
// renders each variant on one line with whiteSpace: nowrap, so long names
// ("Bresnahan voted against rural hospitals") overflow the box and clip the
// values. This wraps each name and skips zero-count series.
function VariantTooltip({
  active, payload, label, seriesMeta,
}: {
  active?: boolean
  payload?: Array<{ dataKey: string; value: number }>
  label?: string
  seriesMeta: Array<{ key: string; name: string; color: string; mentions: number }>
}) {
  if (!active || !payload?.length) return null
  const dateLabel = label
    ? new Date(label + 'T00:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
    : ''
  const rows = payload
    .filter(p => p.value > 0)
    .map(p => {
      const meta = seriesMeta.find(s => s.key === p.dataKey)
      return { name: meta?.name ?? p.dataKey, color: meta?.color ?? '#999', value: p.value }
    })
  if (rows.length === 0) return null
  return (
    <div style={{
      background: 'var(--bg-3)',
      border: `1px solid ${C.border}`,
      borderRadius: 4,
      padding: '8px 10px',
      maxWidth: 280,
      fontSize: 11,
      color: C.text1,
      boxShadow: '0 4px 16px rgba(0,0,0,0.4)',
    }}>
      {dateLabel && (
        <div style={{ fontSize: 10, color: C.text3, marginBottom: 6 }}>
          {dateLabel}
        </div>
      )}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        {rows.map((r, i) => (
          <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: 6, lineHeight: 1.35 }}>
            <span style={{
              width: 9, height: 9, borderRadius: 2, background: r.color,
              flexShrink: 0, marginTop: 3,
            }} />
            <span style={{ flex: 1, minWidth: 0, color: C.text2, whiteSpace: 'normal', wordBreak: 'break-word' }}>
              {r.name}
            </span>
            <span style={{
              color: C.text1,
              fontWeight: 600, flexShrink: 0,
            }}>
              {r.value}
            </span>
          </div>
        ))}
      </div>
      <div style={{ marginTop: 6, fontSize: 10, color: C.text3, fontStyle: 'italic' }}>
        Click to filter articles below
      </div>
    </div>
  )
}
