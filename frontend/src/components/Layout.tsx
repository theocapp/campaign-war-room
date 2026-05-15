import { useEffect, useState, useCallback } from 'react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { api } from '../api/client'

const SB = {
  bg:       '#111827',
  border:   '#1e2a3a',
  text:     '#e2e8f0',
  textSec:  '#8b9cb0',
  textMuted:'#4b6070',
  textXm:   '#2e3f52',
  activeBg: '#1a2535',
  hoverBg:  '#182030',
}

const NAV_ITEMS = [
  { to: '/briefing',   label: 'Briefing',        end: false, badge: false },
  { to: '/narratives', label: 'Narratives',       end: false, badge: false },
  { to: '/review',     label: 'AI Audit',         end: false, badge: true  },
  { to: '/opponents',  label: 'Opponent Tracker', end: false, badge: false },
  { to: '/feeds',      label: 'RSS Feeds',        end: false, badge: false },
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

export default function Layout({ children }: { children: React.ReactNode }) {
  const [candidateName, setCandidateName] = useState('')
  const [reviewCount, setReviewCount] = useState(0)
  const [lastSynced, setLastSynced] = useState<string | null>(null)
  const [syncLabel, setSyncLabel] = useState('')
  const [profileHover, setProfileHover] = useState(false)
  const [mockActive, setMockActive] = useState(false)
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
              background: 'linear-gradient(135deg, #6d28d9 0%, #4f46e5 100%)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: 13, fontWeight: 700, color: '#fff',
              boxShadow: '0 2px 8px rgba(109,40,217,0.4)',
            }}>{initials}</div>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div style={{
                fontWeight: 700, fontSize: '0.75rem', color: SB.text,
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>
                {candidateName || 'Campaign'}
              </div>
              <div style={{ fontSize: '0.6rem', color: SB.textMuted, marginTop: 1, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
                {profileHover ? 'Campaign Setup ›' : 'War Room'}
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
                    <span style={{ width: 3, height: 14, borderRadius: 99, background: '#7c3aed', flexShrink: 0 }} />
                  )}
                  <span style={{ flex: 1 }}>{label}</span>
                  {badge && reviewCount > 0 && (
                    <span style={{
                      background: '#ef4444', color: '#fff',
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

        {/* Top bar */}
        <header style={{
          height: 48, flexShrink: 0,
          borderBottom: '1px solid var(--border, #1e293b)',
          background: 'var(--surface-0, #0f172a)',
          display: 'flex', alignItems: 'center',
          padding: '0 24px', gap: 12,
        }}>
          <span style={{ flex: 1, fontWeight: 600, fontSize: 15, color: 'var(--text, #f1f5f9)' }}>
            {currentPage}
          </span>
          <span style={{ fontSize: 12, color: 'var(--text-muted, #64748b)' }}>
            {new Date().toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })}
          </span>
        </header>

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

        <main style={{ flex: 1, minHeight: 0, overflowY: 'auto', overflowX: 'hidden' }}>
          {children}
        </main>
      </div>
    </div>
  )
}
