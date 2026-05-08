import { useEffect, useState } from 'react'
import { NavLink, useNavigate } from 'react-router-dom'
import { api } from '../api/client'

/* ── Sidebar dark palette — hardcoded so sidebar stays dark while main is light ── */
const SB = {
  bg:       '#111827',
  border:   '#1e2a3a',
  text:     '#e2e8f0',
  textSec:  '#8b9cb0',
  textMuted:'#4b6070',
  textXm:   '#2e3f52',
  activeBg: '#1a2535',
  hoverBg:  '#182030',
  checkBorder: '#2a3e52',
  inputBg:  '#182030',
  inputBorder: '#1e2a3a',
}

const NAV_ITEMS = [
  { to: '/',                label: 'Dashboard',          end: true,  badge: false },
  { to: '/review',          label: 'Review Queue',       end: false, badge: true  },
  { to: '/narratives',      label: 'Narratives',         end: false, badge: false },
  { to: '/issues',          label: 'Issues',             end: false, badge: false },
  { to: '/opponents',       label: 'Opponent Tracker',   end: false, badge: false },
  { to: '/message-library', label: 'Candidate Messaging',end: false, badge: false },
  { to: '/canvassing',      label: 'Canvassing',         end: false, badge: false },
  { to: '/monitors',        label: 'Monitors',           end: false, badge: false },
  { to: '/sources',         label: 'Sources Library',    end: false, badge: false },
  { to: '/talking',         label: 'Reports / Exports',  end: false, badge: false },
]

