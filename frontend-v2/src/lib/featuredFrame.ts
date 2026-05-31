// v2.3 — adds cross-tier transition detectors
/**
 * Featured-narrative card helpers.
 *
 * Powers the Dashboard "Featured Narratives" panel. The old card showed
 * "+N this week" — week-over-week delta of raw mention counts — which is
 * statistically noisy at our scale (most frames have 2-15 weekly mentions)
 * and ignores meaningful signals already on the frame object: cross-tier
 * outlet propagation, the system's own strategic_lens.urgency call, and
 * the activity_30d shape.
 *
 * This module produces, per frame:
 *   - surfaceReason      → "Why am I showing this right now?"
 *                          (Going Viral / Accelerating / Re-emerged / …)
 *   - sparklinePath      → 14-day SVG path d-string for inline rendering
 *
 * And one ranking helper:
 *   - selectFeatured     → multi-objective score + soft owner/stage diversity caps
 *
 * All inputs come from existing API fields on NarrativeFrame — no backend
 * work required.
 */

import type { NarrativeFrame, ActivityPoint } from '@/api/types'

// ────────────────────────────────────────────────────────────────────────
// Surface reason — answers "Why am I showing this right now?"
// Returns at most one label, picked from a priority-ordered set of
// detectors. Returns null when nothing notable is happening (better
// silence than a misleading badge).
//
// Detectors only use fields that are actually populated in the live
// /api/narrative-frames response: momentum_signal, stage, activity_30d
// (date+count only, no per-day tier breakdown), outlet_tiers (aggregate),
// days_active_last_7, unique_outlets_this_week/last_week, mentions_*.
// ────────────────────────────────────────────────────────────────────────

export type SurfaceReason = { label: string } | null

export function surfaceReason(frame: NarrativeFrame): SurfaceReason {
  // 1. The strongest momentum signals from the classifier win unconditionally.
  //    "Viral" and "Under-covered" describe situations that override any
  //    cross-tier framing — a viral story IS already national, and an
  //    under-covered narrative is what the campaign should amplify.
  if (frame.momentum_signal === 'viral')            return { label: 'Going viral' }
  if (frame.momentum_signal === 'missing_coverage') return { label: 'Under-covered' }

  const activity = frame.activity_30d ?? []

  // 2. Cross-tier transitions — high-information discrete events. These
  //    beat the generic "amplified"/"elite_only" categorical labels
  //    because "we just got our first national mention" is more actionable
  //    than "the press is talking about this."
  //
  //    Window: last 7 calendar days vs prior 21 days. The 7-day window is
  //    more natural for "the story is breaking out" than a strict 3-day
  //    cut, and matches how the campaign thinks about coverage cycles.
  //    The activity_30d array ships as a dense 30-day window with zero-
  //    filled gaps (Phase 2.3); we still guard for legacy responses that
  //    don't carry tier fields.
  if (activity.length >= 28 && activity[activity.length - 1].national !== undefined) {
    const last7  = activity.slice(-7)
    const prior21 = activity.slice(-28, -7)
    const last7National = sumTier(last7, 'national')
    const priorNational = sumTier(prior21, 'national')
    const last7Regional = sumTier(last7, 'regional')
    const priorRegional = sumTier(prior21, 'regional')
    const last7Total    = sumCount(last7)
    const priorLocal    = sumTier(prior21, 'local')
    const priorSocial   = sumTier(prior21, 'social')

    // Crossed into national: previously absent, now present with real volume.
    if (last7National >= 1 && priorNational === 0 && last7Total >= 3) {
      return { label: 'Crossed into national' }
    }
    // Regional pickup: previously local/social-only, now hitting regional or
    // national tiers. Requires a real prior baseline in the smaller tiers.
    if (
      (last7Regional + last7National) >= 2
      && priorNational === 0
      && priorRegional === 0
      && (priorLocal + priorSocial) >= 3
    ) {
      return { label: 'Regional pickup' }
    }
  }

  // 3. Remaining classifier labels — categorical state descriptors that
  //    are useful when no discrete event fired above.
  if (frame.momentum_signal === 'amplified')  return { label: 'Press amplification' }
  if (frame.momentum_signal === 'elite_only') return { label: 'Elite outlets only' }

  // 3. Acceleration — last 3-day rate at least 2× the trailing 14-day
  //    rate, gated by a minimum-volume floor so 0→1 doesn't fire.
  if (activity.length >= 14) {
    const last3  = sumCount(activity.slice(-3))
    const last14 = sumCount(activity.slice(-14))
    if (last14 >= 5) {
      const last3Mean  = last3 / 3
      const last14Mean = last14 / 14
      if (last14Mean > 0 && last3Mean >= last14Mean * 2) {
        return { label: 'Accelerating' }
      }
    }
  }

  // 3. Re-emergence — activity 15-30 days ago, a multi-day quiet stretch,
  //    then fresh activity in the last 3 days.
  if (activity.length >= 21) {
    const last3  = sumCount(activity.slice(-3))
    const mid    = sumCount(activity.slice(-14, -3))   // days 4-14 ago
    const old    = sumCount(activity.slice(0, -14))    // 15+ days ago
    if (last3 >= 2 && mid <= 1 && old >= 3) {
      return { label: 'Re-emerged' }
    }
  }

  // 4. Sustained pressure — active most days this week.
  if ((frame.days_active_last_7 ?? 0) >= 5) {
    return { label: 'Sustained pressure' }
  }

  // 5. Broadening reach — picked up by noticeably more outlets WoW.
  const outletDelta = (frame.unique_outlets_this_week ?? 0) - (frame.unique_outlets_last_week ?? 0)
  if (outletDelta >= 3 && (frame.unique_outlets_this_week ?? 0) >= 5) {
    return { label: 'Broadening reach' }
  }

  // 6. Going quiet — was active last week, silent this week.
  if ((frame.mentions_this_week ?? 0) === 0 && (frame.mentions_last_week ?? 0) >= 3) {
    return { label: 'Going quiet' }
  }

  return null
}

