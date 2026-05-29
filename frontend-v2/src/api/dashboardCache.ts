import type { NarrativeFrame, SourceItem, Spike } from './types'
import { api } from './client'

// Tiny module-level cache for the Dashboard's three endpoints. Layout
// prefetches on app mount so opening Home from another page renders
// immediately from cache instead of showing skeletons.
//
// Cache invalidation:
//   - The Dashboard itself refreshes every 60s while mounted.
//   - Other pages (Narratives, Setup) should call `invalidateDashboard()`
//     after mutations so a subsequent Home navigation doesn't show stale
//     data for up to 60s. The Narratives page's frame edits, the promote-
//     candidate-cluster flow, and the Setup save are the main culprits.

interface DashboardCache {
  frames: NarrativeFrame[] | null
  spikes: Spike[] | null
  recent: SourceItem[] | null
  fetchedAt: number
}

const cache: DashboardCache = {
  frames: null, spikes: null, recent: null, fetchedAt: 0,
}

let inFlight: Promise<void> | null = null

export function getDashboardCache(): DashboardCache {
  return cache
}

export function prefetchDashboard(): Promise<void> {
  if (inFlight) return inFlight
  inFlight = (async () => {
    const [fr, sp, ra] = await Promise.allSettled([
      api.narrativeFrames(), api.spikes(), api.recentArticles(10),
    ])
    if (fr.status === 'fulfilled') cache.frames = fr.value
    if (sp.status === 'fulfilled') cache.spikes = sp.value
    if (ra.status === 'fulfilled') cache.recent = ra.value
    cache.fetchedAt = Date.now()
  })()
  inFlight.finally(() => { inFlight = null })
  return inFlight
}

/** Clear the cache so the next Dashboard render fetches fresh data.
 * Call this after any mutation that affects what Home displays — frame
 * edits, candidate-cluster promotion, frame deletion. The next mount
 * will see `cache.frames === null` and pull fresh. */
export function invalidateDashboard(): void {
  cache.frames = null
  cache.spikes = null
  cache.recent = null
  cache.fetchedAt = 0
}