export default function Layout({ children }: { children: React.ReactNode }) {
  const [candidateName, setCandidateName] = useState('')
  const [office, setOffice] = useState('')
  const [reviewCount, setReviewCount] = useState(0)
  const [ingesting, setIngesting] = useState(false)
  const [searchVal, setSearchVal] = useState('')
  const navigate = useNavigate()

  const [showCandidate, setShowCandidate] = useState(true)
  const [showOpponent, setShowOpponent] = useState(true)
  const [showMedia, setShowMedia] = useState(true)
  const [showOutside, setShowOutside] = useState(false)
  const [confidence, setConfidence] = useState(0)
  const [evidenceHigh, setEvidenceHigh] = useState(100)
  const [filtersOpen, setFiltersOpen] = useState(true)

  useEffect(() => {
    api.getCampaign()
      .then(p => { setCandidateName(p.candidate_name || ''); setOffice(p.office || '') })
      .catch(() => {})
    api.getReviewQueue()
      .then(items => setReviewCount(items.length))
      .catch(() => {})
  }, [])

  async function handleIngest() {
    setIngesting(true)
    try { await api.ingestAllFeeds() } catch { /* ignore */ }
    finally { setIngesting(false) }
  }

  const initials = candidateName
    ? candidateName.split(' ').map(w => w[0]).join('').slice(0, 2).toUpperCase()
    : 'WR'

  const raceLabel = office || 'War Room'

  function handleSearch(e: React.FormEvent) {
    e.preventDefault()
    if (searchVal.trim()) navigate(`/sources?q=${encodeURIComponent(searchVal.trim())}`)
  }

  return (
    <div style={{ display: 'flex', height: '100vh', background: 'var(--bg)', overflow: 'hidden' }}>

      {/* ═══════════════ SIDEBAR (always dark) ═══════════════ */}
      <aside style={{
        width: 200, flexShrink: 0,
        background: SB.bg,
        borderRight: `1px solid ${SB.border}`,
        display: 'flex', flexDirection: 'column',
        overflowY: 'auto', overflowX: 'hidden',
      }}>

        {/* Brand */}
        <div style={{ padding: '1rem', borderBottom: `1px solid ${SB.border}` }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <div style={{
              width: 34, height: 34, borderRadius: 8, flexShrink: 0,
              background: 'linear-gradient(135deg, #6d28d9 0%, #4f46e5 100%)',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: 17, boxShadow: '0 2px 8px rgba(109,40,217,0.5)',
            }}>🛡</div>
            <div style={{ minWidth: 0 }}>
              <div style={{
                fontWeight: 800, fontSize: '0.68rem', color: SB.text,
                lineHeight: 1.2, letterSpacing: '0.03em', textTransform: 'uppercase',
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>
                {candidateName || 'Campaign'}
              </div>
              <div style={{
                fontSize: '0.58rem', color: SB.textMuted, marginTop: 1,
                textTransform: 'uppercase', letterSpacing: '0.06em',
              }}>
                For Congress
              </div>
            </div>
          </div>
        </div>

        {/* Nav */}
        <nav style={{ flex: 1, padding: '0.5rem 0' }}>
          <div style={{
            padding: '0.4rem 0.875rem 0.2rem',
            fontSize: '0.58rem', fontWeight: 700, letterSpacing: '0.12em',
            textTransform: 'uppercase', color: SB.textXm,
            fontFamily: 'JetBrains Mono',
          }}>War Room</div>

          {NAV_ITEMS.map(({ to, label, end, badge }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              style={({ isActive }) => ({
                display: 'flex', alignItems: 'center', gap: 0,
                padding: '0.38rem 0.875rem',
                marginInline: '0.375rem',
                borderRadius: 6,
                fontSize: '0.8rem',
                fontWeight: isActive ? 600 : 400,
                color: isActive ? SB.text : SB.textSec,
                background: isActive ? SB.activeBg : 'transparent',
                textDecoration: 'none',
                transition: 'all 0.12s ease',
                marginBottom: 1,
              })}
              onMouseEnter={e => {
                const el = e.currentTarget
                if (el.style.background === 'transparent') {
                  el.style.background = SB.hoverBg
                  el.style.color = SB.text
                }
              }}
              onMouseLeave={e => {
                const el = e.currentTarget
                if (el.style.background === SB.hoverBg) {
                  el.style.background = 'transparent'
                  el.style.color = SB.textSec
                }
              }}
            >
              {({ isActive }) => (
                <>
                  {isActive && <span style={{
                    width: 3, height: 14, borderRadius: 99,
                    background: '#7c3aed', flexShrink: 0, marginRight: 6,
                  }} />}
                  <span style={{ flex: 1 }}>{label}</span>
                  {badge && reviewCount > 0 && (
                    <span style={{
                      background: '#ef4444', color: '#fff',
                      fontSize: '0.58rem', fontWeight: 700,
                      padding: '1px 6px', borderRadius: 99, lineHeight: 1.5,
                    }}>{reviewCount}</span>
                  )}
                </>
              )}
            </NavLink>
          ))}
        </nav>

        {/* Filters */}
        <div style={{ borderTop: `1px solid ${SB.border}`, padding: '0.625rem 0.875rem' }}>
          <button
            onClick={() => setFiltersOpen(f => !f)}
            style={{
              width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              background: 'none', border: 'none', cursor: 'pointer', padding: 0, marginBottom: filtersOpen ? 8 : 0,
            }}
          >
            <span style={{
              fontSize: '0.58rem', fontWeight: 700, letterSpacing: '0.12em',
              textTransform: 'uppercase', color: SB.textXm, fontFamily: 'JetBrains Mono',
            }}>Filters</span>
            <span style={{ fontSize: '0.6rem', color: SB.textMuted }}>{filtersOpen ? '∧' : '∨'}</span>
          </button>

          {filtersOpen && (
            <>
              <div style={{ fontSize: '0.68rem', color: SB.textMuted, marginBottom: 5, fontWeight: 500 }}>Show</div>
              {[
                { label: 'Candidate',     val: showCandidate, set: setShowCandidate, color: '#818cf8' },
                { label: 'Opponent',      val: showOpponent,  set: setShowOpponent,  color: '#f87171' },
                { label: 'Media',         val: showMedia,     set: setShowMedia,     color: '#34d399' },
                { label: 'Outside Groups',val: showOutside,   set: setShowOutside,   color: '#94a3b8' },
              ].map(({ label, val, set, color }) => (
                <label key={label} style={{
                  display: 'flex', alignItems: 'center', gap: 6,
                  marginBottom: 4, cursor: 'pointer',
                  fontSize: '0.75rem', color: SB.textSec,
                }}>
                  <span style={{
                    width: 14, height: 14, borderRadius: 3, flexShrink: 0,
                    border: `1.5px solid ${val ? color : SB.checkBorder}`,
                    background: val ? color : 'transparent',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    fontSize: 9, color: '#fff', cursor: 'pointer', transition: 'all 0.12s',
                  }} onClick={() => set(!val)}>
                    {val ? '✓' : ''}
                  </span>
                  <span style={{ flex: 1 }}>{label}</span>
                  <span style={{ width: 8, height: 8, borderRadius: '50%', background: color, flexShrink: 0 }} />
                </label>
              ))}

              <div style={{ marginTop: 8 }}>
                <div style={{ fontSize: '0.68rem', color: SB.textMuted, marginBottom: 3, fontWeight: 500 }}>Content Type</div>
                <select style={{
                  width: '100%', padding: '0.25rem 0.5rem', fontSize: '0.72rem', borderRadius: 4,
                  background: SB.inputBg, border: `1px solid ${SB.inputBorder}`, color: SB.textSec,
                }}>
                  <option>All Types</option><option>News</option><option>Social</option><option>Public Record</option>
                </select>
              </div>

              <div style={{ marginTop: 6 }}>
                <div style={{ fontSize: '0.68rem', color: SB.textMuted, marginBottom: 3, fontWeight: 500 }}>Geography</div>
                <select style={{
                  width: '100%', padding: '0.25rem 0.5rem', fontSize: '0.72rem', borderRadius: 4,
                  background: SB.inputBg, border: `1px solid ${SB.inputBorder}`, color: SB.textSec,
                }}>
                  <option>All Regions</option><option>District</option><option>State</option><option>National</option>
                </select>
              </div>

              <div style={{ marginTop: 8 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
                  <span style={{ fontSize: '0.68rem', color: SB.textMuted, fontWeight: 500 }}>Confidence</span>
                  <span style={{ fontSize: '0.6rem', color: SB.textXm, fontFamily: 'JetBrains Mono' }}>{confidence} – {evidenceHigh}</span>
                </div>
                <input type="range" min={0} max={100} value={confidence}
                  onChange={e => setConfidence(+e.target.value)}
                  style={{ width: '100%', accentColor: '#7c3aed' }} />
              </div>

              <div style={{ marginTop: 6 }}>
                <div style={{ fontSize: '0.68rem', color: SB.textMuted, marginBottom: 3, fontWeight: 500 }}>Evidence Strength</div>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.6rem', color: SB.textXm, marginBottom: 2 }}>
                  <span>Low</span><span>High</span>
                </div>
                <input type="range" min={0} max={100} value={evidenceHigh}
                  onChange={e => setEvidenceHigh(+e.target.value)}
                  style={{ width: '100%', accentColor: '#7c3aed' }} />
              </div>

              <button
                onClick={() => { setConfidence(0); setEvidenceHigh(100); setShowCandidate(true); setShowOpponent(true); setShowMedia(true); setShowOutside(false) }}
                style={{
                  marginTop: 8, background: 'none', border: 'none', cursor: 'pointer',
                  fontSize: '0.7rem', color: SB.textMuted, padding: 0,
                  display: 'flex', alignItems: 'center', gap: 4,
                }}
              >× Clear Filters</button>
            </>
          )}
        </div>

        {/* Setup */}
        <div style={{ borderTop: `1px solid ${SB.border}`, padding: '0.4rem 0.375rem' }}>
          <NavLink
            to="/campaign"
            style={({ isActive }) => ({
              display: 'flex', alignItems: 'center', gap: 7, padding: '0.38rem 0.5rem',
              borderRadius: 6, fontSize: '0.75rem', textDecoration: 'none',
              color: isActive ? SB.text : SB.textMuted,
              background: isActive ? SB.activeBg : 'transparent',
            })}
          >⚙ Campaign Setup</NavLink>
        </div>
      </aside>

      {/* ═══════════════ MAIN AREA (light) ═══════════════ */}
      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>

        {/* Top bar */}
        <header style={{
          height: 52, borderBottom: '1px solid var(--border)',
          display: 'flex', alignItems: 'center', gap: 10,
          padding: '0 1.25rem',
          background: 'var(--surface-0)',
          flexShrink: 0,
        }}>
          <div style={{
            display: 'flex', alignItems: 'center', gap: 6,
            padding: '0.3rem 0.75rem', borderRadius: 6,
            border: '1px solid var(--border)', background: 'var(--surface-2)',
            cursor: 'pointer', fontSize: '0.82rem', color: 'var(--text-primary)',
            fontWeight: 500, whiteSpace: 'nowrap', flexShrink: 0,
          }}>
            <span style={{ fontSize: 13 }}>🏛</span>
            <span>{raceLabel}</span>
            <span style={{ color: 'var(--text-muted)', fontSize: 10 }}>▾</span>
          </div>

          <form onSubmit={handleSearch} style={{ flex: 1 }}>
            <div style={{ position: 'relative' }}>
              <span style={{
                position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)',
                color: 'var(--text-muted)', fontSize: 13, pointerEvents: 'none',
              }}>⌕</span>
              <input
                value={searchVal} onChange={e => setSearchVal(e.target.value)}
                placeholder="Search narratives, sources, issues, people..."
                style={{
                  width: '100%', padding: '0.32rem 0.75rem 0.32rem 2rem',
                  fontSize: '0.8rem', borderRadius: 6,
                  background: 'var(--surface-2)', border: '1px solid var(--border)',
                  color: 'var(--text-primary)',
                }}
              />
            </div>
          </form>

          <div style={{
            display: 'flex', alignItems: 'center', gap: 6,
            padding: '0.3rem 0.75rem', borderRadius: 6,
            border: '1px solid var(--border)', background: 'var(--surface-2)',
            cursor: 'pointer', fontSize: '0.8rem', color: 'var(--text-secondary)',
            whiteSpace: 'nowrap', flexShrink: 0,
          }}>
            <span>📅</span><span>Last 7 Days</span>
            <span style={{ color: 'var(--text-muted)', fontSize: 10 }}>▾</span>
          </div>

          <button onClick={handleIngest} disabled={ingesting} style={{
            display: 'flex', alignItems: 'center', gap: 6,
            padding: '0.38rem 1rem', borderRadius: 6, border: 'none',
            background: '#6d28d9', color: '#fff',
            fontSize: '0.8rem', fontWeight: 600,
            cursor: ingesting ? 'not-allowed' : 'pointer',
            opacity: ingesting ? 0.7 : 1, whiteSpace: 'nowrap', flexShrink: 0,
            boxShadow: '0 1px 3px rgba(109,40,217,0.4)',
          }}>
            {ingesting ? '⟳' : '↑'} {ingesting ? 'Ingesting…' : 'Ingest Now'}
          </button>

          <button style={{
            width: 34, height: 34, borderRadius: 6,
            border: '1px solid var(--border)', background: 'var(--surface-2)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            cursor: 'pointer', fontSize: 15, color: 'var(--text-secondary)', flexShrink: 0,
          }}>🔔</button>

          <div style={{
            width: 34, height: 34, borderRadius: '50%',
            background: 'linear-gradient(135deg, #7c3aed, #6d28d9)',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            fontSize: '0.7rem', fontWeight: 700, color: '#fff',
            flexShrink: 0, cursor: 'pointer',
            boxShadow: '0 1px 3px rgba(109,40,217,0.4)',
          }}>{initials}</div>
        </header>

        <main style={{ flex: 1, minHeight: 0, overflowY: 'auto', overflowX: 'hidden' }}>
          {children}
        </main>
      </div>
    </div>
  )
}
