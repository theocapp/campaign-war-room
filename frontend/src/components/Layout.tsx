import { useEffect, useState } from 'react'
import { NavLink } from 'react-router-dom'
import { api } from '../api/client'

const NAV_TOP = [
  { to: '/',           label: 'Dashboard',        icon: '▪' },
  { to: '/issues',     label: 'Issue Tracker',     icon: '▪' },
  { to: '/opponents',  label: 'Opponent Tracker',  icon: '▪' },
  { to: '/review',     label: 'Review Queue',      icon: '▪' },
  { to: '/canvassing', label: 'Canvassing',        icon: '▪' },
  { to: '/talking',    label: 'Talking Points',    icon: '▪' },
  { to: '/sources',    label: 'Sources',           icon: '▪' },
  { to: '/feeds',      label: 'RSS Feeds',         icon: '▪' },
]

const NAV_BOTTOM = [
  { to: '/campaign', label: 'Campaign Setup', icon: '▪' },
]

function navLinkStyle({ isActive }: { isActive: boolean }): React.CSSProperties {
  return {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    padding: '0.5rem 0.75rem',
    borderRadius: 6,
    marginBottom: 2,
    fontSize: '0.8rem',
    fontWeight: isActive ? 600 : 400,
    color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
    background: isActive ? 'var(--surface-3)' : 'transparent',
    border: isActive ? '1px solid var(--border)' : '1px solid transparent',
    textDecoration: 'none',
    transition: 'all 0.12s',
  }
}

export default function Layout({ children }: { children: React.ReactNode }) {
  const [candidateName, setCandidateName] = useState<string>('War Room')

  useEffect(() => {
    api.getCampaign()
      .then(p => setCandidateName(p.candidate_name || 'War Room'))
      .catch(() => {/* silent */})
  }, [])

  return (
    <div style={{ display: 'flex', minHeight: '100vh', background: 'var(--bg)' }}>
      {/* Sidebar */}
      <aside style={{
        width: 220,
        minHeight: '100vh',
        background: 'var(--surface-1)',
        borderRight: '1px solid var(--border)',
        display: 'flex',
        flexDirection: 'column',
        flexShrink: 0,
      }}>
        {/* Logo */}
        <div style={{ padding: '1.25rem 1rem 1rem', borderBottom: '1px solid var(--border)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <div style={{
              width: 28, height: 28, borderRadius: 6,
              background: 'rgba(59,130,246,0.2)',
              border: '1px solid rgba(59,130,246,0.4)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: 14,
            }}>⚡</div>
            <div>
              <div style={{ fontWeight: 700, fontSize: '0.8rem', color: 'var(--text-primary)', lineHeight: 1.2 }}>
                {candidateName}
              </div>
              <div style={{ fontSize: '0.65rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>
                Campaign War Room
              </div>
            </div>
          </div>
        </div>

        {/* Primary nav */}
        <nav style={{ padding: '0.75rem 0.5rem', flex: 1 }}>
          {NAV_TOP.map(({ to, label }) => (
            <NavLink key={to} to={to} end={to === '/'} style={navLinkStyle}>
              {label}
            </NavLink>
          ))}
        </nav>

        {/* Bottom nav */}
        <div style={{ padding: '0.5rem 0.5rem', borderTop: '1px solid var(--border)' }}>
          {NAV_BOTTOM.map(({ to, label }) => (
            <NavLink key={to} to={to} style={navLinkStyle}>
              <span style={{ fontSize: '0.7rem', opacity: 0.6 }}>⚙</span>
              {label}
            </NavLink>
          ))}

          <div style={{ padding: '0.5rem 0.75rem', marginTop: 4 }}>
            <div style={{ fontSize: '0.6rem', color: 'var(--text-muted)', lineHeight: 1.5 }}>
              Legal · Ethical · Evidence-based
              <br />
              <span style={{ color: 'rgba(239,68,68,0.6)' }}>No fabrication · No profiling</span>
            </div>
          </div>
        </div>
      </aside>

      {/* Main content */}
      <main style={{ flex: 1, minWidth: 0, overflowX: 'hidden' }}>
        {children}
      </main>
    </div>
  )
}
