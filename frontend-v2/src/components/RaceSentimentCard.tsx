import { Pencil, ExternalLink, X, RefreshCw, CircleAlert, MoreHorizontal } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import { api } from '@/api/client'
import type { RaceSentiment, RaceSentimentUpdate } from '@/api/types'
import { InfoTooltip } from '@/components/InfoTooltip'

// ─────────────────────────────────────────────────────────────────────────────
// Race Sentiment Card
//
// Phase 1: display-only Dashboard card sourced from a manual-entry modal.
// Phase 2 will swap manual values for daily scraped/API values without any
// change to this component — the PUT endpoint stays the same.
//
// Design decisions baked in here:
//   • Markets and forecasters are shown in SEPARATE sections. No blended
//     "composite score" is ever computed or displayed — that would create
//     false epistemic authority from two heterogeneous measurement systems.
//   • Forecaster ratings are shown as BANDS (e.g. "Lean R · 55–65%"), not
//     normalized to a fake single percentage. The mapping
//     "Lean R = 60%" is not supported by anything.
//   • Candidate/opponent names are color-coded (blue/red), but the
//     percentage NUMBERS themselves stay in neutral text — the eye finds
//     "our number" without the number itself becoming a verdict.
//   • Deltas ARE colored by direction, because direction IS the data.
// ─────────────────────────────────────────────────────────────────────────────

const C = {
  bg2: 'var(--bg-2)', bg3: 'var(--bg-3)', bg4: 'var(--bg-4)',
  border: 'var(--border)', borderBright: 'var(--border-bright)',
  text1: 'var(--text-1)', text2: 'var(--text-2)', text3: 'var(--text-3)',
  candidate: 'var(--candidate)', opponent: 'var(--opponent)',
  green: 'var(--green)', red: 'var(--red)', accent: 'var(--accent)',
}

const HEADER_HELP =
  'How prediction markets and election forecasters see this race today.\n\n' +
  'Markets (Polymarket, Kalshi) reflect liquidity-weighted trader sentiment — ' +
  'useful but second-order, since traders react to the same media coverage your ' +
  'narratives track.\n\n' +
  'Forecaster ratings (Cook, Sabato, Inside Elections, DDHQ) reflect expert ' +
  'judgement about structural fundamentals.\n\n' +
  'No single number combines the two — they measure different things.'

const RATING_OPTIONS = [
  'Solid D', 'Likely D', 'Lean D', 'Tilt D',
  'Toss-up',
  'Tilt R', 'Lean R', 'Likely R', 'Solid R',
]

function formatRelativeTime(iso: string | null | undefined): string {
  if (!iso) return ''
  // The backend (Pydantic + datetime.utcnow) emits ISO timestamps WITHOUT
  // a timezone marker, e.g. "2026-05-26T21:38:41.201095". JS interprets
  // unmarked ISO as LOCAL time, which is wrong — the value is actually
  // UTC. Without this fix the footer can be hours off depending on the
  // user's offset (seen as "2h ago" right after a sync in CDT, etc.).
  // Tag unmarked strings as UTC explicitly.
  const hasTz = /[zZ]|[+-]\d{2}:?\d{2}$/.test(iso)
  const t = hasTz ? new Date(iso).getTime() : new Date(iso + 'Z').getTime()
  const diffMin = Math.round((Date.now() - t) / 60000)
  if (diffMin < 1) return 'just now'
  if (diffMin < 60) return `${diffMin}m ago`
  const diffH = Math.round(diffMin / 60)
  if (diffH < 24) return `${diffH}h ago`
  const diffD = Math.round(diffH / 24)
  return `${diffD}d ago`
}

function fmtPct(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  return `${v.toFixed(0)}%`
}

