import type { MorningBriefing, NarrativeFrame, SourceItem, Spike } from './types'
import { api } from './client'

// Tiny module-level cache for the Dashboard's endpoints. Layout prefetches
// on app mount so opening Home from another page renders immediately from
// cache instead of showing skeletons.
//
// Cache invalidation:
//   - The Dashboard itself refreshes every 60s while mounted.
//   - Other pages (Narratives, Setup) should call `invalidateDashboard()`
//     after mutations so a subsequent Home navigation doesn't show stale
//     data for up to 60s.
//
// The briefing call is LLM-backed and can take several seconds. It's
// fetched in PARALLEL with the fast endpoints but its completion is NOT
// awaited by `prefetchDashboard()` — the dashboard skeleton must not be
// blocked by a slow LLM synthesis. Use `awaitBriefing()` if you need to
// know when briefing data lands.

interface DashboardCache {
  frames: NarrativeFrame[] | null
  spikes: Spike[] | null
  recent: SourceItem[] | null
  briefing: MorningBriefing | null
  fetchedAt: number
}

const cache: DashboardCache = {
  frames: null, spikes: null, recent: null, briefing: null, fetchedAt: 0,
}

let inFlight: Promise<void> | null = null
let briefingInFlight: Promise<void> | null = null

export function getDashboardCache(): DashboardCache {
  return cache
}

function startBriefingFetch(): Promise<void> {
  if (briefingInFlight) return briefingInFlight
  briefingInFlight = api.morningBriefing(2)
    .then(b => { cache.briefing = b })
    .catch(() => { /* swallow — UI shows fallback state */ })
    .finally(() => { briefingInFlight = null })
  return briefingInFlight
}

export function prefetchDashboard(): Promise<void> {
  if (inFlight) return inFlight
  // Kick off briefing in parallel but don't wait for it — the dashboard
  // skeleton resolves on the fast endpoints alone.
  if (!cache.briefing) startBriefingFetch()
  inFlight = (async () => {
    const [fr, sp, ra] = await Promise.allSettled([
      api.narrativeFrames(), api.spikes(), api.recentArticles(15),
    ])
    if (fr.status === 'fulfilled') cache.frames = fr.value
    if (sp.status === 'fulfilled') cache.spikes = sp.value
    if (ra.status === 'fulfilled') cache.recent = ra.value
    cache.fetchedAt = Date.now()
  })()
  inFlight.finally(() => { inFlight = null })
  return inFlight
}

/**
 * Resolve once briefing data is in the cache (or has failed to load).
 * The Dashboard awaits this separately from `prefetchDashboard()` so the
 * slow LLM call doesn't gate first paint.
 */
export function awaitBriefing(): Promise<MorningBriefing | null> {
  if (cache.briefing) return Promise.resolve(cache.briefing)
  const inflight = briefingInFlight ?? startBriefingFetch()
  return inflight.then(() => cache.briefing)
}

/** Clear the cache so the next Dashboard render fetches fresh data.
 * Call this after any mutation that affects what Home displays — frame
 * edits, candidate-cluster promotion, frame deletion. The next mount
 * will see `cache.frames === null` and pull fresh. */
export function invalidateDashboard(): void {
  cache.frames = null
  cache.spikes = null
  cache.recent = null
  cache.briefing = null
  cache.fetchedAt = 0
}
