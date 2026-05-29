import { Menu } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '@/api/client'
import { prefetchDashboard } from '@/api/dashboardCache'
import { NotificationsBell } from './NotificationsBell'
import { SearchBar } from './SearchBar'
import { Sidebar } from './Sidebar'
import { ThemeToggle } from './ThemeToggle'

const SIDEBAR_COLLAPSED_KEY = 'cwr-sidebar-collapsed'

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
  // Sidebar collapse — lifted out of Sidebar so the header hamburger can
  // toggle it. Persists in localStorage across sessions.
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(() => {
    try { return localStorage.getItem(SIDEBAR_COLLAPSED_KEY) === '1' }
    catch { return false }
  })
  useEffect(() => {
    try { localStorage.setItem(SIDEBAR_COLLAPSED_KEY, sidebarCollapsed ? '1' : '0') } catch { /* ignore */ }
  }, [sidebarCollapsed])
  const pipeline = usePipelineStatus()

  useEffect(() => {
    // One-time fetches (these don't change frequently).
    api.ingestStatus().then(d => {
      if (d.crawler_last_run) setLastSync(d.crawler_last_run)
    }).catch(() => {})
    api.campaign().then(d => {
      if (d.district) setDistrict(d.district)
    }).catch(() => {})
    api.getLLMStatus().then(s => setMockActive(s.is_mock)).catch(() => {})
    // Warm the Dashboard cache once on mount so opening Home from any page
    // is instant.
    prefetchDashboard()

    // Review-queue count needs to refresh — the user reviews/dismisses
    // items in /review and the sidebar badge must reflect that.
    const fetchQueueCount = () => {
      api.reviewQueueCount().then(d => setQueueCount(d.count)).catch(() => {})
    }
    fetchQueueCount()
    const queueTimer = setInterval(fetchQueueCount, 15_000)
    return () => clearInterval(queueTimer)
  }, [])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', background: 'var(--bg-1)' }}>
      {/* Top header. Hamburger is sized + positioned so its center aligns
          with the collapsed sidebar's icon centers (~30px from viewport
          left). The logo sits ~72px from the left so it visually clears
          the collapsed sidebar (60px wide) and reads as part of the
          content area, not the sidebar. */}
      <header style={{
        height: 48,
        flexShrink: 0,
        background: 'var(--bg-nav)',
        borderBottom: '1px solid var(--border)',
        display: 'flex',
        alignItems: 'center',
        padding: '0 16px 0 0',
        gap: 0,
        zIndex: 100,
        position: 'relative',
      }}>
        {/* Hamburger zone — width matches the sidebar (60px collapsed,
            220px expanded) so the logo to its right ALWAYS aligns with the
            start of the main content area. The button itself stays a
            stable 36×36 square; the "Menu" text appears next to it when
            expanded. To keep the animation perfectly smooth, only the
            zone's width transitions — the button never resizes, and the
            label fades in via opacity (no layout reflow). */}
        <div style={{
          width: sidebarCollapsed ? 60 : 220,
          height: '100%',
          display: 'flex', alignItems: 'center',
          paddingLeft: 12,
          flexShrink: 0,
          transition: 'width 0.18s ease',
        }}>
          <button
            type="button"
            onClick={() => setSidebarCollapsed(c => !c)}
            aria-label={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            title={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            style={{
              display: 'inline-flex', alignItems: 'center', justifyContent: 'flex-start',
              gap: 10,
              height: 36,
              width: sidebarCollapsed ? 36 : 88,
              paddingLeft: 8,
              paddingRight: sidebarCollapsed ? 8 : 12,
              borderRadius: 8,
              background: 'transparent',
              border: 'none',
              color: 'var(--text-2)',
              cursor: 'pointer', flexShrink: 0,
              fontSize: 14, fontWeight: 500, fontFamily: 'inherit',
              overflow: 'hidden',
              transition: 'background 0.1s ease, color 0.1s ease, width 0.18s ease, padding-right 0.18s ease',
            }}
            onMouseEnter={e => {
              e.currentTarget.style.background = 'var(--bg-3)'
              e.currentTarget.style.color = 'var(--text-1)'
            }}
            onMouseLeave={e => {
              e.currentTarget.style.background = 'transparent'
              e.currentTarget.style.color = 'var(--text-2)'
            }}
          >
            <Menu size={20} strokeWidth={2} style={{ flexShrink: 0 }} />
            {/* Label fades in/out via opacity; the button's overflow:hidden
                + width transition clips it cleanly when collapsed. */}
            <span style={{
              opacity: sidebarCollapsed ? 0 : 1,
              transition: 'opacity 0.18s ease',
              pointerEvents: 'none',
              whiteSpace: 'nowrap',
            }}>
              Menu
            </span>
          </button>
        </div>

        {/* Logo + district — sits flush against the right edge of the
            hamburger zone, so it visually aligns with the left edge of
            the main content (and shifts right when the sidebar expands). */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0,
          paddingLeft: 12,
        }}>
          <img
            src="/noctua-logo.png"
            alt="NOCTUA"
            className="noctua-logo"
            style={{ height: 28, width: 'auto', display: 'block' }}
          />
          <span style={{
            fontSize: 10,
            color: 'var(--accent-text)',
            background: 'var(--accent)',
            borderRadius: 4,
            padding: '2px 6px',
            fontWeight: 700,
            marginLeft: 2,
          }}>
            {district}
          </span>
        </div>

        {/* Universal search bar — centered within the MAIN content area
            (viewport minus sidebar), so the logo on the left always has
            room regardless of whether the sidebar is expanded. The `left`
            value is `sidebarWidth + (viewportWidth - sidebarWidth) / 2`
            expressed as `calc((100% + sidebarWidth) / 2)`. */}
        <div style={{
          position: 'absolute',
          left: `calc((100% + ${sidebarCollapsed ? 60 : 220}px) / 2)`,
          top: '50%',
          transform: 'translate(-50%, -50%)',
          width: 600,
          maxWidth: '60vw',
          pointerEvents: 'none',
          transition: 'left 0.18s ease',
        }}>
          <div style={{ pointerEvents: 'auto', display: 'flex' }}>
            <SearchBar />
          </div>
        </div>

        {/* Right cluster */}
        <div style={{
          display: 'flex', alignItems: 'center', gap: 12,
          marginLeft: 'auto', flexShrink: 0,
        }}>
          {lastSync && (
            <div style={{ fontSize: 11, color: 'var(--text-3)' }}>
              synced {formatAgo(lastSync)}
            </div>
          )}
          <NotificationsBell />
          <ThemeToggle />
          <button
            type="button"
            title="theocapeilleres@gmail.com"
            style={{
              width: 32,
              height: 32,
              borderRadius: '50%',
              background: '#7c3aed',
              color: '#fff',
              border: 'none',
              cursor: 'pointer',
              fontWeight: 700,
              fontSize: 12,
              letterSpacing: '0.02em',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            TC
          </button>
        </div>
      </header>

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

      {/* Pipeline status banner — only during active backfill / rescore / rematch jobs */}
      {pipeline.active && (
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

      {/* Body — sidebar on the left, main content on the right */}
      <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
        <Sidebar reviewBadge={queueCount} collapsed={sidebarCollapsed} />
        <main style={{ flex: 1, overflowY: 'auto', background: 'var(--bg-1)' }}>
          {children}
        </main>
      </div>
    </div>
  )
}
