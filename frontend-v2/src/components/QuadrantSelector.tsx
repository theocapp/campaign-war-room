/**
 * QuadrantSelector — five-button strategic-slot picker.
 *
 * Shared between the Add Frame modal, Edit Frame modal, the Promote inline
 * form on the Narratives banner, and the Promote modal opened from the
 * Review Queue. One source of truth for the "Cognetti's Defense / Cognetti's
 * Offense / Bresnahan's Defense / Bresnahan's Offense / Neutral" matrix.
 *
 * `candidateName` / `opponentName` are surname strings ("Cognetti",
 * "Bresnahan"). Falls back to "Our" / "Their" if not loaded yet.
 */
import type { OwnerType } from '@/api/types'
import { QuadrantPalette, quadrantNamedLabel } from '@/lib/quadrantColor'
import type { QuadrantKey } from '@/lib/quadrantColor'

/**
 * Map a quadrant key back to the (owner_type, subject_type) tuple stored
 * in the database. Always returns an explicit subject_type so the user's
 * choice is fully persisted (no falling back to the name heuristic).
 */
export function quadrantToTypes(q: QuadrantKey): { owner_type: OwnerType; subject_type: OwnerType } {
  switch (q) {
    case 'our_defense':   return { owner_type: 'candidate', subject_type: 'candidate' }
    case 'our_offense':   return { owner_type: 'candidate', subject_type: 'opponent' }
    case 'their_defense': return { owner_type: 'opponent',  subject_type: 'opponent' }
    case 'their_offense': return { owner_type: 'opponent',  subject_type: 'candidate' }
    case 'media':         return { owner_type: 'media',     subject_type: 'media' }
  }
}

const C = {
  bg3: 'var(--bg-3)', border: 'var(--border)',
  text2: 'var(--text-2)',
}

export function QuadrantSelector({
  value, onChange, candidateName, opponentName,
}: {
  value: QuadrantKey
  onChange: (q: QuadrantKey) => void
  candidateName: string
  opponentName: string
}) {
  const us = candidateName || 'us'
  const them = opponentName || 'them'
  const options: { key: QuadrantKey; label: string; desc: string }[] = [
    { key: 'our_defense',   label: quadrantNamedLabel('our_defense',   candidateName, opponentName), desc: `Defending ${us}'s record / message` },
    { key: 'our_offense',   label: quadrantNamedLabel('our_offense',   candidateName, opponentName), desc: `Attacking ${them}` },
    { key: 'their_defense', label: quadrantNamedLabel('their_defense', candidateName, opponentName), desc: `Defending ${them}'s record / message` },
    { key: 'their_offense', label: quadrantNamedLabel('their_offense', candidateName, opponentName), desc: `${them} attacking ${us}` },
    { key: 'media',         label: quadrantNamedLabel('media',         candidateName, opponentName), desc: 'Press-driven, no clear side' },
  ]
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
      {options.map(opt => {
        const color = QuadrantPalette[opt.key]
        const selected = value === opt.key
        return (
          <button
            key={opt.key}
            type="button"
            title={opt.desc}
            onClick={() => onChange(opt.key)}
            style={{
              display: 'inline-flex', alignItems: 'center', gap: 6,
              padding: '6px 10px', borderRadius: 6,
              fontSize: 12, fontWeight: selected ? 700 : 500,
              cursor: 'pointer', background: selected ? `${color}22` : C.bg3,
              color: selected ? color : C.text2,
              border: `1px solid ${selected ? color : C.border}`,
              outline: selected ? `1px solid ${color}` : 'none', outlineOffset: 1,
              transition: 'background 0.12s ease',
            }}
          >
            <span style={{
              width: 8, height: 8, borderRadius: 2,
              background: color, display: 'inline-block', flexShrink: 0,
            }} />
            {opt.label}
          </button>
        )
      })}
    </div>
  )
}
