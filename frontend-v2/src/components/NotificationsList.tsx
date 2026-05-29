import {
  AlertTriangle, Bell, CheckCheck, ChevronDown, ChevronRight, Flag, Flame, Inbox,
  MessageSquare, Settings as SettingsIcon, Sparkles, Trash2, X, Zap,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import type { CSSProperties } from 'react'
import { Link } from 'react-router-dom'
import { InfoTooltip } from '@/components/InfoTooltip'
import {
  clearDismissed,
  dismissNotification,
  fetchNotifications,
  getReadIds,
  markAllRead,
  markRead,
  setBadgeCount,
} from '@/lib/notifications'
import type { Notification, NotificationKind } from '@/lib/notifications'

const C = {
  bg2: 'var(--bg-2)', bg3: 'var(--bg-3)',
  border: 'var(--border)', borderBright: 'var(--border-bright)',
  text1: 'var(--text-1)', text2: 'var(--text-2)', text3: 'var(--text-3)',
  accent: 'var(--accent)', red: 'var(--red)',
}

const KIND_META: Record<NotificationKind, { icon: typeof Bell; color: string; label: string }> = {
  opponent_attack:     { icon: AlertTriangle, color: C.red,    label: 'Opponent attack' },
  spike:               { icon: Zap,           color: C.accent, label: 'Spike alert' },
  viral:               { icon: Flame,         color: '#22d3ee', label: 'Viral' },
  review_queue:        { icon: Inbox,         color: C.text2,  label: 'Articles' },
  proposed_narratives: { icon: Sparkles,      color: C.accent, label: 'Proposed narratives' },
  kg_contradictions:   { icon: Flag,          color: '#a78bfa', label: 'KG contradictions' },
  briefing:            { icon: MessageSquare, color: '#a78bfa', label: 'Briefing' },
}

type Bucket = 'critical' | 'momentum' | 'background'

const BUCKET_META: Record<Bucket, { title: string; color: string; icon: typeof Bell }> = {
  critical:   { title: 'Needs your attention', color: C.red,    icon: AlertTriangle },
  momentum:   { title: 'Momentum & spikes',    color: C.accent, icon: Zap },
  background: { title: 'Background',           color: C.text3,  icon: Inbox },
}

function bucketOf(kind: NotificationKind): Bucket {
  if (kind === 'opponent_attack') return 'critical'
  if (kind === 'spike' || kind === 'viral') return 'momentum'
  return 'background'
}

interface Props {
  /** Called when a notification or settings link is clicked. Lets the
   *  popover container close itself after navigation. */
  onNavigate?: () => void
}

/**
 * Notification feed UI — bucketed by priority. Shared between the popover
 * panel (anchored to the header bell) and the standalone /notifications
 * page. Manages its own data fetching, read/dismiss state, and badge sync.
 */
export function NotificationsList({ onNavigate }: Props) {
  const [notifications, setNotifications] = useState<Notification[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [readIds, setReadIds] = useState<Set<string>>(getReadIds())
  const [collapsed, setCollapsed] = useState<Set<Bucket>>(new Set())

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const n = await fetchNotifications()
        if (cancelled) return
        setNotifications(n)
        const currentReadIds = getReadIds()
        setBadgeCount(n.filter(x => !currentReadIds.has(x.id)).length)
      } catch (e: unknown) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    const t = setInterval(load, 60_000)
    return () => { cancelled = true; clearInterval(t) }
  }, [])

  function handleMarkAllRead() {
    markAllRead(notifications)
    setReadIds(new Set(notifications.map(n => n.id)))
    setBadgeCount(0)
  }

  function handleDismiss(id: string) {
    dismissNotification(id)
    setNotifications(prev => {
      const next = prev.filter(n => n.id !== id)
      setBadgeCount(next.filter(x => !readIds.has(x.id)).length)
      return next
    })
  }

  function handleClickRow(n: Notification) {
    if (!readIds.has(n.id)) {
      markRead([n.id])
      const nextRead = new Set(readIds)
      nextRead.add(n.id)
      setReadIds(nextRead)
      setBadgeCount(notifications.filter(x => !nextRead.has(x.id)).length)
    }
    onNavigate?.()
  }

  function toggleBucket(b: Bucket) {
    setCollapsed(prev => {
      const next = new Set(prev)
      if (next.has(b)) next.delete(b); else next.add(b)
      return next
    })
  }

  const buckets: Record<Bucket, Notification[]> = {
    critical:   notifications.filter(n => bucketOf(n.kind) === 'critical'),
    momentum:   notifications.filter(n => bucketOf(n.kind) === 'momentum'),
    background: notifications.filter(n => bucketOf(n.kind) === 'background'),
  }
  const unreadCount = notifications.filter(n => !readIds.has(n.id)).length

  return (
    <div>
      {/* Sticky header with summary + actions */}
      <div style={{
        position: 'sticky', top: 0, zIndex: 1,
        background: C.bg2,
        padding: '12px 14px',
        borderBottom: `1px solid ${C.border}`,
        display: 'flex', alignItems: 'center', gap: 8,
      }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{
            fontSize: 14, fontWeight: 700, color: C.text1,
            display: 'inline-flex', alignItems: 'center',
          }}>
            Notifications
            <InfoTooltip
              text="Live alerts from your campaign data, grouped by priority. Manage triggers and delivery channels in Setup."
              placement="bottom"
            />
          </div>
          <div style={{ fontSize: 11, color: C.text3, marginTop: 2 }}>
            {loading ? 'Loading…' : <BucketSummary buckets={buckets} unread={unreadCount} />}
          </div>
        </div>
        {unreadCount > 0 && (
          <button
            onClick={handleMarkAllRead}
            title="Mark all read"
            style={{
              background: 'none', border: 'none', cursor: 'pointer',
              padding: '4px 8px', borderRadius: 4,
              color: C.text2, fontSize: 11,
              display: 'inline-flex', alignItems: 'center', gap: 4,
              fontFamily: 'inherit',
            }}
            onMouseEnter={e => { e.currentTarget.style.background = C.bg3 }}
            onMouseLeave={e => { e.currentTarget.style.background = 'transparent' }}
          >
            <CheckCheck size={12} /> Mark read
          </button>
        )}
      </div>

      {/* Body */}
      <div style={{ padding: '8px 8px 0' }}>
        {error && (
          <div style={{
            padding: 12, margin: '8px 6px',
            border: `1px solid ${C.red}`,
            background: 'rgba(239,68,68,0.08)', color: C.red,
            borderRadius: 6, fontSize: 12,
          }}>
            Failed to load: {error}
          </div>
        )}

        {loading ? (
          <div style={{ padding: 8 }}>
            {Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="skeleton" style={{ height: 44, marginBottom: 6 }} />
            ))}
          </div>
        ) : notifications.length === 0 ? (
          <EmptyState />
        ) : (
          <>
            {buckets.critical.length > 0 && (
              <BucketSection
                bucket="critical" notifications={buckets.critical}
                collapsed={collapsed.has('critical')} onToggle={() => toggleBucket('critical')}
                readIds={readIds} onClickRow={handleClickRow} onDismiss={handleDismiss}
                density="prominent"
              />
            )}
            {buckets.momentum.length > 0 && (
              <BucketSection
                bucket="momentum" notifications={buckets.momentum}
                collapsed={collapsed.has('momentum')} onToggle={() => toggleBucket('momentum')}
                readIds={readIds} onClickRow={handleClickRow} onDismiss={handleDismiss}
                density="compact"
              />
            )}
            {buckets.background.length > 0 && (
              <BucketSection
                bucket="background" notifications={buckets.background}
                collapsed={collapsed.has('background')} onToggle={() => toggleBucket('background')}
                readIds={readIds} onClickRow={handleClickRow} onDismiss={handleDismiss}
                density="compact"
              />
            )}
          </>
        )}
      </div>

      {/* Footer with Manage settings link */}
      <div style={{
        position: 'sticky', bottom: 0,
        padding: '10px 14px',
        borderTop: `1px solid ${C.border}`,
        background: C.bg2,
        display: 'flex', alignItems: 'center', justifyContent: 'flex-end',
      }}>
        <Link
          to="/setup#notifications"
          onClick={() => onNavigate?.()}
          style={{
            fontSize: 12, color: C.text2, textDecoration: 'none',
            display: 'inline-flex', alignItems: 'center', gap: 5,
          }}
        >
          <SettingsIcon size={12} /> Manage settings
        </Link>
      </div>
    </div>
  )
}

