import { Check, ChevronDown, ChevronRight, Edit2, Plus, RefreshCw, Search, Sparkles, X, Zap } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api } from '@/api/client'
import { invalidateDashboard } from '@/api/dashboardCache'
import { useAuth } from '@/auth/AuthContext'
import type { CandidateFrameCluster, NarrativeFrame, OwnerType } from '@/api/types'
import { InfoTooltip } from '@/components/InfoTooltip'
import { QuadrantSelector, quadrantToTypes } from '@/components/QuadrantSelector'
import { QuadrantPalette, quadrantKey, quadrantNamedLabel } from '@/lib/quadrantColor'
import type { QuadrantKey } from '@/lib/quadrantColor'

// Quadrant filter UI accepts these values (4 quadrants + "all" + "media").
type QuadrantFilter = 'all' | QuadrantKey

interface QuadrantMeta {
  key: QuadrantKey
  color: string
  title: string
  subtitle: string
  description: string
}

/**
 * Build the 4 quadrant metadata objects with the actual candidate /
 * opponent names substituted in. Falls back to "us" / "them" if a
 * name isn't loaded yet (avoids flicker during initial API calls).
 *
 * Labels use pro/anti vocabulary (more plain-English than defense/offense):
 *               ABOUT SELF        ABOUT OTHER
 *   OUR SIDE    Pro-{candidate}   Anti-{opponent}
 *   THEIR SIDE  Pro-{opponent}    Anti-{candidate}
 */
function buildQuadrants(candidateName: string, opponentName: string): QuadrantMeta[] {
  const us = candidateName || 'us'
  const them = opponentName || 'them'
  return [
    {
      key: 'our_defense',
      color: QuadrantPalette.our_defense, // blue
      title: quadrantNamedLabel('our_defense', candidateName, opponentName),
      subtitle: `Defending ${us}'s record`,
      description: `Narratives that ${us} is pushing in favor of ${us} — accomplishments, record, message.`,
    },
    {
      key: 'our_offense',
      color: QuadrantPalette.our_offense, // teal
      title: quadrantNamedLabel('our_offense', candidateName, opponentName),
      subtitle: `Attacking ${them}`,
      description: `Narratives that ${us} is pushing to attack or undermine ${them}.`,
    },
    {
      key: 'their_defense',
      color: QuadrantPalette.their_defense, // red
      title: quadrantNamedLabel('their_defense', candidateName, opponentName),
      subtitle: `Defending ${them}'s record`,
      description: `Narratives that ${them} is pushing in favor of ${them} — their record, their message, their wins.`,
    },
    {
      key: 'their_offense',
      color: QuadrantPalette.their_offense, // orange
      title: quadrantNamedLabel('their_offense', candidateName, opponentName),
      subtitle: `Attacking ${us}`,
      description: `${them} attacking ${us} — narratives we need to respond to or get ahead of.`,
    },
  ]
}

// Colors via CSS variables so the dark/light toggle works. See index.css.
const C = {
  bg1: 'var(--bg-1)', bg2: 'var(--bg-2)', bg3: 'var(--bg-3)',
  border: 'var(--border)', borderBright: 'var(--border-bright)',
  text1: 'var(--text-1)', text2: 'var(--text-2)', text3: 'var(--text-3)',
  candidate: 'var(--candidate)', opponent: 'var(--opponent)', media: 'var(--media)',
  accent: 'var(--accent)',
}

function ownerLabel(t: OwnerType) {
  return t === 'candidate' ? 'Our Frame' : t === 'opponent' ? 'Opponent' : 'Media'
}

function ownerColor(t: OwnerType) {
  return t === 'candidate' ? C.candidate : t === 'opponent' ? C.opponent : C.media
}


// V13.21 — quadrant color uses BOTH owner_type (who benefits) and
// subject_type (who it's about) to render the same 4-quadrant scheme as
// the landscape. Falls back to the 3-color owner palette when subject
// isn't available.
import { quadrantColor as _qc } from '@/lib/quadrantColor'
function frameColor(f: { owner_type?: OwnerType; subject_type?: OwnerType }): string {
  if (f.subject_type) return _qc(f.owner_type ?? null, f.subject_type ?? null)
  return ownerColor(f.owner_type ?? 'media')
}

// Compact momentum + strategic-posture badge. Label = signal (what's
// happening), color = posture (what to do), tooltip = both + urgency.
// See Dashboard.tsx for the long-form treatment with the same matrix.
type StrategicLens = { posture: 'amplify' | 'offensive' | 'defensive' | 'monitor' | 'ignore'; action: string | null; urgency: 'high' | 'medium' | 'low' }
type MomentumBadgeMeta = { label: string; color: string; bg: string; tooltip: string; isUrgent: boolean }

