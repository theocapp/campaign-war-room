import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../api/client'
import type { KGAlert } from '../api/types'

const ALERT_COLORS: Record<string, string> = {
  velocity_spike:   'var(--opponent, #b91c1c)',
  opponent_attack:  'var(--opponent, #b91c1c)',
  entity_surge:     'var(--warning, #b45309)',
  source_surge:     'var(--warning, #b45309)',
  new_narrative:    'var(--candidate, #1a7340)',
}

function alertColor(type: string) {
  return ALERT_COLORS[type] ?? 'var(--text-muted)'
}

function severityBg(score: number) {
  if (score >= 0.7) return '#fee2e2'
  if (score >= 0.4) return '#fef3c7'
  return '#f5f5f5'
}

function severityFg(score: number) {
  if (score >= 0.7) return '#b91c1c'
  if (score >= 0.4) return '#92400e'
  return '#555'
}

function formatDate(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

function AlertCard({ alert, onResolve }: { alert: KGAlert; onResolve: (id: number) => void }) {
  const [resolving, setResolving] = useState(false)

  function handleResolve() {
    setResolving(true)
    api.resolveKGAlert(alert.id)
      .then(() => onResolve(alert.id))
      .catch(() => setResolving(false))
  }

  return (
    <div style={{
      background: 'var(--surface)',
      border: '1px solid var(--border)',
      borderLeft: `4px solid ${alertColor(alert.alert_type)}`,
      borderRadius: '8px',
      padding: '1rem 1.25rem',
      display: 'flex',
      alignItems: 'flex-start',
      gap: '1rem',
    }}>
      {/* Severity badge */}
      <div style={{
        minWidth: '48px',
        textAlign: 'center',
        padding: '0.3rem 0.4rem',
        borderRadius: '6px',
        background: severityBg(alert.severity_score),
        color: severityFg(alert.severity_score),
        fontWeight: 700,
        fontSize: '0.95rem',
        flexShrink: 0,
      }}>
        {(alert.severity_score * 100).toFixed(0)}
        <div style={{ fontSize: '0.6rem', fontWeight: 400, marginTop: '1px' }}>sev</div>
      </div>

      {/* Content */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', flexWrap: 'wrap', marginBottom: '0.3rem' }}>
          <span style={{
            fontSize: '0.72rem', padding: '0.15rem 0.45rem', borderRadius: '3px',
            background: 'var(--surface-alt, #f0f0f0)', color: alertColor(alert.alert_type),
            fontWeight: 600,
          }}>
            {alert.alert_type.replace(/_/g, ' ')}
          </span>
          <Link
            to={`/kg/narratives/${alert.narrative_id}`}
            style={{ fontSize: '0.82rem', fontWeight: 600, color: 'var(--accent)', textDecoration: 'none' }}
          >
            {alert.narrative_label}
          </Link>
        </div>
        <div style={{ fontSize: '0.88rem', marginBottom: '0.4rem', lineHeight: 1.4 }}>
          {alert.message}
        </div>
        <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>
          {formatDate(alert.created_at)}
        </div>
      </div>

      {/* Resolve button */}
      <button
        onClick={handleResolve}
        disabled={resolving}
        style={{
          flexShrink: 0,
          padding: '0.35rem 0.8rem',
          fontSize: '0.78rem',
          borderRadius: '5px',
          border: '1px solid var(--border)',
          background: resolving ? 'var(--surface-alt, #f5f5f5)' : 'var(--surface)',
          color: resolving ? 'var(--text-muted)' : 'var(--text)',
          cursor: resolving ? 'not-allowed' : 'pointer',
          fontWeight: 500,
        }}
      >
        {resolving ? 'Resolving…' : 'Resolve'}
      </button>
    </div>
  )
}

export default function KGAlerts() {
  const [alerts, setAlerts] = useState<KGAlert[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.getKGAlerts(50)
      .then(setAlerts)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  function handleResolve(id: number) {
    setAlerts(prev => prev.filter(a => a.id !== id))
  }

  if (loading) {
    return <div style={{ padding: '2rem', color: 'var(--text-muted)', fontSize: '0.9rem' }}>Loading alerts…</div>
  }

  if (error) {
    return <div style={{ padding: '2rem', color: 'var(--opponent)', fontSize: '0.9rem' }}>Error: {error}</div>
  }

  return (
    <div style={{ padding: '1.5rem 2rem', maxWidth: '860px' }}>
      <div style={{ marginBottom: '1.5rem', display: 'flex', alignItems: 'baseline', justifyContent: 'space-between' }}>
        <div>
          <h1 style={{ fontSize: '1.4rem', fontWeight: 700, margin: 0 }}>KG Alerts</h1>
          <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem', marginTop: '0.3rem' }}>
            Unresolved narrative alerts ranked by severity
          </p>
        </div>
        {alerts.length > 0 && (
          <span style={{
            fontSize: '0.82rem', padding: '0.3rem 0.7rem', borderRadius: '5px',
            background: severityBg(0.8), color: severityFg(0.8), fontWeight: 600,
          }}>
            {alerts.length} active
          </span>
        )}
      </div>

      {alerts.length === 0 ? (
        <div style={{
          padding: '2rem',
          background: 'var(--surface)',
          border: '1px solid var(--border)',
          borderRadius: '8px',
          color: 'var(--text-muted)',
          fontSize: '0.9rem',
          textAlign: 'center',
        }}>
          No active alerts. All clear.
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
          {alerts.map(a => (
            <AlertCard key={a.id} alert={a} onResolve={handleResolve} />
          ))}
        </div>
      )}
    </div>
  )
}
