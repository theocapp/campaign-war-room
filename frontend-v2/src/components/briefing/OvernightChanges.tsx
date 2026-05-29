import type { BriefingClaim } from '@/api/types'
import { InfoTooltip } from '@/components/InfoTooltip'
import { formatArticleDate } from '@/lib/formatDate'

interface Props {
  claims: BriefingClaim[] | null | undefined
}

/**
 * "What Changed in the Race" — labeled candidate-specific claims from the
 * last 48h. Renders nothing when empty (which is normal in quiet windows;
 * race-specific labeled-quote density is ~1-2/day).
 */
export function OvernightChanges({ claims }: Props) {
  if (!claims || claims.length === 0) return null

  return (
    <section style={{ marginBottom: 32 }}>
      <div style={{
        fontSize: 14, fontWeight: 600,
        color: 'var(--text-2)',
        marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8,
      }}>
        What Changed in the Race
        <InfoTooltip
          text="Labeled quotes from the last 48 hours that mention Cognetti or Bresnahan directly. National Trump/Shapiro coverage stays out of this section — the entity gate is the candidates themselves."
          color="var(--text-2)"
        />
        <span style={{ flex: 1, height: 1, background: 'var(--bg-3)', display: 'block' }} />
      </div>
      <div style={{
        background: 'var(--bg-2)',
        border: '1px solid var(--bg-3)',
        borderRadius: 12,
        padding: '4px 0',
      }}>
        {claims.map(c => (
          <div key={c.claim_id} style={{
            padding: '12px 18px',
            borderTop: '1px solid var(--bg-3)',
          }}>
            <div style={{
              display: 'flex', alignItems: 'center', gap: 8,
              marginBottom: 6, fontSize: 11,
              color: 'var(--text-2)',
            }}>
              <span style={{
                padding: '1px 6px', borderRadius: 3,
                background: 'rgba(255,191,0,0.12)', color: 'var(--accent)',
                textTransform: 'uppercase', letterSpacing: '0.06em',
                fontWeight: 700,
              }}>
                {c.label}
              </span>
              <span>{c.outlet}</span>
              {c.published_at && (
                <span style={{ color: 'var(--text-3)' }}>
                  · {formatArticleDate(c.published_at)}
                </span>
              )}
            </div>
            <div style={{
              fontSize: 14, lineHeight: 1.5, color: 'var(--text-1)',
              marginBottom: c.article_url ? 4 : 0,
            }}>
              &ldquo;{c.quote}&rdquo;
            </div>
            {c.article_url && (
              <a
                href={c.article_url}
                target="_blank"
                rel="noreferrer"
                style={{
                  fontSize: 11, color: 'var(--accent)',
                  textDecoration: 'none',
                }}
              >
                Open article →
              </a>
            )}
          </div>
        ))}
      </div>
    </section>
  )
}
