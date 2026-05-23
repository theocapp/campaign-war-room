import { ChevronDown, ChevronRight, Plus, User, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '@/api/client'
import type { Opponent, OpponentActivity } from '@/api/types'

type Tab = 'attacks' | 'claims' | 'promises'

function formatDate(iso: string) {
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
}

function ActivityCard({ item, type }: { item: OpponentActivity; type: Tab }) {
  const text = type === 'attacks' ? item.attack : type === 'claims' ? item.claim : item.promise
  if (!text) return null
  const colors = {
    attacks: { border: '#c91c1c', dim: 'rgba(201,28,28,0.06)', text: '#f05050', label: 'ATTACK' },
    claims: { border: '#1c2a3f', dim: 'rgba(20,32,46,0.4)', text: '#7d8fa8', label: 'CLAIM' },
    promises: { border: '#c47800', dim: 'rgba(196,120,0,0.06)', text: '#f0a020', label: 'PROMISE' },
  }
  const c = colors[type]
  return (
    <div style={{
      padding: '12px 14px',
      background: c.dim,
      border: `1px solid ${c.border}33`,
      borderLeft: `3px solid ${c.border}`,
      borderRadius: 3,
      marginBottom: 8,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 13, color: '#dce7f3', lineHeight: 1.45, marginBottom: 8 }}>
            "{text}"
          </div>
          {item.contradiction_note && (
            <div style={{
              fontSize: 11,
              color: '#c47800',
              fontStyle: 'italic',
              marginBottom: 6,
              padding: '4px 8px',
              background: 'rgba(196,120,0,0.06)',
              borderRadius: 2,
            }}>
              ⚠ CONTRADICTS: {item.contradiction_note}
            </div>
          )}
          {item.repeated_theme && (
            <div style={{
              fontSize: 10,
              color: '#7d8fa8',
              fontFamily: "'IBM Plex Mono', monospace",
              letterSpacing: '0.06em',
            }}>
              THEME: {item.repeated_theme}
            </div>
          )}
        </div>
        <div style={{ textAlign: 'right', flexShrink: 0 }}>
          <div style={{
            fontFamily: "'IBM Plex Mono', monospace",
            fontSize: 9,
            color: c.text,
            letterSpacing: '0.1em',
            marginBottom: 4,
          }}>
            {c.label}
          </div>
          <div style={{ fontSize: 10, color: '#3d4f63', fontFamily: "'IBM Plex Mono', monospace" }}>
            {formatDate(item.first_seen_at)}
          </div>
          {item.first_seen_at !== item.last_seen_at && (
            <div style={{ fontSize: 10, color: '#3d4f63', fontFamily: "'IBM Plex Mono', monospace" }}>
              → {formatDate(item.last_seen_at)}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function OpponentRow({ opponent }: { opponent: Opponent }) {
  const [expanded, setExpanded] = useState(false)
  const [activity, setActivity] = useState<OpponentActivity[]>([])
  const [loadingActivity, setLoadingActivity] = useState(false)
  const [activeTab, setActiveTab] = useState<Tab>('attacks')

  async function loadActivity() {
    if (activity.length || loadingActivity) return
    setLoadingActivity(true)
    try {
      const data = await api.opponentActivity(opponent.id)
      setActivity(data)
    } catch { /* silently fail */ } finally {
      setLoadingActivity(false)
    }
  }

  function handleExpand() {
    setExpanded(e => !e)
    if (!expanded) loadActivity()
  }

  const attacks = activity.filter(a => a.attack)
  const claims = activity.filter(a => a.claim)
  const promises = activity.filter(a => a.promise)

  const tabItems = activeTab === 'attacks' ? attacks : activeTab === 'claims' ? claims : promises

  return (
    <div className="card" style={{ marginBottom: 8 }}>
      {/* Header */}
      <div
        onClick={handleExpand}
        style={{
          padding: '16px 20px',
          display: 'flex',
          alignItems: 'center',
          gap: 14,
          cursor: 'pointer',
        }}
      >
        <div style={{
          width: 36,
          height: 36,
          borderRadius: '50%',
          background: '#14202e',
          border: '1px solid #2a3f5c',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          flexShrink: 0,
        }}>
          <User size={16} style={{ color: '#7d8fa8' }} />
        </div>
        <div style={{ flex: 1 }}>
          <div style={{
            fontFamily: "'Barlow Condensed', sans-serif",
            fontSize: 17,
            fontWeight: 700,
            color: '#dce7f3',
            letterSpacing: '0.02em',
            marginBottom: 2,
          }}>
            {opponent.name}
          </div>
          <div style={{
            color: '#7d8fa8',
            fontFamily: "'IBM Plex Mono', monospace",
            fontSize: 10,
            letterSpacing: '0.06em',
          }}>
            {[opponent.party, opponent.office].filter(Boolean).join(' · ')}
          </div>
        </div>
        <div style={{ display: 'flex', gap: 14, alignItems: 'center' }}>
          {attacks.length > 0 && (
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 18, fontWeight: 600, color: '#f05050', lineHeight: 1 }}>
                {attacks.length}
              </div>
              <div className="section-label" style={{ marginTop: 2 }}>ATTACKS</div>
            </div>
          )}
          {claims.length > 0 && (
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 18, fontWeight: 600, color: '#7d8fa8', lineHeight: 1 }}>
                {claims.length}
              </div>
              <div className="section-label" style={{ marginTop: 2 }}>CLAIMS</div>
            </div>
          )}
          {promises.length > 0 && (
            <div style={{ textAlign: 'center' }}>
              <div style={{ fontFamily: "'IBM Plex Mono', monospace", fontSize: 18, fontWeight: 600, color: '#f0a020', lineHeight: 1 }}>
                {promises.length}
              </div>
              <div className="section-label" style={{ marginTop: 2 }}>PROMISES</div>
            </div>
          )}
          {!loadingActivity && activity.length === 0 && expanded && (
            <span style={{ fontSize: 11, color: '#3d4f63' }}>No activity yet</span>
          )}
          <div style={{ color: '#3d4f63' }}>
            {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
          </div>
        </div>
      </div>

      {/* Expanded activity */}
      {expanded && (
        <div style={{ borderTop: '1px solid #1c2a3f', padding: '16px 20px' }}>
          {loadingActivity && (
            <div className="skeleton" style={{ height: 80, borderRadius: 3 }} />
          )}
          {!loadingActivity && activity.length > 0 && (
            <>
              {/* Tabs */}
              <div style={{ display: 'flex', gap: 2, marginBottom: 16, borderBottom: '1px solid #1c2a3f', paddingBottom: 0 }}>
                {([
                  { key: 'attacks', label: `ATTACKS (${attacks.length})`, color: '#f05050' },
                  { key: 'claims', label: `CLAIMS (${claims.length})`, color: '#7d8fa8' },
                  { key: 'promises', label: `PROMISES (${promises.length})`, color: '#f0a020' },
                ] as const).map(tab => (
                  <button
                    key={tab.key}
                    onClick={() => setActiveTab(tab.key)}
                    style={{
                      background: 'none',
                      border: 'none',
                      borderBottom: activeTab === tab.key ? `2px solid ${tab.color}` : '2px solid transparent',
                      padding: '6px 14px',
                      cursor: 'pointer',
                      fontFamily: "'IBM Plex Mono', monospace",
                      fontSize: 10,
                      letterSpacing: '0.1em',
                      color: activeTab === tab.key ? tab.color : '#3d4f63',
                      marginBottom: -1,
                    }}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>
              {tabItems.length === 0 ? (
                <div style={{ color: '#3d4f63', fontSize: 12, textAlign: 'center', padding: '20px 0' }}>
                  No {activeTab} recorded
                </div>
              ) : (
                tabItems.map((item, i) => (
                  <ActivityCard key={i} item={item} type={activeTab} />
                ))
              )}
            </>
          )}
          {!loadingActivity && activity.length === 0 && (
            <div style={{ color: '#3d4f63', fontSize: 12, textAlign: 'center', padding: '20px 0' }}>
              No activity data available yet. Ingest more sources to track opponent activity.
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function AddOpponentModal({ onClose, onCreated }: { onClose: () => void; onCreated: (o: Opponent) => void }) {
  const [name, setName] = useState('')
  const [office, setOffice] = useState('')
  const [party, setParty] = useState('')
  const [saving, setSaving] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setSaving(true)
    try {
      const opp = await api.createOpponent({ name, office, party })
      onCreated(opp)
      onClose()
    } catch { /* silently fail */ } finally {
      setSaving(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-box" onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
          <div style={{ fontFamily: "'Barlow Condensed', sans-serif", fontSize: 18, fontWeight: 700, color: '#dce7f3', letterSpacing: '0.06em' }}>
            ADD OPPONENT
          </div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: '#7d8fa8' }}><X size={18} /></button>
        </div>
        <form onSubmit={submit}>
          <div style={{ marginBottom: 14 }}>
            <label className="section-label" style={{ display: 'block', marginBottom: 6 }}>NAME *</label>
            <input className="input" value={name} onChange={e => setName(e.target.value)} placeholder="e.g. Rob Bresnahan" required />
          </div>
          <div style={{ marginBottom: 14 }}>
            <label className="section-label" style={{ display: 'block', marginBottom: 6 }}>OFFICE</label>
            <input className="input" value={office} onChange={e => setOffice(e.target.value)} placeholder="e.g. U.S. House PA-08" />
          </div>
          <div style={{ marginBottom: 20 }}>
            <label className="section-label" style={{ display: 'block', marginBottom: 6 }}>PARTY</label>
            <input className="input" value={party} onChange={e => setParty(e.target.value)} placeholder="e.g. Republican" />
          </div>
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <button type="button" onClick={onClose} className="btn btn-ghost">Cancel</button>
            <button type="submit" disabled={saving} className="btn btn-primary">{saving ? 'Adding...' : 'Add Opponent'}</button>
          </div>
        </form>
      </div>
    </div>
  )
}

export function Opponents() {
  const [opponents, setOpponents] = useState<Opponent[]>([])
  const [loading, setLoading] = useState(true)
  const [showAdd, setShowAdd] = useState(false)

  useEffect(() => {
    api.opponents().then(setOpponents).catch(() => {}).finally(() => setLoading(false))
  }, [])

  return (
    <div style={{ minHeight: '100vh' }}>
      <div style={{
        padding: '14px 28px',
        borderBottom: '1px solid #1c2a3f',
        background: 'rgba(6,8,16,0.8)',
        backdropFilter: 'blur(8px)',
        position: 'sticky',
        top: 0,
        zIndex: 10,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
      }}>
        <div>
          <div style={{ fontFamily: "'Barlow Condensed', sans-serif", fontSize: 20, fontWeight: 900, letterSpacing: '0.06em', color: '#dce7f3' }}>
            OPPONENT TRACKER
          </div>
          <div className="section-label" style={{ marginTop: 2 }}>
            {loading ? '...' : `${opponents.length} OPPONENTS MONITORED`}
          </div>
        </div>
        <button onClick={() => setShowAdd(true)} className="btn btn-primary">
          <Plus size={13} />
          Add Opponent
        </button>
      </div>

      <div style={{ padding: '20px 28px', maxWidth: 900, margin: '0 auto' }}>
        {loading && Array.from({ length: 3 }).map((_, i) => (
          <div key={i} className="skeleton" style={{ height: 70, borderRadius: 4, marginBottom: 10 }} />
        ))}

        {!loading && opponents.length === 0 && (
          <div style={{ textAlign: 'center', padding: '80px 0', color: '#3d4f63' }}>
            <User size={48} style={{ margin: '0 auto 16px', opacity: 0.3 }} />
            <div style={{ fontFamily: "'Barlow Condensed', sans-serif", fontSize: 20, marginBottom: 8 }}>No opponents tracked yet</div>
            <div style={{ fontSize: 13 }}>Add your opponent to start monitoring their activity.</div>
          </div>
        )}

        {opponents.map(opp => <OpponentRow key={opp.id} opponent={opp} />)}
      </div>

      {showAdd && (
        <AddOpponentModal
          onClose={() => setShowAdd(false)}
          onCreated={o => setOpponents(prev => [...prev, o])}
        />
      )}
    </div>
  )
}
