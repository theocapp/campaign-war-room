import { Pencil, ExternalLink, CircleAlert, Clock } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '@/api/client'
import type { RaceSentiment } from '@/api/types'

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
  // Backend (Pydantic + datetime.utcnow) emits ISO timestamps without a
  // timezone marker. JS interprets unmarked ISO as LOCAL time, which is
  // wrong here — the value is actually UTC. Tag unmarked strings as UTC.
  const hasTz = /[zZ]|[+-]\d{2}:?\d{2}$/.test(iso)
  return hasTz ? new Date(iso) : new Date(iso + 'Z')
}

// ── Staleness thresholds ──────────────────────────────────────────────────
// A source's number is "stale" once auto-sync has missed roughly three of its
// scheduled refresh cycles — long enough to ride out a transient fetch failure
// or an app restart (which resets APScheduler's interval timer) without false
// alarms, short enough that a genuinely wedged sync surfaces within a day.
// Cadence lives in backend services/scheduler.py: markets repoll every 2h,
// forecaster ratings every 12h. Tune these two numbers if that cadence moves.
const STALE_AFTER_HOURS: Record<RaceSentiment['source_type'], number> = {
  market: 6,    // 3 × 2h market cadence
  rating: 36,   // 3 × 12h forecaster cadence
}
const DEFAULT_STALE_AFTER_HOURS = 36

function staleAfterHours(sourceType: string): number {
  return STALE_AFTER_HOURS[sourceType as RaceSentiment['source_type']] ?? DEFAULT_STALE_AFTER_HOURS
}

function fmtAge(hours: number): string {
  if (hours < 48) return `${Math.max(1, Math.round(hours))}h`
  return `${Math.round(hours / 24)}d`
}

function SyncBadge({ row }: { row: RaceSentiment }) {
  // Four visible states (a fifth, LIVE, is intentionally invisible — most
  // wired sources sync cleanly, so a badge on every healthy row would be
  // noise. The *absence* of a badge means "fresh, nothing to worry about";
  // a visible badge means something needs attention):
  //   STALE   → the number came from a real sync, but that sync is now older
  //             than its refresh cadence allows (or is actively failing), so
  //             the value may be out of date. This is the admin's "don't
  //             trust this as live" flag.
  //   MANUAL  → row has a value that was typed in, never auto-synced
  //   BLOCKED → auto-sync failed AND there's no value to fall back on — the
  //             user needs to take action
  //   none    → fresh successful sync, or an empty placeholder row
  const hasData = rowHasData(row)
  const err = row.last_sync_error
  const staleAfter = staleAfterHours(row.source_type)
  const ageHours = row.last_synced_at
    ? (Date.now() - parseUtcIso(row.last_synced_at).getTime()) / 3600000
    : null

  // 1. Fresh successful sync → no badge (the data speaks for itself).
  if (ageHours !== null && !err && ageHours <= staleAfter) {
    return null
  }

  // 2. Synced before, but the number is now older than its refresh cadence
  //    allows — or the latest sync attempt is failing — → STALE. The value on
  //    screen is real but no longer guaranteed live, which is exactly what the
  //    admin needs flagged. (This also corrects an earlier bug where a stale
  //    auto-synced row was mislabeled "MANUAL".)
  if (ageHours !== null && hasData) {
    const tooltip = err
      ? `Last auto-synced ${fmtAge(ageHours)} ago and the latest refresh is failing (${err}). This number may be out of date.`
      : `Last auto-synced ${fmtAge(ageHours)} ago — auto-sync has fallen behind, so this number may be out of date.`
    return (
      <span
        title={tooltip}
        style={{
          display: 'inline-flex', alignItems: 'center', gap: 3,
          color: C.accent, fontSize: 10, fontWeight: 600,
          letterSpacing: '0.04em',
        }}
      >
        <Clock size={11} />
        STALE
      </span>
    )
  }

  // 3. Never auto-synced but has a value → MANUAL (someone typed it in).
  if (hasData) {
    const tooltip = err
      ? `Manually entered. Auto-sync isn't working yet: ${err}`
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

  // 4. Never synced, nothing to show, and sync errored → BLOCKED (needs action).
  if (err) {
    return (
      <span
        title={`Auto-sync error: ${err}`}
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
      padding: '8px 8px 9.5px',
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
// Card
// ─────────────────────────────────────────────────────────────────────────────

export function RaceSentimentCard() {
  const [rows, setRows] = useState<RaceSentiment[]>([])
  const [loading, setLoading] = useState(true)
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

  return (
    <div style={{ marginBottom: 24 }}>
      {/* Section header (title, InfoTooltip, ··· menu, staleness warning)
          removed 2026-05-29 to declutter. Auto-sync (markets every 2h,
          forecasters every 12h) is the only sync path now. */}

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
              // Banner — same content as before but without card chrome
              // (no bg2 box, no rounded border). A thin bottom rule
              // separates it from the briefing sections below so it
              // still reads as a discrete strip at the top of the page.
              return (
                <div style={{
                  display: 'flex', alignItems: 'stretch',
                  borderBottom: `1px solid ${C.border}`,
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

      {/* Edit modal removed 2026-05-29 — values are now auto-sync only. */}
    </div>
  )
}
