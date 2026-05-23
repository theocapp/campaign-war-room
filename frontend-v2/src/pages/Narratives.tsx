import { Edit2, Plus, RefreshCw, Search, Sparkles, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api } from '@/api/client'
import type { NarrativeFrame, OwnerType } from '@/api/types'

const C = {
  bg1: '#121212', bg2: '#171717', bg3: '#262626',
  border: '#434343', borderBright: '#555',
  text1: '#fff', text2: '#a1a1a1', text3: '#666',
  candidate: '#0059c2', opponent: '#d71913', media: '#a1a1a1',
  accent: '#ffbf00',
}

function ownerLabel(t: OwnerType) {
  return t === 'candidate' ? 'Our Frame' : t === 'opponent' ? 'Opponent' : 'Media'
}

function ownerColor(t: OwnerType) {
  return t === 'candidate' ? C.candidate : t === 'opponent' ? C.opponent : C.media
}

interface AddFrameModalProps {
  onClose: () => void
  onCreated: (f: NarrativeFrame) => void
}

function AddFrameModal({ onClose, onCreated }: AddFrameModalProps) {
  const [name, setName] = useState('')
  const [desc, setDesc] = useState('')
  const [ownerType, setOwnerType] = useState<OwnerType>('candidate')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim()) return
    setSaving(true)
    setError('')
    try {
      const frame = await api.createFrame({ name: name.trim(), description: desc.trim(), owner_type: ownerType })
      onCreated(frame)
      onClose()
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Failed to create frame')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-box" onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
          <div style={{ fontSize: 17, fontWeight: 700, color: C.text1 }}>Add Narrative Frame</div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: C.text2 }}>
            <X size={18} />
          </button>
        </div>
        <form onSubmit={submit}>
          <div style={{ marginBottom: 14 }}>
            <label className="section-label" style={{ display: 'block', marginBottom: 6 }}>FRAME NAME *</label>
            <input className="input" value={name} onChange={e => setName(e.target.value)} placeholder="e.g. Bresnahan's Healthcare Record" required />
          </div>
          <div style={{ marginBottom: 14 }}>
            <label className="section-label" style={{ display: 'block', marginBottom: 6 }}>DESCRIPTION</label>
            <textarea className="input" value={desc} onChange={e => setDesc(e.target.value)} placeholder="What narrative arc does this frame track?" rows={3} style={{ resize: 'vertical' }} />
          </div>
          <div style={{ marginBottom: 20 }}>
            <label className="section-label" style={{ display: 'block', marginBottom: 8 }}>OWNER TYPE</label>
            <div style={{ display: 'flex', gap: 8 }}>
              {(['candidate', 'opponent', 'media'] as OwnerType[]).map(t => (
                <button
                  key={t}
                  type="button"
                  onClick={() => setOwnerType(t)}
                  className={`badge-${t}`}
                  style={{
                    fontSize: 12, letterSpacing: '0.06em', padding: '7px 14px', borderRadius: 6,
                    cursor: 'pointer', fontWeight: ownerType === t ? 700 : 400,
                    outline: ownerType === t ? `2px solid ${ownerColor(t)}` : 'none', outlineOffset: 2,
                  }}
                >
                  {ownerLabel(t)}
                </button>
              ))}
            </div>
          </div>
          {error && <div style={{ color: '#f87171', fontSize: 12, marginBottom: 12 }}>{error}</div>}
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <button type="button" onClick={onClose} className="btn btn-ghost">Cancel</button>
            <button type="submit" disabled={saving} className="btn btn-primary">
              {saving ? 'Creating...' : 'Create Frame'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

function EditFrameModal({ frame, onClose, onUpdated }: {
  frame: NarrativeFrame
  onClose: () => void
  onUpdated: (f: NarrativeFrame) => void
}) {
  const [name, setName] = useState(frame.name)
  const [desc, setDesc] = useState(frame.description)
  const [ownerType, setOwnerType] = useState<OwnerType>(frame.owner_type)
  const [saving, setSaving] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setSaving(true)
    try {
      const updated = await api.updateFrame(frame.id, { name, description: desc, owner_type: ownerType })
      onUpdated(updated)
      onClose()
    } catch { /* silently fail */ } finally {
      setSaving(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-box" onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
          <div style={{ fontSize: 17, fontWeight: 700, color: C.text1 }}>Edit Frame</div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: C.text2 }}>
            <X size={18} />
          </button>
        </div>
        <form onSubmit={submit}>
          <div style={{ marginBottom: 14 }}>
            <label className="section-label" style={{ display: 'block', marginBottom: 6 }}>FRAME NAME</label>
            <input className="input" value={name} onChange={e => setName(e.target.value)} />
          </div>
          <div style={{ marginBottom: 14 }}>
            <label className="section-label" style={{ display: 'block', marginBottom: 6 }}>DESCRIPTION</label>
            <textarea className="input" value={desc} onChange={e => setDesc(e.target.value)} rows={3} style={{ resize: 'vertical' }} />
          </div>
          <div style={{ marginBottom: 20 }}>
            <label className="section-label" style={{ display: 'block', marginBottom: 8 }}>OWNER TYPE</label>
            <div style={{ display: 'flex', gap: 8 }}>
              {(['candidate', 'opponent', 'media'] as OwnerType[]).map(t => (
                <button
                  key={t}
                  type="button"
                  onClick={() => setOwnerType(t)}
                  className={`badge-${t}`}
                  style={{
                    fontSize: 12, padding: '7px 14px', borderRadius: 6, cursor: 'pointer',
                    outline: ownerType === t ? `2px solid ${ownerColor(t)}` : 'none', outlineOffset: 2,
                  }}
                >
                  {ownerLabel(t)}
                </button>
              ))}
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <button type="button" onClick={onClose} className="btn btn-ghost">Cancel</button>
            <button type="submit" disabled={saving} className="btn btn-primary">{saving ? 'Saving...' : 'Save Changes'}</button>
          </div>
        </form>
      </div>
    </div>
  )
}

