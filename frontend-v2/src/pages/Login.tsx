import { useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '@/auth/AuthContext'

interface LocationState { from?: string }

export function Login() {
  const { user, login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const [code, setCode] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  // If the user is already logged in (e.g., they hit /login by mistake),
  // bounce them straight to wherever they were trying to go.
  useEffect(() => {
    if (user) {
      const from = (location.state as LocationState | null)?.from ?? '/'
      navigate(from, { replace: true })
    }
  }, [user, location.state, navigate])

  useEffect(() => { inputRef.current?.focus() }, [])

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    const trimmed = code.trim()
    if (!trimmed) return
    setSubmitting(true)
    setError(null)
    try {
      await login(trimmed)
      const from = (location.state as LocationState | null)?.from ?? '/'
      navigate(from, { replace: true })
    } catch {
      setError('That code didn’t work. Double-check it and try again.')
      setSubmitting(false)
    }
  }

  return (
    <div style={{
      minHeight: '100vh',
      background: 'var(--bg-1)',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      padding: 24,
    }}>
      <div style={{
        width: '100%',
        maxWidth: 380,
        background: 'var(--bg-2)',
        border: '1px solid var(--border)',
        borderRadius: 12,
        padding: '32px 28px',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: 20,
      }}>
        <img
          src="/theosintel-wordmark.png"
          alt="theosintel"
          className="brand-logo"
          style={{ height: 36, width: 'auto' }}
        />
        <div style={{ textAlign: 'center' }}>
          <h1 style={{ fontSize: 18, fontWeight: 600, color: 'var(--text-1)', margin: 0 }}>
            Private preview
          </h1>
          <p style={{ fontSize: 13, color: 'var(--text-3)', margin: '6px 0 0' }}>
            Enter your access code to continue.
          </p>
        </div>

        <form onSubmit={onSubmit} style={{ width: '100%', display: 'flex', flexDirection: 'column', gap: 12 }}>
          <input
            ref={inputRef}
            type="text"
            value={code}
            onChange={e => { setCode(e.target.value); setError(null) }}
            placeholder="Access code"
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            style={{
              width: '100%',
              boxSizing: 'border-box',
              padding: '10px 12px',
              background: 'var(--bg-1)',
              border: `1px solid ${error ? '#d71913' : 'var(--border)'}`,
              borderRadius: 8,
              color: 'var(--text-1)',
              fontSize: 14,
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
              outline: 'none',
            }}
          />
          {error && (
            <div style={{ fontSize: 12, color: '#d71913' }}>{error}</div>
          )}
          <button
            type="submit"
            disabled={submitting || !code.trim()}
            style={{
              width: '100%',
              padding: '10px 12px',
              background: 'var(--accent)',
              color: 'var(--accent-text)',
              border: 'none',
              borderRadius: 8,
              fontSize: 14,
              fontWeight: 600,
              cursor: submitting || !code.trim() ? 'not-allowed' : 'pointer',
              opacity: submitting || !code.trim() ? 0.6 : 1,
            }}
          >
            {submitting ? 'Checking…' : 'Continue'}
          </button>
        </form>
      </div>
    </div>
  )
}