// Reduce a market row (two side-by-side percentages) to a single lead.
// The lead — "who's ahead, by how much" — is the actionable political
// signal; the raw two-side breakdown is extra cognitive load that doesn't
// change what the user does next. Returns null when no data is entered;
// returns a "tied" marker when both sides are equal; returns a "partial"
// marker when only one side has a value (rare, but possible via the edit
// modal saving asymmetrically).
type MarketLead =
  | { kind: 'none' }
  | { kind: 'tied' }
  | { kind: 'lead'; name: string; color: string; lead: number }
  | { kind: 'partial'; name: string; color: string; value: number }

function computeMarketLead(
  row: RaceSentiment, candidateName: string, opponentName: string,
): MarketLead {
  const cand = row.candidate_pct
  const opp = row.opponent_pct
  if (cand === null && opp === null) return { kind: 'none' }
  if (cand === null || opp === null) {
    const isCand = cand !== null
    return {
      kind: 'partial',
      name: isCand ? candidateName : opponentName,
      color: isCand ? C.candidate : C.opponent,
      value: (isCand ? cand : opp) as number,
    }
  }
  if (cand === opp) return { kind: 'tied' }
  const candAhead = cand > opp
  return {
    kind: 'lead',
    name: candAhead ? candidateName : opponentName,
    color: candAhead ? C.candidate : C.opponent,
    lead: Math.abs(cand - opp),
  }
}

function favorsColor(favors: string | null | undefined): string {
  if (favors === 'candidate') return C.candidate
  if (favors === 'opponent') return C.opponent
  return C.text2
}

// Short labels for the horizontal-scoreboard layout — the row of mini-cards
// has ~95px per column at the current card width, which doesn't fit
// "Sabato's Crystal Ball" / "Cook Political Report" / "Inside Elections".
// Full display_name is preserved in the hover-title and in the Edit modal.
const SHORT_LABELS: Record<string, string> = {
  polymarket:       'Polymarket',
  kalshi:           'Kalshi',
  cook:             'Cook',
  sabato:           'Sabato',
  inside_elections: 'Inside Elec.',
}

// ─────────────────────────────────────────────────────────────────────────────
// Shared row helpers
// ─────────────────────────────────────────────────────────────────────────────

function rowHasData(row: RaceSentiment): boolean {
  return (
    row.candidate_pct !== null ||
    row.opponent_pct !== null ||
    row.rating_label !== null ||
    row.rating_min_pct !== null
  )
}

function parseUtcIso(iso: string): Date {
  // See formatRelativeTime — backend emits UTC without a Z suffix.
  const hasTz = /[zZ]|[+-]\d{2}:?\d{2}$/.test(iso)
  return hasTz ? new Date(iso) : new Date(iso + 'Z')
}

function SyncBadge({ row }: { row: RaceSentiment }) {
  // Three visible states (LIVE is intentionally invisible — all wired
  // sources now sync LIVE, so a badge on every row would be noise. The
  // *absence* of a badge means "fresh data, nothing to worry about";
  // a visible badge means something needs attention.):
  //   MANUAL  → row has a value but it came from the Edit modal, not a sync
  //             (covers both "no connector configured" and "auto-sync failed
  //             but user entered a value anyway")
  //   BLOCKED → auto-sync failed AND no manual fallback value — the user
  //             needs to take action
  //   none    → either a successful recent auto-sync (the new default) or
  //             an empty placeholder row, nothing entered or attempted
  const hasData = rowHasData(row)

  // Fresh successful sync → no badge (the data speaks for itself).
  if (row.last_synced_at && !row.last_sync_error) {
    const ageHours = (Date.now() - parseUtcIso(row.last_synced_at).getTime()) / 3600000
    if (ageHours <= 36) {
      return null
    }
  }

  // No data + sync error → BLOCKED. The user needs to take action.
  if (row.last_sync_error && !hasData) {
    return (
      <span
        title={`Auto-sync error: ${row.last_sync_error}`}
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 3,
          color: C.red, fontSize: 10, fontWeight: 600,
          letterSpacing: '0.04em',
        }}
      >
        <CircleAlert size={11} />
        BLOCKED
      </span>
    )
  }

  // Has data but no fresh successful sync → MANUAL entry.
  // Covers: never-synced rows that the user typed values into, AND
  // rows where auto-sync failed but the user manually entered a value
  // anyway. Either way, the badge tells the truth: this number was
  // typed in, not pulled from a live source.
  if (hasData) {
    const tooltip = row.last_sync_error
      ? `Manually entered. Auto-sync still failing: ${row.last_sync_error}`
      : 'Manually entered. Auto-sync not configured for this source.'
    return (
      <span
        title={tooltip}
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 3,
          color: C.text3, fontSize: 10, fontWeight: 600,
          letterSpacing: '0.04em',
        }}
      >
        <Pencil size={10} />
        MANUAL
      </span>
    )
  }

  return null
}

