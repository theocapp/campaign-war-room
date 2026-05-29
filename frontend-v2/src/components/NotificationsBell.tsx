import { Bell } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { getUnreadNotificationCount } from '@/lib/notifications'
import { NotificationsList } from './NotificationsList'

/**
 * Header bell button. Clicking opens a popover panel anchored under the
 * button — the user sees their notifications inline without navigating
 * away from the page they're on.
 *
 * Close behaviors:
 *   - Click outside the panel
 *   - Press Escape
 *   - Click a notification (handled by NotificationsList via onNavigate)
 */
export function NotificationsBell() {
  const [open, setOpen] = useState(false)
  // Re-render on close so the unread badge re-reads from localStorage
  // (the panel may have marked items read).
  const [badgeNonce, setBadgeNonce] = useState(0)
  void badgeNonce
  const unread = getUnreadNotificationCount()
  const wrapRef = useRef<HTMLDivElement | null>(null)

  // Close on outside click or Escape.
  useEffect(() => {
    if (!open) return
    function onDocClick(e: MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false)
        setBadgeNonce(n => n + 1)
      }
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        setOpen(false)
        setBadgeNonce(n => n + 1)
      }
    }
    document.addEventListener('mousedown', onDocClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDocClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div ref={wrapRef} style={{ position: 'relative', display: 'inline-flex' }}>
      <button
        type="button"
        onClick={() => setOpen(o => !o)}
        title="Notifications"
        aria-label={unread > 0 ? `Notifications (${unread} unread)` : 'Notifications'}
        aria-expanded={open}
        style={{
          position: 'relative',
          display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
          width: 32, height: 32, borderRadius: 8,
          background: open ? 'var(--bg-3)' : 'transparent',
          border: '1px solid var(--border)',
          color: open ? 'var(--text-1)' : 'var(--text-2)',
          cursor: 'pointer',
          transition: 'background 0.1s ease, color 0.1s ease',
        }}
        onMouseEnter={e => {
          if (open) return
          e.currentTarget.style.background = 'var(--bg-3)'
          e.currentTarget.style.color = 'var(--text-1)'
        }}
        onMouseLeave={e => {
          if (open) return
          e.currentTarget.style.background = 'transparent'
          e.currentTarget.style.color = 'var(--text-2)'
        }}
      >
        <Bell size={15} />
        {unread > 0 && (
          <span
            aria-hidden
            style={{
              position: 'absolute', top: -3, right: -3,
              minWidth: 16, height: 16, padding: '0 4px',
              borderRadius: 999,
              background: 'var(--accent)', color: 'var(--accent-text)',
              fontSize: 10, fontWeight: 700,
              display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
              lineHeight: 1,
            }}
          >
            {unread > 9 ? '9+' : unread}
          </span>
        )}
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="Notifications"
          style={{
            position: 'absolute',
            top: 'calc(100% + 8px)',
            right: 0,
            width: 420,
            maxHeight: 'min(70vh, 640px)',
            background: 'var(--bg-2)',
            border: '1px solid var(--border)',
            borderRadius: 10,
            boxShadow: 'var(--shadow-elev)',
            overflowY: 'auto',
            overflowX: 'hidden',
            zIndex: 200,
            // The panel is wider than the bell, so it'd be cut off on the
            // right edge — right:0 anchors it to the bell's right edge
            // (so it extends to the LEFT under the avatar area).
          }}
        >
          <NotificationsList
            onNavigate={() => {
              setOpen(false)
              setBadgeNonce(n => n + 1)
            }}
          />
        </div>
      )}
    </div>
  )
}
