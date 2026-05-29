/**
 * Notifications module — generates a live feed of notifications from
 * current campaign data (spikes, review queue, viral narratives) and
 * persists read/dismissed state + delivery preferences in localStorage.
 *
 * No backend yet — the user's delivery channel preferences (email, SMS,
 * Slack) are stored locally. A future commit can wire these to actual
 * delivery services (Resend / Twilio / Slack webhooks).
 */

import { api } from '@/api/client'

export type NotificationKind =
  | 'spike'
  | 'review_queue'
  | 'proposed_narratives'
  | 'kg_contradictions'
  | 'viral'
  | 'opponent_attack'
  | 'briefing'

export interface Notification {
  /** Stable id used for read/dismiss tracking across reloads. */
  id: string
  kind: NotificationKind
  title: string
  body?: string
  /** ISO timestamp — when the underlying event was observed. */
  timestamp: string
  /** Where clicking the notification should navigate. */
  href?: string
}

export interface NotificationSettings {
  triggers: {
    spike_alerts: boolean
    opponent_attacks: boolean
    review_queue_full: boolean
    proposed_narratives_pending: boolean
    kg_contradictions_pending: boolean
    viral_narratives: boolean
    daily_briefing: boolean
  }
  channels: {
    email: { enabled: boolean; address: string }
    sms: { enabled: boolean; phone: string }
    slack: { enabled: boolean; webhook_url: string }
  }
}

const READ_KEY = 'cwr-notifications-read'
const DISMISSED_KEY = 'cwr-notifications-dismissed'
const SETTINGS_KEY = 'cwr-notification-settings'
const FIRST_SEEN_KEY = 'cwr-notifications-first-seen'

/**
 * Persisted map from notification id → ISO timestamp of when we first
 * observed it. Used as a fallback timestamp for notifications whose
 * underlying event has no native timestamp (e.g. "review queue ≥ 10").
 * This way a "you have 47 items pending" alert that's been showing for
 * 3 hours reads "3h ago" instead of "just now" on every refresh.
 */
function getFirstSeenMap(): Record<string, string> {
  try {
    const raw = localStorage.getItem(FIRST_SEEN_KEY)
    return raw ? (JSON.parse(raw) as Record<string, string>) : {}
  } catch {
    return {}
  }
}

function recordFirstSeen(ids: string[], now: string): Record<string, string> {
  const map = getFirstSeenMap()
  let changed = false
  for (const id of ids) {
    if (!map[id]) { map[id] = now; changed = true }
  }
  if (changed) {
    try { localStorage.setItem(FIRST_SEEN_KEY, JSON.stringify(map)) } catch { /* ignore */ }
  }
  return map
}

// ─────────────────────────── Live notifications ───────────────────────────

/**
 * Pull current campaign state and synthesize a notifications feed.
 *
 * **Timestamps**: each notification gets the most accurate timestamp we
 * have for the underlying event:
 *   - Frame-derived (spike, viral, attack) → the frame's `last_seen_at`
 *     (when the most recent matching article landed). This gives "2h ago"
 *     for a spike that happened 2 hours ago, not "just now."
 *   - Review-queue threshold → "first observed" timestamp persisted in
 *     localStorage. The queue count has no event timestamp, so we record
 *     when we first saw the alert and reuse that across refreshes.
 *
 * Notifications that the user dismissed are filtered out. The list is
 * sorted newest-first.
 */