// ─────────────────────────────────────────────────────────────────────────────
// SourceCell — one mini-card in the horizontal scoreboard.
//
// Layout is 3 stacked rows of text:
//   1. Source name (short label) + sync badge + ext-link icon
//   2. The signal (market lead like "Cognetti +18%" or rating label like "Toss-up")
//   3. Subline (delta "+2.0 7d" for markets, band "45–55%" for ratings)
//
// Markets and ratings share the cell — type-specific logic lives in
// the signal/subline computation, not in two parallel components.
// ─────────────────────────────────────────────────────────────────────────────

function SourceCell({
  row, candidateName, opponentName, isLast,
}: {
  row: RaceSentiment
  candidateName: string
  opponentName: string
  isLast: boolean
}) {
  const isMarket = row.source_type === 'market'
  const shortName = SHORT_LABELS[row.source] ?? row.display_name

  // Single signal line per cell (was lead + delta for markets, label + band
  // beneath for ratings — both collapsed to one row at the user's request).
  // For ratings the band rides inline next to the label as a muted tail.
  let signal: React.ReactNode
  if (isMarket) {
    const lead = computeMarketLead(row, candidateName, opponentName)
    if (lead.kind === 'none') {
      signal = <span style={{ color: C.text3 }}>No value</span>
    } else if (lead.kind === 'tied') {
      signal = <span style={{ color: C.text2, fontWeight: 600 }}>Tied</span>
    } else if (lead.kind === 'partial') {
      signal = (
        <span>
          <span style={{ color: lead.color, fontWeight: 600 }}>{lead.name}</span>
          {' '}
          <span style={{ color: C.text1, fontVariantNumeric: 'tabular-nums' }}>
            {lead.value.toFixed(0)}%
          </span>
        </span>
      )
    } else {
      signal = (
        <span title={`${candidateName} ${fmtPct(row.candidate_pct)} · ${opponentName} ${fmtPct(row.opponent_pct)}`}>
          <span style={{ color: lead.color, fontWeight: 600 }}>{lead.name}</span>
          {' '}
          <span style={{ color: C.text1, fontVariantNumeric: 'tabular-nums' }}>
            +{lead.lead.toFixed(0)}%
          </span>
        </span>
      )
    }
  } else {
    if (row.rating_label) {
      const band = (row.rating_min_pct !== null && row.rating_max_pct !== null)
        ? `${row.rating_min_pct.toFixed(0)}–${row.rating_max_pct.toFixed(0)}%`
        : null
      signal = (
        <span>
          <span style={{ color: favorsColor(row.favors), fontWeight: 600 }}>
            {row.rating_label}
          </span>
          {band && (
            <span style={{
              color: C.text3, marginLeft: 3,
              fontSize: 10, fontVariantNumeric: 'tabular-nums',
            }}>
              {band}
            </span>
          )}
        </span>
      )
    } else {
      signal = (
        <span style={{ color: C.text3 }}>
          {row.last_sync_error ? 'Blocked' : 'No rating'}
        </span>
      )
    }
  }

  return (
    <div style={{
      flex: '1 1 0',
      minWidth: 0,
      padding: '8px 8px',
      borderRight: isLast ? 'none' : `1px solid ${C.bg3}`,
    }}>
      {/* Row 1: name + sync badge + ext-link */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 4,
        fontSize: 10, color: C.text3, letterSpacing: '0.06em',
        fontWeight: 600, textTransform: 'uppercase',
        marginBottom: 4, minWidth: 0,
      }}>
        <span
          title={row.display_name}
          style={{
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            flexShrink: 1,
          }}
        >
          {shortName}
        </span>
        <SyncBadge row={row} />
        {row.source_url ? (
          <a
            href={row.source_url} target="_blank" rel="noopener noreferrer"
            title="Open source"
            style={{
              color: C.text3, display: 'inline-flex',
              alignItems: 'center', marginLeft: 'auto', flexShrink: 0,
            }}
          >
            <ExternalLink size={10} />
          </a>
        ) : null}
      </div>

      {/* Row 2: signal (market lead OR rating label + band, single line) */}
      <div style={{
        fontSize: 12, color: C.text1,
        whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
        lineHeight: 1.25,
      }}>
        {signal}
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Edit modal
// ─────────────────────────────────────────────────────────────────────────────

function EditModal({
  rows, onClose, onSaved,
  candidateName, opponentName,
}: {
  rows: RaceSentiment[]
  onClose: () => void
  onSaved: (row: RaceSentiment) => void
  candidateName: string
  opponentName: string
}) {
  return (
    <div
      role="dialog" aria-modal="true"
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)',
        display: 'flex', alignItems: 'flex-start', justifyContent: 'center',
        zIndex: 200, padding: '40px 16px', overflowY: 'auto',
      }}
    >
      <div
        onClick={e => e.stopPropagation()}
        style={{
          background: C.bg2, border: `1px solid ${C.border}`,
          borderRadius: 10, width: '100%', maxWidth: 560,
          padding: '20px 22px',
        }}
      >
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          marginBottom: 16,
        }}>
          <h2 style={{ fontSize: 16, fontWeight: 700, color: C.text1, margin: 0 }}>
            Edit Race Sentiment
          </h2>
          <button
            onClick={onClose}
            style={{
              background: 'transparent', border: 'none',
              color: C.text2, cursor: 'pointer', padding: 4,
              display: 'inline-flex', alignItems: 'center',
            }}
          >
            <X size={18} />
          </button>
        </div>

        <div style={{ fontSize: 12, color: C.text3, marginBottom: 16, lineHeight: 1.45 }}>
          Phase 1 manual entry. Each source saves independently. Live data
          will replace these values in Phase 2.
        </div>

        <SectionLabel>Markets</SectionLabel>
        {rows.filter(r => r.source_type === 'market').map(r => (
          <MarketEditForm
            key={r.id} row={r} onSaved={onSaved}
            candidateName={candidateName} opponentName={opponentName}
          />
        ))}

        <div style={{ height: 18 }} />

        <SectionLabel>Forecasters</SectionLabel>
        {rows.filter(r => r.source_type === 'rating').map(r => (
          <RatingEditForm key={r.id} row={r} onSaved={onSaved} />
        ))}
      </div>
    </div>
  )
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      fontSize: 10, color: C.text3, letterSpacing: '0.12em',
      fontWeight: 600, textTransform: 'uppercase',
      marginBottom: 8,
    }}>
      {children}
    </div>
  )
}

