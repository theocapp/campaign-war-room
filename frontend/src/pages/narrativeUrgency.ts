import type { NarrativeFrameWithCounts } from '../api/types'

export type UrgencyLevel = 'critical' | 'high' | 'medium' | 'low'
export type SortBy = 'urgency' | 'outlets' | 'momentum' | 'recency'

export interface UrgencyResult {
  level: UrgencyLevel
  score: number
  reason: string
}

export function computeUrgency(frame: NarrativeFrameWithCounts): UrgencyResult {
  const { stage, trend, unique_outlets_this_week: tw = 0, unique_outlets_last_week: lw = 0, days_active_last_7: days = 0, owner_type } = frame

  let score = 0
  let reason = ''

  // Opponent frames in spreading/mainstream are most urgent for campaign
  if (owner_type === 'opponent') {
    if (stage === 'spreading')  { score += 60; reason = 'Opposition narrative spreading' }
    if (stage === 'mainstream') { score += 50; reason = 'Opposition narrative mainstream' }
    if (stage === 'emerging')   { score += 30; reason = 'Opposition narrative emerging' }
  }

  // Candidate frames fading are also worth flagging
  if (owner_type === 'candidate') {
    if (stage === 'fading')   { score += 45; reason = 'Campaign narrative fading' }
    if (stage === 'dormant')  { score += 30; reason = 'Campaign narrative dormant' }
  }

  // Trend multiplier
  if (trend === 'up')   score += 20
  if (trend === 'down') score -= 10

  // Outlet velocity
  if (tw > 5)  score += 15
  if (tw > 10) score += 10
  if (lw === 0 && tw > 0) { score += 15; reason = reason || 'New outlet coverage' }

  // Activity density
  if (days >= 5) score += 10

  score = Math.min(100, Math.max(0, score))

  const level: UrgencyLevel =
    score >= 80 ? 'critical' :
    score >= 60 ? 'high' :
    score >= 35 ? 'medium' :
    'low'

  if (!reason) {
    reason = stage === 'dormant' ? 'No recent coverage'
      : trend === 'up' ? 'Gaining traction'
      : trend === 'down' ? 'Losing traction'
      : `${tw} outlet${tw !== 1 ? 's' : ''} this week`
  }

  return { level, score, reason }
}

export function compareFrames(
  a: NarrativeFrameWithCounts,
  b: NarrativeFrameWithCounts,
  sortBy: SortBy,
): number {
  switch (sortBy) {
    case 'outlets':
      return (b.unique_outlets_this_week ?? 0) - (a.unique_outlets_this_week ?? 0)
    case 'momentum': {
      const aM = (a.mentions_this_week ?? 0) - (a.mentions_last_week ?? 0)
      const bM = (b.mentions_this_week ?? 0) - (b.mentions_last_week ?? 0)
      return bM - aM
    }
    case 'recency': {
      const aT = a.last_seen_at ? new Date(a.last_seen_at).getTime() : 0
      const bT = b.last_seen_at ? new Date(b.last_seen_at).getTime() : 0
      return bT - aT
    }
    case 'urgency':
    default:
      return computeUrgency(b).score - computeUrgency(a).score
  }
}
