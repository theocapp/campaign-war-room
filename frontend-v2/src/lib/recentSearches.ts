/**
 * Recent-searches history for the global header search bar.
 *
 * Persisted to localStorage as a simple JSON array of query strings,
 * most-recent first. Dedups on push (re-typing an old query bumps it
 * back to the top instead of leaving a stale duplicate). Capped at
 * MAX_ENTRIES so the dropdown can render the full list without
 * pagination.
 */
const STORAGE_KEY = 'noctua:recent-searches'
const MAX_ENTRIES = 8

function read(): string[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed.filter(s => typeof s === 'string') : []
  } catch {
    return []
  }
}

function write(entries: string[]): void {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(entries)) } catch { /* ignore */ }
}

export function getRecentSearches(): string[] {
  return read()
}

export function pushRecentSearch(query: string): void {
  const term = query.trim()
  if (!term) return
  const lower = term.toLowerCase()
  const existing = read().filter(s => s.toLowerCase() !== lower)
  const next = [term, ...existing].slice(0, MAX_ENTRIES)
  write(next)
  // Let listeners (the SearchBar in the same tab) update without a
  // full reload. Cross-tab sync is handled by the native `storage`
  // event, which only fires in OTHER tabs.
  try { window.dispatchEvent(new CustomEvent('noctua:recent-searches-changed')) } catch { /* ignore */ }
}

export function clearRecentSearches(): void {
  write([])
  try { window.dispatchEvent(new CustomEvent('noctua:recent-searches-changed')) } catch { /* ignore */ }
}