const NARRATIVE_POSTURE_COLORS: Record<string, { color: string; bg: string }> = {
  amplify:   { color: '#22c55e', bg: 'rgba(34, 197, 94, 0.14)' },
  offensive: { color: '#0ea5e9', bg: 'rgba(14, 165, 233, 0.14)' },
  defensive: { color: '#ef4444', bg: 'rgba(239, 68, 68, 0.14)' },
  monitor:   { color: '#a78bfa', bg: 'rgba(167, 139, 250, 0.16)' },
  ignore:    { color: '#666',    bg: 'rgba(102, 102, 102, 0.10)' },
}

function narrativeSignalLabel(s: string): string {
  switch (s) {
    case 'viral': return 'Viral'
    case 'amplified': return 'Amplified'
    case 'missing_coverage': return 'Missing'
    case 'elite_only': return 'Elite only'
    case 'stable': return 'Stable'
    case 'no_trend_signal': return 'No signal'
    default: return s
  }
}

function narrativeMomentumBadge(
  signal: string | null | undefined,
  data: Record<string, unknown> | null | undefined,
  lens: StrategicLens | null | undefined,
): MomentumBadgeMeta | null {
  if (!signal) return null
  if (lens && lens.posture === 'ignore') return null
  if (!lens && (signal === 'stable' || signal === 'no_trend_signal')) return null

  const palette = lens ? NARRATIVE_POSTURE_COLORS[lens.posture] : NARRATIVE_POSTURE_COLORS.monitor
  const ov = data?.outlet_velocity as number | undefined
  const cv = data?.cluster_velocity as number | undefined
  const tv = data?.trend_velocity as number | undefined
  const signalDesc =
    signal === 'viral' ? `Outlets ${ov ? `${ov.toFixed(1)}×` : 'spiking'} AND search ${tv ? `${tv.toFixed(1)}×` : 'spiking'}` :
    signal === 'amplified' ? `Outlets ${ov ? `${ov.toFixed(1)}×` : 'spiking'} (broad pickup) but search flat` :
    signal === 'missing_coverage' ? `Search ${tv ? `${tv.toFixed(1)}×` : 'spiking'} but press flat` :
    signal === 'elite_only' ? `Angles ${cv ? `${cv.toFixed(1)}×` : 'spiking'} but few outlets` :
    signal

  const parts = [signalDesc]
  if (lens?.action) parts.push(`→ ${lens.action}`)
  if (lens?.urgency) parts.push(`Urgency: ${lens.urgency}`)

  return {
    label: narrativeSignalLabel(signal),
    color: palette.color,
    bg: palette.bg,
    tooltip: parts.join('\n'),
    isUrgent: lens?.urgency === 'high',
  }
}

interface AddFrameModalProps {
  onClose: () => void
  onCreated: (f: NarrativeFrame) => void
  candidateName: string
  opponentName: string
}

