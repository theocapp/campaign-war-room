import type { SourceItem } from '../api/types'

const TYPE_META: Record<string, { label: string; color: string }> = {
  news:               { label: 'News',          color: 'var(--accent-light)' },
  public_record:      { label: 'Public Record',  color: '#86efac' },
  opponent_statement: { label: 'Opponent',       color: 'var(--opponent-light)' },
  canvassing:         { label: 'Canvassing',     color: '#c4b5fd' },
  campaign_note:      { label: 'Campaign Note',  color: 'var(--warning-light)' },
  social:             { label: 'Social',         color: '#67e8f9' },
}

function fmtDate(s: string | null) {
  if (!s) return '—'
  return new Date(s).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: '2-digit' })
}

/** Returns the best display date and a label. Label is null when a real publish date is available. */
export function sourceDate(source: { published_at: string | null; ingested_at: string | null; created_at: string }): { date: string; label: string | null } {
  if (source.published_at) return { date: fmtDate(source.published_at), label: null }
  return { date: fmtDate(source.ingested_at ?? source.created_at), label: 'Collected' }
}

function urgencyDot(u: string) {
  if (u === 'high') return 'var(--opponent)'
  if (u === 'medium') return 'var(--warning)'
  return 'transparent'
}

interface Props { source: SourceItem; compact?: boolean }

export default function SourceCard({ source, compact = false }: Props) {
  const meta = TYPE_META[source.source_type] ?? { label: source.source_type.replace(/_/g, ' '), color: 'var(--text-muted)' }

  return (
    <div className="card card-hover" style={{ marginBottom: compact ? 5 : 8 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 10, marginBottom: 6 }}>
        {/* Title */}
        <div style={{ flex: 1, minWidth: 0 }}>
          {source.source_url ? (
            <a
              href={source.source_url}
              target="_blank"
              rel="noopener noreferrer"
              style={{ color: 'var(--text-primary)', fontWeight: 600, fontSize: '0.87rem', lineHeight: 1.3 }}
            >
              {source.title}
            </a>
          ) : (
            <span style={{ fontWeight: 600, fontSize: '0.87rem', lineHeight: 1.3, color: 'var(--text-primary)' }}>
              {source.title}
            </span>
          )}
        </div>
        {/* Right badges */}
        <div style={{ display: 'flex', gap: 5, alignItems: 'center', flexShrink: 0 }}>
          {source.urgency !== 'low' && (
            <span style={{
              width: 7, height: 7, borderRadius: '50%',
              background: urgencyDot(source.urgency), flexShrink: 0,
            }} title={source.urgency} />
          )}
          <span className="badge badge-ghost" style={{ fontSize: '0.6rem' }}>
            {source.race_relevance_label} {source.race_relevance_score}
          </span>
        </div>
      </div>

      {/* Meta row */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', marginBottom: compact ? 0 : 6 }}>
        <span style={{ fontSize: '0.65rem', color: meta.color, fontFamily: 'JetBrains Mono', textTransform: 'uppercase', letterSpacing: '0.06em' }}>
          {meta.label}
        </span>
        <span style={{ fontSize: '0.65rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', textTransform: 'uppercase' }}>
          {source.actionability_label}
        </span>
        {source.source_name && (
          <span style={{ fontSize: '0.7rem', color: 'var(--text-muted)' }}>{source.source_name}</span>
        )}
        {source.duplicate_of_source_id && (
          <span style={{ fontSize: '0.62rem', color: 'var(--text-xmuted)', fontFamily: 'JetBrains Mono' }}>
            cluster
          </span>
        )}
        {(() => {
          const { date, label } = sourceDate(source)
          return (
            <span style={{ fontSize: '0.68rem', color: 'var(--text-muted)', fontFamily: 'JetBrains Mono', marginLeft: 'auto' }}>
              {label && <span style={{ color: 'var(--text-xmuted)', marginRight: 3 }}>{label}:</span>}{date}
            </span>
          )
        })()}
      </div>

      {!compact && source.summary && (
        <p style={{ margin: '6px 0 0', fontSize: '0.8rem', color: 'var(--text-secondary)', lineHeight: 1.55 }}>
          {source.summary}
        </p>
      )}

      {!compact && source.credibility_note && (
        <div className="risk-banner" style={{ marginTop: 8, fontSize: '0.74rem' }}>
          <span style={{ color: 'var(--opponent)', fontFamily: 'JetBrains Mono', fontSize: '0.62rem', fontWeight: 700 }}>⚠ NOTE </span>
          <span style={{ color: 'var(--opponent-light)' }}>{source.credibility_note}</span>
        </div>
      )}
    </div>
  )
}
