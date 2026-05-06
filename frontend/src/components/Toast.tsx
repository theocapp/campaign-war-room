import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'

type ToastType = 'error' | 'warning' | 'info'

interface Toast {
  id: number
  message: string
  type: ToastType
}

interface ToastContextValue {
  addToast: (message: string, type?: ToastType) => void
}

const ToastContext = createContext<ToastContextValue | null>(null)

let _nextId = 0

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])

  const addToast = useCallback((message: string, type: ToastType = 'error') => {
    const id = ++_nextId
    setToasts(prev => [...prev, { id, message, type }])
  }, [])

  const remove = useCallback((id: number) => {
    setToasts(prev => prev.filter(t => t.id !== id))
  }, [])

  return (
    <ToastContext.Provider value={{ addToast }}>
      {children}
      <div style={{
        position: 'fixed',
        bottom: 20,
        right: 20,
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        zIndex: 9999,
        pointerEvents: 'none',
      }}>
        {toasts.map(t => (
          <ToastItem key={t.id} toast={t} onDismiss={remove} />
        ))}
      </div>
    </ToastContext.Provider>
  )
}

function ToastItem({ toast, onDismiss }: { toast: Toast; onDismiss: (id: number) => void }) {
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    timerRef.current = setTimeout(() => onDismiss(toast.id), 5000)
    return () => { if (timerRef.current) clearTimeout(timerRef.current) }
  }, [toast.id, onDismiss])

  const colors: Record<ToastType, { bg: string; border: string; accent: string }> = {
    error:   { bg: 'rgba(127,29,29,0.95)',  border: 'rgba(239,68,68,0.4)',  accent: '#f87171' },
    warning: { bg: 'rgba(92,55,0,0.95)',    border: 'rgba(251,191,36,0.4)', accent: '#fbbf24' },
    info:    { bg: 'rgba(12,35,70,0.95)',   border: 'rgba(59,130,246,0.4)', accent: '#60a5fa' },
  }
  const c = colors[toast.type]

  return (
    <div
      style={{
        pointerEvents: 'auto',
        display: 'flex',
        alignItems: 'flex-start',
        gap: 10,
        padding: '10px 14px',
        borderRadius: 8,
        background: c.bg,
        border: `1px solid ${c.border}`,
        boxShadow: '0 4px 16px rgba(0,0,0,0.5)',
        maxWidth: 380,
        backdropFilter: 'blur(4px)',
      }}
    >
      <span style={{ color: c.accent, fontSize: '0.85rem', lineHeight: 1.5, flex: 1, fontFamily: 'inherit' }}>
        {toast.message}
      </span>
      <button
        onClick={() => onDismiss(toast.id)}
        style={{
          background: 'none',
          border: 'none',
          color: 'var(--text-muted)',
          cursor: 'pointer',
          fontSize: '0.9rem',
          lineHeight: 1,
          padding: '0 2px',
          flexShrink: 0,
        }}
      >
        ×
      </button>
    </div>
  )
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast must be used inside ToastProvider')
  return ctx
}
