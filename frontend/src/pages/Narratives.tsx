import { useState, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { NarrativeFrameWithCounts } from '../api/types'
import FilterChips from '../components/FilterChips'
import { useEffect } from 'react'

const OWNER_LABELS: Record<string, string> = {
  candidate: 'Our message',
  opponent: 'Opponent attack',
  media: 'Media theme',
}

const OWNER_COLORS: Record<string, string> = {
  candidate: '#22c55e',
  opponent: '#ef4444',
  media: '#64748b',
}

const STAGE_COLORS: Record<string, string> = {
  emerging:   '#3b82f6',
  spreading:  '#22c55e',
  mainstream: '#8b5cf6',
  active:     '#94a3b8',
  fading:     '#f59e0b',
  dormant:    '#475569',
}

const STAGE_LABELS: Record<string, string> = {
  emerging:   'Emerging',
  spreading:  'Spreading',
  mainstream: 'Mainstream',
  active:     'Active',
  fading:     'Fading',
  dormant:    'Dormant',
}

function fmtTiers(tiers: { national: number; regional: number; local: number; blog: number; social: number }): string | null {
  const parts: string[] = []
  if (tiers.national > 0) parts.push(`${tiers.national} national`)
  if (tiers.regional > 0) parts.push(`${tiers.regional} regional`)
  if (tiers.local > 0) parts.push(`${tiers.local} local`)
  if (tiers.blog > 0) parts.push(`${tiers.blog} blog`)
  if (tiers.social > 0) parts.push(`${tiers.social} social`)
  return parts.length > 0 ? parts.join(' · ') : null
}

function analyticalSentence(frame: NarrativeFrameWithCounts): string {
  const { stage, mentions_this_week: tw, mentions_last_week: lw, mentions_total: total,
    unique_outlets_this_week: uow, unique_outlets_last_week: uolw, days_active_last_7: days } = frame
  const o = (n: number) => n === 1 ? 'outlet' : 'outlets'
  const s = (n: number) => n === 1 ? 'story' : 'stories'
  const hasOutlets = uow > 0 || uolw > 0

  switch (stage) {
    case 'dormant':
      return 'No coverage in the last 2 weeks.'
    case 'emerging':
      if (hasOutlets) return `Just appearing — picked up by ${uow} ${o(uow)}, ${days} of the last 7 days.`
      return `Just appearing — ${total} ${s(total)} so far.`
    case 'spreading':
      if (hasOutlets) return `Spreading — ${uow} ${o(uow)} this week vs ${uolw} last week, active ${days} of last 7 days.`
      return `Growing fast — ${tw} ${s(tw)} this week, up from ${lw} last week.`
    case 'fading':
      if (hasOutlets) return `Fading — down to ${uow} ${o(uow)} this week from ${uolw} last week.`
      return `Coverage declining — ${tw} ${s(tw)} this week, down from ${lw} last week.`
    case 'mainstream':
      if (hasOutlets) return `Sustained coverage — ${uow} ${o(uow)} this week, active ${days} of last 7 days.`
      return `Established — ${tw} ${s(tw)} this week, ${total} total.`
    default:
      if (hasOutlets) return `${uow} ${o(uow)} this week${uolw > 0 ? ` vs ${uolw} last week` : ''}, active ${days} of last 7 days.`
      return `${tw} ${s(tw)} this week${lw > 0 ? `, ${lw} last week` : ''}.`
  }
}

function FrameCard({
  frame,
  onDelete,
  onEdit,
}: {
  frame: NarrativeFrameWithCounts
  onDelete: (id: number) => void
  onEdit: (frame: NarrativeFrameWithCounts) => void
}) {
  const accentColor = OWNER_COLORS[frame.owner_type] || '#64748b'
  const stageColor = STAGE_COLORS[frame.stage] || '#94a3b8'

  return (
    <Link
      to={`/frames/${frame.id}`}
      style={{ textDecoration: 'none', display: 'block' }}
    >
      <div style={{
        background: 'var(--surface, #1e293b)',
        border: '1px solid var(--border, #334155)',
        borderLeft: `3px solid ${accentColor}`,
        borderRadius: 8,
        padding: '14px 16px',
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        cursor: 'pointer',
      }}
        onMouseEnter={e => (e.currentTarget.style.borderColor = accentColor)}
        onMouseLeave={e => { e.currentTarget.style.borderColor = 'var(--border, #334155)'; e.currentTarget.style.borderLeftColor = accentColor }}
      >
        {/* Top row: owner label + edit/remove buttons */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ fontSize: 10, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em', color: accentColor }}>
            {OWNER_LABELS[frame.owner_type] || frame.owner_type}
            {frame.source === 'llm' && <span style={{ color: '#94a3b8', fontWeight: 400 }}> · auto-suggested</span>}
          </span>
          <div style={{ display: 'flex', gap: 6 }}>
            <button
              onClick={e => { e.preventDefault(); e.stopPropagation(); onEdit(frame) }}
              style={{ background: 'transparent', border: '1px solid var(--border, #334155)', color: 'var(--text-muted, #94a3b8)', borderRadius: 4, padding: '2px 8px', cursor: 'pointer', fontSize: 12 }}
            >Edit</button>
            <button
              onClick={e => { e.preventDefault(); e.stopPropagation(); onDelete(frame.id) }}
              style={{ background: 'transparent', border: '1px solid #ef4444', color: '#ef4444', borderRadius: 4, padding: '2px 8px', cursor: 'pointer', fontSize: 12 }}
            >Remove</button>
          </div>
        </div>

        {/* Frame name */}
        <div style={{ fontWeight: 600, fontSize: 16, color: 'var(--text, #f1f5f9)' }}>{frame.name}</div>

        {/* Description */}
        {frame.description && (
          <div style={{ fontSize: 13, color: 'var(--text-muted, #94a3b8)', lineHeight: 1.5 }}>{frame.description}</div>
        )}

        {/* Stage chip + analytical sentence */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 2 }}>
          <span style={{
            padding: '2px 8px', borderRadius: 10, flexShrink: 0,
            background: stageColor + '22',
            border: `1px solid ${stageColor}`,
            color: stageColor, fontSize: 10, fontWeight: 700,
            letterSpacing: '0.04em', textTransform: 'uppercase',
          }}>{STAGE_LABELS[frame.stage]}</span>
          <span style={{ fontSize: 13, color: 'var(--text-muted, #94a3b8)' }}>
            {analyticalSentence(frame)}
          </span>
        </div>
      </div>
    </Link>
  )
}

function AddFrameModal({ onClose, onSave }: { onClose: () => void; onSave: (name: string, description: string, owner_type: string) => Promise<void> }) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [ownerType, setOwnerType] = useState('candidate')
  const [saving, setSaving] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim()) return
    setSaving(true)
    await onSave(name.trim(), description.trim(), ownerType)
    setSaving(false)
  }

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 }}>
      <div style={{ background: 'var(--surface, #1e293b)', border: '1px solid var(--border, #334155)', borderRadius: 10, padding: 24, width: 420, maxWidth: '90vw' }}>
        <h3 style={{ margin: '0 0 16px', color: 'var(--text, #f1f5f9)' }}>Add Narrative Frame</h3>
        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div>
            <label style={{ fontSize: 12, color: 'var(--text-muted, #94a3b8)', display: 'block', marginBottom: 4 }}>Frame name *</label>
            <input
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="e.g. Healthcare Access"
              style={{ width: '100%', padding: '8px 10px', background: 'var(--bg, #0f172a)', border: '1px solid var(--border, #334155)', borderRadius: 6, color: 'var(--text, #f1f5f9)', fontSize: 14, boxSizing: 'border-box' }}
              required
            />
          </div>
          <div>
            <label style={{ fontSize: 12, color: 'var(--text-muted, #94a3b8)', display: 'block', marginBottom: 4 }}>Description</label>
            <textarea
              value={description}
              onChange={e => setDescription(e.target.value)}
              placeholder="One sentence: what this frame covers and why it matters."
              rows={2}
              style={{ width: '100%', padding: '8px 10px', background: 'var(--bg, #0f172a)', border: '1px solid var(--border, #334155)', borderRadius: 6, color: 'var(--text, #f1f5f9)', fontSize: 14, resize: 'vertical', boxSizing: 'border-box' }}
            />
          </div>
          <div>
            <label style={{ fontSize: 12, color: 'var(--text-muted, #94a3b8)', display: 'block', marginBottom: 4 }}>Owner</label>
            <select
              value={ownerType}
              onChange={e => setOwnerType(e.target.value)}
              style={{ width: '100%', padding: '8px 10px', background: 'var(--bg, #0f172a)', border: '1px solid var(--border, #334155)', borderRadius: 6, color: 'var(--text, #f1f5f9)', fontSize: 14, boxSizing: 'border-box' }}
            >
              <option value="candidate">Our message</option>
              <option value="opponent">Opponent attack</option>
              <option value="media">Media theme</option>
            </select>
          </div>
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 8 }}>
            <button type="button" onClick={onClose} style={{ padding: '8px 16px', background: 'transparent', border: '1px solid var(--border, #334155)', borderRadius: 6, color: 'var(--text-muted, #94a3b8)', cursor: 'pointer' }}>Cancel</button>
            <button type="submit" disabled={saving || !name.trim()} style={{ padding: '8px 16px', background: '#3b82f6', border: 'none', borderRadius: 6, color: '#fff', cursor: 'pointer', fontWeight: 600 }}>
              {saving ? 'Saving…' : 'Add Frame'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

function EditFrameModal({ frame, onClose, onSave }: { frame: NarrativeFrameWithCounts; onClose: () => void; onSave: (id: number, name: string, description: string, owner_type: string) => Promise<void> }) {
  const [name, setName] = useState(frame.name)
  const [description, setDescription] = useState(frame.description || '')
  const [ownerType, setOwnerType] = useState<'candidate' | 'opponent' | 'media'>(frame.owner_type)
  const [saving, setSaving] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setSaving(true)
    await onSave(frame.id, name.trim(), description.trim(), ownerType)
    setSaving(false)
  }

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 }}>
      <div style={{ background: 'var(--surface, #1e293b)', border: '1px solid var(--border, #334155)', borderRadius: 10, padding: 24, width: 420, maxWidth: '90vw' }}>
        <h3 style={{ margin: '0 0 16px', color: 'var(--text, #f1f5f9)' }}>Edit Narrative Frame</h3>
        <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div>
            <label style={{ fontSize: 12, color: 'var(--text-muted, #94a3b8)', display: 'block', marginBottom: 4 }}>Frame name</label>
            <input value={name} onChange={e => setName(e.target.value)} style={{ width: '100%', padding: '8px 10px', background: 'var(--bg, #0f172a)', border: '1px solid var(--border, #334155)', borderRadius: 6, color: 'var(--text, #f1f5f9)', fontSize: 14, boxSizing: 'border-box' }} required />
          </div>
          <div>
            <label style={{ fontSize: 12, color: 'var(--text-muted, #94a3b8)', display: 'block', marginBottom: 4 }}>Description</label>
            <textarea value={description} onChange={e => setDescription(e.target.value)} rows={2} style={{ width: '100%', padding: '8px 10px', background: 'var(--bg, #0f172a)', border: '1px solid var(--border, #334155)', borderRadius: 6, color: 'var(--text, #f1f5f9)', fontSize: 14, resize: 'vertical', boxSizing: 'border-box' }} />
          </div>
          <div>
            <label style={{ fontSize: 12, color: 'var(--text-muted, #94a3b8)', display: 'block', marginBottom: 4 }}>Owner</label>
            <select value={ownerType} onChange={e => setOwnerType(e.target.value as 'candidate' | 'opponent' | 'media')} style={{ width: '100%', padding: '8px 10px', background: 'var(--bg, #0f172a)', border: '1px solid var(--border, #334155)', borderRadius: 6, color: 'var(--text, #f1f5f9)', fontSize: 14, boxSizing: 'border-box' }}>
              <option value="candidate">Our message</option>
              <option value="opponent">Opponent attack</option>
              <option value="media">Media theme</option>
            </select>
          </div>
          <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 8 }}>
            <button type="button" onClick={onClose} style={{ padding: '8px 16px', background: 'transparent', border: '1px solid var(--border, #334155)', borderRadius: 6, color: 'var(--text-muted, #94a3b8)', cursor: 'pointer' }}>Cancel</button>
            <button type="submit" disabled={saving} style={{ padding: '8px 16px', background: '#3b82f6', border: 'none', borderRadius: 6, color: '#fff', cursor: 'pointer', fontWeight: 600 }}>
              {saving ? 'Saving…' : 'Save'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

export default function Narratives() {
  const [frames, setFrames] = useState<NarrativeFrameWithCounts[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [showAdd, setShowAdd] = useState(false)
  const [editFrame, setEditFrame] = useState<NarrativeFrameWithCounts | null>(null)
  const [suggesting, setSuggesting] = useState(false)
  const [rematching, setRematching] = useState(false)
  const [rematchProgress, setRematchProgress] = useState<{ done: number; total: number } | null>(null)
  const [statusMsg, setStatusMsg] = useState<string | null>(null)
  const [filterOwner, setFilterOwner] = useState('all')

  function load() {
    return api.getNarrativeFrames()
      .then(setFrames)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  async function handleAdd(name: string, description: string, owner_type: string) {
    await api.createNarrativeFrame({ name, description, owner_type })
    setShowAdd(false)
    load()
  }

  async function handleEdit(id: number, name: string, description: string, owner_type: string) {
    await api.updateNarrativeFrame(id, { name, description, owner_type })
    setEditFrame(null)
    load()
  }

  async function handleDelete(id: number) {
    if (!confirm('Remove this narrative frame?')) return
    await api.deleteNarrativeFrame(id)
    load()
  }

  async function handleSuggest() {
    setSuggesting(true)
    setStatusMsg(null)
    try {
      const result = await api.suggestNarrativeFrames(14)
      setStatusMsg(`Auto-suggested ${result.suggested} frame${result.suggested === 1 ? '' : 's'} from recent articles.`)
      load()
    } catch (e: any) {
      setStatusMsg('Auto-suggest failed: ' + e.message)
    } finally {
      setSuggesting(false)
    }
  }

  async function handleRematch() {
    setRematching(true)
    setRematchProgress(null)
    setStatusMsg(null)
    try {
      await api.rematchNarrativeFrames(365)
      // Start polling progress every 3 seconds
      const interval = setInterval(async () => {
        try {
          const res = await fetch('/api/narrative-frames/rematch-progress')
          const data = await res.json()
          if (data.total > 0) {
            setRematchProgress({ done: data.done, total: data.total })
          }
          if (!data.running && data.total > 0) {
            clearInterval(interval)
            setRematching(false)
            setRematchProgress(null)
            setStatusMsg(`Rematch complete — processed ${data.total} articles.`)
            load()
          }
        } catch {
          clearInterval(interval)
          setRematching(false)
        }
      }, 3000)
    } catch (e: any) {
      setStatusMsg('Rematch failed: ' + e.message)
      setRematching(false)
    }
  }

  const visibleFrames = useMemo(
    () => filterOwner === 'all' ? frames : frames.filter(f => f.owner_type === filterOwner),
    [frames, filterOwner]
  )

  const candidateFrames = visibleFrames.filter(f => f.owner_type === 'candidate')
  const opponentFrames = visibleFrames.filter(f => f.owner_type === 'opponent')
  const mediaFrames = visibleFrames.filter(f => f.owner_type === 'media')

  return (
    <div className="page">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 24, flexWrap: 'wrap', gap: 12 }}>
        <div>
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: 'var(--text, #f1f5f9)' }}>Narrative Frames</h1>
          <p style={{ margin: '4px 0 0', fontSize: 13, color: 'var(--text-muted, #94a3b8)' }}>
            Track how your message and the opponent's attacks are developing in coverage.
          </p>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <button
            onClick={handleSuggest}
            disabled={suggesting}
            style={{ padding: '8px 14px', background: '#7c3aed', border: 'none', borderRadius: 6, color: '#fff', cursor: 'pointer', fontWeight: 600, fontSize: 13, opacity: suggesting ? 0.7 : 1 }}
          >
            {suggesting ? 'Analyzing…' : 'Auto-suggest frames'}
          </button>
          {frames.length > 0 && (
            <button
              onClick={handleRematch}
              disabled={rematching}
              style={{ padding: '8px 14px', background: 'transparent', border: '1px solid var(--border, #334155)', borderRadius: 6, color: 'var(--text-muted, #94a3b8)', cursor: 'pointer', fontSize: 13, opacity: rematching ? 0.7 : 1 }}
            >
              {rematching ? 'Matching…' : 'Rematch articles'}
            </button>
          )}
          <button
            onClick={() => setShowAdd(true)}
            style={{ padding: '8px 14px', background: '#3b82f6', border: 'none', borderRadius: 6, color: '#fff', cursor: 'pointer', fontWeight: 600, fontSize: 13 }}
          >
            + Add frame
          </button>
        </div>
      </div>

      {/* Filter chips */}
      {frames.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <FilterChips
            label="Show"
            value={filterOwner}
            onChange={setFilterOwner}
            options={[
              { label: 'All frames', value: 'all' },
              { label: 'Our message', value: 'candidate' },
              { label: 'Opponent attacks', value: 'opponent' },
              { label: 'Media themes', value: 'media' },
            ]}
          />
        </div>
      )}

      {rematchProgress && (
        <div style={{ marginBottom: 16, padding: '12px 14px', background: 'var(--surface, #1e293b)', border: '1px solid var(--border, #334155)', borderRadius: 6 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, color: 'var(--text, #f1f5f9)', marginBottom: 8 }}>
            <span>Matching articles to narratives…</span>
            <span style={{ color: 'var(--text-muted, #94a3b8)' }}>{rematchProgress.done} / {rematchProgress.total}</span>
          </div>
          <div style={{ background: 'var(--bg, #0f172a)', borderRadius: 4, height: 6, overflow: 'hidden' }}>
            <div style={{
              height: '100%',
              width: `${Math.round((rematchProgress.done / rematchProgress.total) * 100)}%`,
              background: '#3b82f6',
              borderRadius: 4,
              transition: 'width 0.5s ease',
            }} />
          </div>
          <div style={{ fontSize: 11, color: 'var(--text-muted, #94a3b8)', marginTop: 6 }}>
            {Math.round((rematchProgress.done / rematchProgress.total) * 100)}% — this takes ~25 min, results update when done
          </div>
        </div>
      )}

      {statusMsg && (
        <div style={{ marginBottom: 16, padding: '10px 14px', background: 'var(--surface, #1e293b)', border: '1px solid var(--border, #334155)', borderRadius: 6, fontSize: 13, color: 'var(--text, #f1f5f9)' }}>
          {statusMsg}
        </div>
      )}

      {loading && <div style={{ color: 'var(--text-muted, #94a3b8)' }}>Loading…</div>}
      {error && <div style={{ color: '#ef4444' }}>Error: {error}</div>}

      {!loading && frames.length === 0 && (
        <div style={{ textAlign: 'center', padding: '48px 24px', color: 'var(--text-muted, #94a3b8)' }}>
          <div style={{ fontSize: 16, fontWeight: 600, marginBottom: 8, color: 'var(--text, #f1f5f9)' }}>No narrative frames yet</div>
          <div style={{ fontSize: 13, color: 'var(--text-muted, #94a3b8)' }}>
            Frames are generated automatically once enough relevant articles have been ingested.
            If you have articles already, click <strong>Auto-suggest frames</strong> above to generate them now.
          </div>
        </div>
      )}

      {[
        { label: 'Our Message', frames: candidateFrames },
        { label: 'Opponent Attacks', frames: opponentFrames },
        { label: 'Media Themes', frames: mediaFrames },
      ].map(({ label, frames: group }) => group.length > 0 && (
        <div key={label} style={{ marginBottom: 32 }}>
          <h2 style={{ fontSize: 14, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--text-muted, #94a3b8)', margin: '0 0 12px' }}>{label}</h2>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {group.map(frame => (
              <FrameCard
                key={frame.id}
                frame={frame}
                onDelete={handleDelete}
                onEdit={setEditFrame}
              />
            ))}
          </div>
        </div>
      ))}

      {showAdd && <AddFrameModal onClose={() => setShowAdd(false)} onSave={handleAdd} />}
      {editFrame && <EditFrameModal frame={editFrame} onClose={() => setEditFrame(null)} onSave={handleEdit} />}
    </div>
  )
}
