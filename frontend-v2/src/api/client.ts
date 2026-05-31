import type {
  ArticleDetail,
  CampaignConfig,
  CandidateFrameCluster,
  RaceDirectory,
  RaceSelectResult,
  DotLandscape,
  EntitySearchHit,
  EstablishedLandscape,
  FrameLandscapeDetail,
  HandleDiscoveryResult,
  IngestStatus,
  ThirdPartyDiscoveryResult,
  TrackedThirdPartyAccount,
  MorningBriefing,
  NarrativeFrame,
  NarrativeFrameDetail,
  NarrativeFrameTimeline,
  NarrativeLandscape,
  NarrativeTriageVerdict,
  Opponent,
  OpponentActivity,
  OutletSearchHit,
  QuoteSearchHit,
  SearchSuggestions,
  RaceSentiment,
  RaceSentimentUpdate,
  ReviewQueueItem,
  SourceItem,
  SetupStatus,
  SourceMonitor,
  Spike,
  TimelineEvent,
  TimeseriesPoint,
  ToneSeries,
  TrendSeries,
} from './types'

const BASE = '/api'
const ACCESS_CODE_KEY = 'cwr-access-code'

export function getAccessCode(): string | null {
  try { return localStorage.getItem(ACCESS_CODE_KEY) } catch { return null }
}

export function setAccessCode(code: string): void {
  try { localStorage.setItem(ACCESS_CODE_KEY, code) } catch { /* ignore */ }
}

export function clearAccessCode(): void {
  try { localStorage.removeItem(ACCESS_CODE_KEY) } catch { /* ignore */ }
}

// Subscribers that want to react to a forced logout (the AuthContext
// registers one to clear React state). We dispatch via a custom DOM event
// so the API client doesn't need to import React.
function notifyUnauthorized() {
  try { window.dispatchEvent(new CustomEvent('cwr:unauthorized')) } catch { /* ignore */ }
}

async function req<T>(path: string, options?: RequestInit): Promise<T> {
  // Merge the access-code header into whatever the caller passed without
  // clobbering Content-Type and friends.
  const code = getAccessCode()
  const headers = new Headers(options?.headers)
  if (code) headers.set('X-Access-Code', code)
  const res = await fetch(`${BASE}${path}`, { ...options, headers })
  if (res.status === 401) {
    // Server says our code is bad (or missing). Wipe local state and
    // let the AuthContext bounce the user to /login. Don't recurse —
    // throwing here means callers see a clean error too.
    clearAccessCode()
    notifyUnauthorized()
    throw new Error(`${options?.method ?? 'GET'} ${path} → 401: unauthorized`)
  }
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`${options?.method ?? 'GET'} ${path} → ${res.status}: ${text}`)
  }
  return res.json() as Promise<T>
}

function get<T>(path: string) {
  return req<T>(path)
}

