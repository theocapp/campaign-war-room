import { Pencil, ExternalLink, X, RefreshCw, CircleAlert, Wifi } from 'lucide-react'
import { useEffect, useState } from 'react'
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

function fmtDelta(v: number | null | undefined): { text: string; color: string } {
  if (v === null || v === undefined) return { text: '', color: C.text3 }
  if (v > 0) return { text: `+${v.toFixed(1)} 7d`, color: C.green }
  if (v < 0) return { text: `${v.toFixed(1)} 7d`, color: C.red }
  return { text: 'flat 7d', color: C.text3 }
}

function favorsColor(favors: string | null | undefined): string {
  if (favors === 'candidate') return C.candidate
  if (favors === 'opponent') return C.opponent
  return C.text2
}

// ─────────────────────────────────────────────────────────────────────────────
// Market row
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
  // Four states:
  //   LIVE    → recent successful auto-sync (green)
  //   MANUAL  → row has a value but it came from the Edit modal, not a sync
  //             (covers both "no connector configured" and "auto-sync failed
  //             but user entered a value anyway")
  //   BLOCKED → auto-sync failed AND no manual fallback value — the user
  //             needs to take action
  //   none    → empty placeholder row, nothing entered or attempted
  const hasData = rowHasData(row)

  // LIVE wins: a fresh successful sync overrides everything else.
  if (row.last_synced_at && !row.last_sync_error) {
    const ageHours = (Date.now() - parseUtcIso(row.last_synced_at).getTime()) / 3600000
    if (ageHours <= 36) {
      return (
        <span
          title={`Auto-synced ${formatRelativeTime(row.last_synced_at)} from ${row.display_name}`}
          style={{
            display: 'inline-flex', alignItems: 'center', gap: 3,
            color: C.green, fontSize: 10, fontWeight: 600,
            letterSpacing: '0.04em',
          }}
        >
          <Wifi size={11} />
          LIVE
        </span>
      )
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

function MarketRow({
  row, candidateName, opponentName,
}: { row: RaceSentiment; candidateName: string; opponentName: string }) {
  const delta = fmtDelta(row.delta_7d)
  const hasData = row.candidate_pct !== null || row.opponent_pct !== null

  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: '120px 1fr auto auto',
      alignItems: 'center', gap: 12,
      padding: '8px 0',
      borderBottom: `1px solid ${C.bg3}`,
      fontSize: 13,
    }}>
      <div style={{ color: C.text2, fontWeight: 500, display: 'flex', alignItems: 'center', gap: 6 }}>
        {row.display_name}
        <SyncBadge row={row} />
      </div>
      <div style={{ color: C.text1 }}>
        {hasData ? (
          <span>
            <span style={{ color: C.candidate, fontWeight: 600 }}>{candidateName}</span>
            {' '}
            <span style={{ color: C.text1, fontVariantNumeric: 'tabular-nums' }}>
              {fmtPct(row.candidate_pct)}
            </span>
            <span style={{ color: C.text3, margin: '0 8px' }}>·</span>
            <span style={{ color: C.opponent, fontWeight: 600 }}>{opponentName}</span>
            {' '}
            <span style={{ color: C.text1, fontVariantNumeric: 'tabular-nums' }}>
              {fmtPct(row.opponent_pct)}
            </span>
          </span>
        ) : (
          <span style={{ color: C.text3 }}>No value entered</span>
        )}
      </div>
      <div style={{
        color: delta.color,
        fontSize: 12, fontWeight: 600,
        fontVariantNumeric: 'tabular-nums',
        minWidth: 70, textAlign: 'right',
      }}>
        {delta.text}
      </div>
      <div style={{ minWidth: 16, textAlign: 'right' }}>
        {row.source_url ? (
          <a
            href={row.source_url} target="_blank" rel="noopener noreferrer"
            style={{ color: C.text3, display: 'inline-flex', alignItems: 'center' }}
            title="Open source"
          >
            <ExternalLink size={13} />
          </a>
        ) : null}
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Forecaster (rating) row
// ─────────────────────────────────────────────────────────────────────────────

function RatingRow({ row }: { row: RaceSentiment }) {
  const hasData = !!row.rating_label
  const minMax = (row.rating_min_pct !== null && row.rating_max_pct !== null)
    ? `${row.rating_min_pct.toFixed(0)}–${row.rating_max_pct.toFixed(0)}%`
    : null
  const fc = favorsColor(row.favors)

  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: '120px 1fr auto auto',
      alignItems: 'center', gap: 12,
      padding: '8px 0',
      borderBottom: `1px solid ${C.bg3}`,
      fontSize: 13,
    }}>
      <div style={{ color: C.text2, fontWeight: 500, display: 'flex', alignItems: 'center', gap: 6 }}>
        {row.display_name}
        <SyncBadge row={row} />
      </div>
      <div>
        {hasData ? (
          <span>
            <span style={{ color: fc, fontWeight: 600 }}>{row.rating_label}</span>
            {minMax && (
              <span style={{ color: C.text3, marginLeft: 8, fontVariantNumeric: 'tabular-nums' }}>
                · {minMax}
              </span>
            )}
          </span>
        ) : (
          <span style={{ color: C.text3 }}>
            {row.last_sync_error ? 'Auto-sync blocked — use Edit' : 'No rating entered'}
          </span>
        )}
      </div>
      <div style={{ minWidth: 70, textAlign: 'right' }}>
        {/* Reserved for future deltas on rating changes — Phase 2 */}
      </div>
      <div style={{ minWidth: 16, textAlign: 'right' }}>
        {row.source_url ? (
          <a
            href={row.source_url} target="_blank" rel="noopener noreferrer"
            style={{ color: C.text3, display: 'inline-flex', alignItems: 'center' }}
            title="Open source"
          >
            <ExternalLink size={13} />
          </a>
        ) : null}
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

  const markets = rows.filter(r => r.source_type === 'market')
  const ratings = rows.filter(r => r.source_type === 'rating')
  // Footer timestamp = the most-recent updated_at among rows that ACTUALLY
  // have a value entered. Rows that are still empty seeds shouldn't make
  // the card look "updated" — that misleads the user into thinking data
  // exists when it doesn't.
  const rowsWithData = rows.filter(r =>
    r.candidate_pct !== null || r.opponent_pct !== null ||
    r.rating_label || r.rating_min_pct !== null
  )
  const mostRecent = rowsWithData
    .map(r => r.updated_at ? parseUtcIso(r.updated_at).getTime() : 0)
    .reduce((m, t) => Math.max(m, t), 0)
  const mostRecentIso = mostRecent ? new Date(mostRecent).toISOString() : null

  return (
    <div style={{
      background: C.bg2, border: `1px solid ${C.border}`,
      borderRadius: '0.625rem', padding: '14px 16px',
      marginBottom: 24,
    }}>
      {/* ── Header ── */}
      <div style={{
        display: 'flex', alignItems: 'center',
        gap: 10, marginBottom: 10, flexWrap: 'wrap',
      }}>
        <div style={{
          fontSize: 11, color: C.text3, letterSpacing: '0.12em',
          fontWeight: 600, textTransform: 'uppercase',
          display: 'inline-flex', alignItems: 'center',
        }}>
          Race Sentiment
          <InfoTooltip text={HEADER_HELP} maxWidth={360} />
        </div>
        <div style={{ marginLeft: 'auto', display: 'inline-flex', gap: 6 }}>
          <button
            onClick={async () => {
              setSyncing(true)
              try {
                await api.syncAllRaceSentiment()
                const fresh = await api.raceSentiment()
                setRows(fresh)
              } finally {
                setSyncing(false)
              }
            }}
            disabled={syncing}
            style={{
              background: 'transparent', border: `1px solid ${C.border}`,
              borderRadius: 5, padding: '4px 10px',
              color: C.text2, fontSize: 12,
              cursor: syncing ? 'wait' : 'pointer',
              opacity: syncing ? 0.6 : 1,
              display: 'inline-flex', alignItems: 'center', gap: 6,
              fontFamily: 'inherit',
            }}
            title="Refresh from connected sources (Polymarket etc.). Sources without a connector or blocked by Cloudflare are skipped."
            onMouseEnter={e => { if (!syncing) e.currentTarget.style.borderColor = C.borderBright }}
            onMouseLeave={e => { e.currentTarget.style.borderColor = C.border }}
          >
            <RefreshCw size={12} className={syncing ? 'animate-spin' : ''} />
            {syncing ? 'Syncing…' : 'Sync now'}
          </button>
          <button
            onClick={() => setEditing(true)}
            style={{
              background: 'transparent', border: `1px solid ${C.border}`,
              borderRadius: 5, padding: '4px 10px',
              color: C.text2, fontSize: 12, cursor: 'pointer',
              display: 'inline-flex', alignItems: 'center', gap: 6,
              fontFamily: 'inherit',
            }}
            onMouseEnter={e => { e.currentTarget.style.borderColor = C.borderBright }}
            onMouseLeave={e => { e.currentTarget.style.borderColor = C.border }}
          >
            <Pencil size={12} />
            Edit values
          </button>
        </div>
      </div>

      {/* ── Loading / body ── */}
      {loading ? (
        <div style={{ color: C.text3, fontSize: 13, padding: '6px 0' }}>Loading…</div>
      ) : (
        <>
          {/* Markets */}
          <div style={{ marginBottom: 14 }}>
            <div style={{
              fontSize: 10, color: C.text3, letterSpacing: '0.1em',
              fontWeight: 600, textTransform: 'uppercase', marginBottom: 4,
            }}>
              Markets
            </div>
            {markets.length === 0 ? (
              <div style={{ color: C.text3, fontSize: 12, padding: '6px 0' }}>—</div>
            ) : (
              markets.map(m => (
                <MarketRow
                  key={m.id} row={m}
                  candidateName={candidateName || 'Candidate'}
                  opponentName={opponentName || 'Opponent'}
                />
              ))
            )}
          </div>

          {/* Forecasters */}
          <div>
            <div style={{
              fontSize: 10, color: C.text3, letterSpacing: '0.1em',
              fontWeight: 600, textTransform: 'uppercase', marginBottom: 4,
            }}>
              Forecasters
            </div>
            {ratings.length === 0 ? (
              <div style={{ color: C.text3, fontSize: 12, padding: '6px 0' }}>—</div>
            ) : (
              ratings.map(r => <RatingRow key={r.id} row={r} />)
            )}
          </div>

          {/* Footer */}
          <div style={{
            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
            marginTop: 12, fontSize: 11, color: C.text3,
          }}>
            <span>
              Live: Polymarket. Manual: forecasters (Cloudflare-blocked). Daily auto-sync.
            </span>
            {mostRecentIso && (
              <span>Updated {formatRelativeTime(mostRecentIso)}</span>
            )}
          </div>
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
