import { Eye } from 'lucide-react'
import { useEffect, useState } from 'react'
import { NavLink } from 'react-router-dom'
import { api } from '@/api/client'
import { prefetchDashboard } from '@/api/dashboardCache'

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

function fmtEta(done: number, total: number, startedAt?: string | null): string {
  if (done === 0 || !startedAt) return ''
  // Backend sends naive UTC timestamps (no 'Z'); without this JS parses them as local time.
  const iso = /[zZ]|[+-]\d\d:?\d\d$/.test(startedAt) ? startedAt : startedAt + 'Z'
  const elapsedMin = (Date.now() - new Date(iso).getTime()) / 60000
  if (elapsedMin < 0.5) return ''
  const rate = done / elapsedMin
  if (rate === 0) return ''
  const remainingMin = (total - done) / rate
  if (remainingMin < 1) return ' — almost done'
  if (remainingMin < 60) return ` — ~${Math.ceil(remainingMin)} min left`
  const h = Math.floor(remainingMin / 60)
  const m = Math.ceil(remainingMin % 60)
  return ` — ~${h}h ${m}m left`
}

function usePipelineStatus(): PipelineStatus {
  const [status, setStatus] = useState<PipelineStatus>({ active: false, steps: [] })

  useEffect(() => {
    let cancelled = false

    async function poll() {
      try {
        const p = await api.getPipelineStatus() as Record<string, {
          running: boolean; done: boolean;
          progress_done?: number; progress_total?: number; progress_added?: number;
          probed?: number; created?: number;
          total?: number; processed?: number; started_at?: string | null;
          done_count?: number; fallbacks?: number;
        }>
        const bf = p.backfill ?? { running: false, done: false }
        const fd = p.feed_discovery ?? { running: false, done: false }
        const rs = p.rescore ?? { running: false, done: false }
        const rm = p.rematch ?? { running: false, done: false }

        const anyRunning = bf.running || fd.running || rs.running || rm.running
        const midStream = bf.done && (!rs.done || !rm.done)
        const active = anyRunning || midStream

        const bfDetail = bf.running
          ? (bf.progress_total ?? 0) > 0
            ? `${bf.progress_done} of ${bf.progress_total} articles — ${bf.progress_added} saved${fmtEta(bf.progress_done ?? 0, bf.progress_total ?? 1, bf.started_at)}`
            : 'Querying GDELT…'
          : bf.done ? `${bf.progress_added} articles saved` : ''

        const fdDetail = fd.running
          ? (fd.probed ?? 0) > 0 ? `probing outlets — ${fd.created} feeds found so far` : 'scanning GDELT domains…'
          : fd.done ? `${fd.created} new feeds created` : ''

        const fallbacks = (rs as Record<string, unknown>).fallbacks as number ?? 0
        const rsDetail = rs.running
          ? (rs.total ?? 0) > 0
            ? `${rs.processed} of ${rs.total} scored${fallbacks > 0 ? ` — ⚠ ${fallbacks} skipped (LLM rate limit)` : ''}${fmtEta(rs.processed ?? 0, rs.total ?? 1, rs.started_at)}`
            : 'starting…'
          : rs.done ? `${rs.processed} articles scored` : ''

        const rmDetail = rm.running
          ? (rm.total ?? 0) > 0 ? `${rm.done_count} of ${rm.total} clusters` : 'starting…'
          : rm.done ? `${rm.done_count} clusters matched` : ''

        const steps: PipelineStep[] = [
          {
            key: 'backfill', label: 'Load historical articles', detail: bfDetail,
            state: bf.running ? 'running' : bf.done ? 'done' : 'pending',
            progress: bf.running && (bf.progress_total ?? 0) > 0
              ? { done: bf.progress_done ?? 0, total: bf.progress_total ?? 1 } : undefined,
          },
          {
            key: 'feeds', label: 'Discover new RSS sources', detail: fdDetail,
            state: fd.running ? 'running' : fd.done ? 'done' : 'pending',
          },
          {
            key: 'rescore', label: 'Score articles with AI', detail: rsDetail,
            state: rs.running ? 'running' : rs.done ? 'done' : 'pending',
            progress: rs.running && (rs.total ?? 0) > 0
              ? { done: rs.processed ?? 0, total: rs.total ?? 1 } : undefined,
          },
          {
            key: 'rematch', label: 'Match articles to narrative frames', detail: rmDetail,
            state: rm.running ? 'running' : rm.done ? 'done' : 'pending',
            progress: rm.running && (rm.total ?? 0) > 0
              ? { done: rm.done_count ?? 0, total: rm.total ?? 1 } : undefined,
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

const NAV = [
  { to: '/', label: 'Home', exact: true },
  { to: '/briefing', label: 'Briefing' },
  { to: '/analytics', label: 'Analytics' },
  { to: '/narratives', label: 'Narratives' },
  { to: '/opponents', label: 'Opponents' },
  { to: '/monitors', label: 'Monitors' },
  { to: '/setup', label: 'Setup' },
]

function formatAgo(iso: string) {
  const diff = Date.now() - new Date(iso).getTime()
  const m = Math.floor(diff / 60000)
  if (m < 1) return 'just now'
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

export function Layout({ children }: { children: React.ReactNode }) {
  const [queueCount, setQueueCount] = useState(0)
  const [lastSync, setLastSync] = useState<string | null>(null)
  const [district, setDistrict] = useState('PA-08')
  const [mockActive, setMockActive] = useState(false)
  const pipeline = usePipelineStatus()

  useEffect(() => {
    api.reviewQueueCount().then(d => setQueueCount(d.count)).catch(() => {})
    api.ingestStatus().then(d => {
      if (d.crawler_last_run) setLastSync(d.crawler_last_run)
    }).catch(() => {})
    api.campaign().then(d => {
      if (d.district) setDistrict(d.district)
    }).catch(() => {})
    api.getLLMStatus().then(s => setMockActive(s.is_mock)).catch(() => {})
    // Warm the Dashboard cache so opening Home from any page is instant.
    prefetchDashboard()
    // Re-warm every 60s so cached data is fresh when the user navigates.
    const id = setInterval(() => { prefetchDashboard() }, 60_000)
    return () => clearInterval(id)
  }, [])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', background: '#121212' }}>
      {/* Top nav bar */}
      <nav style={{
        height: 76,
        flexShrink: 0,
        background: '#000',
        borderBottom: '1px solid #262626',
        display: 'flex',
        alignItems: 'center',
        padding: '0 28px',
        gap: 0,
        zIndex: 100,
        position: 'relative',
      }}>
        {/* Logo */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          marginRight: 28,
          textDecoration: 'none',
          flexShrink: 0,
        }}>
          <Eye size={26} strokeWidth={2.25} style={{ color: '#a78bfa' }} />
          <span style={{ fontSize: 22, fontWeight: 800, color: '#fff', letterSpacing: '-0.01em' }}>
            campaign
          </span>
          <span style={{
            fontSize: 12,
            color: '#fff',
            background: '#ffbf00',
            borderRadius: 4,
            padding: '2px 7px',
            fontWeight: 700,
            marginLeft: 4,
          }}>
            {district}
          </span>
        </div>

        {/* Nav links — absolutely centered to the viewport, independent of
            the logo / right-side widths. */}
        <div style={{
          position: 'absolute',
          left: '50%',
          top: '50%',
          transform: 'translate(-50%, -50%)',
          display: 'flex',
          alignItems: 'center',
          gap: 2,
        }}>
          {NAV.map(item => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.exact}
              style={({ isActive }) => ({
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                padding: '8px 16px',
                borderRadius: 8,
                textDecoration: 'none',
                fontSize: 15,
                fontWeight: isActive ? 600 : 400,
                color: isActive ? '#fff' : '#a1a1a1',
                background: isActive ? '#1a1a1a' : 'transparent',
                transition: 'all 0.1s ease',
                whiteSpace: 'nowrap',
                position: 'relative',
              })}
            >
              {({ isActive }) => (
                <>
                  {item.label}
                  {isActive && (
                    <span style={{
                      position: 'absolute',
                      bottom: -1,
                      left: '50%',
                      transform: 'translateX(-50%)',
                      width: '60%',
                      height: 2,
                      background: '#ffbf00',
                      borderRadius: 1,
                    }} />
                  )}
                </>
              )}
            </NavLink>
          ))}
        </div>

        {/* Right cluster — sync time + user avatar. marginLeft auto pushes
            it to the far right; nav links sit absolute-centered behind it. */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: 14,
          marginLeft: 'auto',
          flexShrink: 0,
        }}>
          {lastSync && (
            <div style={{ fontSize: 12, color: '#555' }}>
              synced {formatAgo(lastSync)}
            </div>
          )}
          <button
            type="button"
            title="theocapeilleres@gmail.com"
            style={{
              width: 40,
              height: 40,
              borderRadius: '50%',
              background: '#7c3aed',
              color: '#fff',
              border: 'none',
              cursor: 'pointer',
              fontWeight: 700,
              fontSize: 14,
              letterSpacing: '0.02em',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontFamily: "'Inter', sans-serif",
            }}
          >
            TC
          </button>
        </div>
      </nav>

      {/* LLM mock warning */}
      {mockActive && (
        <div style={{
          background: '#7f1d1d', color: '#fef2f2',
          padding: '8px 24px', fontSize: 12, fontWeight: 600,
          borderBottom: '1px solid #991b1b', flexShrink: 0,
        }}>
          ⚠ AI scoring is OFF — no LLM key configured. Articles will be marked irrelevant.
          Set <code style={{ background: '#991b1b', padding: '1px 5px', borderRadius: 3 }}>LLM_PROVIDER=groq</code> and{' '}
          <code style={{ background: '#991b1b', padding: '1px 5px', borderRadius: 3 }}>GROQ_API_KEY</code> in{' '}
          <code style={{ background: '#991b1b', padding: '1px 5px', borderRadius: 3 }}>backend/.env</code> and restart the backend.
        </div>
      )}

      {/* Pipeline status banner */}
      {false && pipeline.active && (
        <div style={{
          background: '#0f2744', color: '#bfdbfe',
          borderBottom: '1px solid #1d4ed8',
          flexShrink: 0,
          padding: '10px 24px',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
            <span style={{
              width: 7, height: 7, borderRadius: '50%',
              background: '#60a5fa', flexShrink: 0,
              animation: 'pulse-dot 1.5s ease-in-out infinite',
            }} />
            <span style={{ fontSize: 11, fontWeight: 700, color: '#93c5fd', letterSpacing: '0.08em', textTransform: 'uppercase' }}>
              Initializing campaign data
            </span>
            <span style={{ fontSize: 11, color: '#2d5fa8', marginLeft: 'auto' }}>
              Data shown may be incomplete
            </span>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
            {pipeline.steps.map(step => {
              const isDone = step.state === 'done'
              const isRunning = step.state === 'running'
              return (
                <div key={step.key} style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <span style={{
                    width: 14, flexShrink: 0, fontSize: 11, fontWeight: 700,
                    color: isDone ? '#34d399' : isRunning ? '#60a5fa' : '#1e3a5f',
                  }}>
                    {isDone ? '✓' : isRunning ? '●' : '○'}
                  </span>
                  <span style={{
                    fontSize: 12, fontWeight: isRunning ? 600 : 400,
                    color: isDone ? '#34d399' : isRunning ? '#bfdbfe' : '#2d4a6e',
                    minWidth: 220,
                  }}>
                    {step.label}
                  </span>
                  {step.detail && (
                    <span style={{ fontSize: 11, color: isDone ? '#34d39988' : '#7dd3fc' }}>
                      {step.detail}
                    </span>
                  )}
                  {isRunning && step.progress && (
                    <div style={{ width: 120, height: 3, background: '#1e3a5f', borderRadius: 99, overflow: 'hidden' }}>
                      <div style={{
                        height: '100%',
                        width: `${Math.round((step.progress.done / step.progress.total) * 100)}%`,
                        background: '#60a5fa', borderRadius: 99,
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

      {/* Main content — scrollable */}
      <main style={{ flex: 1, overflowY: 'auto', background: '#121212' }}>
        {children}
      </main>
    </div>
  )
}