// ─────────────────────── Summary ───────────────────────

function BucketSummary({ buckets, unread }: {
  buckets: Record<Bucket, Notification[]>; unread: number
}) {
  const parts: string[] = []
  if (buckets.critical.length > 0)   parts.push(`${buckets.critical.length} need attention`)
  if (buckets.momentum.length > 0)   parts.push(`${buckets.momentum.length} momentum`)
  if (buckets.background.length > 0) parts.push(`${buckets.background.length} background`)
  return (
    <span>
      {parts.join(' · ')}
      {unread > 0 && <span> · {unread} unread</span>}
    </span>
  )
}

// ─────────────────────── Bucket section ───────────────────────

function BucketSection({
  bucket, notifications, collapsed, onToggle, readIds, onClickRow, onDismiss, density,
}: {
  bucket: Bucket
  notifications: Notification[]
  collapsed: boolean
  onToggle: () => void
  readIds: Set<string>
  onClickRow: (n: Notification) => void
  onDismiss: (id: string) => void
  density: 'prominent' | 'compact'
}) {
  const meta = BUCKET_META[bucket]
  const Icon = meta.icon
  const unreadHere = notifications.filter(n => !readIds.has(n.id)).length

  return (
    <section style={{ marginBottom: 8 }}>
      <button
        type="button"
        onClick={onToggle}
        style={{
          display: 'flex', alignItems: 'center', gap: 6, width: '100%',
          padding: '4px 6px 6px',
          background: 'none', border: 'none', cursor: 'pointer',
          color: 'inherit', textAlign: 'left', fontFamily: 'inherit',
        }}
      >
        {collapsed
          ? <ChevronRight size={12} style={{ color: C.text3 }} />
          : <ChevronDown size={12} style={{ color: C.text3 }} />
        }
        <Icon size={12} style={{ color: meta.color }} />
        <span style={{
          fontSize: 10, fontWeight: 700, letterSpacing: '0.08em',
          textTransform: 'uppercase', color: C.text2,
        }}>
          {meta.title}
        </span>
        <span style={{
          fontSize: 10, color: C.text3, fontVariantNumeric: 'tabular-nums',
        }}>
          {notifications.length}
          {unreadHere > 0 && unreadHere < notifications.length && ` · ${unreadHere} new`}
        </span>
      </button>

      {!collapsed && (
        <div>
          {notifications.map(n =>
            density === 'prominent'
              ? <ProminentRow
                  key={n.id} notification={n}
                  read={readIds.has(n.id)}
                  onClick={() => onClickRow(n)}
                  onDismiss={() => onDismiss(n.id)}
                />
              : <CompactRow
                  key={n.id} notification={n}
                  read={readIds.has(n.id)}
                  onClick={() => onClickRow(n)}
                  onDismiss={() => onDismiss(n.id)}
                />
          )}
        </div>
      )}
    </section>
  )
}

