// Shared date formatters for article-style timestamps.
//
// Rule (applied wherever an article / quote / observation date is shown):
//   < 1 minute  → "just now"
//   < 1 hour    → "Xm ago"
//   < 24 hours  → "Xh ago"
//   >= 24 hours → date + time, e.g. "Nov 26, 3:45 PM" (year added if not current)
//
// `formatArticleDateLong` adds the weekday and always shows the year, for
// detail-page headers where the extra context fits.

const SHORT_TIME: Intl.DateTimeFormatOptions = { hour: 'numeric', minute: '2-digit' }

// Backend timestamps are stored as UTC but often serialized without a tz
// designator (e.g. "2026-05-27T11:01:19" rather than "...Z"). Treat any
// tz-less string as UTC so users in every timezone get the same elapsed
// time and the same absolute moment converted into their own local clock.
function hasTimezoneMarker(iso: string): boolean {
  return /Z$|[+-]\d{2}:?\d{2}$/.test(iso)
}

function parseAsUtcWhenNaive(iso: string): Date {
  return new Date(hasTimezoneMarker(iso) ? iso : iso + 'Z')
}

function diffMs(iso: string): number | null {
  const t = parseAsUtcWhenNaive(iso).getTime()
  if (isNaN(t)) return null
  return Date.now() - t
}

function relativeWithin24h(ms: number): string | null {
  if (ms < 0) return null
  const min = Math.floor(ms / 60_000)
  if (min < 1) return 'just now'
  if (min < 60) return `${min}m ago`
  const h = Math.floor(min / 60)
  if (h < 24) return `${h}h ago`
  return null
}

export function formatArticleDate(iso?: string | null): string {
  if (!iso) return ''
  const ms = diffMs(iso)
  if (ms === null) return ''
  const rel = relativeWithin24h(ms)
  if (rel) return rel
  const d = parseAsUtcWhenNaive(iso)
  const currentYear = new Date().getFullYear()
  const opts: Intl.DateTimeFormatOptions = {
    month: 'short',
    day: 'numeric',
    ...SHORT_TIME,
  }
  if (d.getFullYear() !== currentYear) opts.year = 'numeric'
  return d.toLocaleString('en-US', opts)
}

export function formatArticleDateLong(iso?: string | null): string {
  if (!iso) return '—'
  const ms = diffMs(iso)
  if (ms === null) return '—'
  const rel = relativeWithin24h(ms)
  if (rel) return rel
  return parseAsUtcWhenNaive(iso).toLocaleString('en-US', {
    weekday: 'short',
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    ...SHORT_TIME,
  })
}
