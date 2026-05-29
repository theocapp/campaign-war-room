import { NotificationsList } from '@/components/NotificationsList'

/**
 * Standalone notifications page. The primary entry point is the bell
 * popover in the header — this route exists so deep links and refreshes
 * to /notifications still render something sensible.
 */
export function Notifications() {
  return (
    <div style={{ maxWidth: 540, margin: '0 auto', padding: '24px 16px' }}>
      <div style={{
        background: 'var(--bg-2)',
        border: '1px solid var(--border)',
        borderRadius: 10,
        overflow: 'hidden',
      }}>
        <NotificationsList />
      </div>
    </div>
  )
}
