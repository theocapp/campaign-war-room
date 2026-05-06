import { Component } from 'react'
import type { ErrorInfo, ReactNode } from 'react'

interface Props {
  children: ReactNode
  fallback?: ReactNode
}

interface State {
  error: Error | null
}

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('[ErrorBoundary]', error, info.componentStack)
  }

  render() {
    if (this.state.error) {
      if (this.props.fallback) return this.props.fallback
      return (
        <div style={{
          padding: '2rem',
          color: 'var(--text-secondary)',
          fontFamily: "'JetBrains Mono', monospace",
          fontSize: '0.82rem',
        }}>
          <div style={{ color: '#f87171', fontWeight: 600, marginBottom: 8 }}>
            Something went wrong rendering this page.
          </div>
          <div style={{ color: 'var(--text-muted)', marginBottom: 16 }}>
            {this.state.error.message}
          </div>
          <button
            onClick={() => this.setState({ error: null })}
            style={{
              background: 'var(--surface-2)',
              border: '1px solid var(--border)',
              color: 'var(--text-secondary)',
              borderRadius: 6,
              padding: '6px 14px',
              cursor: 'pointer',
              fontSize: '0.8rem',
            }}
          >
            Try again
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