export async function fetchNotifications(): Promise<Notification[]> {
  const settings = getSettings()
  const dismissed = getDismissedIds()
  const now = new Date().toISOString()

  const out: Notification[] = []

  // Run independently so one failure doesn't sink the rest.
  const [spikes, queueCount, frames, proposals, kg] = await Promise.allSettled([
    api.spikes(),
    api.reviewQueueCount(),
    api.narrativeFrames(),
    api.narrativeProposalsSnapshot(),
    api.entityReviewQueue(),
  ])

  // Build a frame lookup so spike alerts can use the frame's last_seen_at
  // (the Spike API response has no per-spike timestamp).
  const frameById = new Map<number, { last_seen_at?: string }>()
  if (frames.status === 'fulfilled') {
    for (const f of frames.value) frameById.set(f.id, { last_seen_at: f.last_seen_at })
  }

  // Spike alerts — one per surging narrative.
  if (settings.triggers.spike_alerts && spikes.status === 'fulfilled') {
    for (const s of spikes.value) {
      const frameLastSeen = frameById.get(s.frame_id)?.last_seen_at
      out.push({
        id: `spike-${s.frame_id}`,
        kind: 'spike',
        title: `${s.frame_name} surged ${s.ratio.toFixed(1)}× in 24h`,
        body: `Reach ${s.reach_24h.toLocaleString()} — worth a quick look`,
        timestamp: frameLastSeen || now,
        href: `/narratives/${s.frame_id}`,
      })
    }
  }

  // Review queue threshold alert. Stable id (no count in it) so the alert
  // persists across small count changes, and so the first-observed
  // timestamp remains valid as the queue grows.
  if (
    settings.triggers.review_queue_full &&
    queueCount.status === 'fulfilled' &&
    queueCount.value.count >= 10
  ) {
    out.push({
      id: 'review-queue-backed-up',
      kind: 'review_queue',
      title: `${queueCount.value.count} articles pending review`,
      body: 'Triage them in the Articles tab of the Review Queue',
      timestamp: now, // overridden below by first-observed
      href: '/review',
    })
  }

  // Proposed-narrative alert — surfaces any open snapshot row. Even one
  // pending proposal is worth flagging because each represents a cluster
  // the system thinks is forming. Stable id so the first-observed
  // timestamp survives count changes.
  if (
    settings.triggers.proposed_narratives_pending &&
    proposals.status === 'fulfilled'
  ) {
    const n = (proposals.value.clusters || []).length
    if (n >= 1) {
      out.push({
        id: 'proposed-narratives-pending',
        kind: 'proposed_narratives',
        title: `${n} proposed narrative${n === 1 ? '' : 's'} pending`,
        body: 'Review and promote in the Proposed Narratives tab',
        timestamp: now, // overridden below
        href: '/review?tab=narratives',
      })
    }
  }

  // KG contradictions alert — pairs where the graph has both support- and
  // opposition-type relations against the same target. Threshold of 1 so
  // even a single contradiction surfaces; the user can filter on the page.
  if (
    settings.triggers.kg_contradictions_pending &&
    kg.status === 'fulfilled'
  ) {
    const n = kg.value.summary?.contradictions ?? 0
    if (n >= 1) {
      out.push({
        id: 'kg-contradictions-pending',
        kind: 'kg_contradictions',
        title: `${n} KG contradiction${n === 1 ? '' : 's'} pending`,
        body: 'Resolve in the KG Contradictions tab',
        timestamp: now, // overridden below
        href: '/review?tab=kg',
      })
    }
  }

  // Viral narratives — momentum_signal === 'viral'.
  if (settings.triggers.viral_narratives && frames.status === 'fulfilled') {
    for (const f of frames.value) {
      if (f.momentum_signal === 'viral') {
        out.push({
          id: `viral-${f.id}`,
          kind: 'viral',
          title: `${f.name} is going viral`,
          body: 'Articles AND search volume both spiking',
          timestamp: f.last_seen_at || now,
          href: `/narratives/${f.id}`,
        })
      }
    }
  }

  // Opponent-attack alert — defensive posture, high urgency.
  if (settings.triggers.opponent_attacks && frames.status === 'fulfilled') {
    for (const f of frames.value) {
      const lens = f.strategic_lens
      if (lens?.posture === 'defensive' && lens?.urgency === 'high') {
        out.push({
          id: `attack-${f.id}`,
          kind: 'opponent_attack',
          title: `Opponent attack needs response: ${f.name}`,
          body: lens.action || 'Strategic response required',
          timestamp: f.last_seen_at || now,
          href: `/narratives/${f.id}`,
        })
      }
    }
  }

  // Filter dismissed first so we don't pollute the first-seen tracker
  // with re-dismissed alerts.
  const live = out.filter(n => !dismissed.has(n.id))

  // For notifications whose underlying event has no native timestamp
  // (the review queue threshold alert), pull the persisted first-observed
  // timestamp so the relative-time display ages naturally instead of
  // resetting to "just now" on every refresh.
  const firstSeen = recordFirstSeen(live.map(n => n.id), now)
  const queueLikeKinds: NotificationKind[] = [
    'review_queue', 'proposed_narratives', 'kg_contradictions',
  ]
  for (const n of live) {
    if (queueLikeKinds.includes(n.kind) && firstSeen[n.id]) {
      n.timestamp = firstSeen[n.id]
    }
  }

  // Sort newest-first so the freshest activity is at the top.
  live.sort((a, b) => b.timestamp.localeCompare(a.timestamp))

  return live
}

