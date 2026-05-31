import {
  BarChart3, Calendar, FileText, Home, Inbox, Layers,
  MapPin, Radio, Settings,
} from 'lucide-react'
import { NavLink } from 'react-router-dom'
import type { LucideIcon } from 'lucide-react'
import { useAuth } from '@/auth/AuthContext'

type NavItem = { to: string; label: string; icon: LucideIcon; exact?: boolean; adminOnly?: boolean }

const NAV: NavItem[] = [
  { to: '/',           label: 'Home',       icon: Home,       exact: true },
  // Briefing consolidated into Home on 2026-05-29. /briefing redirects to /.
  // Forecast retired 2026-05-29 — race-sentiment chart moved to Analytics.
  // /forecast redirects to /analytics.
  { to: '/articles',   label: 'Articles',   icon: FileText },
  { to: '/analytics',  label: 'Analytics',  icon: BarChart3 },
  { to: '/narratives', label: 'Narratives', icon: Layers },
  // Landscape + Opponents temporarily hidden 2026-05-30 at user request.
  // Routes redirect to / for now (see App.tsx). Page components preserved.
  // { to: '/landscape',  label: 'Landscape',  icon: Map },
  // Entity Network hidden 2026-05-29: legacy v14.x triple-shape data; v15.0
  // claim_records will surface as evidence inside narrative frames + briefing
  // rather than as a standalone graph page. See INTER_SESSION.md Session F.
  // { to: '/entity-network', label: 'Entity Network', icon: Network },
  { to: '/map',        label: 'Geographic',  icon: MapPin },
  { to: '/timeline',   label: 'Timeline',    icon: Calendar },
  // { to: '/opponents',  label: 'Opponents',  icon: Users },
  { to: '/review',     label: 'Review',     icon: Inbox },
  { to: '/monitors',   label: 'Monitors',   icon: Radio },
  // Settings is the campaign-setup wizard. Visible to everyone so non-admin
  // viewers can see the linked race, the setup checklist, and adjust their
  // own notification preferences. Cost-incurring actions inside (Pick race,
  // Save, Discover) render disabled for non-admins with a top-of-page
  // banner explaining why — backend require_admin gate is the real
  // authority and would 403 anything they bypassed.
  { to: '/setup',      label: 'Settings',   icon: Settings },
]

interface SidebarProps {
  /** Badge count for the Review nav item (queue length). */
  reviewBadge: number
  /** Collapsed state — managed by Layout so the header hamburger can toggle it. */
  collapsed: boolean
}

export function Sidebar({ reviewBadge, collapsed }: SidebarProps) {
  const { user } = useAuth()
  const isAdmin = !!user?.isAdmin
  const visibleNav = NAV.filter(item => !item.adminOnly || isAdmin)
  const width = collapsed ? 60 : 180

  return (
    <aside
      data-collapsed={collapsed}
      style={{
        width,
        flexShrink: 0,
        background: 'var(--bg-sidebar)',
        borderRight: '1px solid var(--border)',
        display: 'flex',
        flexDirection: 'column',
        overflow: collapsed ? 'visible' : 'hidden',
        transition: 'width 0.18s ease',
      }}
    >
      <nav style={{ flex: 1, padding: '12px 8px', display: 'flex', flexDirection: 'column', gap: 2 }}>
        {visibleNav.map(item => {
          const Icon = item.icon
          return (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.exact}
              aria-label={item.label}
              data-tooltip={item.label}
              className="sidebar-nav-link"
              style={({ isActive }) => ({
                display: 'flex',
                alignItems: 'center',
                gap: 12,
                padding: collapsed ? '10px 0' : '10px 12px',
                justifyContent: collapsed ? 'center' : 'flex-start',
                borderRadius: 8,
                textDecoration: 'none',
                fontSize: 14,
                fontWeight: isActive ? 600 : 400,
                color: isActive ? 'var(--text-1)' : 'var(--text-2)',
                background: isActive ? 'var(--bg-3)' : 'transparent',
                paddingLeft: collapsed ? 0 : 12,
                whiteSpace: 'nowrap',
                transition: 'background 0.1s ease, color 0.1s ease',
                position: 'relative',
              })}
              onMouseEnter={e => {
                if (!e.currentTarget.style.background.includes('var(--bg-3)')) {
                  e.currentTarget.style.background = 'var(--bg-3)'
                }
              }}
              onMouseLeave={e => {
                // Only revert if not active. Active links keep the bg.
                const isActive = e.currentTarget.classList.contains('active')
                if (!isActive) e.currentTarget.style.background = 'transparent'
              }}
            >
              <Icon size={18} strokeWidth={2} style={{ flexShrink: 0 }} />
              {!collapsed && (
                <>
                  <span style={{ flex: 1 }}>{item.label}</span>
                  {item.to === '/review' && reviewBadge > 0 && (
                    <span style={{
                      padding: '1px 7px', borderRadius: 8,
                      background: 'var(--accent)', color: 'var(--accent-text)',
                      fontSize: 11, fontWeight: 700, lineHeight: '14px',
                    }}>
                      {reviewBadge}
                    </span>
                  )}
                </>
              )}
              {collapsed && item.to === '/review' && reviewBadge > 0 && (
                <span
                  style={{
                    position: 'absolute', top: 4, right: 8,
                    width: 8, height: 8, borderRadius: '50%',
                    background: 'var(--accent)',
                  }}
                />
              )}
            </NavLink>
          )
        })}
      </nav>
    </aside>
  )
}