function FieldLabel({ children }: { children: React.ReactNode }) {
  return (
    <label style={{
      display: 'block', fontSize: 11, color: C.text3,
      marginBottom: 3, fontWeight: 500,
    }}>
      {children}
    </label>
  )
}

const inputStyle: CSSProperties = {
  width: '100%', background: C.bg3, border: `1px solid ${C.border}`,
  borderRadius: 5, padding: '6px 8px',
  color: C.text1, fontSize: 13, fontFamily: 'inherit',
  outline: 'none',
}

const saveBtnStyle: CSSProperties = {
  background: C.accent, color: '#000', fontWeight: 600,
  border: 'none', borderRadius: 5,
  padding: '6px 12px', fontSize: 12, cursor: 'pointer',
  fontFamily: 'inherit',
}

function MarketEditForm({
  row, onSaved, candidateName, opponentName,
}: {
  row: RaceSentiment
  onSaved: (row: RaceSentiment) => void
  candidateName: string
  opponentName: string
}) {
  const [candidatePct, setCandidatePct] = useState<string>(row.candidate_pct?.toString() ?? '')
  const [opponentPct, setOpponentPct] = useState<string>(row.opponent_pct?.toString() ?? '')
  const [delta7d, setDelta7d] = useState<string>(row.delta_7d?.toString() ?? '')
  const [sourceUrl, setSourceUrl] = useState<string>(row.source_url ?? '')
  const [saving, setSaving] = useState(false)
  const [savedAt, setSavedAt] = useState<number | null>(null)

  const parseNum = (s: string): number | null => {
    const t = s.trim()
    if (!t) return null
    const n = Number(t)
    return isNaN(n) ? null : n
  }

  const save = async () => {
    setSaving(true)
    try {
      const payload: RaceSentimentUpdate = {
        candidate_pct: parseNum(candidatePct),
        opponent_pct: parseNum(opponentPct),
        delta_7d: parseNum(delta7d),
        source_url: sourceUrl.trim() || null,
        as_of: new Date().toISOString(),
      }
      const updated = await api.updateRaceSentiment(row.source, payload)
      onSaved(updated)
      setSavedAt(Date.now())
    } finally {
      setSaving(false)
    }
  }

  const justSaved = savedAt && Date.now() - savedAt < 2000

  return (
    <div style={{
      background: C.bg3, border: `1px solid ${C.border}`,
      borderRadius: 6, padding: 12, marginBottom: 10,
    }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: C.text1, marginBottom: 8 }}>
        {row.display_name}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 8 }}>
        <div>
          <FieldLabel>{candidateName || 'Candidate'} %</FieldLabel>
          <input
            style={inputStyle} value={candidatePct}
            placeholder="e.g. 47"
            onChange={e => setCandidatePct(e.target.value)}
          />
        </div>
        <div>
          <FieldLabel>{opponentName || 'Opponent'} %</FieldLabel>
          <input
            style={inputStyle} value={opponentPct}
            placeholder="e.g. 53"
            onChange={e => setOpponentPct(e.target.value)}
          />
        </div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 2fr', gap: 10, marginBottom: 10 }}>
        <div>
          <FieldLabel>7-day Δ (candidate)</FieldLabel>
          <input
            style={inputStyle} value={delta7d}
            placeholder="+2.3"
            onChange={e => setDelta7d(e.target.value)}
          />
        </div>
        <div>
          <FieldLabel>Source URL</FieldLabel>
          <input
            style={inputStyle} value={sourceUrl}
            placeholder="https://polymarket.com/..."
            onChange={e => setSourceUrl(e.target.value)}
          />
        </div>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <button
          style={{ ...saveBtnStyle, opacity: saving ? 0.6 : 1 }}
          disabled={saving} onClick={save}
        >
          {saving ? 'Saving…' : 'Save'}
        </button>
        {justSaved && (
          <span style={{ fontSize: 11, color: C.green }}>Saved</span>
        )}
      </div>
    </div>
  )
}

