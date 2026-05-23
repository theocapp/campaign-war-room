import { ChevronRight, Zap } from 'lucide-react'
import { useEffect, useState } from 'react'
import type { CSSProperties } from 'react'
import { Link } from 'react-router-dom'
import { Area, AreaChart, ResponsiveContainer } from 'recharts'
import { api } from '@/api/client'
import { getDashboardCache, prefetchDashboard } from '@/api/dashboardCache'
import type { NarrativeFrame, OwnerType, SourceItem, Spike, TimeseriesPoint } from '@/api/types'

const C = {
  bg1: '#121212', bg2: '#171717', bg3: '#262626', bg4: '#2f2f2f',
  border: '#434343', borderBright: '#555',
  text1: '#fff', text2: '#a1a1a1', text3: '#666',
  candidate: '#0059c2', opponent: '#d71913', media: '#a1a1a1',
  accent: '#ffbf00',
  green: '#22c55e', red: '#ef4444',
}

const STAGE_ORDER = ['mainstream', 'spreading', 'emerging', 'fading', 'dormant']

function ownerColor(t: OwnerType): string {
  return t === 'candidate' ? C.candidate : t === 'opponent' ? C.opponent : C.media
}

function stageLabel(s: string): string {
  return s.charAt(0).toUpperCase() + s.slice(1)
}

function formatDate(iso?: string) {
  if (!iso) return ''
  return new Date(iso).toLocaleDateString('en-US', { month: 'numeric', day: 'numeric' })
}

function TrendArrow({ delta }: { delta: number }) {
  if (delta > 0) return <span style={{ color: C.green, fontSize: 13 }}>↑</span>
  if (delta < 0) return <span style={{ color: C.red, fontSize: 13 }}>↓</span>
  return <span style={{ color: C.text3, fontSize: 13 }}>—</span>
}

function FeaturedCard({ frame }: { frame: NarrativeFrame }) {
  const oc = ownerColor(frame.owner_type)
  const delta = frame.mentions_this_week - frame.mentions_last_week
  const [hovered, setHovered] = useState(false)

  return (
    <div
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        background: hovered ? C.bg3 : C.bg2,
        border: `1px solid ${C.border}`,
        borderRadius: '0.625rem',
        padding: '12px 14px',
        cursor: 'pointer',
        transition: 'background 0.12s ease',
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
    </div>
  )
}

function DetailPanel({ frame }: { frame: NarrativeFrame }) {
  const [timeseries, setTimeseries] = useState<TimeseriesPoint[]>([])
  const oc = ownerColor(frame.owner_type)

  useEffect(() => {
    api.frameTimeseries(frame.id).then(setTimeseries).catch(() => {})
  }, [frame.id])

  const articleDelta = frame.mentions_this_week - frame.mentions_last_week
  const outletDelta = frame.unique_outlets_this_week - frame.unique_outlets_last_week
  const reachDelta = frame.reach_this_week - frame.reach_last_week
  const reachFmt = (v: number) => v > 0 ? `${(v / 1000).toFixed(1)}K` : '—'

  const rows = [
    { label: 'Articles', total: frame.mentions_total, wk: frame.mentions_this_week, delta: articleDelta },
    { label: 'Outlets', total: frame.unique_outlets_this_week, wk: frame.unique_outlets_this_week, delta: outletDelta },
    { label: 'Reach', total: reachFmt(frame.reach_total), wk: reachFmt(frame.reach_this_week), delta: reachDelta },
  ]

  return (
    <div style={{
      background: C.bg2, border: `1px solid ${C.border}`,
      borderRadius: '0.625rem', padding: 16, overflow: 'hidden',
    } as CSSProperties}>
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

      <div style={{ display: 'flex', gap: 6, marginBottom: 14 }}>
        <span style={{
          background: C.bg3, border: `1px solid ${C.border}`,
          borderRadius: 4, padding: '3px 8px', fontSize: 11, color: C.text2,
        }}>
          {stageLabel(frame.stage)}
        </span>
        <span style={{
          background: C.bg3, border: `1px solid ${C.border}`,
          borderRadius: 4, padding: '3px 8px', fontSize: 11, color: oc, fontWeight: 600,
        }}>
          {frame.owner_type.charAt(0).toUpperCase() + frame.owner_type.slice(1)}
        </span>
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
    </div>
  )
}

function ArticleRow({ item }: { item: SourceItem }) {
  const score = item.race_relevance_score ?? 0
  const scoreColor = score >= 80 ? C.accent : score >= 50 ? C.text2 : C.text3
  const href = item.source_url

  const row = (
    <div style={{
      display: 'flex', alignItems: 'flex-start', gap: 9,
      padding: '10px 0', borderBottom: `1px solid ${C.bg3}`,
    }}>
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
          {formatDate(item.published_at ?? item.created_at)}
        </div>
      </div>
    </div>
  )
  return href ? (
    <a href={href} target="_blank" rel="noreferrer" style={{ textDecoration: 'none', color: 'inherit' }}>
      {row}
    </a>
  ) : row
}

type FilterKey = 'all' | OwnerType | 'mainstream' | 'spreading' | 'emerging' | 'fading' | 'dormant'

