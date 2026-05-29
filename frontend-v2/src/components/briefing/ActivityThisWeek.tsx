import type { BriefingEntity } from '@/api/types'
import { InfoTooltip } from '@/components/InfoTooltip'

interface Props {
  entities: BriefingEntity[] | null | undefined
}

/**
 * "Activity This Week" — the race-allowlist entity grid. Restricted to
 * the candidate, opponent, and a short list of high-impact race-adjacent
 * figures/bills so this doesn't drift into trending-names noise.
 */
export function ActivityThisWeek({ entities }: Props) {
  if (!entities || entities.length === 0) return null

  return (
    <section style={{ marginBottom: 32 }}>
      <div style={{
        fontSize: 14, fontWeight: 600,
        color: 'var(--text-2)',
        marginBottom: 12, display: 'flex', alignItems: 'center', gap: 8,
      }}>
        Activity This Week
        <InfoTooltip
          text="Who's getting coverage this week vs last week. Restricted to your candidate, opponent, and a short allowlist of high-impact race-adjacent figures and bills — so this doesn't drift into trending-names noise."
          color="var(--text-2)"
        />
        <span style={{ flex: 1, height: 1, background: 'var(--bg-3)', display: 'block' }} />
      </div>
      <div style={{
        display: 'grid',
        // minmax(0,1fr) lets cards shrink below their content's natural
        // width — fixes overflow when long sample titles push wider than
        // the column.
        gridTemplateColumns: 'repeat(3, minmax(0, 1fr))',
        gap: 10,
      }}>
        {entities.map(e => {
          const delta = e.delta
          const arrow = delta > 0 ? '↑' : delta < 0 ? '↓' : '→'
          const color =
            e.affiliation === 'D' ? 'var(--candidate)'
            : e.affiliation === 'R' ? 'var(--opponent)'
            : 'var(--text-2)'
          return (
            <div key={e.id} className="card" style={{
              padding: '14px 16px',
              minWidth: 0,
              overflow: 'hidden',
            }}>
              <div style={{
                display: 'flex', justifyContent: 'space-between',
                alignItems: 'baseline', marginBottom: 8, gap: 6,
              }}>
                <span style={{
                  color, fontSize: 14, fontWeight: 700,
                  overflow: 'hidden', textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap', minWidth: 0,
                }}>
                  {e.name}
                </span>
                <span style={{
                  fontSize: 10, color: 'var(--text-3)',
                  textTransform: 'uppercase', letterSpacing: '0.06em',
                  flexShrink: 0,
                }}>
                  {e.type}
                </span>
              </div>
              <div style={{
                display: 'flex', alignItems: 'baseline', gap: 8,
                marginBottom: 8,
              }}>
                <span style={{
                  fontSize: 22, fontWeight: 700, color: 'var(--text-1)',
                }}>
                  {e.mentions_this_week}
                </span>
                <span style={{ fontSize: 11, color: 'var(--text-3)' }}>
                  this week
                </span>
                <span style={{
                  marginLeft: 'auto', fontSize: 11,
                  color: delta > 0 ? 'var(--green, #22c55e)'
                       : delta < 0 ? 'var(--red, #ef4444)'
                       : 'var(--text-3)',
                  flexShrink: 0,
                }}>
                  {arrow}{Math.abs(delta)} vs {e.mentions_last_week}
                </span>
              </div>
              {e.sample_recent_titles.length > 0 && (
                <ul style={{
                  margin: 0, padding: 0, listStyle: 'none',
                  fontSize: 11, color: 'var(--text-2)', lineHeight: 1.45,
                }}>
                  {e.sample_recent_titles.slice(0, 2).map((t, i) => (
                    <li key={i} style={{
                      overflow: 'hidden', textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap', marginTop: i > 0 ? 4 : 0,
                    }}>
                      · {t}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )
        })}
      </div>
    </section>
  )
}