function RatingEditForm({
  row, onSaved,
}: {
  row: RaceSentiment
  onSaved: (row: RaceSentiment) => void
}) {
  const [ratingLabel, setRatingLabel] = useState<string>(row.rating_label ?? '')
  const [ratingMin, setRatingMin] = useState<string>(row.rating_min_pct?.toString() ?? '')
  const [ratingMax, setRatingMax] = useState<string>(row.rating_max_pct?.toString() ?? '')
  const [favors, setFavors] = useState<string>(row.favors ?? '')
  const [sourceUrl, setSourceUrl] = useState<string>(row.source_url ?? '')
  const [saving, setSaving] = useState(false)
  const [savedAt, setSavedAt] = useState<number | null>(null)

  const parseNum = (s: string): number | null => {
    const t = s.trim()
    if (!t) return null
    const n = Number(t)
    return isNaN(n) ? null : n
  }

  const save = async () => {
    setSaving(true)
    try {
      const payload: RaceSentimentUpdate = {
        rating_label: ratingLabel.trim() || null,
        rating_min_pct: parseNum(ratingMin),
        rating_max_pct: parseNum(ratingMax),
        favors: (favors === 'candidate' || favors === 'opponent' || favors === 'tossup')
          ? favors : null,
        source_url: sourceUrl.trim() || null,
        as_of: new Date().toISOString(),
      }
      const updated = await api.updateRaceSentiment(row.source, payload)
      onSaved(updated)
      setSavedAt(Date.now())
    } finally {
      setSaving(false)
    }
  }

  const justSaved = savedAt && Date.now() - savedAt < 2000

  return (
    <div style={{
      background: C.bg3, border: `1px solid ${C.border}`,
      borderRadius: 6, padding: 12, marginBottom: 10,
    }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: C.text1, marginBottom: 8 }}>
        {row.display_name}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 8 }}>
        <div>
          <FieldLabel>Rating</FieldLabel>
          <select
            style={inputStyle as CSSProperties}
            value={ratingLabel}
            onChange={e => setRatingLabel(e.target.value)}
          >
            <option value="">— select —</option>
            {RATING_OPTIONS.map(r => (
              <option key={r} value={r}>{r}</option>
            ))}
          </select>
        </div>
        <div>
          <FieldLabel>Favors</FieldLabel>
          <select
            style={inputStyle as CSSProperties}
            value={favors}
            onChange={e => setFavors(e.target.value)}
          >
            <option value="">— select —</option>
            <option value="candidate">Candidate</option>
            <option value="opponent">Opponent</option>
            <option value="tossup">Toss-up</option>
          </select>
        </div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 2fr', gap: 10, marginBottom: 10 }}>
        <div>
          <FieldLabel>Band low %</FieldLabel>
          <input
            style={inputStyle} value={ratingMin}
            placeholder="55"
            onChange={e => setRatingMin(e.target.value)}
          />
        </div>
        <div>
          <FieldLabel>Band high %</FieldLabel>
          <input
            style={inputStyle} value={ratingMax}
            placeholder="65"
            onChange={e => setRatingMax(e.target.value)}
          />
        </div>
        <div>
          <FieldLabel>Source URL</FieldLabel>
          <input
            style={inputStyle} value={sourceUrl}
            placeholder="https://cookpolitical.com/..."
            onChange={e => setSourceUrl(e.target.value)}
          />
        </div>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <button
          style={{ ...saveBtnStyle, opacity: saving ? 0.6 : 1 }}
          disabled={saving} onClick={save}
        >
          {saving ? 'Saving…' : 'Save'}
        </button>
        {justSaved && (
          <span style={{ fontSize: 11, color: C.green }}>Saved</span>
        )}
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Card
// ─────────────────────────────────────────────────────────────────────────────