interface FilterItemProps {
  label: string
  filterKey: FilterKey
  count: number
  active: boolean
  onClick: () => void
}

function FilterItem({ label, filterKey: _filterKey, count, active, onClick }: FilterItemProps) {
  const [hovered, setHovered] = useState(false)
  return (
    <button
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        display: 'flex', alignItems: 'center', width: '100%',
        padding: '9px 16px 9px 20px',
        background: active || hovered ? '#1a1a1a' : 'transparent',
        border: 'none',
        borderLeft: `3px solid ${active ? C.accent : 'transparent'}`,
        cursor: 'pointer', textAlign: 'left',
        transition: 'all 0.1s ease',
      } as CSSProperties}
    >
      <span style={{ flex: 1, fontSize: 14, color: active ? C.text1 : C.text2, fontWeight: active ? 600 : 400 }}>
        {label}
      </span>
      {count > 0 && (
        <span style={{ fontSize: 12, color: C.text3, marginRight: 6 }}>{count}</span>
      )}
      <ChevronRight size={14} style={{ color: C.text3, flexShrink: 0 }} />
    </button>
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
    .sort((a, b) => {
      const sa = STAGE_ORDER.indexOf(a.stage), sb = STAGE_ORDER.indexOf(b.stage)
      if (sa !== sb) return sa - sb
      return b.mentions_total - a.mentions_total
    })

  const featuredFrames = filteredFrames.slice(0, 8)
  const topFrames = filteredFrames
    .filter(f => f.stage === 'mainstream' || f.stage === 'spreading')
    .slice(0, 2)

  const counts = {
    all: frames.length,
    candidate: frames.filter(f => f.owner_type === 'candidate').length,
    opponent: frames.filter(f => f.owner_type === 'opponent').length,
    media: frames.filter(f => f.owner_type === 'media').length,
    mainstream: frames.filter(f => f.stage === 'mainstream').length,
    spreading: frames.filter(f => f.stage === 'spreading').length,
    emerging: frames.filter(f => f.stage === 'emerging').length,
    fading: frames.filter(f => f.stage === 'fading').length,
    dormant: frames.filter(f => f.stage === 'dormant').length,
  }

  return (
    <div style={{ background: C.bg1, minHeight: '100%' }}>
      {/* Three-column layout */}
      <div style={{ display: 'grid', gridTemplateColumns: '220px 1fr 280px', minHeight: '100%' }}>

        {/* ── Left: Filter list ── */}
        <div style={{
          borderRight: `1px solid ${C.border}`,
          position: 'sticky', top: 0, alignSelf: 'start',
          paddingBottom: 24,
        }}>
          <FilterItem label="All Narratives" filterKey="all" count={counts.all} active={activeFilter === 'all'} onClick={() => setActiveFilter('all')} />

          <div style={{ padding: '12px 20px 4px', fontSize: 10, color: C.text3, letterSpacing: '0.1em', fontWeight: 600 }}>
            BY OWNER
          </div>
          <FilterItem label="Candidate" filterKey="candidate" count={counts.candidate} active={activeFilter === 'candidate'} onClick={() => setActiveFilter('candidate')} />
          <FilterItem label="Opponent" filterKey="opponent" count={counts.opponent} active={activeFilter === 'opponent'} onClick={() => setActiveFilter('opponent')} />
          <FilterItem label="Media" filterKey="media" count={counts.media} active={activeFilter === 'media'} onClick={() => setActiveFilter('media')} />

          <div style={{ padding: '12px 20px 4px', fontSize: 10, color: C.text3, letterSpacing: '0.1em', fontWeight: 600 }}>
            BY STAGE
          </div>
          <FilterItem label="Mainstream" filterKey="mainstream" count={counts.mainstream} active={activeFilter === 'mainstream'} onClick={() => setActiveFilter('mainstream')} />
          <FilterItem label="Spreading" filterKey="spreading" count={counts.spreading} active={activeFilter === 'spreading'} onClick={() => setActiveFilter('spreading')} />
          <FilterItem label="Emerging" filterKey="emerging" count={counts.emerging} active={activeFilter === 'emerging'} onClick={() => setActiveFilter('emerging')} />
          <FilterItem label="Fading" filterKey="fading" count={counts.fading} active={activeFilter === 'fading'} onClick={() => setActiveFilter('fading')} />
          <FilterItem label="Dormant" filterKey="dormant" count={counts.dormant} active={activeFilter === 'dormant'} onClick={() => setActiveFilter('dormant')} />
        </div>

        {/* ── Center: Featured cards + detail panels ── */}
        <div style={{ padding: '16px 24px', borderRight: `1px solid ${C.border}` }}>
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
              {/* Featured narrative cards */}
              {featuredFrames.length > 0 && (
                <div style={{ marginBottom: 28 }}>
                  <div style={{ fontSize: 11, color: C.text3, letterSpacing: '0.12em', marginBottom: 12, fontWeight: 600, textTransform: 'uppercase' }}>
                    Featured Narratives
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
                }}>
                  24h Spikes {spikes.length > 0 ? `(${spikes.length})` : ''}
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
            <span style={{ fontSize: 12, fontWeight: 700, color: C.text1, letterSpacing: '0.08em' }}>
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