// ─────────────────────── Rows ───────────────────────

function ProminentRow({ notification, read, onClick, onDismiss }: {
  notification: Notification; read: boolean
  onClick: () => void; onDismiss: () => void
}) {
  const meta = KIND_META[notification.kind]
  const [hovered, setHovered] = useState(false)

  const inner = (
    <div
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onClick={onClick}
      style={{
        display: 'flex', alignItems: 'flex-start', gap: 10,
        padding: '10px 12px',
        borderRadius: 6, marginBottom: 4,
        background: hovered ? C.bg3 : `${meta.color}0a`,
        border: `1px solid ${hovered ? meta.color : `${meta.color}33`}`,
        borderLeft: `3px solid ${meta.color}`,
        cursor: notification.href ? 'pointer' : 'default',
        transition: 'background 0.1s ease, border-color 0.1s ease',
      } as CSSProperties}
    >
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{
          fontSize: 13, fontWeight: read ? 500 : 700, color: C.text1,
          lineHeight: 1.3,
        }}>
          {notification.title}
        </div>
        {notification.body && (
          <div style={{ fontSize: 11, color: C.text2, marginTop: 2, lineHeight: 1.4 }}>
            {notification.body}
          </div>
        )}
        <div style={{
          fontSize: 9, color: C.text3, letterSpacing: '0.05em',
          textTransform: 'uppercase', marginTop: 4,
        }}>
          {meta.label} · {formatRelative(notification.timestamp)}
        </div>
      </div>
      <DismissButton onDismiss={onDismiss} />
    </div>
  )

  return notification.href
    ? <Link to={notification.href} style={{ textDecoration: 'none', color: 'inherit' }}>{inner}</Link>
    : inner
}