function AddFrameModal({ onClose, onCreated, candidateName, opponentName }: AddFrameModalProps) {
  const [name, setName] = useState('')
  const [desc, setDesc] = useState('')
  const [quadrant, setQuadrant] = useState<QuadrantKey>('our_defense')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim()) return
    setSaving(true)
    setError('')
    try {
      const { owner_type, subject_type } = quadrantToTypes(quadrant)
      const frame = await api.createFrame({
        name: name.trim(), description: desc.trim(),
        owner_type, subject_type,
      })
      invalidateDashboard()  // Home shows frames — refresh next visit.
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
            <label className="section-label" style={{ display: 'block', marginBottom: 8 }}>STRATEGIC SLOT</label>
            <QuadrantSelector
              value={quadrant}
              onChange={setQuadrant}
              candidateName={candidateName}
              opponentName={opponentName}
            />
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

function EditFrameModal({ frame, onClose, onUpdated, candidateName, opponentName }: {
  frame: NarrativeFrame
  onClose: () => void
  onUpdated: (f: NarrativeFrame) => void
  candidateName: string
  opponentName: string
}) {
  const [name, setName] = useState(frame.name)
  const [desc, setDesc] = useState(frame.description)
  // Initialize quadrant from the frame's current (owner_type, subject_type).
  // Backend returns subject_type either from the stored column or the
  // heuristic fallback — either way it's the correct starting position.
  const [quadrant, setQuadrant] = useState<QuadrantKey>(
    quadrantKey(frame.owner_type, frame.subject_type ?? null),
  )
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setSaving(true)
    setError('')
    try {
      const { owner_type, subject_type } = quadrantToTypes(quadrant)
      const updated = await api.updateFrame(frame.id, {
        name, description: desc,
        owner_type, subject_type,
      })
      invalidateDashboard()  // Home shows frame names — refresh next visit.
      onUpdated(updated)
      onClose()
    } catch (err: unknown) {
      // Surface the error instead of silently swallowing — previously the
      // modal just closed with no acknowledgement on API failure.
      setError(err instanceof Error ? err.message : 'Failed to update frame')
    } finally {
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
            <label className="section-label" style={{ display: 'block', marginBottom: 8 }}>STRATEGIC SLOT</label>
            <QuadrantSelector
              value={quadrant}
              onChange={setQuadrant}
              candidateName={candidateName}
              opponentName={opponentName}
            />
          </div>
          {error && (
            <div style={{
              padding: '8px 12px', marginBottom: 10, fontSize: 12,
              color: '#f87171', background: 'rgba(248,113,113,0.08)',
              border: '1px solid rgba(248,113,113,0.3)', borderRadius: 4,
            }}>
              {error}
            </div>
          )}
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
  const oc = frameColor(frame)

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
        {/* Title row — title + momentum badge + edit. No chevron; clicking the card opens detail. */}
        <div style={{ padding: '14px 18px 8px', display: 'flex', alignItems: 'flex-start', gap: 10 }}>
          <div style={{ flex: 1, minWidth: 0, fontSize: 16, fontWeight: 700, color: C.text1, lineHeight: 1.3 }}>
            {frame.name}
          </div>
          {(() => {
            const m = narrativeMomentumBadge(frame.momentum_signal, frame.momentum_data, frame.strategic_lens)
            if (!m) return null
            return (
              <span
                title={m.tooltip}
                onClick={e => { e.preventDefault(); e.stopPropagation() }}
                style={{
                  background: m.bg, border: `${m.isUrgent ? 2 : 1}px solid ${m.color}`,
                  borderRadius: 4, padding: '2px 7px', fontSize: 10,
                  color: m.color, fontWeight: 700, flexShrink: 0, cursor: 'help',
                  letterSpacing: '0.03em', textTransform: 'uppercase',
                  alignSelf: 'center',
                }}
              >
                {m.label}
              </span>
            )
          })()}
          <button
            onClick={e => { e.preventDefault(); e.stopPropagation(); onEdit(frame) }}
            style={{ background: 'none', border: 'none', cursor: 'pointer', color: C.text3, padding: 4, borderRadius: 4, flexShrink: 0 }}
            title="Edit narrative"
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
                  tick={{ fontSize: 9, fill: C.text3 }}
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
                  tick={{ fontSize: 9, fill: C.text3 }}
                  axisLine={false}
                  tickLine={false}
                  allowDecimals={false}
                  width={24}
                />
                <Tooltip
                  contentStyle={{
                    background: 'var(--bg-3)', border: `1px solid ${C.border}`,
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
              fontSize: 11, color: C.text3,
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

/**
 * Pending candidate-frame clusters — narratives the LLM has been flagging
 * during per-article scoring that haven't been promoted into real
 * NarrativeFrames yet. Surfaces as a banner-style section at the top of
 * the Narratives page, sorted by signal strength.
 *
 * Each card has: name, owner badge, support stats (rows × articles × outlets),
 * one representative quote, and a Promote button that opens an inline form
 * (so the user can edit the LLM-suggested name/description/owner before
 * committing). Dismissal isn't supported in v1 — clusters age out of the
 * 21-day window naturally, and once a real frame exists in that territory
 * the LLM stops generating duplicate candidates for it.
 */
function PendingSuggestionsSection({
  onPromoted, candidateName, opponentName,
}: {
  onPromoted: () => void
  candidateName: string
  opponentName: string
}) {
  // Promoting a candidate cluster triggers an LLM-backed write on the
  // backend (and the backend now rejects non-admin callers). Hide the
  // whole banner for non-admins so they don't see an action they can't
  // take. Admins keep the existing UX. NOTE: the early-return guard sits
  // below all the useState/useEffect calls so React's rules-of-hooks
  // ordering is preserved across admin → non-admin transitions.
  const { user } = useAuth()
  const [clusters, setClusters] = useState<CandidateFrameCluster[] | null>(null)
  const [lastError, setLastError] = useState<string | null>(null)
  const [editingIdx, setEditingIdx] = useState<number | null>(null)
  const [editName, setEditName] = useState('')
  const [editDesc, setEditDesc] = useState('')
  // Replaces the old 3-option owner_type select. Pre-fills from the LLM's
  // owner_type_hint (which only sets owner; subject_type defaults sensibly:
  // candidate-owner → our_defense, opponent-owner → their_defense, media →
  // media). The user can then re-pick to any of 5 quadrants.
  const [editQuadrant, setEditQuadrant] = useState<QuadrantKey>('media')
  const [promoting, setPromoting] = useState(false)

  useEffect(() => {
    api.pendingCandidateClusters(21)
      .then(r => {
        setClusters(r.suggestions)
        setLastError(r.last_error)
      })
      .catch(err => {
        setClusters([])
        // Network/auth failure — show a generic error in the banner rather
        // than rendering nothing. Don't leak the raw error object to the UI;
        // wrap in a user-readable message.
        setLastError(`Failed to load suggestions: ${err?.message || 'network error'}`)
      })
  }, [])

  function startEdit(i: number, c: CandidateFrameCluster) {
    setEditingIdx(i)
    setEditName(c.suggested_name)
    setEditDesc(c.suggested_description || '')
    // Pre-fill the quadrant from the backend's (owner_type_hint, subject_type_hint)
    // pair. If the backend response is old/cached and subject_type_hint is
    // missing, quadrantKey() falls back to 'media' — still one click for the
    // user to correct it.
    setEditQuadrant(quadrantKey(c.owner_type_hint, c.subject_type_hint ?? null))
  }

  async function confirmPromote(c: CandidateFrameCluster) {
    setPromoting(true)
    try {
      const { owner_type, subject_type } = quadrantToTypes(editQuadrant)
      await api.promoteCandidateCluster({
        suggested_name: editName.trim(),
        suggested_description: editDesc.trim(),
        owner_type,
        subject_type,
        candidate_frame_ids: c.candidate_frame_ids,
      })
      // Optimistically drop the promoted cluster locally; refresh main list.
      // Backend's _CACHE is also cleaned up by promote_cluster — this is
      // belt-and-suspenders so a slow round-trip doesn't briefly show the
      // promoted cluster again.
      setClusters(prev => (prev || []).filter(x => x !== c))
      invalidateDashboard()  // promoted frame becomes a real one — Home needs to know
      setEditingIdx(null)
      onPromoted()
    } catch {
      // silently fail for now — user can retry
    } finally {
      setPromoting(false)
    }
  }

  // Non-admin: never render the banner. This guard goes AFTER all hooks
  // so the hooks call order stays stable when isAdmin flips. Backend also
  // 403s the underlying promote endpoint — this is the cosmetic half.
  if (!user?.isAdmin) return null

  // Three states:
  //   loading: clusters === null
  //   error  : lastError !== null  (show diagnostic banner so user knows
  //            "0 suggestions" isn't just "nothing happened")
  //   empty  : clusters.length === 0 and no error  (render nothing)
  //   normal : show the cluster cards
  if (clusters === null) return null
  if (clusters.length === 0 && !lastError) return null

  if (clusters.length === 0 && lastError) {
    // Diagnostic banner — typically Gemini quota exhausted with no fallback.
    // Strip the "RuntimeError:" prefix and similar noise for readability.
    const cleaned = lastError.replace(/^[A-Za-z]+Error:\s*/, '')
    return (
      <div style={{
        padding: '14px 28px',
        borderBottom: `1px solid ${C.border}`,
        background: 'rgba(215, 25, 19, 0.08)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
          <span style={{
            fontSize: 11, fontWeight: 700, letterSpacing: '0.1em',
            color: C.opponent, textTransform: 'uppercase',
          }}>
            AI narrative discovery is paused
          </span>
        </div>
        <div style={{ fontSize: 12, color: C.text2, lineHeight: 1.5 }}>
          The promoter couldn't fetch embeddings on its last run, so it can't
          tell whether new narratives are emerging. Suggestions will resume
          on the next scheduled run once quota recovers.
        </div>
        <div style={{
          fontSize: 11, color: C.text3, fontFamily: 'monospace',
          marginTop: 6, opacity: 0.7,
        }}>
          {cleaned}
        </div>
      </div>
    )
  }

  return (
    <div style={{
      padding: '16px 28px',
      borderBottom: `1px solid ${C.border}`,
      background: 'linear-gradient(to bottom, rgba(255,191,0,0.06), rgba(255,191,0,0))',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
        <Zap size={14} color={C.accent} />
        <span style={{
          fontSize: 11, fontWeight: 700, letterSpacing: '0.1em',
          color: C.accent, textTransform: 'uppercase',
          display: 'inline-flex', alignItems: 'center',
        }}>
          {clusters.length} emerging narrative{clusters.length === 1 ? '' : 's'}
        </span>
        <span style={{ fontSize: 12, color: C.text3 }}>
          — promote into tracked frames, or leave to keep observing
        </span>
      </div>
      <div style={{
        display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 10,
      }}>
        {clusters.map((c, i) => {
          // 5-quadrant color/label using both owner_type_hint AND the
          // backend-supplied subject_type_hint. Falls back to media gray
          // when subject_type_hint isn't present yet (old cached responses).
          const qk = quadrantKey(c.owner_type_hint, c.subject_type_hint ?? null)
          const oc = QuadrantPalette[qk]
          const quadrantLabelText = quadrantNamedLabel(qk, candidateName, opponentName)
          const isEditing = editingIdx === i
          return (
            <div key={i} style={{
              background: C.bg2, border: `1px solid ${C.border}`,
              borderLeft: `3px solid ${oc}`, borderRadius: 6, padding: 12,
            }}>
              {isEditing ? (
                <>
                  <input
                    className="input" value={editName} onChange={e => setEditName(e.target.value)}
                    style={{ fontSize: 13, marginBottom: 6 }}
                    placeholder="Frame name"
                  />
                  <textarea
                    className="input" value={editDesc} onChange={e => setEditDesc(e.target.value)}
                    style={{ fontSize: 12, marginBottom: 8, minHeight: 50, resize: 'vertical' }}
                    placeholder="Description (optional)"
                  />
                  <div style={{ marginBottom: 8 }}>
                    <QuadrantSelector
                      value={editQuadrant}
                      onChange={setEditQuadrant}
                      candidateName={candidateName}
                      opponentName={opponentName}
                    />
                  </div>
                  <div style={{ display: 'flex', gap: 6 }}>
                    <button
                      onClick={() => confirmPromote(c)}
                      disabled={promoting || !editName.trim()}
                      className="btn btn-primary"
                      style={{ fontSize: 11, padding: '4px 10px' }}
                    >
                      <Check size={12} /> {promoting ? 'Promoting…' : 'Confirm'}
                    </button>
                    <button
                      onClick={() => setEditingIdx(null)}
                      className="btn btn-ghost"
                      style={{ fontSize: 11, padding: '4px 10px' }}
                    >
                      Cancel
                    </button>
                  </div>
                </>
              ) : (
                <>
                  <div style={{
                    fontSize: 13, fontWeight: 600, color: C.text1,
                    marginBottom: 4, lineHeight: 1.3,
                  }}>
                    {c.suggested_name}
                  </div>
                  <div style={{
                    fontSize: 10, color: C.text3, letterSpacing: '0.05em',
                    textTransform: 'uppercase', marginBottom: 6,
                  }}>
                    <span style={{ color: oc, fontWeight: 600 }}>
                      {quadrantLabelText}
                    </span>
                    {' · '}{c.n_articles} articles · {c.n_outlets} outlets
                  </div>
                  {c.evidence_quotes[0] && (
                    <div style={{
                      fontSize: 11, color: C.text2, fontStyle: 'italic',
                      lineHeight: 1.4, marginBottom: 8,
                      display: '-webkit-box', WebkitLineClamp: 3, WebkitBoxOrient: 'vertical',
                      overflow: 'hidden',
                    }}>
                      "{c.evidence_quotes[0]}"
                    </div>
                  )}
                  <button
                    onClick={() => startEdit(i, c)}
                    className="btn btn-primary"
                    style={{ fontSize: 11, padding: '4px 10px' }}
                  >
                    Promote
                  </button>
                </>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}


/**
 * Collapsible card for one quadrant. Collapsed (default): a single-line
 * header with chevron, color swatch, title, count, and this-week delta.
 * Expanded: shows the subtitle and the full FrameCard list inside.
 *
 * Stacked vertically inside the "Our Campaign" or "Their Campaign" column.
 */
function CollapsibleQuadrantCard({
  quadrant, frames, expanded, onToggle, onEdit,
}: {
  quadrant: { key: QuadrantKey; color: string; title: string; subtitle: string; description: string }
  frames: NarrativeFrame[]
  expanded: boolean
  onToggle: () => void
  onEdit: (f: NarrativeFrame) => void
}) {
  // Aggregate "this week" delta so the collapsed header shows whether
  // anything's moving without the user having to expand. Positive = growing.
  const thisWeek = frames.reduce((sum, f) => sum + (f.mentions_this_week ?? 0), 0)

  return (
    <div style={{
      background: C.bg2,
      border: `1px solid ${C.border}`,
      borderLeft: `3px solid ${quadrant.color}`,
      borderRadius: 8,
      marginBottom: 10,
      overflow: 'hidden',
      transition: 'border-color 0.12s ease',
    }}>
      {/* Header — clickable, always visible */}
      <button
        onClick={onToggle}
        title={expanded ? 'Collapse' : 'Expand'}
        style={{
          display: 'flex', alignItems: 'center', gap: 10, width: '100%',
          padding: '12px 14px',
          background: 'none', border: 'none', cursor: 'pointer',
          color: 'inherit', textAlign: 'left', fontFamily: 'inherit',
        }}
      >
        {expanded
          ? <ChevronDown size={16} style={{ color: C.text2, flexShrink: 0 }} />
          : <ChevronRight size={16} style={{ color: C.text2, flexShrink: 0 }} />}
        <span style={{
          width: 10, height: 10, borderRadius: 2,
          background: quadrant.color, display: 'inline-block', flexShrink: 0,
        }} />
        <span style={{
          fontSize: 14, fontWeight: 700, color: C.text1, letterSpacing: '-0.01em',
          display: 'inline-flex', alignItems: 'center',
        }}>
          {quadrant.title}
          <InfoTooltip text={quadrant.description} />
        </span>
        <span style={{ flex: 1 }} />
        <span style={{
          fontSize: 12, color: C.text3, fontVariantNumeric: 'tabular-nums',
          display: 'inline-flex', alignItems: 'baseline', gap: 6,
        }}>
          <span style={{ color: C.text2, fontWeight: 600 }}>{frames.length}</span>
          {thisWeek > 0 && (
            <>
              <span>·</span>
              <span style={{ color: quadrant.color, fontWeight: 600 }}>+{thisWeek} this wk</span>
            </>
          )}
        </span>
      </button>

      {/* Body — only when expanded */}
      {expanded && (
        <div style={{ padding: '0 14px 12px' }}>
          <div style={{ fontSize: 11, color: C.text3, marginBottom: 10, marginLeft: 32 }}>
            {quadrant.subtitle}
          </div>
          {frames.length === 0 ? (
            <div style={{
              padding: '18px 0', textAlign: 'center',
              color: C.text3, fontSize: 12, fontStyle: 'italic',
            }}>
              No narratives in this quadrant yet
            </div>
          ) : (
            frames.map(frame => (
              <FrameCard key={frame.id} frame={frame} onEdit={onEdit} />
            ))
          )}
        </div>
      )}
    </div>
  )
}

export function Narratives() {
  const [frames, setFrames] = useState<NarrativeFrame[]>([])
  const [loading, setLoading] = useState(true)
  const [suggesting, setSuggesting] = useState(false)
  const [search, setSearch] = useState('')
  const [filterQuadrant, setFilterQuadrant] = useState<QuadrantFilter>('all')
  const [filterStage, setFilterStage] = useState<string>('all')
  const [sortBy, setSortBy] = useState<'relevance' | 'volume' | 'newest'>('relevance')
  const [showAdd, setShowAdd] = useState(false)
  const [editFrame, setEditFrame] = useState<NarrativeFrame | null>(null)
  const [candidateName, setCandidateName] = useState('')
  const [opponentName, setOpponentName] = useState('')
  // Track which quadrant cards are expanded. All collapsed by default
  // (the user wants a compact view); they click to expand any group.
  const [expandedQuadrants, setExpandedQuadrants] = useState<Set<QuadrantKey>>(new Set())
  function toggleQuadrant(k: QuadrantKey) {
    setExpandedQuadrants(prev => {
      const next = new Set(prev)
      if (next.has(k)) next.delete(k); else next.add(k)
      return next
    })
  }

  useEffect(() => {
    api.narrativeFrames().then(setFrames).catch(() => {}).finally(() => setLoading(false))
    api.campaign().then(c => setCandidateName(lastName(c.candidate_name))).catch(() => {})
    api.opponents().then(o => { if (o[0]) setOpponentName(lastName(o[0].name)) }).catch(() => {})
  }, [])

  // Per-frame quadrant key (cached on the frame object for cheap re-use).
  function frameQuadrant(f: NarrativeFrame): QuadrantKey {
    return quadrantKey(f.owner_type ?? null, f.subject_type ?? null)
  }

  // Quadrant metadata with the actual candidate / opponent names baked in
  // (e.g. "Cognetti's Defense" instead of "Our Defense"). Rebuilt only
  // when names change.
  const quadrants = buildQuadrants(candidateName, opponentName)

  const filtered = frames
    .filter(f => {
      if (filterQuadrant !== 'all' && frameQuadrant(f) !== filterQuadrant) return false
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

  // Group filtered frames by quadrant — used both for the 2×2 matrix
  // (filterQuadrant === 'all') and for showing the active count badges.
  const byQuadrant = (k: QuadrantKey) => filtered.filter(f => frameQuadrant(f) === k)
  const mediaFrames = filtered.filter(f => frameQuadrant(f) === 'media')

  // Total-data counts for the quadrant filter dropdown (so labels show
  // "Our Offense · 11" even when the current filter has narrowed the set).
  const totalCounts: Record<QuadrantKey, number> = {
    our_defense:   frames.filter(f => frameQuadrant(f) === 'our_defense').length,
    our_offense:   frames.filter(f => frameQuadrant(f) === 'our_offense').length,
    their_defense: frames.filter(f => frameQuadrant(f) === 'their_defense').length,
    their_offense: frames.filter(f => frameQuadrant(f) === 'their_offense').length,
    media:         frames.filter(f => frameQuadrant(f) === 'media').length,
  }

  async function suggestFrames() {
    setSuggesting(true)
    try {
      const result = await api.suggestFrames()
      if (result.suggestions?.length) setFrames(prev => [...prev, ...result.suggestions])
    } catch { /* silently fail */ } finally {
      setSuggesting(false)
    }
  }

  function reloadFrames() {
    api.narrativeFrames().then(setFrames).catch(() => {})
  }

  return (
    <div style={{ minHeight: '100%', background: C.bg1 }}>
      {/* AI-noticed pending suggestions banner. Renders nothing when empty. */}
      <PendingSuggestionsSection
        onPromoted={reloadFrames}
        candidateName={candidateName}
        opponentName={opponentName}
      />

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
          <select
            className="input"
            value={filterQuadrant}
            onChange={e => setFilterQuadrant(e.target.value as QuadrantFilter)}
            style={{ width: 220 }}
            title="Filter by strategic quadrant"
          >
            <option value="all">All quadrants</option>
            <option value="our_defense">🟦 {quadrants[0].title} · {totalCounts.our_defense}</option>
            <option value="our_offense">🟩 {quadrants[1].title} · {totalCounts.our_offense}</option>
            <option value="their_defense">🟥 {quadrants[2].title} · {totalCounts.their_defense}</option>
            <option value="their_offense">🟧 {quadrants[3].title} · {totalCounts.their_offense}</option>
            {totalCounts.media > 0 && (
              <option value="media">⚪️ Neutral · {totalCounts.media}</option>
            )}
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
          {(filterQuadrant !== 'all' || filterStage !== 'all' || search || sortBy !== 'relevance') && (
            <button
              onClick={() => { setFilterQuadrant('all'); setFilterStage('all'); setSearch(''); setSortBy('relevance') }}
              style={{ background: 'none', border: 'none', color: C.text2, cursor: 'pointer', fontSize: 13, display: 'flex', alignItems: 'center', gap: 4 }}
            >
              <RefreshCw size={12} /> Clear
            </button>
          )}
          <div style={{ display: 'flex', gap: 8, marginLeft: 'auto', alignItems: 'center' }}>
            <button onClick={() => setShowAdd(true)} className="btn btn-primary">
              <Plus size={13} />
              Add Frame
            </button>
          </div>
        </div>
      </div>

      {/* Quadrant matrix (or single-quadrant focused list when filtered) */}
      <div style={{ padding: '16px 28px' }}>
        {loading && (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 16 }}>
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i}>
                {Array.from({ length: 2 }).map((_, j) => (
                  <div key={j} className="skeleton" style={{ height: 100, marginBottom: 8 }} />
                ))}
              </div>
            ))}
          </div>
        )}

        {!loading && filtered.length === 0 && (
          <div style={{ textAlign: 'center', padding: '60px 0', color: C.text3 }}>
            <div style={{ fontSize: 18, fontWeight: 600, color: C.text2, marginBottom: 8 }}>
              No narratives match your filters
            </div>
            <div style={{ fontSize: 13 }}>Try adjusting your search or add a new frame.</div>
          </div>
        )}

        {/* ─── Default view: two campaign columns, each with stacked
              collapsible cards (Defense on top, Offense below). ─── */}
        {!loading && filtered.length > 0 && filterQuadrant === 'all' && (
          <div style={{
            display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)',
            gap: 20, alignItems: 'start',
          }}>
            {/* ── Our Campaign column ── */}
            <div>
              <ColumnHeader title={candidateName ? `${candidateName}'s Campaign` : 'Our Campaign'} />
              <CollapsibleQuadrantCard
                quadrant={quadrants[0]} // our_defense
                frames={byQuadrant('our_defense')}
                expanded={expandedQuadrants.has('our_defense')}
                onToggle={() => toggleQuadrant('our_defense')}
                onEdit={setEditFrame}
              />
              <CollapsibleQuadrantCard
                quadrant={quadrants[1]} // our_offense
                frames={byQuadrant('our_offense')}
                expanded={expandedQuadrants.has('our_offense')}
                onToggle={() => toggleQuadrant('our_offense')}
                onEdit={setEditFrame}
              />
            </div>

            {/* ── Their Campaign column ── */}
            <div>
              <ColumnHeader title={opponentName ? `${opponentName}'s Campaign` : 'Their Campaign'} />
              <CollapsibleQuadrantCard
                quadrant={quadrants[2]} // their_defense
                frames={byQuadrant('their_defense')}
                expanded={expandedQuadrants.has('their_defense')}
                onToggle={() => toggleQuadrant('their_defense')}
                onEdit={setEditFrame}
              />
              <CollapsibleQuadrantCard
                quadrant={quadrants[3]} // their_offense
                frames={byQuadrant('their_offense')}
                expanded={expandedQuadrants.has('their_offense')}
                onToggle={() => toggleQuadrant('their_offense')}
                onEdit={setEditFrame}
              />
            </div>

            {/* Neutral (press-driven or missing subject) — full-width below,
                also collapsible. Bucket name maps to the legacy 'media'
                quadrant key, which is still what quadrantKey() returns. */}
            {mediaFrames.length > 0 && (
              <div style={{ gridColumn: '1 / -1', marginTop: 4 }}>
                <CollapsibleQuadrantCard
                  quadrant={{
                    key: 'media',
                    color: QuadrantPalette.media,
                    title: 'Neutral',
                    subtitle: 'Press-driven or no clear side',
                    description: 'Narratives that don\'t clearly favor either side — press-driven coverage, horse-race stories, or narratives where the subject isn\'t pinned to a specific candidate.',
                  }}
                  frames={mediaFrames}
                  expanded={expandedQuadrants.has('media')}
                  onToggle={() => toggleQuadrant('media')}
                  onEdit={setEditFrame}
                />
              </div>
            )}
          </div>
        )}

        {/* ─── Filtered view: single quadrant, wider card grid ─── */}
        {!loading && filtered.length > 0 && filterQuadrant !== 'all' && (() => {
          const q = quadrants.find(x => x.key === filterQuadrant)
          const color = filterQuadrant === 'media' ? QuadrantPalette.media : (q?.color ?? QuadrantPalette.media)
          const title = q?.title ?? 'Neutral'
          const subtitle = q?.subtitle ?? 'Press-driven or no clear side'
          const description = q?.description ?? 'Narratives that don\'t clearly favor either side — press-driven coverage, horse-race stories, or narratives where the subject isn\'t pinned to a specific candidate.'
          return (
            <>
              <div style={{
                display: 'flex', alignItems: 'center', gap: 10,
                padding: '8px 4px 12px', marginBottom: 4,
                borderBottom: `2px solid ${color}`,
              }}>
                <span style={{ width: 11, height: 11, borderRadius: '50%', background: color, display: 'inline-block' }} />
                <span style={{ fontSize: 16, fontWeight: 800, color: C.text1, display: 'inline-flex', alignItems: 'center' }}>
                  {title}
                  <InfoTooltip text={description} />
                </span>
                <span style={{ fontSize: 12, color: C.text3 }}>· {subtitle}</span>
                <span style={{ marginLeft: 'auto', fontSize: 13, fontWeight: 700, color: C.text3 }}>
                  {filtered.length} {filtered.length === 1 ? 'narrative' : 'narratives'}
                </span>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 12 }}>
                {filtered.map(frame => (
                  <FrameCard key={frame.id} frame={frame} onEdit={setEditFrame} />
                ))}
              </div>
            </>
          )
        })()}
      </div>

      {showAdd && (
        <AddFrameModal
          onClose={() => setShowAdd(false)}
          onCreated={f => setFrames(prev => [...prev, f])}
          candidateName={candidateName}
          opponentName={opponentName}
        />
      )}
      {editFrame && (
        <EditFrameModal
          frame={editFrame}
          onClose={() => setEditFrame(null)}
          onUpdated={updated => setFrames(prev => prev.map(f => f.id === updated.id ? updated : f))}
          candidateName={candidateName}
          opponentName={opponentName}
        />
      )}
    </div>
  )
}

/**
 * Small subdued label above each campaign column (e.g. "Cognetti's
 * Campaign" / "Bresnahan's Campaign"). Keeps the column ownership clear
 * without competing visually with the quadrant card titles.
 */
function ColumnHeader({ title }: { title: string }) {
  return (
    <div style={{
      fontSize: 11, fontWeight: 700, letterSpacing: '0.1em',
      textTransform: 'uppercase', color: C.text3,
      padding: '4px 4px 10px',
    }}>
      {title}
    </div>
  )
}