// ────────────────────────────────────────────────────────────────────────
// Sparkline — SVG path "d" string for inline rendering. Values normalized
// to the max in the window. Empty string when nothing to draw.
//
// activity_30d entries from the frames-list endpoint only carry {date,
// count}; we ignore tier sub-fields that may or may not be present.
// ────────────────────────────────────────────────────────────────────────

export function sparklinePath(
  points: ActivityPoint[] | undefined,
  width: number,
  height: number,
): string {
  if (!points || points.length < 2) return ''
  const max = Math.max(...points.map(p => p.count ?? 0), 1)
  const step = width / (points.length - 1)
  return points.map((p, i) => {
    const x = i * step
    const y = height - ((p.count ?? 0) / max) * height
    return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
}

// ────────────────────────────────────────────────────────────────────────
// Multi-objective ranking.
// Each component is a "reason to surface" — a frame can earn visibility
// multiple ways. Caps prevent any one component from drowning the others.
// Selection adds soft owner/stage diversity so the panel doesn't show 8
// versions of the same flavor.
// ────────────────────────────────────────────────────────────────────────

export function urgencyPoints(frame: NarrativeFrame): number {
  switch (frame.strategic_lens?.urgency) {
    case 'high':   return 40
    case 'medium': return 20
    case 'low':    return 5
    default:        return 0
  }
}

export function accelerationPoints(frame: NarrativeFrame): number {
  const activity = frame.activity_30d ?? []
  if (activity.length < 14) return 0
  const last3  = sumCount(activity.slice(-3))
  const last14 = sumCount(activity.slice(-14))
  if (last14 < 5) return 0
  const last3Mean  = last3 / 3
  const last14Mean = last14 / 14
  if (last14Mean === 0) return 0
  const ratio = last3Mean / last14Mean
  if (ratio >= 3)   return 25
  if (ratio >= 2)   return 18
  if (ratio >= 1.5) return 10
  return 0
}

export function noveltyPoints(frame: NarrativeFrame): number {
  switch (frame.stage) {
    case 'emerging':    return 15
    case 'resurfacing': return 12
    case 'spreading':   return 8
    default:             return 0
  }
}

export function propagationPoints(frame: NarrativeFrame): number {
  const t = frame.outlet_tiers
  if (!t) return 0
  // National pickup is rarer and worth proportionally more than a local
  // blog hit. Cap so a wire-syndicated story across 30 locals can't
  // outrun a 2-national + defensive-urgency narrative.
  const raw = t.national * 6 + t.regional * 3 + t.local * 1.5 + t.social * 0.5
  return Math.min(raw, 30)
}

export function persistencePoints(frame: NarrativeFrame): number {
  const d = frame.days_active_last_7 ?? 0
  return Math.min(d * 2, 14)
}

export function momentumPoints(frame: NarrativeFrame): number {
  switch (frame.momentum_signal) {
    case 'viral':            return 20
    case 'missing_coverage': return 18
    case 'amplified':        return 15
    case 'elite_only':        return 8
    default:                  return 0
  }
}

// Saturation penalty — demote a frame that has already been featured
// 3+ of the last 7 days, unless its urgency or acceleration keeps it
// strong enough to outrun the penalty. The threshold is set so a high-
// urgency (40 pts) defensive frame still wins; a stable "amplified"
// frame featured 5 days running drops out of the top 8.
//
// 0-2 days featured: no penalty
// 3 days: -8
// 4 days: -16
// 5+ days: -24
export function saturationPenalty(frame: NarrativeFrame): number {
  const days = frame.days_featured_last_7 ?? 0
  if (days <= 2) return 0
  return Math.min((days - 2) * 8, 24)
}

export function multiObjectiveScore(frame: NarrativeFrame): number {
  return urgencyPoints(frame)
       + accelerationPoints(frame)
       + noveltyPoints(frame)
       + propagationPoints(frame)
       + persistencePoints(frame)
       + momentumPoints(frame)
       - saturationPenalty(frame)
}

// ────────────────────────────────────────────────────────────────────────
// Selection with soft diversity caps + memo pinning.
//
// Frames cited in today's briefing memo (passed via pinnedFrameIds) are
// guaranteed slots in the panel — the editorial memo and the algorithmic
// featured panel should never disagree about what matters today. Pinned
// frames go first (sorted by multi-objective score) and are exempt from
// the diversity caps. Remaining slots are filled by the existing ranker
// with caps decremented by what was already pinned, so the *combined*
// panel still respects diversity. If pinned > n, lowest-scoring pinned
// drop out (rare — memos typically cite 2–4 distinct frames).
// ────────────────────────────────────────────────────────────────────────

const OWNER_CAP = 4   // ≤ 4 of 8 cards from one owner
const STAGE_CAP = 3   // ≤ 3 of 8 cards from one stage

// A frame is "live" enough to be ranked into Featured Narratives if it has
// any activity in the last 30 days AND isn't marked dormant. This is a
// hard gate that fires before scoring — without it, propagationPoints
// (which sums lifetime outlet_tiers) can push a long-quiet frame into the
// top 8 on a quiet day, producing a card with an empty sparkline and a
// "0 this week" detail page. The operator's mental model is "if the graph
// is empty, it doesn't belong here," and this filter encodes that. Pinned
// frames bypass the gate — the memo's editorial call always wins.
function isLive(frame: NarrativeFrame): boolean {
  if (frame.stage === 'dormant') return false
  const activity = frame.activity_30d
  if (!activity || activity.length === 0) return true  // legacy: assume live
  return activity.some(p => (p.count ?? 0) > 0)
}

export function selectFeatured(
  frames: NarrativeFrame[],
  n: number = 8,
  pinnedFrameIds?: ReadonlySet<number> | null,
): NarrativeFrame[] {
  const eligible = frames.filter(
    f => (pinnedFrameIds?.has(f.id) ?? false) || isLive(f),
  )
  const ranked = [...eligible].sort((a, b) =>
    multiObjectiveScore(b) - multiObjectiveScore(a),
  )

  const picked: NarrativeFrame[] = []
  const pickedIds = new Set<number>()
  const ownerCounts: Record<string, number> = {}
  const stageCounts: Record<string, number> = {}

  // Pinning pass: place memo-cited frames first (score-ordered), exempt
  // from caps, truncated to n if there's somehow more pinned than slots.
  // Filter operates on the post-filter ranked list — if the user has an
  // active owner/stage filter that excludes a pinned frame, we respect
  // that override.
  if (pinnedFrameIds && pinnedFrameIds.size > 0) {
    for (const f of ranked) {
      if (picked.length >= n) break
      if (!pinnedFrameIds.has(f.id)) continue
      picked.push(f)
      pickedIds.add(f.id)
      const owner = f.owner_type ?? 'media'
      ownerCounts[owner] = (ownerCounts[owner] ?? 0) + 1
      stageCounts[f.stage] = (stageCounts[f.stage] ?? 0) + 1
    }
  }

  // Pass 1: fill remaining slots respecting caps (counts already include
  // anything pinned above).
  for (const f of ranked) {
    if (picked.length >= n) break
    if (pickedIds.has(f.id)) continue
    const owner = f.owner_type ?? 'media'
    const stage = f.stage
    if ((ownerCounts[owner] ?? 0) >= OWNER_CAP) continue
    if ((stageCounts[stage] ?? 0) >= STAGE_CAP) continue
    picked.push(f)
    pickedIds.add(f.id)
    ownerCounts[owner] = (ownerCounts[owner] ?? 0) + 1
    stageCounts[stage] = (stageCounts[stage] ?? 0) + 1
  }

  // Pass 2: backfill ignoring caps if we couldn't fill N (e.g. small DB,
  // only one owner type). Better to show 8 same-flavor than fewer.
  if (picked.length < n) {
    for (const f of ranked) {
      if (picked.length >= n) break
      if (!pickedIds.has(f.id)) {
        picked.push(f)
        pickedIds.add(f.id)
      }
    }
  }

  return picked
}

// ────────────────────────────────────────────────────────────────────────
// Internals
// ────────────────────────────────────────────────────────────────────────

function sumCount(points: ActivityPoint[]): number {
  return points.reduce((s, p) => s + (p.count ?? 0), 0)
}

// Sum a specific tier field across an activity window. Tolerates legacy
// ActivityPoint entries that don't carry tier breakdowns (returns 0).
type TierField = 'national' | 'regional' | 'local' | 'blog' | 'social' | 'unknown'
function sumTier(points: ActivityPoint[], tier: TierField): number {
  return points.reduce((s, p) => {
    const v = (p as Partial<Record<TierField, number>>)[tier]
    return s + (v ?? 0)
  }, 0)
}