function post<T>(path: string, body?: unknown) {
  return req<T>(path, {
    method: 'POST',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
}

function put<T>(path: string, body: unknown) {
  return req<T>(path, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
}

function del<T>(path: string) {
  return req<T>(path, { method: 'DELETE' })
}

export const api = {
  // Campaign
  campaign: () => get<CampaignConfig>('/campaign'),
  updateCampaign: (data: Partial<CampaignConfig>) => put<CampaignConfig>('/campaign', data),

  // Setup status
  setupStatus: () => get<SetupStatus>('/setup/status'),

  // Race directory — FEC-seeded snapshot of 2026 federal races. Powers the
  // "Pick your race" picker in Setup. Selecting a race auto-fills party,
  // district, office, location, election_date, geography_keywords, and
  // creates Opponent rows for every other candidate in the race.
  searchRaces: (q: string, limit = 25) =>
    get<RaceDirectory[]>(`/races/search?q=${encodeURIComponent(q)}&limit=${limit}`),
  getRace: (raceId: number) => get<RaceDirectory>(`/races/${raceId}`),
  selectRace: (raceId: number, candidateId?: number) =>
    post<RaceSelectResult>(`/races/${raceId}/select`, candidateId ? { candidate_id: candidateId } : {}),

  // Social handle discovery — ranks Instagram/Facebook handle candidates
  // via the configured search provider so the wizard can offer one-click
  // confirmation per actor.
  discoverHandles: (name: string, location?: string, limit = 3) => {
    const params = new URLSearchParams({ name, limit: String(limit) })
    if (location) params.set('location', location)
    return get<HandleDiscoveryResult>(`/setup/discover-handles?${params}`)
  },
  saveHandles: (body: {
    target: 'candidate' | 'opponent'
    opponent_id?: number
    // Full-replacement semantics: pass a list (incl. []) to replace, omit
    // (null/undefined) to leave that platform's stored list untouched.
    instagram_handles?: string[] | null
    facebook_pages?: string[] | null
  }) => post<{
    target: string
    opponent_id: number | null
    instagram_handles: string[]
    facebook_pages: string[]
  }>('/setup/save-handles', body),

  // Phase 2: third-party accounts that talk about this race (NOT the
  // candidate's or opponents' own accounts — those are saveHandles above).
  discoverThirdParty: () =>
    get<ThirdPartyDiscoveryResult>('/setup/discover-third-party'),
  listTrackedAccounts: () =>
    get<TrackedThirdPartyAccount[]>('/setup/tracked-accounts'),
  saveTrackedAccounts: (accounts: Array<Omit<TrackedThirdPartyAccount, 'id' | 'added_at'>>) =>
    post<TrackedThirdPartyAccount[]>('/setup/tracked-accounts', { accounts }),
  deleteTrackedAccount: (id: number) =>
    del<void>(`/setup/tracked-accounts/${id}`),

  // Briefing
  // v=2 returns a grounded race_memo (object with citations) + top_entities.
  // Default (v=1) keeps the legacy string memo for backward compat.
  morningBriefing: (v?: number) =>
    get<MorningBriefing>(`/briefing/morning${v && v > 1 ? `?v=${v}` : ''}`),

  // Admin manual text overrides. Backend pins each override to the
  // input_hash of the inputs the AI was working from when the admin
  // edited; on the next briefing fetch, if the current hash matches,
  // the override applies. If the hash differs (news has moved on), the
  // backend auto-clears the row.
  saveTextOverride: (key: string, value: string, input_hash?: string | null) =>
    put<{
      key: string; value: string; input_hash: string | null;
      created_by_name: string | null;
      created_at: string | null; updated_at: string | null;
    }>(`/admin/text-overrides/${encodeURIComponent(key)}`, { value, input_hash: input_hash ?? null }),
  clearTextOverride: (key: string) =>
    del<{ cleared: boolean; key: string }>(`/admin/text-overrides/${encodeURIComponent(key)}`),

  // District geometry — drives the Geographic Overlay page.
  // Returns a GeoJSON FeatureCollection for the current campaign's district.
  // Backend fetches from US Census TIGERweb on first request and caches.
  raceDistrictGeoJSON: () => get<GeoJSON.FeatureCollection>('/race/district-geojson'),
  // Auto-derived cities inside the current campaign's district, from the
  // US Census Gazetteer. Works for any US House race automatically.
  raceCities: (limit = 12) => get<{ district: string; cities: Array<{
    id: string; name: string; lat: number; lon: number;
    state: string; lsad: string; aland: number;
  }> }>(`/race/cities?limit=${limit}`),

  // Entity detail — profile + stats + recent articles + supporting quotes +
  // top narrative frames the entity's coverage matches.
  entity: (canonicalId: string) =>
    get<import('./types').EntityDetail>(`/entities/${encodeURIComponent(canonicalId)}`),

  // Narrative frames
  narrativeFrames: () => get<NarrativeFrame[]>('/narrative-frames'),
  narrativeFrameDetail: (id: number) => get<NarrativeFrameDetail>(`/narrative-frames/${id}/detail`),
  narrativeFrameTimeline: (id: number, days = 90) =>
    get<NarrativeFrameTimeline>(`/narrative-frames/${id}/timeline?days=${days}`),
  // Articles supporting one variant of a frame, optionally one date. Powers
  // click-to-drill-down on the Variant Evolution chart. Returns the same
  // DetailArticle[] shape used by narrativeFrameDetail.
  narrativeFrameVariantArticles: (frameId: number, variantId: number, date?: string) =>
    get<import('./types').DetailArticle[]>(
      `/narrative-frames/${frameId}/variant-articles?variant_id=${variantId}` +
      (date ? `&date=${encodeURIComponent(date)}` : ''),
    ),
  // v15.0 supporting-quote evidence for a frame. Returns verbatim claim_record
  // spans drawn from articles matched to this frame, with source attribution,
  // entity tags, and shallow label (attack / endorsement / policy_position /
  // vote / commitment / etc.). Group by label in the UI.
  frameQuoteEvidence: (frameId: number, limit = 200) =>
    get<{
      frame_id: number
      frame_name: string
      total: number
      by_label: Record<string, number>
      quotes: Array<{
        id: number
        evidence_span: string
        label: string | null
        confidence: string
        extractor_version: string | null
        entities: Array<{ id: string; name: string; type: string; surface: string | null }>
        article: {
          id: number
          title: string
          url: string | null
          published_at: string | null
          outlet_name: string | null
          bias_label: string | null
          reliability_score: number | null
        }
      }>
    }>(`/narrative-frames/${frameId}/quote-evidence?limit=${limit}`),
  createFrame: (data: { name: string; description: string; owner_type: string; subject_type?: string | null }) =>
    post<NarrativeFrame>('/narrative-frames', data),
  updateFrame: (id: number, data: Partial<NarrativeFrame>) =>
    put<NarrativeFrame>(`/narrative-frames/${id}`, data),
  // Frame deletion cascades through FrameClusterMatch, FrameVariant,
  // FrameStageHistory, and NarrativeFrameMention — see backend
  // `safe_delete_frame`. The backend requires `?confirm=DELETE+FRAME` to
  // proceed; pass `dryRun: true` to preview the cascade counts without
  // touching data. Type the confirm string at the call site so a misclick
  // can't bypass the guard.
  previewDeleteFrame: (id: number) =>
    del<{
      dry_run: true
      frame_id: number
      frame_name: string
      would_delete: {
        frame_cluster_matches: number
        narrative_frame_mentions: number
        frame_variants: number
        frame_stage_history: number
        candidate_frame_refs_cleared: number
        narrative_frame: number
      }
    }>(`/narrative-frames/${id}?dry_run=true`),
  deleteFrame: (id: number, confirm: 'DELETE FRAME') =>
    del<{ ok: true; deleted: Record<string, number> }>(
      `/narrative-frames/${id}?confirm=${encodeURIComponent(confirm)}`,
    ),
  suggestFrames: () => post<{ suggestions: NarrativeFrame[] }>('/narrative-frames/suggest'),
  rematchFrames: () => post<unknown>('/narrative-frames/rematch'),
  // Pending candidate-frame clusters — narratives the LLM has been flagging
  // during scoring that haven't been promoted into real frames yet.
  //
  // `last_error` is non-null when the promoter's last refresh failed —
  // typically Gemini quota exhausted with no OpenAI fallback available.
  // UI should surface this so the user knows "0 narratives" might mean
  // "system broken" rather than "nothing emerged."
  pendingCandidateClusters: (daysBack = 21) =>
    get<{
      count: number;
      suggestions: CandidateFrameCluster[];
      computed_at: string | null;
      from_cache: boolean;
      last_error: string | null;
      embeddings_available: boolean;
    }>(
      `/narrative-frames/candidate-frames/pending?days_back=${daysBack}`
    ),
  promoteCandidateCluster: (payload: {
    suggested_name: string;
    suggested_description?: string;
    owner_type: string;
    subject_type?: string | null;
    candidate_frame_ids: number[];
  }) => post<NarrativeFrame>('/narrative-frames/candidate-frames/promote', payload),
  // 2D narrative landscape — UMAP projection of candidate_frame embeddings
  // with HDBSCAN cluster IDs. Visualization only; clusters here match the
  // ones surfaced via `pendingCandidateClusters`.
  narrativeLandscape: (daysBack = 21) =>
    get<NarrativeLandscape>(
      `/narrative-frames/candidate-frames/landscape?days_back=${daysBack}`,
    ),
  // Persistent snapshot of proposed clusters. Reads from the snapshot
  // table so the list stays stable between user actions instead of
  // mutating on every recompute. Open snapshots only — dismissed/applied
  // rows are excluded.
  narrativeProposalsSnapshot: () =>
    get<NarrativeLandscape>('/narrative-frames/candidate-frames/snapshot'),
  // Trigger a snapshot refresh. Inserts new clusters from the live
  // HDBSCAN compute, updates existing rows in place. Open clusters that
  // are no longer in the compute STAY in the list — only user action
  // removes them.
  refreshNarrativeProposalsSnapshot: () =>
    post<{ inserted: number; refreshed: number; total_clusters_in_compute: number; computed_at: string }>(
      '/narrative-frames/candidate-frames/snapshot/refresh',
    ),
  // Dismiss a snapshot row by its member candidate_frame_ids — used when
  // the user clicks the X on a proposal that has no triage verdict (the
  // verdict-backed dismiss path stays separate so the audit trail of AI
  // verdicts is preserved).
  dismissProposalSnapshot: (candidateFrameIds: number[]) =>
    post<{ ok: boolean }>(
      '/narrative-frames/candidate-frames/snapshot/dismiss',
      { candidate_frame_ids: candidateFrameIds },
    ),
  // Companion view: UMAP projection of ESTABLISHED (already-promoted)
  // narrative frames. One bubble per frame; positions reflect topical
  // similarity. See backend `services/narrative_landscape_established.py`.
  establishedLandscape: () =>
    get<EstablishedLandscape>('/narrative-frames/landscape-established'),
  // V12 dot-level landscape: every article extract as a dot. Narrative
  // and topic groupings overlay as nested hulls. See backend
  // `services/landscape_dots.py`.
  dotLandscape: () =>
    get<DotLandscape>('/narrative-frames/landscape-established-dots'),
  // Member articles for one established frame — used as dots inside the
  // focused bubble on the Landscape page. Lazy: only fetched when the
  // user zooms in.
  frameLandscapeDetail: (frameId: number) =>
    get<FrameLandscapeDetail>(`/narrative-frames/${frameId}/landscape-detail`),
  // Rename a topic region. Marks edited_by_user=true so the LLM never
  // overwrites it via Jaccard match on subsequent recomputes.
  updateTopicRegionLabel: (persistedId: number, label: string) =>
    put<{ id: number; label: string; edited_by_user: boolean }>(
      `/topic-regions/${persistedId}/label`, { label },
    ),
  // ── Narrative triage (Phase B/D auto-triage workflow) ───────────────
  // GET verdicts: one row per proposed cluster fingerprint, with verdict
  // (auto_merge / auto_promote_suggested / human_review / auto_reject)
  // + LLM reasoning + optional pre-fill (suggested_name/description/owner).
  narrativeTriageVerdicts: (includeDismissed = false) =>
    get<{ verdicts: NarrativeTriageVerdict[] }>(
      `/narrative-triage?include_dismissed=${includeDismissed}`,
    ).then(r => r.verdicts),
  // Trigger a fresh triage pass. Costs LLM money (~$0.40 per pass over
  // ~20 clusters), so this is explicitly user-triggered, not background.
  // hands_off=true (default) means high-confidence verdicts are
  // automatically executed (auto_executed lists what was created/merged).
  runNarrativeTriage: (daysBack = 21, forceRefresh = false, handsOff = true) =>
    post<{
      evaluated: number; skipped_cached: number;
      auto_reject: number; auto_merge: number;
      auto_promote_suggested: number; human_review: number;
      errors: number; elapsed_seconds: number;
      auto_executed: Array<{
        triage_id: number;
        action: 'auto_merge' | 'auto_promote';
        frame_id: number;
        frame_name: string;
        candidate_frames_attached?: number;
      }>;
    }>('/narrative-triage/run', {
      days_back: daysBack, force_refresh: forceRefresh, hands_off: handsOff,
    }),
  dismissTriageVerdict: (triageId: number) =>
    post<{ id: number; dismissed_at: string }>(`/narrative-triage/${triageId}/dismiss`, {}),
  applyTriageVerdict: (triageId: number) =>
    post<{ id: number; applied_at: string }>(`/narrative-triage/${triageId}/apply`, {}),
  // For auto_merge verdicts: one-click execution that marks the cluster's
  // candidate frames resolved into the suggested target frame.
  executeTriageMerge: (triageId: number) =>
    post<{
      id: number; merged_into_frame_id: number; merged_into_frame_name: string;
      candidate_frames_marked: number; applied_at: string;
    }>(`/narrative-triage/${triageId}/execute-merge`, {}),
  frameTimeseries: (id: number, days = 30) =>
    get<{ series: TimeseriesPoint[] }>(`/frames/${id}/timeseries?days=${days}`)
      .then(r => r.series),
  shareOfVoice: (id: number) =>
    get<{ candidate: number; opponent: number; neutral: number }>(`/frames/${id}/share-of-voice`),

  // Full-text search across all articles (FTS5 backend — sources.py /search)
  search: (q: string, limit = 100) =>
    get<SourceItem[]>(`/search?q=${encodeURIComponent(q)}&limit=${limit}`),

  // Header dropdown — universal search across NOCTUA primitives.
  // Backed by routes/global_search.py. Articles use api.search() above.
  searchEntities: (q: string, limit = 5) =>
    get<EntitySearchHit[]>(`/search/entities?q=${encodeURIComponent(q)}&limit=${limit}`),
  searchQuotes: (q: string, limit = 5) =>
    get<QuoteSearchHit[]>(`/search/quotes?q=${encodeURIComponent(q)}&limit=${limit}`),
  searchOutlets: (q: string, limit = 5) =>
    get<OutletSearchHit[]>(`/search/outlets?q=${encodeURIComponent(q)}&limit=${limit}`),
  // Empty-state suggestions — the "Try searching" tour shown when the
  // header dropdown is focused with no query. Ranked by last-7-day
  // activity so the suggestions feel fresh on every focus.
  searchSuggestions: (perType = 3) =>
    get<SearchSuggestions>(`/search/suggestions?per_type=${perType}`),

  // Log Featured-Narratives card appearances for the day. Idempotent on
  // (frame_id, today) via a unique constraint on the backend. Frontend
  // fires this once per dashboard mount; the count feeds the saturation
  // penalty in lib/featuredFrame.ts on subsequent loads.
  logFeaturedAppearance: (frame_ids: number[]) =>
    post<{ recorded: number; day: string }>('/dashboard/featured-appearance', { frame_ids }),

  // Recent relevant articles (Dashboard right rail)
  recentArticles: (limit = 10) =>
    get<{ items: SourceItem[] }>(`/articles/recent?limit=${limit}`).then(r => r.items),
  // Configured candidate + opponent display names — used by the Articles
  // page sentiment filter to render dynamic labels per campaign.
  campaignNames: () =>
    get<{ candidate_name: string | null; opponent_name: string | null }>('/campaign/names'),
  // Full detail for one article — powers the article-detail modal.
  articleDetail: (id: number) => get<ArticleDetail>(`/articles/${id}`),

  // Review queue
  reviewQueue: () => get<ReviewQueueItem[]>('/review-queue'),
  // Items the keyword relevance gate excluded from the main queue. Same
  // base SQL filter as /review-queue but the opposite side of the
  // partition. Used by the "Recently filtered" safety view so the user
  // can spot-check what's being held back and tune if needed.
  reviewQueueFilteredOut: () => get<ReviewQueueItem[]>('/review-queue/filtered-out'),
  reviewQueueCount: () => get<{ count: number }>('/review-queue/count'),

  // Ingestion-quality alerts — list of currently-firing per-source alerts
  // (short-body collapse, gone-silent feed). Surfaced via the notifications
  // bell so a feed regression like the 2026-05-26 Google News collapse
  // doesn't go unnoticed for days. Backend job at
  // services/ingestion_health.py runs every 6h.
  ingestionAlerts: () =>
    get<{
      alerts: Array<{
        id: number
        source_name: string
        kind: 'short_body' | 'silent'
        detected_at: string | null
        baseline_avg_len: number | null
        current_avg_len: number | null
        sample_count_24h: number | null
        sample_count_7d: number | null
        last_checked_at: string | null
      }>
    }>('/health/ingestion-alerts'),
  reviewItem: (id: number, note?: string) =>
    post<unknown>(`/review-queue/${id}/review`, note ? { review_note: note } : undefined),
  dismissItem: (id: number) => post<unknown>(`/review-queue/${id}/dismiss`),
  markRelevant: (id: number) => post<unknown>(`/review-queue/${id}/mark-relevant`),
  markIrrelevant: (id: number) => post<unknown>(`/review-queue/${id}/mark-irrelevant`),
  bulkReview: (ids: number[]) => post<unknown>('/review-queue/bulk/review', { source_ids: ids }),
  bulkDismiss: (ids: number[]) => post<unknown>('/review-queue/bulk/dismiss', { source_ids: ids }),

  // Opponents
  opponents: () => get<Opponent[]>('/opponents'),
  createOpponent: (data: Partial<Opponent>) => post<Opponent>('/opponents', data),
  opponentActivity: (id: number) => get<OpponentActivity[]>(`/opponents/${id}/activity`),

  // Monitors
  monitors: () => get<SourceMonitor[]>('/source-monitors'),
  createMonitor: (data: Partial<SourceMonitor>) => post<SourceMonitor>('/source-monitors', data),
  updateMonitor: (id: number, data: Partial<SourceMonitor>) =>
    put<SourceMonitor>(`/source-monitors/${id}`, data),
  deleteMonitor: (id: number) => del<unknown>(`/source-monitors/${id}`),
  ingestAll: () => post<unknown>('/rss-feeds/ingest-all'),
  triggerCrawl: () => post<unknown>('/ingest/crawl'),
  discoverMonitorUrls: () =>
    post<{
      eligible: number
      converted: number
      failed: number
      skipped_cooldown: number
      details: {
        converted: Array<{ monitor_id: number; name: string; person: string; url: string; reason: string }>
        failed: Array<{ monitor_id: number; name: string; person?: string; reason: string }>
        skipped_cooldown: Array<{ monitor_id: number; name: string; last_checked_at: string | null }>
      }
    }>('/admin/discover-monitor-urls'),

  // Analytics — endpoints wrap their payload in {spikes|terms|entities: [...]}
  spikes: () =>
    get<{ spikes?: Array<Record<string, number | string | null>> }>('/analytics/spikes').then(r =>
      (r.spikes ?? []).map((s): Spike => ({
        frame_id: s.frame_id as number,
        frame_name: s.frame_name as string,
        reach_24h: (s.count_24h ?? s.reach_24h ?? 0) as number,
        avg_daily_reach: (s.daily_avg_7d ?? s.avg_daily_reach ?? 0) as number,
        ratio: (s.ratio ?? 0) as number,
        peak_at: (s.peak_at as string | null | undefined) ?? null,
      })),
    ),
  searchTrends: (geo = 'US-PA', days = 30) =>
    get<{ terms?: Array<{ term: string; series: Array<{ date: string; interest: number }> }> }>(
      `/analytics/search-trends?geo=${encodeURIComponent(geo)}&days=${days}`,
    ).then(r =>
      (r.terms ?? []).map((t): TrendSeries => ({ term: t.term, data: t.series ?? [] })),
    ),
  tone: (days = 30) =>
    get<{
      entities?: Array<{
        label: string
        entity_type: 'candidate' | 'opponent'
        series: Array<{ date: string; tone: number }>
      }>
    }>(`/analytics/tone?days=${days}`).then(r =>
      (r.entities ?? []).map((e): ToneSeries => ({
        query_label: e.label,
        entity_type: e.entity_type,
        data: (e.series ?? []).map(p => ({ date: p.date, avg_tone: p.tone, article_count: 0 })),
      })),
    ),

  // Ingest status
  ingestStatus: () => get<IngestStatus>('/ingest/status'),

  // Pipeline / system status
  getPipelineStatus: () => get<Record<string, unknown>>('/campaign/pipeline-status'),
  getLLMStatus: () => get<{ is_mock: boolean }>('/system/llm-status'),

  // Claim-layer — inspect a single (subject, predicate, object) claim,
  // its supporting and contesting articles, retract / reactivate.
  getClaim: (claimId: number) =>
    get<{
      claim: {
        id: number
        subject: { id: string; name: string; type: string; affiliation: string | null }
        predicate: string
        object: { id: string; name: string; type: string; affiliation: string | null }
        stance: { procedural: string | null; rhetorical: string | null; ideological: string | null }
        status: 'active' | 'contested' | 'retracted'
        retracted_at: string | null
        retracted_by: string | null
        retracted_reason: string | null
        first_seen: string | null
        last_seen: string | null
        sample_quote: string | null
        confidence: string
        extractor_version: string | null
        supporting_count: number
        contesting_count: number
      }
      supporting_articles: Array<{
        article_id: number
        article_title: string | null
        article_url: string | null
        outlet: string | null
        published_at: string | null
        sample_quote: string | null
        confidence: string
        extractor_version: string | null
        extracted_at: string | null
        stance: string
      }>
      contesting_articles: Array<{
        article_id: number
        article_title: string | null
        article_url: string | null
        outlet: string | null
        published_at: string | null
        sample_quote: string | null
        confidence: string
        extractor_version: string | null
        extracted_at: string | null
        stance: string
      }>
    }>(`/claims/${claimId}`),
  retractClaim: (claimId: number, reason?: string, by?: string) =>
    post<{ ok: boolean; claim_id: number; status: string }>(`/claims/${claimId}/retract`, { reason, by }),
  reactivateClaim: (claimId: number) =>
    post<{ ok: boolean; claim_id: number; status: string }>(`/claims/${claimId}/reactivate`, {}),

  // Multi-hop traversal — paths between two entities. Used by the
  // EntityNetwork "Find path" feature.
  entityNetworkPath: (fromCanonical: string, toCanonical: string, maxHops = 3, minRelationWeight = 2) =>
    get<{
      from: { id: string; name: string; type: string }
      to: { id: string; name: string; type: string }
      max_hops: number
      paths: Array<Array<{
        id: string
        name: string
        type: string
        affiliation: string | null
        mention_count: number
        seeded: boolean
        predicate: string | null
        direction: 'forward' | 'backward' | null
        weight: number | null
      }>>
      path_count: number
      truncated: boolean
    }>(`/entity-network/path?from=${encodeURIComponent(fromCanonical)}&to=${encodeURIComponent(toCanonical)}&max_hops=${maxHops}&min_relation_weight=${minRelationWeight}`),

  // Multi-hop traversal — N-hop ego network around a seed entity.
  // Used by the EntityNetwork "Show N-hop neighborhood" feature.
  entityNetworkNeighbors: (canonicalId: string, depth = 2, minRelationWeight = 2) =>
    get<{
      seed: { id: string; name: string; type: string; affiliation: string | null; mention_count: number; seeded: boolean }
      depth: number
      entities: Array<{ id: string; name: string; type: string; affiliation: string | null; mention_count: number; seeded: boolean; hop: number }>
      edges: Array<{ source: string; target: string; predicate: string; weight: number }>
      stats: { entity_count: number; edge_count: number }
    }>(`/entity-network/neighbors?entity=${encodeURIComponent(canonicalId)}&depth=${depth}&min_relation_weight=${minRelationWeight}`),

  // Entity review queue — surfaces contradictions and other items needing
  // human triage for the KG quality pass.
  entityReviewQueue: () => get<{
    summary: { contradictions: number; total: number }
    contradictions: Array<{
      item_type: string
      item_key: string
      title: string
      subject: { id: string; name: string; type: string; affiliation: string | null }
      object: { id: string; name: string; type: string }
      support_relations: Array<{ predicate: string; weight: number; confidence: string; sample_quote: string | null; stance?: { procedural: string; rhetorical: string; ideological: string; intensity: number } | null }>
      oppose_relations: Array<{ predicate: string; weight: number; confidence: string; sample_quote: string | null; stance?: { procedural: string; rhetorical: string; ideological: string; intensity: number } | null }>
      support_weight: number
      oppose_weight: number
      balance_score: number
      support_titles: string[]
      oppose_titles: string[]
      aggregate_stance?: { procedural: string; rhetorical: string; ideological: string; intensity: number; dimension_conflicts: string[] }
      conflicting_dimensions?: string[]
    }>
  }>('/entity-review-queue/items'),
  entityReviewDecide: (item_type: string, item_key: string, decision: 'approve' | 'reject' | 'skip', notes?: string) =>
    post<{ ok: boolean }>('/entity-review-queue/decide', { item_type, item_key, decision, notes }),

  // Narrative-frames ↔ entity-graph bridge.
  // Returns entities + relations propagated by the frame's supporting articles.
  frameGraph: (frameId: number, limit = 30) =>
    get<{
      frame: { id: number; name: string; description: string | null; owner_type: string }
      supporting_article_count: number
      entities: Array<{
        id: string
        name: string
        type: string
        affiliation: string | null
        mention_count_in_frame: number
        overall_mention_count: number
        seeded: boolean
      }>
      relations: Array<{
        id: string
        source: string
        source_name: string
        target: string
        target_name: string
        type: string
        weight_in_frame: number
        overall_weight: number
        in_frame_share: number
        sample_quote: string
        confidence: string
      }>
    }>(`/narrative-frames/${frameId}/graph?limit=${limit}`),
  framesForEntity: (canonicalId: string) =>
    get<{
      entity: { id: string; name: string; type: string }
      frames: Array<{ id: number; name: string; owner_type: string; description: string | null; article_overlap_count: number }>
    }>(`/narrative-frames/by-entity/${encodeURIComponent(canonicalId)}`),

  // v15.0 quote-anchored claim records for a given entity. Returns the
  // entity summary + all claim_records where this entity appears in
  // the verbatim quote span, with article + co-entity context.
  claimRecordsForEntity: (canonicalId: string, limit = 200) =>
    get<import('@/data/entityNetworkMock').ClaimRecordsResponse>(
      `/claim-records?entity=${encodeURIComponent(canonicalId)}&limit=${limit}`,
    ),

  // Extractor drift — surfaces ontology drift between live extractor and
  // the versions that produced existing evidence.
  extractorDriftSummary: () => get<{
    current_version: string
    current_summary: string
    total_relations: number
    relations_fresh: number
    relations_with_any_stale_evidence: number
    relations_with_all_stale_evidence: number
    by_version: Array<{
      version: string
      released_at: string | null
      summary: string
      breaking_changes: string[]
      evidence_count: number
      stale: boolean
      relations_with_evidence_at_this_version: number
    }>
    diffs: Array<{
      from_version: string
      to_version: string
      changes: string[]
    }>
  }>('/extractor-drift/summary'),

  // Entity network — real entities/relations extracted from articles.
  // min_mentions filters out low-signal nodes for a cleaner visualization.
  entityNetwork: (minMentions = 1) =>
    get<{
      entities: Array<{
        id: string
        name: string
        type: 'person' | 'organization' | 'bill' | 'event' | 'location'
        description: string
        affiliation: 'D' | 'R' | 'I' | null
        mention_count: number
        recent_article_titles: string[]
        first_seen: string | null
        last_seen: string | null
        seeded: boolean
      }>
      relations: Array<{
        id: string
        source: string
        target: string
        type: string
        weight: number
        sample_quote: string
        first_seen: string | null
        last_seen: string | null
        valid_from: string | null
        valid_to: string | null
        is_expired: boolean
        confidence: string
        claim_id?: number | null
        claim_status?: 'active' | 'contested' | 'retracted'
      }>
      stats: { entity_count: number; relation_count: number; seeded_count: number }
    }>(`/entity-network?min_mentions=${minMentions}`),

  // Race sentiment — markets + forecaster ratings.
  // Phase 1 added the manual-entry flow.
  // Phase 2 added connector sync (Polymarket live + Cook/etc. stubs).
  raceSentiment: () => get<RaceSentiment[]>('/race-sentiment'),
  updateRaceSentiment: (source: string, data: RaceSentimentUpdate) =>
    put<RaceSentiment>(`/race-sentiment/${source}`, data),
  syncRaceSentiment: (source: string) =>
    post<RaceSentiment>(`/race-sentiment/${source}/sync`),
  syncAllRaceSentiment: () =>
    post<{ synced: string[]; failed: string[] }>(`/race-sentiment/sync-all`),
  backfillRaceSentiment: (source: string, daysBack = 90) =>
    post<{ source: string; written: number; skipped_dedup: number }>(
      `/race-sentiment/${source}/backfill?days_back=${daysBack}`,
    ),
  raceSentimentHistory: (source: string, days = 30) =>
    get<import('./types').RaceSentimentSnapshot[]>(
      `/race-sentiment/${source}/history?days=${days}`,
    ),
  // Phase 3: unified timeline events for the /forecast chart
  // (frame promotions, stage transitions, top articles).
  raceSentimentEvents: (days = 30) =>
    get<TimelineEvent[]>(`/race-sentiment/events?days=${days}`),

  // Per-frame lifecycle events derived from article-match data:
  // emerged / peaked / faded. Used by the Timeline page to show real
  // narrative arcs rather than system-promotion timestamps.
  narrativeLifecycle: (staleDays = 30) =>
    get<import('./types').NarrativeLifecycleEvent[]>(
      `/race-sentiment/narrative-lifecycle?stale_days=${staleDays}`,
    ),

  // Friend-share access codes (see backend/app/services/access_codes.py).
  // verifyAccessCode validates a code from the login page before we
  // commit it to localStorage. getCurrentUser is what AuthContext calls
  // on mount to restore session — it sends the saved code via the
  // X-Access-Code header (added automatically in req()).
  verifyAccessCode: (code: string) =>
    post<{ name: string; initials: string; color: string; is_admin?: boolean }>('/auth/verify', { code }),
  getCurrentUser: () =>
    get<{ name: string; initials: string; color: string; is_admin?: boolean }>('/auth/me'),
}
