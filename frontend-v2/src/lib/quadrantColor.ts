/**
 * V13.21 — 4-quadrant color scheme, shared across all pages.
 *
 * Encodes TWO axes of campaign-relevant info per dot/narrative/topic:
 *   - Beneficiary (cool family vs warm family): whose campaign does this help?
 *   - Subject (primary vs outer hue): who is this narrative ABOUT?
 *
 * Five mutually-exclusive quadrants:
 *   our_defense    — owner=candidate × subject=candidate     (blue)    self-promotion / record defense
 *   our_offense    — owner=candidate × subject=opponent      (cyan)    our attacks on opponent
 *   their_defense  — owner=opponent  × subject=opponent      (red)     their self-promotion
 *   their_offense  — owner=opponent  × subject=candidate     (orange)  their attacks on us
 *   media          — owner=media OR subject=media           (gray)    neutral / off-topic
 *
 * At-a-glance reading:
 *   cool (blue/cyan)   = our side
 *   warm (red/orange)  = their side
 *   primary (blue/red) = defense (about themselves)
 *   outer  (cyan/orange) = offense (about the other side)
 */

export const QuadrantPalette = {
  our_defense:   '#0059c2',  // blue (matches existing candidate color)
  our_offense:   '#06b6d4',  // cyan
  their_defense: '#d71913',  // red (matches existing opponent color)
  their_offense: '#f97316',  // orange
  media:         '#a1a1a1',  // gray
} as const

export type OwnerType = 'candidate' | 'opponent' | 'media'
export type QuadrantKey = keyof typeof QuadrantPalette

/**
 * Maps (owner_type, subject_type) → quadrant key.
 * If subject_type is missing (older API responses), falls back to media.
 */
export function quadrantKey(
  owner: OwnerType | undefined | null,
  subject: OwnerType | undefined | null,
): QuadrantKey {
  if (!owner || owner === 'media') return 'media'
  if (!subject || subject === 'media') return 'media'
  if (owner === 'candidate' && subject === 'candidate') return 'our_defense'
  if (owner === 'candidate' && subject === 'opponent')  return 'our_offense'
  if (owner === 'opponent'  && subject === 'opponent')  return 'their_defense'
  if (owner === 'opponent'  && subject === 'candidate') return 'their_offense'
  return 'media'
}

/** Hex color for the (owner, subject) pair. */
export function quadrantColor(
  owner: OwnerType | undefined | null,
  subject: OwnerType | undefined | null,
): string {
  return QuadrantPalette[quadrantKey(owner, subject)]
}

/**
 * Backwards-compat wrapper for code that only knows owner_type and hasn't
 * been updated to thread subject_type through yet. Returns the OLD 3-color
 * mapping (which is the primary axis of the new palette: blue / red / gray).
 *
 * Prefer quadrantColor() everywhere; this exists only so we don't have to
 * rewrite every legacy color call in one pass.
 */
export function ownerColor(owner: OwnerType | undefined | null): string {
  if (owner === 'candidate') return QuadrantPalette.our_defense
  if (owner === 'opponent')  return QuadrantPalette.their_defense
  return QuadrantPalette.media
}

/** Generic (name-free) label for a quadrant — used when surnames aren't
 *  loaded yet. Prefer quadrantNamedLabel() when names are available. */
export function quadrantLabel(q: QuadrantKey): string {
  switch (q) {
    case 'our_defense':   return 'Pro-us'
    case 'our_offense':   return 'Anti-them'
    case 'their_defense': return 'Pro-them'
    case 'their_offense': return 'Anti-us'
    case 'media':         return 'Neutral'
  }
}

/**
 * Surname-substituted quadrant label — e.g. "Pro-Cognetti" or
 * "Anti-Bresnahan". Same 5-quadrant scheme used throughout the app.
 *
 * Mapping:
 *   our_defense   (owner=cand, subject=cand) → "Pro-{candidate}"     (defending us)
 *   our_offense   (owner=cand, subject=opp)  → "Anti-{opponent}"     (attacking them)
 *   their_defense (owner=opp,  subject=opp)  → "Pro-{opponent}"      (defending them)
 *   their_offense (owner=opp,  subject=cand) → "Anti-{candidate}"    (attacking us)
 *   media                                     → "Neutral"
 *
 * Falls back to generic "Pro-us / Anti-them / ..." when names aren't
 * loaded yet (avoids a jarring relabel on first render).
 */
export function quadrantNamedLabel(
  q: QuadrantKey,
  candidateName: string,
  opponentName: string,
): string {
  switch (q) {
    case 'our_defense':   return candidateName ? `Pro-${candidateName}` : 'Pro-us'
    case 'our_offense':   return opponentName  ? `Anti-${opponentName}` : 'Anti-them'
    case 'their_defense': return opponentName  ? `Pro-${opponentName}`  : 'Pro-them'
    case 'their_offense': return candidateName ? `Anti-${candidateName}` : 'Anti-us'
    case 'media':         return 'Neutral'
  }
}