function CompactRow({ notification, read, onClick, onDismiss }: {
  notification: Notification; read: boolean
  onClick: () => void; onDismiss: () => void
}) {
  const meta = KIND_META[notification.kind]
  const [hovered, setHovered] = useState(false)

  const inner = (
    <div
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onClick={onClick}
      style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '6px 10px',
        borderRadius: 4, marginBottom: 2,
        background: hovered ? C.bg3 : 'transparent',
        borderLeft: `3px solid ${read ? 'transparent' : meta.color}`,
        cursor: notification.href ? 'pointer' : 'default',
        transition: 'background 0.1s ease',
      } as CSSProperties}
    >
      <span style={{
        flexShrink: 0, width: 5, height: 5, borderRadius: '50%',
        background: meta.color,
      }} />
      <span style={{
        fontSize: 12, color: C.text1, fontWeight: read ? 400 : 500,
        flex: 1, minWidth: 0,
        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
      }}>
        {notification.title}
      </span>
      <span style={{
        fontSize: 10, color: C.text3, flexShrink: 0,
        fontVariantNumeric: 'tabular-nums',
      }}>
        {formatRelative(notification.timestamp)}
      </span>
      <DismissButton onDismiss={onDismiss} visible={hovered} />
    </div>
  )

  return notification.href
    ? <Link to={notification.href} style={{ textDecoration: 'none', color: 'inherit' }}>{inner}</Link>
    : inner
}

function DismissButton({ onDismiss, visible = true }: {
  onDismiss: () => void; visible?: boolean
}) {
  return (
    <button
      type="button"
      onClick={e => { e.stopPropagation(); onDismiss() }}
      title="Dismiss"
      aria-label="Dismiss notification"
      style={{
        flexShrink: 0,
        background: 'transparent', border: 'none', cursor: 'pointer',
        color: C.text3, padding: 2, borderRadius: 4,
        display: 'inline-flex', alignItems: 'center',
        opacity: visible ? 1 : 0,
        transition: 'opacity 0.1s ease',
      }}
    >
      <X size={12} />
    </button>
  )
}

function EmptyState() {
  return (
    <div style={{
      padding: '36px 20px', textAlign: 'center',
      color: C.text3, fontSize: 13,
    }}>
      <Bell size={28} style={{ opacity: 0.3, marginBottom: 8 }} />
      <div style={{ fontSize: 13, fontWeight: 600, color: C.text2, marginBottom: 2 }}>
        You're all caught up
      </div>
      <div style={{ fontSize: 12 }}>No notifications right now.</div>
      <button
        onClick={() => { clearDismissed(); location.reload() }}
        className="btn btn-ghost"
        style={{ marginTop: 12, fontSize: 11 }}
      >
        <Trash2 size={11} /> Restore dismissed
      </button>
    </div>
  )
}

function formatRelative(iso: string): string {
  const diffMin = Math.floor((Date.now() - new Date(iso).getTime()) / 60_000)
  if (diffMin < 1) return 'just now'
  if (diffMin < 60) return `${diffMin}m ago`
  const h = Math.floor(diffMin / 60)
  if (h < 24) return `${h}h ago`
  const d = Math.floor(h / 24)
  if (d < 30) return `${d}d ago`
  return new Date(iso).toLocaleDateString()
}
