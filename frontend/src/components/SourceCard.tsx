import type { SourceItem } from '../api/types'
import UrgencyBadge from './UrgencyBadge'

const TYPE_COLORS: Record<string, string> = {
  news: '#93c5fd',
  public_record: '#86efac',
  opponent_statement: '#fca5a5',
  canvassing: '#c4b5fd',
  campaign_note: '#fdba74',
  social: '#67e8f9',
}

function fmtDate(s: string | null) {
  if (!s) return '—'
  return new Date(s).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: '2-digit' })
}

interface Props {
  source: SourceItem
  compact?: boolean
}

export default function SourceCard({ source, compact = false }: Props) {
  const typeColor = TYPE_COLORS[source.source_type] ?? '#8892a4'

  return (
    <div className="card card-hover" style={{ marginBottom: compact ? 6 : 10 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 8, marginBottom: 6 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          {source.source_url ? (
            <a href={source.source_url} target="_blank" rel="noopener noreferrer"
              style={{ color: 'var(--text-primary)', fontWeight: 500, fontSize: '0.875rem', lineHeight: 1.3 }}>
              {source.title}
            </a>
          ) : (
            <span style={{ fontWeight: 500, fontSize: '0.875rem', lineHeight: 1.3 }}>{source.title}</span>
          )}
        </div>
        <div style={{ display: 'flex', gap: 6, flexShrink: 0 }}>
          <UrgencyBadge urgency={source.urgency} size="sm" />
        </div>
      </div>

      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: compact ? 0 : 6 }}>
        <span style={{ fontSize: '0.65rem', color: typeColor, fontFamily: 'JetBrains Mono', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
          {source.source_type.replace('_', ' ')}
        </span>
        {source.source_name && (
          <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>{source.source_name}</span>
        )}
        <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)', marginLeft: 'auto' }}>
          {fmtDate(source.published_at)}
        </span>
      </div>

      {!compact && source.summary && (
        <p style={{ margin: '6px 0 0', fontSize: '0.8rem', color: 'var(--text-secondary)', lineHeight: 1.5 }}>
          {source.summary}
        </p>
      )}

      {!compact && source.credibility_note && (
        <div className="risk-banner" style={{ marginTop: 8 }}>
          <div style={{ fontSize: '0.65rem', fontWeight: 700, color: '#f87171', marginBottom: 2, fontFamily: 'JetBrains Mono', letterSpacing: '0.06em' }}>
            ⚠ INTELLIGENCE NOTE
          </div>
          <p style={{ margin: 0, fontSize: '0.78rem', color: '#fca5a5', lineHeight: 1.5 }}>
            {source.credibility_note}
          </p>
        </div>
      )}
    </div>
  )
}