function FrameCard({ frame, onEdit }: {
  frame: NarrativeFrame
  onEdit: (f: NarrativeFrame) => void
}) {
  const oc = ownerColor(frame.owner_type)

  // Always-visible 30-day sparkline. Backend supplies sparse data (only days
  // with matches), so fill in zero-count days for the last 30 so the bars
  // are positioned on a stable calendar axis.
  const activity = (() => {
    const map = new Map<string, number>()
    for (const p of frame.activity_30d ?? []) map.set(p.date, p.count)
    const out: { date: string; count: number }[] = []
    const today = new Date()
    for (let i = 29; i >= 0; i--) {
      const d = new Date(today)
      d.setDate(d.getDate() - i)
      const key = d.toISOString().slice(0, 10)
      out.push({ date: key, count: map.get(key) ?? 0 })
    }
    return out
  })()

  const hasAny = activity.some(p => p.count > 0)

  return (
    <div
      style={{
        marginBottom: 8,
        background: C.bg2, border: `1px solid ${C.border}`,
        borderRadius: '0.625rem', overflow: 'hidden',
      }}
    >
      <Link
        to={`/narratives/${frame.id}`}
        style={{ textDecoration: 'none', display: 'block', color: 'inherit' }}
      >
        {/* Title row — title + edit only. No chevron; clicking the card opens detail. */}
        <div style={{ padding: '14px 18px 8px', display: 'flex', alignItems: 'flex-start', gap: 10 }}>
          <div style={{ flex: 1, minWidth: 0, fontSize: 16, fontWeight: 700, color: C.text1, lineHeight: 1.3 }}>
            {frame.name}
          </div>
          <button
            onClick={e => { e.preventDefault(); e.stopPropagation(); onEdit(frame) }}
            style={{ background: 'none', border: 'none', cursor: 'pointer', color: C.text3, padding: 4, borderRadius: 4, flexShrink: 0 }}
            title="Edit frame"
          >
            <Edit2 size={13} />
          </button>
        </div>

        {/* Always-visible 30-day volume chart with axis labels + tooltip. */}
        <div style={{ padding: '0 14px 10px' }}>
          <div className="section-label" style={{
            marginBottom: 4, fontSize: 9, color: C.text3, letterSpacing: '0.08em',
          }}>
            VOLUME · LAST 30 DAYS
          </div>
          {hasAny ? (
            <ResponsiveContainer width="100%" height={90}>
              <BarChart data={activity} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 9, fill: C.text3, fontFamily: "'IBM Plex Mono', monospace" }}
                  tickFormatter={(v: string) => {
                    // "2026-05-18" → "May 18"
                    const d = new Date(v + 'T00:00:00')
                    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
                  }}
                  minTickGap={32}
                  axisLine={{ stroke: C.border }}
                  tickLine={false}
                />
                <YAxis
                  tick={{ fontSize: 9, fill: C.text3, fontFamily: "'IBM Plex Mono', monospace" }}
                  axisLine={false}
                  tickLine={false}
                  allowDecimals={false}
                  width={24}
                />
                <Tooltip
                  contentStyle={{
                    background: '#0e1422', border: `1px solid ${C.border}`,
                    borderRadius: 3, fontSize: 11, color: C.text1, padding: '4px 8px',
                  }}
                  labelStyle={{ color: C.text3, fontSize: 10 }}
                  cursor={{ fill: 'rgba(255,255,255,0.05)' }}
                  labelFormatter={(v: string) => {
                    const d = new Date(v + 'T00:00:00')
                    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
                  }}
                  formatter={(v: number) => [`${v}`, 'articles']}
                />
                <Bar dataKey="count" fill={oc} radius={[1, 1, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <div style={{
              height: 90, display: 'flex', alignItems: 'center', justifyContent: 'center',
              fontSize: 11, color: C.text3, fontFamily: "'IBM Plex Mono', monospace",
            }}>
              No activity · last 30 days
            </div>
          )}
        </div>
      </Link>
    </div>
  )
}

