import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { NarrativeFrameWithCounts, MorningBriefing } from '../api/types'

const STAGE_COLORS: Record<string, string> = {
  emerging:   '#4a90d9',
  spreading:  '#0ea5e9',
  mainstream: '#a1a1a1',
  active:     '#6b6b6b',
  fading:     '#d97706',
  dormant:    '#3f3f3f',
}
const STAGE_LABELS: Record<string, string> = {
  emerging: 'Emerging', spreading: 'Spreading', mainstream: 'Mainstream',
  active: 'Active', fading: 'Fading', dormant: 'Dormant',
}
const TREND_ICON: Record<string, string>  = { up: '↑', down: '↓', flat: '→' }
const TREND_COLOR: Record<string, string> = { up: '#22c55e', down: '#d71913', flat: '#6b6b6b' }

function fmtDate(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleDateString('en-US', { month: 'numeric', day: 'numeric', year: 'numeric' })
}

function PartyCircle({ ownerType }: { ownerType: string }) {
  const cls = ownerType === 'candidate' ? 'party-circle party-circle-dem'
    : ownerType === 'opponent' ? 'party-circle party-circle-rep'
    : 'party-circle party-circle-med'
  const letter = ownerType === 'candidate' ? 'C' : ownerType === 'opponent' ? 'O' : 'M'
  return <div className={cls}>{letter}</div>
}

function NarrativeRaceRow({ frame }: { frame: NarrativeFrameWithCounts }) {
  const stageColor = STAGE_COLORS[frame.stage] || '#6b6b6b'
  const trendIcon  = TREND_ICON[frame.trend]  || '→'
  const trendColor = TREND_COLOR[frame.trend] || '#6b6b6b'

  return (
    <Link to={`/frames/${frame.id}`} className="race-row">
      <PartyCircle ownerType={frame.owner_type} />
      <span style={{ flex: 1, fontSize: 13, fontWeight: 600, color: '#fff', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {frame.name}
      </span>
      <span className="stage-chip" style={{ background: stageColor + '22', color: stageColor, border: `1px solid ${stageColor}44` }}>
        {STAGE_LABELS[frame.stage] || frame.stage}
      </span>
      <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 12, fontWeight: 700, color: '#a1a1a1', flexShrink: 0 }}>
        +{frame.mentions_this_week}
      </span>
      <span style={{ fontSize: 12, fontWeight: 700, color: trendColor, flexShrink: 0 }}>{trendIcon}</span>
      <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 11, color: '#555', flexShrink: 0, minWidth: 70, textAlign: 'right' }}>
        {fmtDate(frame.last_seen_at)}
      </span>
    </Link>
  )
}

function FrameGroup({ title, frames }: { title: string; frames: NarrativeFrameWithCounts[] }) {
  if (frames.length === 0) return null
  return (
    <div style={{ marginBottom: 24 }}>
      <div className="section-title" style={{ marginBottom: 10 }}>{title}</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        {frames.map(f => <NarrativeRaceRow key={f.id} frame={f} />)}
      </div>
    </div>
  )
}

