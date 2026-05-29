/**
 * PromoteModal — shared "track this cluster as a narrative" dialog.
 *
 * Extracted from Landscape.tsx (V13.x) so the Review Queue page can
 * also open it. Pre-V13.10 this lived on the Landscape proposed tab;
 * now Landscape is established-only and proposals are reviewed from
 * /review instead.
 *
 * Takes a cluster + its member candidate frames directly (no Bubble
 * wrapper required), so any page with a NarrativeLandscapeCluster +
 * NarrativeLandscapePoint[] can promote it.
 */
import { useState } from 'react'
import { api } from '@/api/client'
import { invalidateDashboard } from '@/api/dashboardCache'
import { QuadrantSelector, quadrantToTypes } from '@/components/QuadrantSelector'
import { quadrantKey } from '@/lib/quadrantColor'
import type { QuadrantKey } from '@/lib/quadrantColor'
import type {
  NarrativeLandscapeCluster, NarrativeLandscapePoint, OwnerType,
} from '@/api/types'

const C = {
  bg1: 'var(--bg-1)', bg2: 'var(--bg-2)', bg3: 'var(--bg-3)',
  border: 'var(--border)', borderBright: 'var(--border-bright)',
  text1: 'var(--text-1)', text2: 'var(--text-2)', text3: 'var(--text-3)',
  candidate: 'var(--candidate)', opponent: 'var(--opponent)', media: 'var(--media)',
  accent: 'var(--accent)',
  // Tier accents stay fixed across themes — they're semantic data colors.
  tier_national: '#22c55e', tier_regional: '#3b82f6',
  tier_local: '#a78bfa', tier_blog: '#f59e0b', tier_social: '#ef4444',
}

export type OutletTierCounts = {
  national: number; regional: number; local: number; blog: number; social: number;
}

export function OutletTierBar({ counts }: { counts: OutletTierCounts }) {
  const total = counts.national + counts.regional + counts.local + counts.blog + counts.social
  if (total === 0) return <div style={{ fontSize: 11, color: C.text3, fontStyle: 'italic' }}>No outlets linked.</div>
  const segs: Array<[string, number, string, string]> = [
    ['national', counts.national, C.tier_national, 'National'],
    ['regional', counts.regional, C.tier_regional, 'Regional'],
    ['local', counts.local, C.tier_local, 'Local'],
    ['blog', counts.blog, C.tier_blog, 'Blog'],
    ['social', counts.social, C.tier_social, 'Social'],
  ]
  return (
    <div>
      <div style={{ display: 'flex', height: 18, borderRadius: 4, overflow: 'hidden', border: `1px solid ${C.border}`, marginBottom: 6 }}>
        {segs.map(([key, n, color]) => (
          n > 0 ? <div key={key} title={`${key}: ${n}`} style={{ flex: n, background: color, opacity: 0.85 }} /> : null
        ))}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, fontSize: 10, color: C.text2 }}>
        {segs.filter(([, n]) => n > 0).map(([key, n, color, label]) => (
          <span key={key} style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
            <span style={{ width: 8, height: 8, borderRadius: 2, background: color }} />
            <span>{label} {n}</span>
          </span>
        ))}
      </div>
    </div>
  )
}