/** Title-cased surname from "First Last" or FEC "LAST, FIRST" format. */
function lastName(raw?: string): string {
  if (!raw) return ''
  const t = raw.trim()
  const last = (t.includes(',') ? t.split(',')[0] : t.split(/\s+/).pop() || '').trim()
  return last ? last[0].toUpperCase() + last.slice(1).toLowerCase() : ''
}

export function Narratives() {
  const [frames, setFrames] = useState<NarrativeFrame[]>([])
  const [loading, setLoading] = useState(true)
  const [suggesting, setSuggesting] = useState(false)
  const [search, setSearch] = useState('')
  const [filterOwner, setFilterOwner] = useState<'all' | OwnerType>('all')
  const [filterStage, setFilterStage] = useState<string>('all')
  const [sortBy, setSortBy] = useState<'relevance' | 'volume' | 'newest'>('relevance')
  const [showAdd, setShowAdd] = useState(false)
  const [editFrame, setEditFrame] = useState<NarrativeFrame | null>(null)
  const [candidateName, setCandidateName] = useState('')
  const [opponentName, setOpponentName] = useState('')

  useEffect(() => {
    api.narrativeFrames().then(setFrames).catch(() => {}).finally(() => setLoading(false))
    api.campaign().then(c => setCandidateName(lastName(c.candidate_name))).catch(() => {})
    api.opponents().then(o => { if (o[0]) setOpponentName(lastName(o[0].name)) }).catch(() => {})
  }, [])

  const columns: { type: OwnerType; label: string }[] = [
    { type: 'candidate', label: candidateName ? `Favors ${candidateName}` : 'Favors Candidate' },
    { type: 'opponent', label: opponentName ? `Favors ${opponentName}` : 'Favors Opponent' },
    { type: 'media', label: 'Media' },
  ]

  const filtered = frames
    .filter(f => {
      if (filterOwner !== 'all' && f.owner_type !== filterOwner) return false
      if (filterStage !== 'all' && f.stage !== filterStage) return false
      if (search && !f.name.toLowerCase().includes(search.toLowerCase())) return false
      return true
    })
    .sort((a, b) => {
      if (sortBy === 'volume') return b.mentions_total - a.mentions_total
      if (sortBy === 'newest') {
        return new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
      }
      // relevance: most recent activity first, then total volume
      if (b.mentions_this_week !== a.mentions_this_week) {
        return b.mentions_this_week - a.mentions_this_week
      }
      return b.mentions_total - a.mentions_total
    })

  async function suggestFrames() {
    setSuggesting(true)
    try {
      const result = await api.suggestFrames()
      if (result.suggestions?.length) setFrames(prev => [...prev, ...result.suggestions])
    } catch { /* silently fail */ } finally {
      setSuggesting(false)
    }
  }

  return (
    <div style={{ minHeight: '100%', background: C.bg1 }}>
      {/* Filter + action toolbar */}
      <div style={{
        padding: '16px 28px', borderBottom: `1px solid ${C.border}`,
        background: C.bg1,
      }}>
        {/* Filters + actions */}
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <div style={{ position: 'relative', flex: '0 0 200px' }}>
            <Search size={12} style={{ position: 'absolute', left: 10, top: '50%', transform: 'translateY(-50%)', color: C.text3 }} />
            <input
              className="input"
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search frames..."
              style={{ paddingLeft: 30, fontSize: 13 }}
            />
          </div>
          <select className="input" value={filterOwner} onChange={e => setFilterOwner(e.target.value as 'all' | OwnerType)} style={{ width: 140 }}>
            <option value="all">All owners</option>
            <option value="candidate">Our frames</option>
            <option value="opponent">Opponent</option>
            <option value="media">Media</option>
          </select>
          <select className="input" value={filterStage} onChange={e => setFilterStage(e.target.value)} style={{ width: 140 }}>
            <option value="all">All stages</option>
            <option value="mainstream">Mainstream</option>
            <option value="spreading">Spreading</option>
            <option value="emerging">Emerging</option>
            <option value="fading">Fading</option>
            <option value="dormant">Dormant</option>
          </select>
          <select className="input" value={sortBy} onChange={e => setSortBy(e.target.value as 'relevance' | 'volume' | 'newest')} style={{ width: 150 }}>
            <option value="relevance">Sort: Relevance</option>
            <option value="volume">Sort: Volume</option>
            <option value="newest">Sort: Newest</option>
          </select>
          {(filterOwner !== 'all' || filterStage !== 'all' || search) && (
            <button
              onClick={() => { setFilterOwner('all'); setFilterStage('all'); setSearch('') }}
              style={{ background: 'none', border: 'none', color: C.text2, cursor: 'pointer', fontSize: 13, display: 'flex', alignItems: 'center', gap: 4 }}
            >
              <RefreshCw size={12} /> Clear
            </button>
          )}
          <div style={{ display: 'flex', gap: 8, marginLeft: 'auto' }}>
            <button onClick={suggestFrames} disabled={suggesting} className="btn btn-ghost">
              <Sparkles size={13} style={suggesting ? { animation: 'spin 1s linear infinite' } : {}} />
              {suggesting ? 'Suggesting...' : 'Suggest Frames'}
            </button>
            <button onClick={() => setShowAdd(true)} className="btn btn-primary">
              <Plus size={13} />
              Add Frame
            </button>
          </div>
        </div>
      </div>

      {/* Frame columns */}
      <div style={{ padding: '16px 28px' }}>
        {loading && (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16 }}>
            {columns.map(col => (
              <div key={col.type}>
                {Array.from({ length: 3 }).map((_, i) => (
                  <div key={i} className="skeleton" style={{ height: 100, marginBottom: 8 }} />
                ))}
              </div>
            ))}
          </div>
        )}

        {!loading && filtered.length === 0 && (
          <div style={{ textAlign: 'center', padding: '60px 0', color: C.text3 }}>
            <div style={{ fontSize: 18, fontWeight: 600, color: C.text2, marginBottom: 8 }}>No frames match your filters</div>
            <div style={{ fontSize: 13 }}>Try adjusting your search or add a new frame.</div>
          </div>
        )}

        {!loading && filtered.length > 0 && (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 16, alignItems: 'start' }}>
            {columns.map(col => {
              const colFrames = filtered.filter(f => f.owner_type === col.type)
              const oc = ownerColor(col.type)
              return (
                <div key={col.type}>
                  {/* Column header — sticks to the top of the scroll area
                      so the column name stays visible as you scroll the cards. */}
                  <div style={{
                    display: 'flex', alignItems: 'center', gap: 8,
                    padding: '8px 4px 12px', marginBottom: 4,
                    borderBottom: `2px solid ${oc}`,
                    position: 'sticky', top: 0, zIndex: 5,
                    background: C.bg1,
                  }}>
                    <span style={{ width: 9, height: 9, borderRadius: '50%', background: oc, display: 'inline-block' }} />
                    <span style={{ fontSize: 14, fontWeight: 800, color: C.text1, letterSpacing: '-0.01em' }}>
                      {col.label}
                    </span>
                    <span style={{ fontSize: 12, fontWeight: 700, color: C.text3 }}>
                      {colFrames.length}
                    </span>
                  </div>

                  {colFrames.length === 0 ? (
                    <div style={{ padding: '32px 0', textAlign: 'center', color: C.text3, fontSize: 12 }}>
                      No frames
                    </div>
                  ) : (
                    colFrames.map(frame => (
                      <FrameCard
                        key={frame.id}
                        frame={frame}
                        onEdit={setEditFrame}
                      />
                    ))
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>

      {showAdd && (
        <AddFrameModal onClose={() => setShowAdd(false)} onCreated={f => setFrames(prev => [...prev, f])} />
      )}
      {editFrame && (
        <EditFrameModal
          frame={editFrame}
          onClose={() => setEditFrame(null)}
          onUpdated={updated => setFrames(prev => prev.map(f => f.id === updated.id ? updated : f))}
        />
      )}
    </div>
  )
}
