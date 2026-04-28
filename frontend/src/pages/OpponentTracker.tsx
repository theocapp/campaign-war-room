import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { Opponent, OpponentActivity } from '../api/types'

const ACTIVITY_COLORS: Record<string, string> = {
  attack: '#f87171',
  claim: '#fbbf24',
  promise: '#34d399',
  contradiction: '#c084fc',
}

export default function OpponentTracker() {
  const [opponents, setOpponents] = useState<Opponent[]>([])
  const [activities, setActivities] = useState<OpponentActivity[]>([])
  const [selected, setSelected] = useState<Opponent | null>(null)
  const [loading, setLoading] = useState(true)
  const [showAdd, setShowAdd] = useState(false)
  const [form, setForm] = useState({ name: '', office: '', party: '', notes: '' })

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
    const opp = await api.addOpponent(form)
    setOpponents(prev => [...prev, opp])
    setShowAdd(false)
    setForm({ name: '', office: '', party: '', notes: '' })
    loadActivity(opp)
  }

  if (loading) return <div style={{ padding: '2rem', color: 'var(--text-muted)' }}>Loading…</div>

  const attacks = activities.filter(a => a.attack)
  const claims = activities.filter(a => a.claim && !a.attack)
  const promises = activities.filter(a => a.promise)
  const contradictions = activities.filter(a => a.contradiction_note)

  return (
    <div style={{ padding: '1.5rem', maxWidth: 1100 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '1.5rem' }}>
        <div>
          <div className="label" style={{ marginBottom: 4 }}>Opponent Tracker</div>
          <h1 style={{ margin: 0, fontSize: '1.2rem', fontWeight: 700 }}>Opposition Intelligence</h1>
        </div>
        <button className="btn-ghost" onClick={() => setShowAdd(!showAdd)}>+ Add Opponent</button>
      </div>

      {showAdd && (
        <div className="card" style={{ marginBottom: '1.5rem' }}>
          <div className="section-title">Add Opponent</div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 10 }}>
            <div>
              <div className="label" style={{ marginBottom: 4 }}>Name *</div>
              <input value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))} placeholder="Full name" />
            </div>
            <div>
              <div className="label" style={{ marginBottom: 4 }}>Office</div>
              <input value={form.office} onChange={e => setForm(f => ({ ...f, office: e.target.value }))} placeholder="City Council District 7" />
            </div>
            <div>
              <div className="label" style={{ marginBottom: 4 }}>Party</div>
              <input value={form.party} onChange={e => setForm(f => ({ ...f, party: e.target.value }))} placeholder="Party affiliation" />
            </div>
            <div>
              <div className="label" style={{ marginBottom: 4 }}>Notes</div>
              <input value={form.notes} onChange={e => setForm(f => ({ ...f, notes: e.target.value }))} placeholder="Brief notes" />
            </div>
          </div>
          <button className="btn-primary" onClick={addOpponent}>Add</button>
        </div>
      )}

      {/* Opponent tabs */}
      <div style={{ display: 'flex', gap: 8, marginBottom: '1.5rem' }}>
        {opponents.map(opp => (
          <button
            key={opp.id}
            onClick={() => loadActivity(opp)}
            style={{
              padding: '0.5rem 1rem', borderRadius: 6,
              background: selected?.id === opp.id ? 'var(--surface-3)' : 'var(--surface-1)',
              border: `1px solid ${selected?.id === opp.id ? 'var(--accent)' : 'var(--border)'}`,
              color: 'var(--text-primary)', cursor: 'pointer', fontSize: '0.82rem', fontWeight: selected?.id === opp.id ? 600 : 400,
            }}
          >
            {opp.name}
            {opp.party && <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginLeft: 6 }}>({opp.party})</span>}
          </button>
        ))}
      </div>

      {selected && (
        <>
          <div className="card" style={{ marginBottom: '1.5rem', borderLeft: '3px solid rgba(239,68,68,0.4)' }}>
            <div style={{ fontWeight: 700, marginBottom: 4 }}>{selected.name}</div>
            <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>
              {selected.office} · {selected.party}
            </div>
            {selected.notes && (
              <p style={{ margin: '8px 0 0', fontSize: '0.78rem', color: 'var(--text-muted)', lineHeight: 1.4 }}>
                {selected.notes}
              </p>
            )}
          </div>

          {/* Summary row */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10, marginBottom: '1.5rem' }}>
            {[
              { label: 'Attacks', count: attacks.length, color: ACTIVITY_COLORS.attack },
              { label: 'Claims', count: claims.length, color: ACTIVITY_COLORS.claim },
              { label: 'Promises', count: promises.length, color: ACTIVITY_COLORS.promise },
              { label: 'Contradictions', count: contradictions.length, color: ACTIVITY_COLORS.contradiction },
            ].map(({ label, count, color }) => (
              <div key={label} className="card" style={{ textAlign: 'center' }}>
                <div style={{ fontSize: '1.5rem', fontWeight: 700, color, fontFamily: 'JetBrains Mono' }}>{count}</div>
                <div style={{ fontSize: '0.7rem', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{label}</div>
              </div>
            ))}
          </div>

          {/* Activities */}
          {activities.length === 0 && (
            <div style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>No activity recorded yet.</div>
          )}
          {activities.map(act => {
            const type = act.attack ? 'attack' : act.claim ? 'claim' : act.promise ? 'promise' : 'note'
            const color = ACTIVITY_COLORS[type] ?? '#8892a4'
            const text = act.attack || act.claim || act.promise
            return (
              <div key={act.id} className="card" style={{ marginBottom: 10, borderLeft: `3px solid ${color}33` }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 6 }}>
                  <span style={{ fontSize: '0.65rem', fontFamily: 'JetBrains Mono', color, letterSpacing: '0.06em', textTransform: 'uppercase' }}>
                    {type}
                  </span>
                  {act.repeated_theme && (
                    <span className="badge badge-ghost" style={{ fontSize: '0.6rem' }}>{act.repeated_theme}</span>
                  )}
                </div>
                {text && (
                  <p style={{ margin: '0 0 8px', fontSize: '0.85rem', color: 'var(--text-primary)', lineHeight: 1.4 }}>
                    "{text}"
                  </p>
                )}
                {act.contradiction_note && (
                  <div style={{ background: 'rgba(192,132,252,0.08)', border: '1px solid rgba(192,132,252,0.2)', borderRadius: 5, padding: '0.5rem 0.75rem' }}>
                    <div style={{ fontSize: '0.6rem', fontFamily: 'JetBrains Mono', color: '#c084fc', letterSpacing: '0.06em', marginBottom: 3 }}>
                      CONTRADICTION / FACT CHECK
                    </div>
                    <p style={{ margin: 0, fontSize: '0.78rem', color: '#e9d5ff', lineHeight: 1.5 }}>{act.contradiction_note}</p>
                  </div>
                )}
                {act.source_item && (
                  <div style={{ marginTop: 8, fontSize: '0.72rem', color: 'var(--text-muted)' }}>
                    Source: {act.source_item.source_name || 'Unknown'} · {act.source_item.source_type.replace('_', ' ')}
                  </div>
                )}
              </div>
            )
          })}
        </>
      )}
    </div>
  )
}
