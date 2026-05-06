import { useEffect, useState } from 'react'
import { NavLink } from 'react-router-dom'
import { api } from '../api/client'

const NAV = [
  {
    group: 'Briefing',
    items: [
      { to: '/',           label: 'Dashboard',     icon: '⬡' },
      { to: '/narratives', label: 'Narratives',     icon: '◈' },
      { to: '/battle',     label: 'Message Battle', icon: '⚔' },
    ],
  },
  {
    group: 'Operations',
    items: [
      { to: '/opponents',       label: 'Opponents',     icon: '◎' },
      { to: '/review',          label: 'Review Queue',  icon: '▤' },
      { to: '/talking',         label: 'Talking Points',icon: '◷' },
      { to: '/message-library', label: 'Message Lib',   icon: '◫' },
      { to: '/canvassing',      label: 'Canvassing',    icon: '◉' },
    ],
  },
  {
    group: 'Intelligence',
    items: [
      { to: '/sources',  label: 'Sources',   icon: '◈' },
      { to: '/monitors', label: 'Monitors',  icon: '◎' },
      { to: '/feeds',    label: 'RSS Feeds', icon: '◷' },
      { to: '/issues',   label: 'Issues',    icon: '◫' },
    ],
  },
]

export default function Layout({ children }: { children: React.ReactNode }) {
  const [candidateName, setCandidateName] = useState('')
  const [office, setOffice] = useState('')

  useEffect(() => {
    api.getCampaign()
      .then(p => {
        setCandidateName(p.candidate_name || '')
        setOffice(p.office || '')
      })
      .catch(() => {})
  }, [])

  return (
    <div style={{ display: 'flex', height: '100vh', background: 'var(--bg)', overflow: 'hidden' }}>
      {/* Sidebar */}
      <aside style={{
        width: 220,
        flexShrink: 0,
        background: 'var(--surface-1)',
        borderRight: '1px solid var(--border)',
        display: 'flex',
        flexDirection: 'column',
        overflowY: 'auto',
        overflowX: 'hidden',
      }}>
        {/* Brand */}
        <div style={{
          padding: '1.25rem 1rem 1rem',
          borderBottom: '1px solid var(--border)',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 9, marginBottom: 8 }}>
            <div style={{
              width: 32, height: 32,
              borderRadius: 9,
              background: 'linear-gradient(135deg, #7c3aed 0%, #4f46e5 100%)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: 15, flexShrink: 0,
              boxShadow: '0 2px 8px rgba(124,58,237,0.4)',
            }}>⚡</div>
            <div style={{ minWidth: 0 }}>
              <div style={{
                fontWeight: 700, fontSize: '0.8rem',
                color: 'var(--text-primary)',
                lineHeight: 1.2,
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>
                {candidateName || 'War Room'}
              </div>
              {office && (
                <div style={{
                  fontSize: '0.62rem',
                  color: 'var(--text-muted)',
                  marginTop: 1,
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}>
                  {office}
                </div>
              )}
            </div>
          </div>
          <div style={{
            fontSize: '0.58rem',
            color: 'var(--text-xmuted)',
            fontFamily: 'JetBrains Mono',
            letterSpacing: '0.08em',
            textTransform: 'uppercase',
          }}>
            Campaign War Room
          </div>
        </div>

        {/* Nav groups */}
        <nav style={{ flex: 1, padding: '0.75rem 0' }}>
          {NAV.map(({ group, items }) => (
            <div key={group} style={{ marginBottom: '0.25rem' }}>
              <div style={{
                padding: '0.5rem 1rem 0.25rem',
                fontSize: '0.58rem',
                fontWeight: 700,
                letterSpacing: '0.12em',
                textTransform: 'uppercase',
                color: 'var(--text-xmuted)',
                fontFamily: 'JetBrains Mono',
              }}>
                {group}
              </div>
              {items.map(({ to, label }) => (
                <NavLink
                  key={to}
                  to={to}
                  end={to === '/'}
                  style={({ isActive }) => ({
                    display: 'flex',
                    alignItems: 'center',
                    padding: '0.42rem 1rem',
                    marginInline: '0.5rem',
                    borderRadius: 'var(--radius-sm)',
                    fontSize: '0.8rem',
                    fontWeight: isActive ? 600 : 400,
                    color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
                    background: isActive ? 'var(--surface-3)' : 'transparent',
                    textDecoration: 'none',
                    transition: 'all 0.12s ease',
                    gap: 8,
                    marginBottom: 1,
                  })}
                  onMouseEnter={e => {
                    const el = e.currentTarget
                    if (!el.style.background || el.style.background === 'transparent') {
                      el.style.background = 'var(--surface-2)'
                      el.style.color = 'var(--text-primary)'
                    }
                  }}
                  onMouseLeave={e => {
                    const el = e.currentTarget
                    if (el.style.background === 'var(--surface-2)') {
                      el.style.background = 'transparent'
                      el.style.color = 'var(--text-secondary)'
                    }
                  }}
                >
                  {({ isActive }) => (
                    <>
                      {isActive && (
                        <span style={{
                          width: 3, height: 14, borderRadius: 99,
                          background: 'var(--accent)',
                          flexShrink: 0,
                          marginLeft: -4,
                        }} />
                      )}
                      <span style={{ flex: 1 }}>{label}</span>
                    </>
                  )}
                </NavLink>
              ))}
            </div>
          ))}
        </nav>

        {/* Bottom — Setup + ethics note */}
        <div style={{ borderTop: '1px solid var(--border)', padding: '0.75rem 0 0.5rem' }}>
          <NavLink
            to="/campaign"
            style={({ isActive }) => ({
              display: 'flex',
              alignItems: 'center',
              gap: 7,
              padding: '0.42rem 1rem',
              marginInline: '0.5rem',
              borderRadius: 'var(--radius-sm)',
              fontSize: '0.78rem',
              fontWeight: isActive ? 600 : 400,
              color: isActive ? 'var(--text-primary)' : 'var(--text-muted)',
              background: isActive ? 'var(--surface-3)' : 'transparent',
              textDecoration: 'none',
              marginBottom: 4,
            })}
          >
            ⚙ Campaign Setup
          </NavLink>
          <div style={{
            padding: '0.5rem 1rem',
            fontSize: '0.56rem',
            color: 'var(--text-xmuted)',
            lineHeight: 1.65,
            fontFamily: 'JetBrains Mono',
          }}>
            Evidence-only · No fabrication<br />
            <span style={{ color: 'rgba(248,113,113,0.4)' }}>No profiling · No suppression</span>
          </div>
        </div>
      </aside>

      {/* Main */}
      <main style={{
        flex: 1,
        minWidth: 0,
        overflowY: 'auto',
        overflowX: 'hidden',
      }}>
        {children}
      </main>
    </div>
  )
}
