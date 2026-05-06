import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { CandidateMessageLibrary, CandidateNarrative } from '../api/types'

const emptyNarrative = {
  short_label: '',
  canonical_text: '',
  narrative_kind: 'issue_frame' as CandidateNarrative['narrative_kind'],
  issue_name: '',
  preferred_phrases: '',
  avoid_phrases: '',
  must_mention_points: '',
  red_lines: '',
  priority: 0,
  active: true,
}

function splitLines(value: string) {
  return value.split(/\n|,/).map(v => v.trim()).filter(Boolean)
}

function joinLines(values: string[] | null | undefined) {
  return (values || []).join('\n')
}

const KIND_LABELS: Record<string, string> = {
  self_definition: 'Self Definition',
  issue_frame:     'Issue Frame',
  contrast:        'Contrast',
  rebuttal:        'Rebuttal',
}

export default function MessageLibrary() {
  const [library, setLibrary]     = useState<CandidateMessageLibrary | null>(null)
  const [narratives, setNarratives] = useState<CandidateNarrative[]>([])
  const [draft, setDraft]         = useState(emptyNarrative)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [status, setStatus]       = useState<string | null>(null)
  const [saving, setSaving]       = useState(false)

  function load() {
    Promise.all([api.getMessageLibrary(), api.getCandidateNarratives()])
      .then(([lib, rows]) => { setLibrary(lib); setNarratives(rows) })
      .catch(e => setStatus(e.message))
  }

  useEffect(() => { load() }, [])

  function edit(row: CandidateNarrative) {
    setEditingId(row.id)
    setDraft({
      short_label: row.short_label,
      canonical_text: row.canonical_text,
      narrative_kind: row.narrative_kind,
      issue_name: row.issue_name || '',
      preferred_phrases: joinLines(row.preferred_phrases),
      avoid_phrases: joinLines(row.avoid_phrases),
      must_mention_points: joinLines(row.must_mention_points),
      red_lines: joinLines(row.red_lines),
      priority: row.priority,
      active: row.active,
    })
  }

  function cancelEdit() {
    setEditingId(null)
    setDraft(emptyNarrative)
  }

  async function saveLibrary() {
    if (!library) return
    setSaving(true)
    try {
      const updated = await api.updateMessageLibrary({
        core_message: library.core_message,
        short_bio_frame: library.short_bio_frame,
        tone_guidance: library.tone_guidance,
      })
      setLibrary(updated)
      setStatus('Message library saved.')
    } catch (e: unknown) {
      setStatus(e instanceof Error ? e.message : 'Save failed')
    } finally { setSaving(false) }
  }

  async function saveNarrative() {
    setSaving(true)
    try {
      const body = {
        short_label: draft.short_label,
        canonical_text: draft.canonical_text,
        narrative_kind: draft.narrative_kind,
        issue_name: draft.issue_name || null,
        preferred_phrases: splitLines(draft.preferred_phrases),
        avoid_phrases: splitLines(draft.avoid_phrases),
        must_mention_points: splitLines(draft.must_mention_points),
        red_lines: splitLines(draft.red_lines),
        priority: Number(draft.priority) || 0,
        active: draft.active,
      }
      if (editingId) await api.updateCandidateNarrative(editingId, body)
      else await api.createCandidateNarrative(body)
      setDraft(emptyNarrative)
      setEditingId(null)
      setStatus(editingId ? 'Narrative updated.' : 'Narrative created.')
      load()
    } catch (e: unknown) {
      setStatus(e instanceof Error ? e.message : 'Save failed')
    } finally { setSaving(false) }
  }

  async function remove(id: number) {
    if (!confirm('Delete this narrative?')) return
    await api.deleteCandidateNarrative(id)
    setStatus('Narrative deleted.')
    load()
  }

  if (!library) return <div className="loading-text">Loading…</div>

  return (
    <div className="page-wide">
      {/* Header */}
      <div className="page-header">
        <div className="label" style={{ marginBottom: 5 }}>Campaign Strategy</div>
        <h1 className="page-title">Message Library</h1>
        <p className="page-subtitle">Define your campaign's core message and candidate frames. Used to identify narrative traction and generate on-message talking points.</p>
      </div>

      {status && (
        <div className="info-banner" style={{ marginBottom: '1rem', color: status.toLowerCase().includes('fail') || status.toLowerCase().includes('error') ? 'var(--opponent)' : 'var(--ok-light)' }}>
          {status}
        </div>
      )}

      {/* Core library card */}
      <div className="card" style={{ marginBottom: '1.5rem' }}>
        <div className="section-title" style={{ marginBottom: '1rem' }}>Campaign Voice</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
          <div>
            <div className="label" style={{ marginBottom: 5 }}>Core Message</div>
            <textarea
              value={library.core_message || ''}
              onChange={e => setLibrary({ ...library, core_message: e.target.value })}
              rows={3}
              style={{ width: '100%' }}
              placeholder="2–4 sentences that capture what this campaign is about."
            />
          </div>
          <div>
            <div className="label" style={{ marginBottom: 5 }}>Short Bio Frame</div>
            <textarea
              value={library.short_bio_frame || ''}
              onChange={e => setLibrary({ ...library, short_bio_frame: e.target.value })}
              rows={2}
              style={{ width: '100%' }}
              placeholder="One-sentence bio for door-knocking and social media."
            />
          </div>
          <div>
            <div className="label" style={{ marginBottom: 5 }}>Tone Guidance</div>
            <textarea
              value={library.tone_guidance || ''}
              onChange={e => setLibrary({ ...library, tone_guidance: e.target.value })}
              rows={2}
              style={{ width: '100%' }}
              placeholder="How the candidate sounds. e.g. Direct, community-focused, never attacks first."
            />
          </div>
        </div>
        <div style={{ marginTop: '1rem' }}>
          <button className="btn btn-primary" onClick={saveLibrary} disabled={saving}>
            {saving ? 'Saving…' : 'Save library'}
          </button>
        </div>
      </div>

      {/* Two-column: form + list */}
      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(300px, 380px) 1fr', gap: '1.5rem', alignItems: 'start' }}>
        {/* Left: add/edit form */}
        <div className="card">
          <div className="section-title" style={{ marginBottom: '1rem' }}>
            {editingId ? 'Edit Narrative' : 'Add Narrative'}
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div>
              <div className="label" style={{ marginBottom: 4 }}>Label *</div>
              <input
                placeholder="Short label (e.g. Housing Affordability Frame)"
                value={draft.short_label}
                onChange={e => setDraft({ ...draft, short_label: e.target.value })}
              />
            </div>

            <div>
              <div className="label" style={{ marginBottom: 4 }}>Canonical Text *</div>
              <textarea
                placeholder="The frame in one or two sentences."
                value={draft.canonical_text}
                onChange={e => setDraft({ ...draft, canonical_text: e.target.value })}
                rows={3}
                style={{ width: '100%' }}
              />
            </div>

            <div>
              <div className="label" style={{ marginBottom: 4 }}>Kind</div>
              <select
                value={draft.narrative_kind}
                onChange={e => setDraft({ ...draft, narrative_kind: e.target.value as CandidateNarrative['narrative_kind'] })}
                style={{ width: '100%' }}
              >
                <option value="self_definition">Self Definition</option>
                <option value="issue_frame">Issue Frame</option>
                <option value="contrast">Contrast</option>
                <option value="rebuttal">Rebuttal</option>
              </select>
            </div>

            <div>
              <div className="label" style={{ marginBottom: 4 }}>Issue Association</div>
              <input
                placeholder="e.g. Housing, Public Safety"
                value={draft.issue_name}
                onChange={e => setDraft({ ...draft, issue_name: e.target.value })}
              />
            </div>

            <div>
              <div className="label" style={{ marginBottom: 4 }}>Preferred Phrases</div>
              <textarea
                placeholder="One per line or comma-separated"
                value={draft.preferred_phrases}
                onChange={e => setDraft({ ...draft, preferred_phrases: e.target.value })}
                rows={2}
                style={{ width: '100%' }}
              />
            </div>

            <div>
              <div className="label" style={{ marginBottom: 4 }}>Must-Mention Points</div>
              <textarea
                placeholder="One per line"
                value={draft.must_mention_points}
                onChange={e => setDraft({ ...draft, must_mention_points: e.target.value })}
                rows={2}
                style={{ width: '100%' }}
              />
            </div>

            <div>
              <div className="label" style={{ marginBottom: 4 }}>Avoid Phrases</div>
              <textarea
                placeholder="Words or phrases to avoid"
                value={draft.avoid_phrases}
                onChange={e => setDraft({ ...draft, avoid_phrases: e.target.value })}
                rows={2}
                style={{ width: '100%' }}
              />
            </div>

            <div>
              <div className="label" style={{ marginBottom: 4 }}>Red Lines</div>
              <textarea
                placeholder="Absolute limits — things the campaign will never say"
                value={draft.red_lines}
                onChange={e => setDraft({ ...draft, red_lines: e.target.value })}
                rows={2}
                style={{ width: '100%' }}
              />
            </div>

            <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
              <div>
                <div className="label" style={{ marginBottom: 4 }}>Priority</div>
                <input
                  type="number"
                  value={draft.priority}
                  onChange={e => setDraft({ ...draft, priority: Number(e.target.value) })}
                  style={{ width: 80 }}
                />
              </div>
              <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '0.78rem', color: 'var(--text-secondary)', cursor: 'pointer', marginTop: 18 }}>
                <input
                  type="checkbox"
                  checked={draft.active}
                  onChange={e => setDraft({ ...draft, active: e.target.checked })}
                />
                Active
              </label>
            </div>
          </div>

          <div style={{ display: 'flex', gap: 8, marginTop: '1rem' }}>
            <button
              className="btn btn-primary"
              onClick={saveNarrative}
              disabled={saving || !draft.short_label || !draft.canonical_text}
            >
              {saving ? '…' : editingId ? 'Update' : 'Add Narrative'}
            </button>
            {editingId && (
              <button className="btn btn-ghost" onClick={cancelEdit}>Cancel</button>
            )}
          </div>
        </div>

        {/* Right: narrative list */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {narratives.length === 0 && (
            <div className="empty-state">
              <div className="empty-state-icon">◻</div>
              <div className="empty-state-title">No narratives yet</div>
              <div className="empty-state-body">Add your first candidate frame to start tracking message traction.</div>
            </div>
          )}
          {narratives.map(row => (
            <div key={row.id} className="card">
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 10, marginBottom: 6 }}>
                <div>
                  <div style={{ fontWeight: 650, fontSize: '0.88rem', color: 'var(--text-primary)', marginBottom: 3 }}>{row.short_label}</div>
                  <div style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
                    <span style={{ fontSize: '0.62rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                      {KIND_LABELS[row.narrative_kind] ?? row.narrative_kind}
                    </span>
                    {row.issue_name && (
                      <span style={{ fontSize: '0.62rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>· {row.issue_name}</span>
                    )}
                    <span style={{ fontSize: '0.62rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono' }}>· p{row.priority}</span>
                  </div>
                </div>
                <span className={`badge ${row.active ? 'badge-success' : 'badge-ghost'}`} style={{ fontSize: '0.58rem', flexShrink: 0 }}>
                  {row.active ? 'active' : 'inactive'}
                </span>
              </div>
              <p style={{ margin: '0 0 10px', fontSize: '0.79rem', color: 'var(--text-secondary)', lineHeight: 1.5 }}>{row.canonical_text}</p>
              <div style={{ display: 'flex', gap: 6 }}>
                <button className="btn btn-ghost btn-sm" onClick={() => edit(row)}>Edit</button>
                <button className="btn btn-danger btn-sm" onClick={() => remove(row.id)}>Delete</button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
