import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { Opponent, OpponentActivity } from '../api/types'

const ACTIVITY_META: Record<string, { label: string; color: string; bg: string }> = {
  attack:        { label: 'Attack',        color: 'var(--opponent)',    bg: 'var(--urgent-bg)' },
  claim:         { label: 'Claim',         color: '#fbbf24',            bg: 'rgba(251,191,36,0.08)' },
  promise:       { label: 'Promise',       color: '#34d399',            bg: 'rgba(52,211,153,0.08)' },
  contradiction: { label: 'Contradiction', color: '#c084fc',            bg: 'rgba(192,132,252,0.08)' },
  note:          { label: 'Note',          color: 'var(--text-muted)',  bg: 'var(--surface-2)' },
}

function activityType(act: OpponentActivity) {
  if (act.attack)            return 'attack'
  if (act.claim)             return 'claim'
  if (act.promise)           return 'promise'
  if (act.contradiction_note) return 'contradiction'
  return 'note'
}

export default function OpponentTracker() {
  const [opponents, setOpponents]   = useState<Opponent[]>([])
  const [activities, setActivities] = useState<OpponentActivity[]>([])
  const [selected, setSelected]     = useState<Opponent | null>(null)
  const [loading, setLoading]       = useState(true)
  const [showAdd, setShowAdd]       = useState(false)
  const [form, setForm]             = useState({ name: '', office: '', party: '', notes: '' })
  const [saving, setSaving]         = useState(false)

  useEffect(() => {
    api.getOpponents().then(d => {
      setOpponents(d)
      setLoading(false)
      if (d.length > 0) loadActivity(d[0])
    })
  }, [])

  function loadActivity(opp: Opponent) {
    setSelected(opp)
    api.getOpponentActivity(opp.id).then(setActivities)
  }

  async function addOpponent() {
    if (!form.name.trim()) return
    setSaving(true)
    try {
      const opp = await api.addOpponent(form)
      setOpponents(prev => [...prev, opp])
      setShowAdd(false)
      setForm({ name: '', office: '', party: '', notes: '' })
      loadActivity(opp)
    } finally { setSaving(false) }
  }

  if (loading) return <div className="loading-text">Loading…</div>

  const attacks       = activities.filter(a => a.attack)
  const claims        = activities.filter(a => a.claim && !a.attack)
  const promises      = activities.filter(a => a.promise)
  const contradictions = activities.filter(a => a.contradiction_note)

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '240px 1fr', minHeight: '100vh' }}>
      {/* Sidebar */}
      <div style={{ borderRight: '1px solid var(--border)', background: 'var(--surface-1)', padding: '1.5rem 0.75rem', overflowY: 'auto' }}>
        <div style={{ padding: '0 0.25rem', marginBottom: '1rem' }}>
          <div className="label" style={{ marginBottom: 4 }}>Intelligence</div>
          <h1 style={{ margin: 0, fontSize: '1.05rem', fontWeight: 700, letterSpacing: '-0.01em' }}>Opponents</h1>
        </div>

        {opponents.map(opp => {
          const isActive = selected?.id === opp.id
          return (
            <button
              key={opp.id}
              onClick={() => loadActivity(opp)}
              style={{
                width: '100%', textAlign: 'left', cursor: 'pointer',
                background: isActive ? 'var(--surface-3)' : 'transparent',
                border: `1px solid ${isActive ? 'var(--accent-border)' : 'transparent'}`,
                borderRadius: 'var(--radius-sm)',
                padding: '0.65rem 0.75rem',
                marginBottom: 2, transition: 'all 0.12s', fontFamily: 'inherit',
              }}
              onMouseEnter={e => { if (!isActive) (e.currentTarget as HTMLElement).style.background = 'var(--surface-2)' }}
              onMouseLeave={e => { if (!isActive) (e.currentTarget as HTMLElement).style.background = 'transparent' }}
            >
              <div style={{ fontWeight: 600, fontSize: '0.83rem', color: 'var(--text-primary)', marginBottom: 2 }}>{opp.name}</div>
              {(opp.party || opp.office) && (
                <div style={{ fontSize: '0.68rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>
                  {[opp.party, opp.office].filter(Boolean).join(' · ')}
                </div>
              )}
            </button>
          )
        })}

        <button
          className="btn btn-ghost btn-sm"
          style={{ width: '100%', marginTop: 12 }}
          onClick={() => setShowAdd(s => !s)}
        >
          {showAdd ? '− Cancel' : '+ Add Opponent'}
        </button>

        {showAdd && (
          <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 6 }}>
            <input value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} placeholder="Full name *" />
            <input value={form.office} onChange={e => setForm(f => ({ ...f, office: e.target.value }))} placeholder="Office" />
            <input value={form.party} onChange={e => setForm(f => ({ ...f, party: e.target.value }))} placeholder="Party" />
            <input value={form.notes} onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} placeholder="Notes" />
            <button className="btn btn-primary btn-sm" onClick={addOpponent} disabled={saving || !form.name.trim()}>
              {saving ? '…' : 'Add'}
            </button>
          </div>
        )}
      </div>

      {/* Detail */}
      <div style={{ padding: '2rem', overflowY: 'auto' }}>
        {!selected && (
          <div className="empty-state" style={{ marginTop: '3rem' }}>
            <div className="empty-state-icon">◫</div>
            <div className="empty-state-title">Select an opponent</div>
            <div className="empty-state-body">Track attacks, claims, promises and contradictions.</div>
          </div>
        )}

        {selected && (
          <>
            {/* Header */}
            <div style={{ marginBottom: '1.5rem' }}>
              <h2 style={{ margin: '0 0 4px', fontSize: '1.25rem', fontWeight: 700, letterSpacing: '-0.02em' }}>{selected.name}</h2>
              {(selected.party || selected.office) && (
                <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>
                  {[selected.party, selected.office].filter(Boolean).join(' · ')}
                </div>
              )}
              {selected.notes && (
                <p style={{ margin: '8px 0 0', fontSize: '0.8rem', color: 'var(--text-secondary)', lineHeight: 1.5 }}>{selected.notes}</p>
              )}
            </div>

            {/* Stats row */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10, marginBottom: '1.5rem' }}>
              {[
                { label: 'Attacks',        count: attacks.length,       color: ACTIVITY_META.attack.color },
                { label: 'Claims',         count: claims.length,        color: ACTIVITY_META.claim.color },
                { label: 'Promises',       count: promises.length,      color: ACTIVITY_META.promise.color },
                { label: 'Contradictions', count: contradictions.length, color: ACTIVITY_META.contradiction.color },
              ].map(({ label, count, color }) => (
                <div key={label} className="card" style={{ textAlign: 'center' }}>
                  <div style={{ fontSize: '1.6rem', fontWeight: 700, color, fontFamily: 'JetBrains Mono', lineHeight: 1 }}>{count}</div>
                  <div style={{ fontSize: '0.62rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em', marginTop: 4 }}>{label}</div>
                </div>
              ))}
            </div>

            {/* Activity feed */}
            {activities.length === 0 && (
              <div className="empty-state" style={{ marginTop: '1rem' }}>
                <div className="empty-state-icon">—</div>
                <div className="empty-state-title">No activity yet</div>
                <div className="empty-state-body">Add opponent statements or sources attributed to this opponent.</div>
              </div>
            )}

            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {activities.map(act => {
                const type = activityType(act)
                const meta = ACTIVITY_META[type]
                const text = act.attack || act.claim || act.promise
                return (
                  <div key={act.id} className="card" style={{ borderLeft: `3px solid ${meta.color}44` }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                      <span style={{ fontSize: '0.62rem', fontFamily: 'JetBrains Mono', color: meta.color, letterSpacing: '0.07em', textTransform: 'uppercase' }}>
                        {meta.label}
                      </span>
                      {act.repeated_theme && (
                        <span className="badge badge-ghost" style={{ fontSize: '0.58rem' }}>{act.repeated_theme}</span>
                      )}
                    </div>

                    {text && (
                      <blockquote style={{ margin: '0 0 10px', padding: '0.5rem 0.75rem', borderLeft: `2px solid ${meta.color}55`, background: meta.bg, borderRadius: '0 var(--radius-sm) var(--radius-sm) 0' }}>
                        <p style={{ margin: 0, fontSize: '0.85rem', color: 'var(--text-primary)', lineHeight: 1.5, fontStyle: 'italic' }}>
                          "{text}"
                        </p>
                      </blockquote>
                    )}

                    {act.contradiction_note && (
                      <div style={{ background: 'rgba(192,132,252,0.08)', border: '1px solid rgba(192,132,252,0.2)', borderRadius: 'var(--radius-sm)', padding: '0.5rem 0.75rem', marginBottom: 8 }}>
                        <div style={{ fontSize: '0.6rem', fontFamily: 'JetBrains Mono', color: '#c084fc', letterSpacing: '0.06em', marginBottom: 4, textTransform: 'uppercase' }}>
                          Contradiction
                        </div>
                        <p style={{ margin: 0, fontSize: '0.78rem', color: '#e9d5ff', lineHeight: 1.5 }}>{act.contradiction_note}</p>
                      </div>
                    )}

                    {act.source_item && (
                      <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>
                        {act.source_item.source_name || 'Unknown'} · {act.source_item.source_type.replace('_', ' ')}
                        {' · '}
                        {act.source_item.source_url ? (
                          <a href={act.source_item.source_url} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--accent-light)' }}>
                            View ↗
                          </a>
                        ) : (
                          <a href={`/sources?source_id=${act.source_item.id}`} style={{ color: 'var(--accent-light)' }}>
                            View →
                          </a>
                        )}
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
