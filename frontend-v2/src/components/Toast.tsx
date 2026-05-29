import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'

/**
 * Minimal toast for surfacing async-action outcomes.
 *
 * Why this exists: a number of pages (Opponents, ReviewQueue, Monitors,
 * etc.) call `api.something()` from event handlers and previously caught
 * the rejection with `catch { /* silently fail *\/ }`. Failed writes
 * looked indistinguishable from successes — operators would double-fire
 * the same action. This component is the surface for "your action just
 * failed" feedback so they stop and read instead of retrying.
 *
 * Render `<ToastProvider>` near the app root (App.tsx) and call
 * `useToast()` from any component that needs to flash a message.
 *
 * Errors auto-dismiss after 6s. Successes after 3s. Click to dismiss
 * sooner.
 */

type ToastKind = 'error' | 'success' | 'info'

interface Toast {
  id: number
  kind: ToastKind
  message: string
}

interface ToastContextValue {
  push: (message: string, kind?: ToastKind) => void
}

const ToastContext = createContext<ToastContextValue | null>(null)

let nextId = 1

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const timers = useRef<Map<number, ReturnType<typeof setTimeout>>>(new Map())

  const dismiss = useCallback((id: number) => {
    setToasts(prev => prev.filter(t => t.id !== id))
    const t = timers.current.get(id)
    if (t) { clearTimeout(t); timers.current.delete(id) }
  }, [])

  const push = useCallback((message: string, kind: ToastKind = 'error') => {
    const id = nextId++
    setToasts(prev => [...prev, { id, kind, message }])
    const ttl = kind === 'success' ? 3000 : 6000
    const handle = setTimeout(() => dismiss(id), ttl)
    timers.current.set(id, handle)
  }, [dismiss])

  // Clean up any pending timers when the provider unmounts.
  useEffect(() => {
    const t = timers.current
    return () => { t.forEach(h => clearTimeout(h)); t.clear() }
  }, [])

  return (
    <ToastContext.Provider value={{ push }}>
      {children}
      <div
        aria-live="polite"
        style={{
          position: 'fixed',
          right: 20,
          bottom: 20,
          zIndex: 9999,
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
          maxWidth: 420,
          pointerEvents: 'none',
        }}
      >
        {toasts.map(t => (
          <div
            key={t.id}
            role={t.kind === 'error' ? 'alert' : 'status'}
            onClick={() => dismiss(t.id)}
            style={{
              pointerEvents: 'auto',
              background:
                t.kind === 'error' ? '#7f1d1d' :
                t.kind === 'success' ? '#14532d' :
                '#1e3a5f',
              color:
                t.kind === 'error' ? '#fecaca' :
                t.kind === 'success' ? '#bbf7d0' :
                '#bfdbfe',
              border: '1px solid ' + (
                t.kind === 'error' ? '#dc2626' :
                t.kind === 'success' ? '#16a34a' :
                '#3b82f6'
              ),
              borderRadius: 6,
              padding: '10px 14px',
              fontSize: 13,
              lineHeight: 1.4,
              cursor: 'pointer',
              boxShadow: '0 6px 18px rgba(0,0,0,0.35)',
              wordBreak: 'break-word',
            }}
          >
            {t.message}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

/**
 * Hook to push a toast from inside a component. Tolerant of being called
 * outside a provider — falls back to console.warn so tests / Storybook /
 * stories don't crash when no provider is mounted. In production this
 * never fires because App.tsx mounts the provider at the root.
 */
export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext)
  if (!ctx) {
    return {
      push: (message, kind = 'error') => {
        // eslint-disable-next-line no-console
        console.warn(`[Toast: no provider mounted] ${kind}: ${message}`)
      },
    }
  }
  return ctx
}

/**
 * Convenience: format an unknown rejection as a user-readable string.
 * `req()` in api/client.ts throws Error with `${METHOD} ${path} → ${status}: ${body}`,
 * which is too low-level for end-users. Strip the URL prefix and keep
 * just the status + reason.
 */
export function describeError(err: unknown, fallback: string): string {
  if (err instanceof Error) {
    const m = err.message.match(/→\s*(\d{3})(?:[:\s]+(.*))?$/)
    if (m) {
      const status = m[1]
      const detail = (m[2] || '').trim()
      // Try to surface FastAPI's {detail: "..."} payload.
      try {
        const parsed = JSON.parse(detail) as { detail?: unknown }
        if (parsed && typeof parsed.detail === 'string') {
          return `${fallback} (${status}): ${parsed.detail}`
        }
      } catch {
        // not JSON
      }
      return detail ? `${fallback} (${status}): ${detail}` : `${fallback} (${status})`
    }
    return `${fallback}: ${err.message}`
  }
  return fallback
}
