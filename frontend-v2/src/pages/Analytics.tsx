import { TrendingUp } from 'lucide-react'
import { useEffect, useState } from 'react'
import {
  Area,
  AreaChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { api } from '@/api/client'
import { InfoTooltip } from '@/components/InfoTooltip'
import type { Spike, ToneSeries, TrendSeries } from '@/api/types'

const CHART_TOOLTIP_STYLE = {
  contentStyle: {
    background: 'var(--bg-4)',
    border: '1px solid #2a3f5c',
    borderRadius: 3,
    fontSize: 11,
    color: 'var(--text-1)',
  },
  labelStyle: { color: 'var(--text-2)' },
}

function SectionTitle({ children, tooltip }: { children: React.ReactNode; tooltip?: string }) {
  return (
    <div style={{
      fontSize: 14,
      fontWeight: 700,
      letterSpacing: '0.1em',
      color: 'var(--text-2)',
      textTransform: 'uppercase',
      marginBottom: 14,
      paddingBottom: 8,
      borderBottom: '1px solid var(--bg-3)',
      display: 'inline-flex', alignItems: 'center',
    }}>
      {children}
      {tooltip && <InfoTooltip text={tooltip} />}
    </div>
  )
}

function EmptyChart({ label }: { label: string }) {
  return (
    <div style={{
      height: 160,
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      color: '#525252',
      border: '1px solid var(--bg-3)',
      borderRadius: 12,
      background: 'var(--bg-4)',
    }}>
      <TrendingUp size={28} style={{ marginBottom: 10, opacity: 0.3 }} />
      <div style={{ fontSize: 12 }}>{label}</div>
    </div>
  )
}

const TIMEFRAME_OPTIONS = [[7, '7D'], [30, '30D'], [90, '90D']] as const
const GEO_OPTIONS = [
  ['US-PA', 'PENNSYLVANIA'],
  ['US-PA-577', 'SCRANTON / WILKES-BARRE'],
] as const

function PillToggle<T extends string | number>({ options, value, onChange }: {
  options: ReadonlyArray<readonly [T, string]>
  value: T
  onChange: (v: T) => void
}) {
  return (
    <div style={{ display: 'flex', gap: 6 }}>
      {options.map(([val, label]) => (
        <button
          key={String(val)}
          onClick={() => onChange(val)}
          style={{
            fontSize: 10,
            letterSpacing: '0.05em',
            padding: '4px 10px',
            borderRadius: 3,
            cursor: 'pointer',
            border: `1px solid ${value === val ? '#4f8ef7' : 'var(--bg-3)'}`,
            background: value === val ? 'rgba(79,142,247,0.15)' : 'transparent',
            color: value === val ? '#4f8ef7' : 'var(--text-2)',
          }}
        >
          {label}
        </button>
      ))}
    </div>
  )
}

export function Analytics() {
  const [trends, setTrends] = useState<TrendSeries[] | null>(null)
  const [tone, setTone] = useState<ToneSeries[] | null>(null)
  const [spikes, setSpikes] = useState<Spike[] | null>(null)
  const [trendsGeo, setTrendsGeo] = useState<'US-PA' | 'US-PA-577'>('US-PA')
  const [trendsDays, setTrendsDays] = useState<7 | 30 | 90>(30)
  const [toneDays, setToneDays] = useState<7 | 30 | 90>(30)

  // Each section loads independently — no gating overlay. Spikes is the
  // slowest endpoint (one query per frame); decoupling means trends + tone
  // appear immediately even while spikes is still computing.
  useEffect(() => {
    api.spikes().then(setSpikes).catch(() => setSpikes([]))
  }, [])

  useEffect(() => {
    api.searchTrends(trendsGeo, trendsDays).then(setTrends).catch(() => setTrends([]))
  }, [trendsGeo, trendsDays])

  useEffect(() => {
    api.tone(toneDays).then(setTone).catch(() => setTone([]))
  }, [toneDays])

  // Merge tone data into a unified chart dataset
  const candidateTone = tone?.find(t => t.entity_type === 'candidate')
  const opponentTone = tone?.find(t => t.entity_type === 'opponent')

  // Trends line colors — candidate blue / opponent red, consistent with the
  // tone chart. Owner is resolved via the tone labels; other terms (custom
  // keywords) fall back to a neutral palette.
  const TREND_FALLBACK_COLORS = ['#f0a020', '#2db866', '#f07030']
  const trendColor = (term: string, idx: number): string => {
    const t = term.toLowerCase()
    if (candidateTone?.query_label.toLowerCase().includes(t)) return '#4f8ef7'
    if (opponentTone?.query_label.toLowerCase().includes(t)) return '#f05050'
    return TREND_FALLBACK_COLORS[idx % TREND_FALLBACK_COLORS.length]
  }

  const mergedTone = (() => {
    const dates = new Set<string>()
    candidateTone?.data.forEach(d => dates.add(d.date))
    opponentTone?.data.forEach(d => dates.add(d.date))
    return Array.from(dates).sort().map(date => ({
      date,
      candidate: candidateTone?.data.find(d => d.date === date)?.avg_tone ?? null,
      opponent: opponentTone?.data.find(d => d.date === date)?.avg_tone ?? null,
    }))
  })()

  // Spike share of voice (simplified donut data)
  const spikesList = spikes ?? []
  const spikeDonutData = spikesList.slice(0, 5).map(s => ({
    name: s.frame_name.length > 20 ? s.frame_name.slice(0, 20) + '…' : s.frame_name,
    value: s.reach_24h,
  }))
  const PIE_COLORS = ['#f05050', '#f07030', '#f0a020', '#4f8ef7', '#2db866']

  // Card styling tokens — dark grey panels (was dark navy)
  const CARD_BG = 'var(--bg-2)'
  const CARD_BORDER = '1px solid var(--bg-3)'

  return (
    <div style={{ minHeight: '100vh' }}>
      <div style={{ padding: '24px 28px', margin: '0 auto' }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 24 }}>

            {/* GDELT Tone — moved to top-left */}
            <div style={{ background: CARD_BG, border: CARD_BORDER, borderRadius: 12, padding: '18px 20px' }}>
              <SectionTitle tooltip={
                'Average emotional tone of news coverage that mentions your candidate ' +
                '(blue) or opponent (red), scored by GDELT. The score runs roughly from ' +
                '-10 (very negative) to +10 (very positive), aggregated daily.\n\n' +
                'GDELT computes this by counting positive and negative words in each ' +
                'article — so the score drops when articles include words like ' +
                '"scandal", "loss", "attack", "failure", and rises with words like ' +
                '"win", "success", "praise", "endorse". A spike up or down usually ' +
                'tracks a specific news event.\n\nUse it as a vibes-check: a sudden ' +
                'dip on one side typically means a bad-news cycle for them; check the ' +
                'Articles feed for that day to see which story drove it.'
              }>Media Tone</SectionTitle>
              <div style={{ marginBottom: 12 }}>
                <PillToggle options={TIMEFRAME_OPTIONS} value={toneDays} onChange={setToneDays} />
              </div>
              {tone === null ? (
                <div className="skeleton" style={{ height: 180, borderRadius: 12 }} />
              ) : mergedTone.length === 0 ? (
                <EmptyChart label="NO TONE DATA YET" />
              ) : (
                <>
                  <ResponsiveContainer width="100%" height={180}>
                    <AreaChart data={mergedTone} margin={{ top: 5, right: 10, bottom: 0, left: -25 }}>
                      <CartesianGrid strokeDasharray="2 4" stroke="var(--bg-3)" />
                      <XAxis
                        dataKey="date"
                        minTickGap={28}
                        tick={{ fontSize: 9, fill: 'var(--text-3)' }}
                        tickFormatter={v => v.slice(5)}
                        axisLine={false}
                        tickLine={false}
                      />
                      <YAxis
                        tick={{ fontSize: 9, fill: 'var(--text-3)' }}
                        axisLine={false}
                        tickLine={false}
                      />
                      <ReferenceLine y={0} stroke="#2a3f5c" strokeDasharray="4 4" />
                      <Tooltip {...CHART_TOOLTIP_STYLE} />
                      <Area
                        type="monotone"
                        dataKey="candidate"
                        name="Candidate"
                        stroke="#4f8ef7"
                        fill="rgba(79,142,247,0.12)"
                        strokeWidth={2}
                        connectNulls
                      />
                      <Area
                        type="monotone"
                        dataKey="opponent"
                        name="Opponent"
                        stroke="#f05050"
                        fill="rgba(240,80,80,0.08)"
                        strokeWidth={2}
                        connectNulls
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                  <div style={{ display: 'flex', gap: 16, marginTop: 8 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <span style={{ width: 12, height: 2, background: '#4f8ef7', display: 'inline-block' }} />
                      <span style={{ fontSize: 10, color: 'var(--text-2)' }}>
                        {candidateTone?.query_label ?? 'Candidate'}
                      </span>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <span style={{ width: 12, height: 2, background: '#f05050', display: 'inline-block' }} />
                      <span style={{ fontSize: 10, color: 'var(--text-2)' }}>
                        {opponentTone?.query_label ?? 'Opponent'}
                      </span>
                    </div>
                  </div>
                </>
              )}
            </div>

            {/* Google Trends — moved to top-right */}
            <div style={{ background: CARD_BG, border: CARD_BORDER, borderRadius: 12, padding: '18px 20px' }}>
              <SectionTitle>Search Interest Over Time</SectionTitle>
              <div style={{ display: 'flex', gap: 16, marginBottom: 12, flexWrap: 'wrap' }}>
                <PillToggle options={GEO_OPTIONS} value={trendsGeo} onChange={setTrendsGeo} />
                <PillToggle options={TIMEFRAME_OPTIONS} value={trendsDays} onChange={setTrendsDays} />
              </div>
              {trends === null ? (
                <div className="skeleton" style={{ height: 180, borderRadius: 12 }} />
              ) : trends.length === 0 ? (
                <EmptyChart label="NO TRENDS DATA FOR THIS AREA" />
              ) : (
                <ResponsiveContainer width="100%" height={180}>
                  <LineChart margin={{ top: 5, right: 10, bottom: 0, left: -25 }}>
                    <CartesianGrid strokeDasharray="2 4" stroke="var(--bg-3)" />
                    <XAxis
                      dataKey="date"
                      type="category"
                      allowDuplicatedCategory={false}
                      minTickGap={28}
                      tick={{ fontSize: 9, fill: 'var(--text-3)' }}
                      tickFormatter={v => v.slice(5)}
                      axisLine={false}
                      tickLine={false}
                    />
                    <YAxis
                      tick={{ fontSize: 9, fill: 'var(--text-3)' }}
                      domain={[0, 100]}
                      axisLine={false}
                      tickLine={false}
                    />
                    <Tooltip {...CHART_TOOLTIP_STYLE} />
                    {trends!.map((series, i) => (
                      <Line
                        key={series.term}
                        data={series.data}
                        dataKey="interest"
                        name={series.term}
                        type="monotone"
                        stroke={trendColor(series.term, i)}
                        strokeWidth={2}
                        dot={false}
                      />
                    ))}
                  </LineChart>
                </ResponsiveContainer>
              )}
              {trends && trends.length > 0 && (
                <div style={{ display: 'flex', gap: 16, marginTop: 10, flexWrap: 'wrap' }}>
                  {trends!.map((series, i) => (
                    <div key={series.term} style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                      <span style={{
                        width: 12,
                        height: 2,
                        background: trendColor(series.term, i),
                        display: 'inline-block',
                      }} />
                      <span style={{ fontSize: 10, color: 'var(--text-2)' }}>
                        {series.term}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Tone snapshot stats — row 2 col 1, under Media Tone */}
            {(candidateTone || opponentTone) && (
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 10, alignContent: 'start' }}>
                {[
                  {
                    label: 'CANDIDATE AVG TONE',
                    value: candidateTone
                      ? (candidateTone.data.reduce((s, d) => s + d.avg_tone, 0) / candidateTone.data.length).toFixed(1)
                      : '—',
                    color: '#4f8ef7',
                  },
                  {
                    label: 'OPPONENT AVG TONE',
                    value: opponentTone
                      ? (opponentTone.data.reduce((s, d) => s + d.avg_tone, 0) / opponentTone.data.length).toFixed(1)
                      : '—',
                    color: '#f05050',
                  },
                  {
                    label: 'TONE ADVANTAGE',
                    value: candidateTone && opponentTone
                      ? (() => {
                          const ca = candidateTone.data.reduce((s, d) => s + d.avg_tone, 0) / candidateTone.data.length
                          const oa = opponentTone.data.reduce((s, d) => s + d.avg_tone, 0) / opponentTone.data.length
                          return `${ca - oa > 0 ? '+' : ''}${(ca - oa).toFixed(1)}`
                        })()
                      : '—',
                    color: '#2db866',
                  },
                  { label: 'ACTIVE SPIKES', value: spikesList.length, color: '#f0a020' },
                ].map(stat => (
                  <div key={stat.label} style={{
                    padding: '14px 16px',
                    background: CARD_BG,
                    border: CARD_BORDER,
                    borderRadius: 12,
                  }}>
                    <div style={{
                      fontSize: 26,
                      fontWeight: 600,
                      color: stat.color,
                      lineHeight: 1,
                    }}>
                      {stat.value}
                    </div>
                    <div className="section-label" style={{ marginTop: 4 }}>{stat.label}</div>
                  </div>
                ))}
              </div>
            )}

            {/* Spike activity — row 2 col 2, under Search Interest */}
            <div style={{ background: CARD_BG, border: CARD_BORDER, borderRadius: 12, padding: '18px 20px' }}>
              <SectionTitle>24h Spike Activity — Reach Distribution</SectionTitle>
              {spikes === null ? (
                <div className="skeleton" style={{ height: 160, borderRadius: 12 }} />
              ) : spikesList.length === 0 ? (
                <EmptyChart label="NO SPIKES DETECTED (no frame had ≥2× its 7-day daily reach in the last 24h)" />
              ) : (
                <div style={{ display: 'flex', gap: 20, alignItems: 'center' }}>
                  <ResponsiveContainer width={160} height={160}>
                    <PieChart>
                      <Pie
                        data={spikeDonutData}
                        cx="50%"
                        cy="50%"
                        innerRadius={45}
                        outerRadius={70}
                        paddingAngle={2}
                        dataKey="value"
                      >
                        {spikeDonutData.map((_, index) => (
                          <Cell key={index} fill={PIE_COLORS[index % PIE_COLORS.length]} />
                        ))}
                      </Pie>
                    </PieChart>
                  </ResponsiveContainer>
                  <div style={{ flex: 1 }}>
                    {spikesList.map((spike, i) => (
                      <div key={i} style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 8,
                        marginBottom: 8,
                      }}>
                        <span style={{
                          width: 8,
                          height: 8,
                          borderRadius: '50%',
                          background: PIE_COLORS[i % PIE_COLORS.length],
                          flexShrink: 0,
                        }} />
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{
                            fontSize: 12,
                            color: 'var(--text-1)',
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap',
                          }}>
                            {spike.frame_name}
                          </div>
                          <div style={{
                            fontSize: 10,
                            color: 'var(--text-3)',
                          }}>
                            {spike.ratio.toFixed(1)}× · {spike.reach_24h.toLocaleString()} reach
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>

        </div>
      </div>
    </div>
  )
}
