import { useEffect, useState, useCallback } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { api } from '../api/client'

const SB = {
  bg:       '#000',
  border:   '#2a2a2a',
  text:     '#fff',
  textSec:  '#a1a1a1',
  textMuted:'#555',
  textXm:   '#333',
  activeBg: '#1a1a1a',
  hoverBg:  '#111',
}

const NAV_ITEMS = [
  { to: '/',           label: 'Dashboard',        end: true,  badge: false },
  { to: '/narratives', label: 'Narratives',       end: false, badge: false },
  { to: '/briefing',   label: 'Briefing',         end: false, badge: false },
  { to: '/review',     label: 'Review Queue',     end: false, badge: true  },
  { to: '/opponents',  label: 'Opponent Tracker', end: false, badge: false },
  { to: '/monitors',   label: 'Source Monitors',  end: false, badge: false },
  { to: '/campaign',   label: 'Campaign Setup',   end: false, badge: false },
]

function timeAgo(iso: string | null): string {
  if (!iso) return ''
  const diff = Date.now() - new Date(iso).getTime()
  const m = Math.floor(diff / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

type StepState = 'pending' | 'running' | 'done'

interface PipelineStep {
  key: string
  label: string
  detail: string
  state: StepState
  progress?: { done: number; total: number }
}

interface PipelineStatus {
  active: boolean
  steps: PipelineStep[]
}

function fmtEta(done: number, total: number, startedAt: string | null): string {
  if (!startedAt || done === 0) return ''
  const elapsed = (Date.now() - new Date(startedAt + 'Z').getTime()) / 1000
  const remaining = Math.ceil((total - done) * (elapsed / done) / 60)
  return ` — ~${remaining} min left`
}

function usePipelineStatus(): PipelineStatus {
  const [status, setStatus] = useState<PipelineStatus>({ active: false, steps: [] })

  useEffect(() => {
    let cancelled = false

    async function poll() {
      try {
        const p = await api.getPipelineStatus()
        const { backfill: bf, feed_discovery: fd, rescore: rs, rematch: rm } = p

        const anyRunning = bf.running || fd.running || rs.running || rm.running
        // Show checklist while any step is running or while pipeline is mid-stream
        // (backfill done but downstream steps haven't finished yet)
        const midStream = bf.done && (!rs.done || !rm.done)
        const active = anyRunning || midStream

        const bfDetail = bf.running
          ? bf.progress_total > 0
            ? `${bf.progress_done} of ${bf.progress_total} articles — ${bf.progress_added} saved${fmtEta(bf.progress_done, bf.progress_total, null)}`
            : 'Querying GDELT…'
          : bf.done ? `${bf.progress_added} articles saved` : ''

        const fdDetail = fd.running
          ? fd.probed > 0 ? `probing outlets — ${fd.created} feeds found so far` : 'scanning GDELT domains…'
          : fd.done ? `${fd.created} new feeds created` : ''

        const rsDetail = rs.running
          ? rs.total > 0
            ? `${rs.processed} of ${rs.total} articles${fmtEta(rs.processed, rs.total, rs.started_at)}`
            : 'starting…'
          : rs.done ? `${rs.processed} articles scored` : ''

        const rmDetail = rm.running
          ? rm.total > 0 ? `${rm.done_count} of ${rm.total} clusters` : 'starting…'
          : rm.done ? `${rm.done_count} clusters matched` : ''

        const steps: PipelineStep[] = [
          {
            key: 'backfill',
            label: 'Load historical articles',
            detail: bfDetail,
            state: bf.running ? 'running' : bf.done ? 'done' : 'pending',
            progress: bf.running && bf.progress_total > 0
              ? { done: bf.progress_done, total: bf.progress_total } : undefined,
          },
          {
            key: 'feeds',
            label: 'Discover new RSS sources',
            detail: fdDetail,
            state: fd.running ? 'running' : fd.done ? 'done' : 'pending',
          },
          {
            key: 'rescore',
            label: 'Score articles with AI',
            detail: rsDetail,
            state: rs.running ? 'running' : rs.done ? 'done' : 'pending',
            progress: rs.running && rs.total > 0
              ? { done: rs.processed, total: rs.total } : undefined,
          },
          {
            key: 'rematch',
            label: 'Match articles to narrative frames',
            detail: rmDetail,
            state: rm.running ? 'running' : rm.done ? 'done' : 'pending',
            progress: rm.running && rm.total > 0
              ? { done: rm.done_count, total: rm.total } : undefined,
          },
        ]

        if (!cancelled) setStatus({ active, steps })
      } catch { /* ignore */ }
    }

    poll()
    const t = setInterval(poll, 8000)
    return () => { cancelled = true; clearInterval(t) }
  }, [])

  return status
}

export default function Layout({ children }: { children: React.ReactNode }) {
  const [candidateName, setCandidateName] = useState('')
  const [reviewCount, setReviewCount] = useState(0)
  const [lastSynced, setLastSynced] = useState<string | null>(null)
  const [syncLabel, setSyncLabel] = useState('')
  const [profileHover, setProfileHover] = useState(false)
  const [mockActive, setMockActive] = useState(false)
  const pipeline = usePipelineStatus()
  const navigate = useNavigate()

  const refreshSyncLabel = useCallback((iso: string | null) => {
    setSyncLabel(iso ? `Synced ${timeAgo(iso)}` : 'Not yet synced')
  }, [])

  useEffect(() => {
    api.getCampaign()
      .then(p => setCandidateName(p.candidate_name || ''))
      .catch(() => {})
    api.getReviewQueueCount()
      .then(setReviewCount)
      .catch(() => {})
    api.getLastSynced()
      .then(iso => { setLastSynced(iso); refreshSyncLabel(iso) })
      .catch(() => {})
    api.getLLMStatus()
      .then(s => setMockActive(s.is_mock))
      .catch(() => {})

    // Auto-trigger crawler + Reddit on app open if they haven't run in 30 min.
    // Runs silently in the background — no UI feedback needed here.
    const THIRTY_MIN = 30 * 60 * 1000
    api.getIngestStatus().then(status => {
      const stale = (iso: string | null) =>
        !iso || (Date.now() - new Date(iso).getTime()) > THIRTY_MIN
      if (stale(status.last_crawl_at))  api.triggerCrawl().catch(() => {})
      if (stale(status.last_reddit_at)) api.triggerReddit().catch(() => {})
    }).catch(() => {})
  }, [refreshSyncLabel])

  // Every 30s, re-fetch the last-synced timestamp (a poll from the backend) so
  // the indicator reflects new ingests too — not just a stale relative-time
  // label computed from the original load.
  useEffect(() => {
    const t = setInterval(() => {
      api.getLastSynced()
        .then(iso => { setLastSynced(iso); refreshSyncLabel(iso) })
        .catch(() => refreshSyncLabel(lastSynced))
    }, 30000)
    return () => clearInterval(t)
  }, [lastSynced, refreshSyncLabel])

  const location = useLocation()
  const currentPage = NAV_ITEMS.find(n => n.to === location.pathname)?.label ?? ''

  const initials = candidateName
    ? candidateName.split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase()
    : 'WR'

  return (
    <div style={{ display: 'flex', height: '100vh', background: 'var(--bg)', overflow: 'hidden' }}>

      {/* Sidebar */}
      <aside style={{
        width: 192, flexShrink: 0,
        background: SB.bg,
        borderRight: `1px solid ${SB.border}`,
        display: 'flex', flexDirection: 'column',
      }}>

        {/* Brand / Profile — click to open Campaign Setup */}
        <button
          onClick={() => navigate('/campaign')}
          onMouseEnter={() => setProfileHover(true)}
          onMouseLeave={() => setProfileHover(false)}
          style={{
            all: 'unset', display: 'block', cursor: 'pointer',
            padding: '1rem', borderBottom: `1px solid ${SB.border}`,
            background: profileHover ? SB.hoverBg : 'transparent',
            transition: 'background 0.1s',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <div style={{
              width: 32, height: 32, borderRadius: 8, flexShrink: 0,
              background: 'linear-gradient(135deg, #0059c2 0%, #1a4faa 100%)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: 13, fontWeight: 700, color: '#fff',
            }}>{initials}</div>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div style={{
                fontWeight: 700, fontSize: '0.75rem', color: SB.text,
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>
                {candidateName || 'Campaign'}
              </div>
              <div style={{ fontSize: '0.6rem', color: SB.textMuted, marginTop: 1, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                {profileHover ? 'Campaign Setup ›' : 'Campaign Intelligence'}
              </div>
            </div>
          </div>
        </button>

        {/* Nav */}
        <nav style={{ flex: 1, padding: '0.5rem 0.375rem', overflowY: 'auto' }}>
          {NAV_ITEMS.map(({ to, label, end, badge }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              style={({ isActive }) => ({
                display: 'flex', alignItems: 'center', gap: 6,
                padding: '0.42rem 0.625rem',
                borderRadius: 6,
                fontSize: '0.82rem',
                fontWeight: isActive ? 600 : 400,
                color: isActive ? SB.text : SB.textSec,
                background: isActive ? SB.activeBg : 'transparent',
                textDecoration: 'none',
                marginBottom: 2,
                transition: 'all 0.1s',
              })}
              onMouseEnter={e => {
                const el = e.currentTarget
                if (!el.getAttribute('aria-current')) {
                  el.style.background = SB.hoverBg
                  el.style.color = SB.text
                }
              }}
              onMouseLeave={e => {
                const el = e.currentTarget
                if (!el.getAttribute('aria-current')) {
                  el.style.background = 'transparent'
                  el.style.color = SB.textSec
                }
              }}
            >
              {({ isActive }) => (
                <>
                  {isActive && (
                    <span style={{ width: 3, height: 14, borderRadius: 99, background: '#ffbf00', flexShrink: 0 }} />
                  )}
                  <span style={{ flex: 1 }}>{label}</span>
                  {badge && reviewCount > 0 && (
                    <span style={{
                      background: '#ffbf00', color: '#000',
                      fontSize: '0.6rem', fontWeight: 700,
                      padding: '1px 6px', borderRadius: 99,
                    }}>{reviewCount}</span>
                  )}
                </>
              )}
            </NavLink>
          ))}
        </nav>

        {/* Sync status */}
        <div style={{
          padding: '10px 14px',
          borderTop: `1px solid ${SB.border}`,
          fontSize: 11,
          color: SB.textMuted,
          display: 'flex', alignItems: 'center', gap: 6,
        }}>
          <span style={{
            width: 6, height: 6, borderRadius: '50%',
            background: lastSynced ? '#22c55e' : '#475569',
            flexShrink: 0,
          }} />
          {syncLabel || 'Checking…'}
        </div>

      </aside>

      {/* Main */}
      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>

{mockActive && (
          <div style={{
            background: '#7f1d1d', color: '#fef2f2',
            padding: '8px 24px', fontSize: 12, fontWeight: 600,
            borderBottom: '1px solid #991b1b', flexShrink: 0,
          }}>
            ⚠ AI scoring is OFF — no LLM key configured. Articles will be marked irrelevant.
            Set <code style={{ background: '#991b1b', padding: '1px 5px', borderRadius: 3 }}>LLM_PROVIDER=groq</code> and
            <code style={{ background: '#991b1b', padding: '1px 5px', borderRadius: 3, marginLeft: 4 }}>GROQ_API_KEY</code> in <code style={{ background: '#991b1b', padding: '1px 5px', borderRadius: 3 }}>backend/.env</code> and restart the backend.
          </div>
        )}

        {pipeline.active && (
          <div style={{
            background: '#1a1500', color: '#ffd44d',
            borderBottom: '1px solid rgba(255,191,0,0.2)',
            flexShrink: 0,
            padding: '10px 24px',
          }}>
            {/* Header row */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
              <span style={{
                width: 7, height: 7, borderRadius: '50%',
                background: '#ffbf00', flexShrink: 0,
                animation: 'pulse 1.5s ease-in-out infinite',
              }} />
              <span style={{ fontSize: 11, fontWeight: 700, color: '#ffd44d', letterSpacing: '0.08em', textTransform: 'uppercase' }}>
                Initializing campaign data
              </span>
              <span style={{ fontSize: 11, color: 'rgba(255,191,0,0.4)', marginLeft: 'auto' }}>
                Data shown may be incomplete
              </span>
            </div>
            {/* Checklist */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
              {pipeline.steps.map(step => {
                const isDone    = step.state === 'done'
                const isRunning = step.state === 'running'
                const isPending = step.state === 'pending'
                return (
                  <div key={step.key} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    {/* State icon */}
                    <span style={{
                      width: 14, flexShrink: 0, fontSize: 11, fontWeight: 700,
                      color: isDone ? '#22c55e' : isRunning ? '#ffbf00' : '#3f3f3f',
                    }}>
                      {isDone ? '✓' : isRunning ? '●' : '○'}
                    </span>
                    {/* Label */}
                    <span style={{
                      fontSize: 12, fontWeight: isRunning ? 600 : 400,
                      color: isDone ? '#22c55e' : isRunning ? '#ffd44d' : '#555',
                      minWidth: 220,
                    }}>
                      {step.label}
                    </span>
                    {/* Detail */}
                    {step.detail && (
                      <span style={{ fontSize: 11, color: isDone ? '#22c55e88' : '#ffbf0099' }}>
                        {step.detail}
                      </span>
                    )}
                    {/* Progress bar */}
                    {isRunning && step.progress && (
                      <div style={{ width: 120, height: 3, background: '#3f3f3f', borderRadius: 99, overflow: 'hidden' }}>
                        <div style={{
                          height: '100%',
                          width: `${Math.round((step.progress.done / step.progress.total) * 100)}%`,
                          background: '#ffbf00', borderRadius: 99,
                          transition: 'width 0.5s ease',
                        }} />
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
        )}

        <main style={{ flex: 1, minHeight: 0, overflowY: 'auto', overflowX: 'hidden' }}>
          {children}
        </main>
      </div>
    </div>
  )
}
