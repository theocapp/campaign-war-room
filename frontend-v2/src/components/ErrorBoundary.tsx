import { Component, type ErrorInfo, type ReactNode } from 'react'

interface ErrorBoundaryProps {
  children: ReactNode
}

interface ErrorBoundaryState {
  error: Error | null
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('ErrorBoundary caught:', error, info.componentStack)
  }

  render() {
    if (!this.state.error) return this.props.children
    const err = this.state.error
    return (
      <div style={{ padding: 32 }}>
        <div style={{
          background: 'var(--bg-2)', border: '1px solid var(--border)',
          borderRadius: 8, padding: 20, maxWidth: 720, margin: '0 auto',
        }}>
          <h2 style={{
            fontSize: 16, fontWeight: 700, color: 'var(--text-1)',
            margin: 0, marginBottom: 8,
          }}>
            Something broke on this page.
          </h2>
          <p style={{
            fontSize: 13, color: 'var(--text-2)', margin: 0,
            marginBottom: 16, lineHeight: 1.5,
          }}>
            The rest of the app is still working — use the sidebar to switch pages, or reload to retry this one.
          </p>
          <details style={{ fontSize: 12, color: 'var(--text-3)' }}>
            <summary style={{ cursor: 'pointer', marginBottom: 6 }}>Error details</summary>
            <pre style={{
              fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
              fontSize: 11,
              background: 'var(--bg-1)',
              border: '1px solid var(--border)',
              borderRadius: 4,
              padding: 10,
              overflow: 'auto',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              maxHeight: 320,
            }}>
              {err.message}{'\n\n'}{err.stack || '(no stack)'}
            </pre>
          </details>
          <button
            type="button"
            onClick={() => window.location.reload()}
            style={{
              marginTop: 14,
              padding: '6px 12px', fontSize: 12, fontWeight: 600,
              background: 'var(--accent)', color: '#000', border: 'none',
              borderRadius: 4, cursor: 'pointer',
            }}
          >
            Reload page
          </button>
        </div>
      </div>
    )
  }
}