export function PromoteModal({
  cluster, members, defaultOwner, outletTiers,
  prefilledName, prefilledDescription, prefilledOwner,
  candidateName, opponentName,
  aiBadge,
  onClose, onPromoted,
}: {
  cluster: NarrativeLandscapeCluster
  members: NarrativeLandscapePoint[]
  defaultOwner: OwnerType
  outletTiers: OutletTierCounts
  // Phase D — when a triage verdict pre-fills the form, the AI's
  // improved suggestions seed the initial inputs (user can still edit).
  prefilledName?: string | null
  prefilledDescription?: string | null
  prefilledOwner?: OwnerType | null
  // Surnames so the QuadrantSelector renders "Cognetti's Defense" etc.
  // Default to empty string so callers that haven't been updated yet
  // still get the 5-button picker (with "Our" / "Their" fallback labels).
  candidateName?: string
  opponentName?: string
  // Optional badge to render above the title (e.g. "AI: Suggest promote · 90% confidence")
  aiBadge?: { text: string; tone: 'suggest' | 'merge' | 'uncertain' | 'noise' }
  onClose: () => void
  onPromoted: () => void
}) {
  const [name, setName] = useState(prefilledName || cluster.representative_name)
  const [description, setDescription] = useState(
    prefilledDescription || members[0]?.evidence_quote?.slice(0, 240) || '',
  )
  // Initial quadrant: explicit triage pre-fill wins; else combine the
  // cluster's owner+subject hints into a 5-quadrant key; else fall back
  // to the legacy 3-state default.
  const [quadrant, setQuadrant] = useState<QuadrantKey>(() => {
    const initialOwner = prefilledOwner || defaultOwner
    return quadrantKey(initialOwner, cluster.subject_type_hint ?? null)
  })
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function confirm() {
    setBusy(true); setErr(null)
    try {
      const { owner_type, subject_type } = quadrantToTypes(quadrant)
      await api.promoteCandidateCluster({
        suggested_name: name.trim(),
        suggested_description: description.trim(),
        owner_type,
        subject_type,
        candidate_frame_ids: members.map(m => m.candidate_frame_id),
      })
      invalidateDashboard()
      onPromoted(); onClose()
    } catch (e) { setErr((e as Error)?.message || 'Failed to promote') }
    finally { setBusy(false) }
  }

  // AI badge tone → color. Suggest = accent yellow; merge = candidate blue;
  // uncertain/noise = muted gray.
  const badgeColor = aiBadge?.tone === 'merge' ? C.candidate
                    : (aiBadge?.tone === 'uncertain' || aiBadge?.tone === 'noise') ? C.text3
                    : C.accent

  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', zIndex: 1000, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <div onClick={e => e.stopPropagation()} style={{ background: C.bg2, border: `1px solid ${C.border}`, borderRadius: 10, padding: 20, width: 620, maxHeight: '88vh', overflowY: 'auto' }}>
        {aiBadge && (
          <div style={{
            fontSize: 10, fontWeight: 700, letterSpacing: '0.08em', textTransform: 'uppercase',
            color: badgeColor, background: `${badgeColor}1c`,
            border: `1px solid ${badgeColor}55`, borderRadius: 4,
            padding: '4px 8px', marginBottom: 10, display: 'inline-block',
          }}>
            {aiBadge.text}
          </div>
        )}
        <div style={{ fontSize: 11, color: C.text3, marginBottom: 4, letterSpacing: '0.08em', textTransform: 'uppercase' }}>
          {cluster.size} candidate frames · {cluster.outlet_count} {cluster.outlet_count === 1 ? 'outlet' : 'outlets'}
        </div>
        <div style={{ fontSize: 19, fontWeight: 700, color: C.text1, marginBottom: 16 }}>Track as a narrative</div>
        <div style={{ padding: 12, background: C.bg3, borderRadius: 6, marginBottom: 14 }}>
          <div style={{ fontSize: 10, color: C.text3, marginBottom: 6, letterSpacing: '0.08em', textTransform: 'uppercase', fontWeight: 600 }}>Outlet mix</div>
          <OutletTierBar counts={outletTiers} />
          {cluster.outlet_names.length > 0 && (
            <div style={{ marginTop: 10, fontSize: 11, color: C.text2, lineHeight: 1.5 }}>
              <span style={{ color: C.text3 }}>outlets: </span>{cluster.outlet_names.join(' · ')}
            </div>
          )}
        </div>
        <label style={{ display: 'block', fontSize: 10, color: C.text3, marginBottom: 4, letterSpacing: '0.08em', textTransform: 'uppercase', fontWeight: 600 }}>Name</label>
        <input value={name} onChange={e => setName(e.target.value)} autoFocus
               style={{ width: '100%', padding: '8px 10px', fontSize: 13, background: C.bg3, border: `1px solid ${C.border}`, borderRadius: 5, color: C.text1, marginBottom: 12 }} />
        <label style={{ display: 'block', fontSize: 10, color: C.text3, marginBottom: 4, letterSpacing: '0.08em', textTransform: 'uppercase', fontWeight: 600 }}>Description</label>
        <textarea value={description} onChange={e => setDescription(e.target.value)} rows={3}
                  style={{ width: '100%', padding: '8px 10px', fontSize: 12, background: C.bg3, border: `1px solid ${C.border}`, borderRadius: 5, color: C.text1, marginBottom: 12, fontFamily: 'inherit', resize: 'vertical' }} />
        <label style={{ display: 'block', fontSize: 10, color: C.text3, marginBottom: 4, letterSpacing: '0.08em', textTransform: 'uppercase', fontWeight: 600 }}>Strategic slot</label>
        <div style={{ marginBottom: 14 }}>
          <QuadrantSelector
            value={quadrant}
            onChange={setQuadrant}
            candidateName={candidateName ?? ''}
            opponentName={opponentName ?? ''}
          />
        </div>
        <label style={{ display: 'block', fontSize: 10, color: C.text3, marginBottom: 4, letterSpacing: '0.08em', textTransform: 'uppercase', fontWeight: 600 }}>Member articles ({cluster.size})</label>
        <div style={{ background: C.bg3, border: `1px solid ${C.border}`, borderRadius: 5, maxHeight: 180, overflowY: 'auto', padding: '6px 10px', marginBottom: 14 }}>
          {members.map(m => (
            <div key={m.candidate_frame_id} style={{ padding: '6px 0', borderBottom: `1px solid ${C.border}`, fontSize: 11, lineHeight: 1.4 }}>
              <div style={{ color: C.text1, fontWeight: 500 }}>{m.source_title || m.suggested_name}</div>
              <div style={{ color: C.text3, marginTop: 2, fontSize: 10 }}>
                {m.outlet_name || m.source_name || 'unknown outlet'}
                {m.outlet_type && ` · ${m.outlet_type}`}
                {' · '}AI-proposed: {m.suggested_name}
              </div>
            </div>
          ))}
        </div>
        {err && <div style={{ color: C.opponent, fontSize: 12, marginBottom: 10 }}>{err}</div>}
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <button onClick={onClose} disabled={busy} style={{ padding: '8px 14px', fontSize: 13, background: 'transparent', border: `1px solid ${C.border}`, color: C.text2, borderRadius: 5, cursor: busy ? 'wait' : 'pointer' }}>Cancel</button>
          <button onClick={confirm} disabled={busy || !name.trim()} style={{ padding: '8px 14px', fontSize: 13, fontWeight: 600, background: C.accent, color: '#000', border: 'none', borderRadius: 5, cursor: busy ? 'wait' : 'pointer', opacity: busy || !name.trim() ? 0.6 : 1 }}>
            {busy ? 'Promoting…' : 'Promote'}
          </button>
        </div>
      </div>
    </div>
  )
}