// ─────────────────────────── Read / dismissed state ───────────────────────────

function readSet(key: string): Set<string> {
  try {
    const raw = localStorage.getItem(key)
    if (!raw) return new Set()
    const arr = JSON.parse(raw) as unknown
    return new Set(Array.isArray(arr) ? arr.map(String) : [])
  } catch {
    return new Set()
  }
}

function writeSet(key: string, set: Set<string>): void {
  try { localStorage.setItem(key, JSON.stringify([...set])) } catch { /* ignore */ }
}

export function getReadIds(): Set<string> { return readSet(READ_KEY) }
export function getDismissedIds(): Set<string> { return readSet(DISMISSED_KEY) }

export function markRead(ids: string[]): void {
  const s = getReadIds()
  ids.forEach(id => s.add(id))
  writeSet(READ_KEY, s)
}

export function markAllRead(notifications: Notification[]): void {
  markRead(notifications.map(n => n.id))
}

export function dismissNotification(id: string): void {
  const s = getDismissedIds()
  s.add(id)
  writeSet(DISMISSED_KEY, s)
}

export function clearDismissed(): void {
  writeSet(DISMISSED_KEY, new Set())
}

/**
 * Snapshot of the badge count. Reads localStorage + a cached count of
 * notifications computed by the page on its last load (stored in a
 * separate key). Used by the header bell.
 */
const BADGE_COUNT_KEY = 'cwr-notifications-badge-count'

export function setBadgeCount(n: number): void {
  try { localStorage.setItem(BADGE_COUNT_KEY, String(n)) } catch { /* ignore */ }
}

export function getUnreadNotificationCount(): number {
  try {
    const raw = localStorage.getItem(BADGE_COUNT_KEY)
    return raw ? Math.max(0, parseInt(raw, 10) || 0) : 0
  } catch {
    return 0
  }
}

// ─────────────────────────── Settings ───────────────────────────

const DEFAULT_SETTINGS: NotificationSettings = {
  triggers: {
    spike_alerts: true,
    opponent_attacks: true,
    review_queue_full: true,
    proposed_narratives_pending: true,
    kg_contradictions_pending: true,
    viral_narratives: true,
    daily_briefing: false,
  },
  channels: {
    email:  { enabled: false, address: '' },
    sms:    { enabled: false, phone: '' },
    slack:  { enabled: false, webhook_url: '' },
  },
}

export function getSettings(): NotificationSettings {
  try {
    const raw = localStorage.getItem(SETTINGS_KEY)
    if (!raw) return DEFAULT_SETTINGS
    const parsed = JSON.parse(raw) as Partial<NotificationSettings>
    // Merge defensively — missing keys fall back to defaults so adding
    // new triggers/channels in code doesn't break existing prefs.
    return {
      triggers: { ...DEFAULT_SETTINGS.triggers, ...(parsed.triggers || {}) },
      channels: {
        email:  { ...DEFAULT_SETTINGS.channels.email,  ...(parsed.channels?.email  || {}) },
        sms:    { ...DEFAULT_SETTINGS.channels.sms,    ...(parsed.channels?.sms    || {}) },
        slack:  { ...DEFAULT_SETTINGS.channels.slack,  ...(parsed.channels?.slack  || {}) },
      },
    }
  } catch {
    return DEFAULT_SETTINGS
  }
}

export function saveSettings(s: NotificationSettings): void {
  try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(s)) } catch { /* ignore */ }
}