export function RaceSentimentCard() {
  const [rows, setRows] = useState<RaceSentiment[]>([])
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [candidateName, setCandidateName] = useState('')
  const [opponentName, setOpponentName] = useState('')
  // Header context menu (replaces the standalone Sync/Edit buttons).
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    api.raceSentiment().then(r => { setRows(r); setLoading(false) }).catch(() => setLoading(false))
    api.campaign().then(c => {
      // Use last name only for compactness (matches the rest of the app's
      // surname-substitution pattern used in QuadrantSelector).
      const parts = (c.candidate_name || '').trim().split(/\s+/)
      setCandidateName(parts[parts.length - 1] || '')
    }).catch(() => {})
    api.opponents().then(opps => {
      if (opps && opps.length > 0) {
        const parts = (opps[0].name || '').trim().split(/\s+/)
        setOpponentName(parts[parts.length - 1] || '')
      }
    }).catch(() => {})
  }, [])

  // Close the header context menu on any click outside its trigger/dropdown.
  useEffect(() => {
    if (!menuOpen) return
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [menuOpen])

  const runSync = async () => {
    setSyncing(true)
    setMenuOpen(false)
    try {
      await api.syncAllRaceSentiment()
      const fresh = await api.raceSentiment()
      setRows(fresh)
    } finally {
      setSyncing(false)
    }
  }

  const markets = rows.filter(r => r.source_type === 'market')
  const ratings = rows.filter(r => r.source_type === 'rating')
  // Footer timestamp = the most-recent updated_at among rows that ACTUALLY
  // have a value entered. Rows that are still empty seeds shouldn't make
  // the card look "updated" — that misleads the user into thinking data
  // exists when it doesn't.
  // (mostRecent / mostRecentIso removed — replaced by per-type stale
  //  detection below, which catches forecaster-only failures that a
  //  global "latest sync" timestamp would miss.)

  // Per-source-type stale detection. Backend scheduler runs markets every
  // 2h and forecasters every 12h (split cadences — see scheduler.py). A
  // single global "max(updated_at)" check would miss a forecaster-only
  // failure because fresh market syncs would keep the global max fresh.
  // So check each type against its own threshold:
  //   • markets: stale > 6h  (3 missed 2h cycles, tolerates one-off skip)
  //   • ratings: stale > 24h (2 missed 12h cycles)
  // Pick the freshest sync per type and compare to its threshold; surface
  // the FIRST type that's stale so the user knows what's broken.
  function freshestSyncAge(srcs: RaceSentiment[]): number | null {
    const times = srcs
      .map(r => r.last_synced_at ? parseUtcIso(r.last_synced_at).getTime() : 0)
      .filter(t => t > 0)
    if (times.length === 0) return null
    return (Date.now() - Math.max(...times)) / 3600000
  }
  const marketAge = freshestSyncAge(markets)
  const ratingAge = freshestSyncAge(ratings)
  let staleWarning: { label: string; age: number } | null = null
  if (marketAge !== null && marketAge > 6) {
    staleWarning = { label: 'Markets', age: marketAge }
  } else if (ratingAge !== null && ratingAge > 24) {
    staleWarning = { label: 'Forecasters', age: ratingAge }
  }

  return (
    <div style={{ marginBottom: 24 }}>
      {/* ── Section header (sits OUTSIDE the card to match the Featured
          Narratives section format on the same page) ── */}
      <div style={{
        display: 'flex', alignItems: 'center',
        gap: 12, marginBottom: 12, flexWrap: 'wrap',
      }}>
        <div style={{
          fontSize: 11, color: C.text3, letterSpacing: '0.12em',
          fontWeight: 600, textTransform: 'uppercase',
          display: 'inline-flex', alignItems: 'center',
        }}>
          Race Sentiment
          <InfoTooltip text={HEADER_HELP} maxWidth={360} />
        </div>
        <div style={{
          marginLeft: 'auto', display: 'inline-flex',
          alignItems: 'center', gap: 8,
        }}>
          {syncing ? (
            <span style={{ fontSize: 11, color: C.text3 }}>Syncing…</span>
          ) : staleWarning ? (
            <span
              title={`${staleWarning.label} auto-sync hasn't run successfully in ${Math.round(staleWarning.age)}h — open the menu to Sync now.`}
              style={{ fontSize: 11, color: C.red, fontWeight: 600 }}
            >
              {staleWarning.label} stale ({Math.round(staleWarning.age)}h)
            </span>
          ) : null}
          <div ref={menuRef} style={{ position: 'relative' }}>
            <button
              onClick={() => setMenuOpen(o => !o)}
              aria-label="More options"
              style={{
                background: 'transparent', border: 'none',
                color: C.text3, cursor: 'pointer',
                padding: 4, borderRadius: 4,
                display: 'inline-flex', alignItems: 'center',
              }}
              onMouseEnter={e => { e.currentTarget.style.color = C.text1 }}
              onMouseLeave={e => { e.currentTarget.style.color = C.text3 }}
            >
              <MoreHorizontal size={16} />
            </button>
            {menuOpen && (
              <div style={{
                position: 'absolute', top: '100%', right: 0,
                marginTop: 4, zIndex: 50,
                background: C.bg3, border: `1px solid ${C.border}`,
                borderRadius: 6, minWidth: 140,
                boxShadow: '0 4px 12px rgba(0,0,0,0.35)',
                padding: 4,
              }}>
                <button
                  onClick={runSync}
                  disabled={syncing}
                  style={{
                    width: '100%', background: 'transparent', border: 'none',
                    color: C.text1, fontSize: 12,
                    padding: '6px 10px', textAlign: 'left',
                    cursor: syncing ? 'wait' : 'pointer',
                    opacity: syncing ? 0.6 : 1,
                    display: 'inline-flex', alignItems: 'center', gap: 8,
                    fontFamily: 'inherit', borderRadius: 4,
                  }}
                  onMouseEnter={e => { if (!syncing) e.currentTarget.style.background = C.bg4 }}
                  onMouseLeave={e => { e.currentTarget.style.background = 'transparent' }}
                >
                  <RefreshCw size={12} className={syncing ? 'animate-spin' : ''} />
                  {syncing ? 'Syncing…' : 'Sync now'}
                </button>
                <button
                  onClick={() => { setEditing(true); setMenuOpen(false) }}
                  style={{
                    width: '100%', background: 'transparent', border: 'none',
                    color: C.text1, fontSize: 12,
                    padding: '6px 10px', textAlign: 'left', cursor: 'pointer',
                    display: 'inline-flex', alignItems: 'center', gap: 8,
                    fontFamily: 'inherit', borderRadius: 4,
                  }}
                  onMouseEnter={e => { e.currentTarget.style.background = C.bg4 }}
                  onMouseLeave={e => { e.currentTarget.style.background = 'transparent' }}
                >
                  <Pencil size={12} />
                  Edit values
                </button>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ── Loading / body ── */}
      {loading ? (
        <div style={{ color: C.text3, fontSize: 13, padding: '6px 0' }}>Loading…</div>
      ) : (
        <>
          {/* Horizontal scoreboard — one mini-cell per source. Markets first
              (Polymarket, Kalshi) then forecaster ratings (Cook, IE, Sabato).
              The signal format (lead vs rating label) already distinguishes
              the two without a section divider. */}
          {rows.length === 0 ? (
            <div style={{ color: C.text3, fontSize: 12, padding: '6px 0' }}>—</div>
          ) : (
            (() => {
              const ordered = [...ratings, ...markets]
              return (
                <div style={{
                  display: 'flex', alignItems: 'stretch',
                  background: C.bg2,
                  border: `1px solid ${C.border}`,
                  borderRadius: '0.625rem',
                  overflow: 'hidden',
                }}>
                  {ordered.map((row, idx) => (
                    <SourceCell
                      key={row.id} row={row}
                      candidateName={candidateName || 'Candidate'}
                      opponentName={opponentName || 'Opponent'}
                      isLast={idx === ordered.length - 1}
                    />
                  ))}
                </div>
              )
            })()
          )}

        </>
      )}

      {/* ── Edit modal ── */}
      {editing && (
        <EditModal
          rows={rows}
          candidateName={candidateName}
          opponentName={opponentName}
          onClose={() => setEditing(false)}
          onSaved={(updated) => {
            setRows(prev => prev.map(r => r.id === updated.id ? updated : r))
          }}
        />
      )}
    </div>
  )
}
