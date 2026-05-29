import { AlertTriangle } from 'lucide-react'
import type { BriefingArticle } from '@/api/types'
import { InfoTooltip } from '@/components/InfoTooltip'
import { formatArticleDate } from '@/lib/formatDate'

interface Props {
  items: BriefingArticle[] | null | undefined
}

/**
 * "Needs Response" — articles the AI flagged as potentially damaging or
 * requiring a same-day response. Hidden entirely when there's nothing to
 * show: no header, no card, no empty state — the section just doesn't
 * exist on the page.
 */
export function NeedsResponse({ items }: Props) {
  if (!items || items.length === 0) return null

  return (
    <section style={{ marginBottom: 32 }}>
      <div style={{
        fontSize: 14,
        fontWeight: 600,
        color: '#f05050',
        marginBottom: 12,
        display: 'flex',
        alignItems: 'center',
        gap: 8,
      }}>
        <AlertTriangle size={13} />
        Needs Response
        <InfoTooltip
          text="Recent potentially damaging coverage such as opponent attacks or unfavorable framing."
          color="var(--red)"
        />
        <span style={{ flex: 1, height: 1, background: 'rgba(201,28,28,0.2)', display: 'block', marginLeft: 4 }} />
      </div>
      {items!.map((item, i) => (
        <div key={i} style={{
          padding: '14px 18px',
          background: 'var(--bg-2)',
          border: '1px solid rgba(240, 80, 80, 0.5)',
          borderLeft: '3px solid #f05050',
          borderRadius: 12,
          marginBottom: 10,
        }}>
          <div style={{ fontSize: 14, color: 'var(--text-1)', fontWeight: 500, marginBottom: 6 }}>
            {item.title}
          </div>
          <div style={{ fontSize: 12, color: 'var(--text-2)' }}>
            {item.source_name}
            {item.published_at && (
              <span style={{ marginLeft: 8, color: 'var(--text-3)', fontSize: 10 }}>
                · {formatArticleDate(item.published_at)}
              </span>
            )}
          </div>
          {item.summary && (
            <div style={{ fontSize: 13, color: 'var(--text-2)', marginTop: 8, lineHeight: 1.5 }}>
              {item.summary}
            </div>
          )}
        </div>
      ))}
    </section>
  )
}