export default function Dashboard() {
  const [frames, setFrames] = useState<NarrativeFrameWithCounts[]>([])
  const [briefing, setBriefing] = useState<MorningBriefing | null>(null)
  const [reviewCount, setReviewCount] = useState(0)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    Promise.allSettled([
      api.getNarrativeFrames().then(setFrames),
      api.getMorningBriefing().then(setBriefing),
      api.getReviewQueueCount().then(setReviewCount),
    ]).finally(() => setLoading(false))
  }, [])

  const candidateFrames = frames.filter(f => f.owner_type === 'candidate')
    .sort((a, b) => b.mentions_this_week - a.mentions_this_week)
  const opponentFrames  = frames.filter(f => f.owner_type === 'opponent')
    .sort((a, b) => b.mentions_this_week - a.mentions_this_week)

  const spikeAlerts = briefing?.spike_alerts ?? []
  const needsResponse = briefing?.needs_response ?? []

  return (
    <div style={{ display: 'flex', gap: 32, padding: '32px 40px 64px', maxWidth: 1200 }}>

      {/* Main column */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ marginBottom: 28 }}>
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, letterSpacing: '-0.02em', color: '#fff', lineHeight: 1.2 }}>
            Dashboard
          </h1>
          <div style={{ fontSize: 12, color: '#6b6b6b', marginTop: 4 }}>
            {frames.length} narrative frames tracked
          </div>
        </div>

        {loading && <div style={{ color: '#6b6b6b', fontSize: 13 }}>Loading…</div>}

        {!loading && frames.length === 0 && (
          <div style={{ color: '#6b6b6b', fontSize: 13, padding: '32px 0' }}>
            No narrative frames yet.{' '}
            <Link to="/narratives" style={{ color: '#ffbf00' }}>Go to Narratives</Link> to add frames.
          </div>
        )}

        {frames.length > 0 && (
          <>
            <FrameGroup title="Campaign Narratives" frames={candidateFrames} />
            <FrameGroup title="Opposition Narratives" frames={opponentFrames} />
          </>
        )}
      </div>

      {/* Right panel */}
      <div style={{ width: 280, flexShrink: 0 }}>

        {/* Review queue CTA */}
        <Link to="/review" style={{ display: 'block', textDecoration: 'none', marginBottom: 20 }}>
          <div style={{
            background: '#171717', border: '1px solid #434343', borderRadius: 6,
            padding: '14px 16px',
            transition: 'border-color 0.1s',
          }}
            onMouseEnter={e => { e.currentTarget.style.borderColor = '#ffbf00' }}
            onMouseLeave={e => { e.currentTarget.style.borderColor = '#434343' }}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <span style={{ fontSize: 12, fontWeight: 600, color: '#a1a1a1', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
                Review Queue
              </span>
              {reviewCount > 0 && (
                <span style={{
                  background: '#ffbf00', color: '#000',
                  fontSize: 11, fontWeight: 700,
                  padding: '2px 8px', borderRadius: 99,
                }}>{reviewCount}</span>
              )}
            </div>
            <div style={{ fontSize: 12, color: '#555', marginTop: 4 }}>
              {reviewCount > 0 ? `${reviewCount} article${reviewCount !== 1 ? 's' : ''} need attention` : 'Queue is clear'}
            </div>
          </div>
        </Link>

        {/* Spike alerts */}
        {spikeAlerts.length > 0 && (
          <div style={{ marginBottom: 20 }}>
            <div className="section-title" style={{ marginBottom: 10 }}>Spike Alerts</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {spikeAlerts.map(alert => (
                <Link key={alert.frame_id} to={`/frames/${alert.frame_id}`} style={{ textDecoration: 'none' }}>
                  <div style={{
                    background: '#171717',
                    border: '1px solid #434343',
                    borderLeft: '3px solid #ffbf00',
                    borderRadius: 6,
                    padding: '9px 12px',
                    transition: 'background 0.1s',
                  }}
                    onMouseEnter={e => { e.currentTarget.style.background = '#262626' }}
                    onMouseLeave={e => { e.currentTarget.style.background = '#171717' }}
                  >
                    <div style={{ fontSize: 12, fontWeight: 600, color: '#fff', marginBottom: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                      {alert.frame_name}
                    </div>
                    <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10, color: '#ffbf00' }}>
                      {alert.count_24h}× in 24h · {alert.ratio.toFixed(1)}× avg
                    </div>
                  </div>
                </Link>
              ))}
            </div>
          </div>
        )}

        {/* Needs response */}
        {needsResponse.length > 0 && (
          <div>
            <div className="section-title" style={{ marginBottom: 10 }}>Needs Response</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {needsResponse.slice(0, 5).map(article => (
                <a key={article.id} href={article.source_url ?? '#'} target="_blank" rel="noopener noreferrer"
                  style={{ textDecoration: 'none' }}>
                  <div style={{
                    background: '#171717',
                    border: '1px solid #434343',
                    borderLeft: '3px solid #d71913',
                    borderRadius: 6,
                    padding: '9px 12px',
                    transition: 'background 0.1s',
                  }}
                    onMouseEnter={e => { e.currentTarget.style.background = '#262626' }}
                    onMouseLeave={e => { e.currentTarget.style.background = '#171717' }}
                  >
                    <div style={{ fontSize: 12, fontWeight: 500, color: '#fff', lineHeight: 1.35, display: '-webkit-box', WebkitBoxOrient: 'vertical', WebkitLineClamp: 2, overflow: 'hidden' }}>
                      {article.title ?? 'Untitled'}
                    </div>
                    <div style={{ fontSize: 11, color: '#555', marginTop: 3 }}>
                      {article.source_name ?? 'Unknown source'}
                    </div>
                  </div>
                </a>
              ))}
            </div>
          </div>
        )}

      </div>
    </div>
  )
}
