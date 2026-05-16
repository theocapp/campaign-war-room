import { useEffect, useState, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { NarrativeFrameWithCounts } from '../api/types'
import Sparkline from '../components/Sparkline'
import FilterChips from '../components/FilterChips'

function stripHtml(html: string): string {
  return html.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim()
}

const OWNER_LABELS: Record<string, string> = {
  candidate: 'Our message',
  opponent: 'Opponent attack',
  media: 'Media theme',
}

const OWNER_COLORS: Record<string, string> = {
  candidate: 'var(--candidate-border, #22c55e)',
  opponent: 'var(--opponent-border, #ef4444)',
  media: 'var(--border, #64748b)',
}

const TREND_ICONS: Record<string, string> = {
  up: '↑',
  down: '↓',
  flat: '→',
}

const TREND_COLORS: Record<string, string> = {
  up: '#f97316',
  down: '#64748b',
  flat: '#94a3b8',
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
  const [series, setSeries] = useState<{ date: string; count: number }[] | null>(null)

  useEffect(() => {
    fetch(`/api/frames/${frame.id}/timeseries?bucket=day&days=0`)
      .then(r => r.ok ? r.json() : null)
      .then(data => { if (data?.series) setSeries(data.series) })
      .catch(() => {/* sparkline is non-critical */})
  }, [frame.id])

  return (
    <div style={{
      background: 'var(--surface, #1e293b)',
      border: `2px solid ${OWNER_COLORS[frame.owner_type] || 'var(--border)'}`,
      borderRadius: 8,
      padding: '16px',
      display: 'flex',
      flexDirection: 'column',
      gap: 12,
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 }}>
        <div>
          <span style={{
            fontSize: 10,
            fontWeight: 600,
            textTransform: 'uppercase',
            letterSpacing: '0.08em',
            color: OWNER_COLORS[frame.owner_type],
            marginBottom: 4,
            display: 'block',
          }}>
            {OWNER_LABELS[frame.owner_type] || frame.owner_type}
            {frame.source === 'llm' && <span style={{ color: '#94a3b8', fontWeight: 400 }}> · auto-suggested</span>}
          </span>
          <div style={{ fontWeight: 600, fontSize: 16, color: 'var(--text, #f1f5f9)' }}>{frame.name}</div>
          {frame.description && (
            <div style={{ fontSize: 13, color: 'var(--text-muted, #94a3b8)', marginTop: 4 }}>{frame.description}</div>
          )}
        </div>
        <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
          <button
            onClick={() => onEdit(frame)}
            style={{ background: 'transparent', border: '1px solid var(--border, #334155)', color: 'var(--text-muted, #94a3b8)', borderRadius: 4, padding: '2px 8px', cursor: 'pointer', fontSize: 12 }}
          >Edit</button>
          <button
            onClick={() => onDelete(frame.id)}
            style={{ background: 'transparent', border: '1px solid #ef4444', color: '#ef4444', borderRadius: 4, padding: '2px 8px', cursor: 'pointer', fontSize: 12 }}
          >Remove</button>
        </div>
      </div>

      <div style={{ display: 'flex', gap: 20, alignItems: 'flex-end' }}>
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--text, #f1f5f9)', display: 'flex', alignItems: 'center', gap: 4 }}>
            {frame.mentions_this_week}
            <span style={{ fontSize: 14, color: TREND_COLORS[frame.trend] }}>{TREND_ICONS[frame.trend]}</span>
          </div>
          <div style={{ fontSize: 11, color: 'var(--text-muted, #94a3b8)' }}>this week</div>
        </div>
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--text-muted, #94a3b8)' }}>{frame.mentions_last_week}</div>
          <div style={{ fontSize: 11, color: 'var(--text-muted, #94a3b8)' }}>last week</div>
        </div>
        <div style={{ textAlign: 'center' }}>
          <div style={{ fontSize: 22, fontWeight: 700, color: 'var(--text-muted, #94a3b8)' }}>{frame.mentions_total}</div>
          <div style={{ fontSize: 11, color: 'var(--text-muted, #94a3b8)' }}>total</div>
        </div>
        <div style={{ flex: 1, minWidth: 80 }}>
          {series && <Sparkline data={series} color={OWNER_COLORS[frame.owner_type] || '#3b82f6'} height={36} />}
        </div>
        <Link
          to={`/frames/${frame.id}`}
          style={{ fontSize: 11, color: 'var(--text-muted, #94a3b8)', textDecoration: 'none', whiteSpace: 'nowrap', paddingBottom: 2 }}
          onMouseEnter={e => (e.currentTarget.style.color = 'var(--text, #f1f5f9)')}
          onMouseLeave={e => (e.currentTarget.style.color = 'var(--text-muted, #94a3b8)')}
        >
          View detail →
        </Link>
      </div>

      {frame.recent_articles.length > 0 && (
        <div>
          <div style={{ fontSize: 11, fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-muted, #94a3b8)', marginBottom: 6 }}>Recent</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {frame.recent_articles.map(a => {
              const title = stripHtml(a.title || '(no title)')
              const claim = a.extracted_text ? stripHtml(a.extracted_text) : null
              return (
                <div key={a.id} style={{
                  fontSize: 12, color: 'var(--text, #f1f5f9)',
                  borderLeft: '2px solid var(--border, #334155)', paddingLeft: 8,
                  overflow: 'hidden', minWidth: 0,
                }}>
                  {claim ? (
                    <>
                      <div style={{
                        fontStyle: 'italic', color: 'var(--text, #f1f5f9)', lineHeight: 1.4,
                        display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical',
                        overflow: 'hidden',
                      }}>
                        &ldquo;{claim}&rdquo;
                      </div>
                      <div style={{ marginTop: 3, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {a.source_url
                          ? <a href={a.source_url} target="_blank" rel="noopener noreferrer" style={{ color: 'var(--text-muted, #94a3b8)', textDecoration: 'none', fontSize: 11 }} onMouseEnter={e => (e.currentTarget.style.textDecoration = 'underline')} onMouseLeave={e => (e.currentTarget.style.textDecoration = 'none')}>{a.source_name ? `${a.source_name} · ` : ''}{title}</a>
                          : <span style={{ color: 'var(--text-muted, #94a3b8)', fontSize: 11 }}>{a.source_name ? `${a.source_name} · ` : ''}{title}</span>}
                      </div>
                    </>
                  ) : (
                    <>
                      <div style={{ fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {a.source_url
                          ? <a href={a.source_url} target="_blank" rel="noopener noreferrer" style={{ color: 'inherit', textDecoration: 'none' }} onMouseEnter={e => (e.currentTarget.style.textDecoration = 'underline')} onMouseLeave={e => (e.currentTarget.style.textDecoration = 'none')}>{title}</a>
                          : title}
                      </div>
                      {a.source_name && (
                        <div style={{ color: 'var(--text-muted, #94a3b8)', fontSize: 11, marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.source_name}</div>
                      )}
                    </>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
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
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: 16 }}>
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
