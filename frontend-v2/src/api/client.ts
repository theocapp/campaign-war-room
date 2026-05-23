import type {
  CampaignConfig,
  IngestStatus,
  MorningBriefing,
  NarrativeFrame,
  NarrativeFrameDetail,
  NarrativeFrameTimeline,
  Opponent,
  OpponentActivity,
  ReviewQueueItem,
  SourceItem,
  SetupStatus,
  SourceMonitor,
  Spike,
  TimeseriesPoint,
  ToneSeries,
  TrendSeries,
} from './types'

const BASE = '/api'

async function req<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, options)
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

  // Briefing
  morningBriefing: () => get<MorningBriefing>('/briefing/morning'),

  // Narrative frames
  narrativeFrames: () => get<NarrativeFrame[]>('/narrative-frames'),
  narrativeFrameDetail: (id: number) => get<NarrativeFrameDetail>(`/narrative-frames/${id}/detail`),
  narrativeFrameTimeline: (id: number, days = 90) =>
    get<NarrativeFrameTimeline>(`/narrative-frames/${id}/timeline?days=${days}`),
  createFrame: (data: { name: string; description: string; owner_type: string }) =>
    post<NarrativeFrame>('/narrative-frames', data),
  updateFrame: (id: number, data: Partial<NarrativeFrame>) =>
    put<NarrativeFrame>(`/narrative-frames/${id}`, data),
  deleteFrame: (id: number) => del<unknown>(`/narrative-frames/${id}`),
  suggestFrames: () => post<{ suggestions: NarrativeFrame[] }>('/narrative-frames/suggest'),
  rematchFrames: () => post<unknown>('/narrative-frames/rematch'),
  frameTimeseries: (id: number, days = 30) =>
    get<{ series: TimeseriesPoint[] }>(`/frames/${id}/timeseries?days=${days}`)
      .then(r => r.series),
  shareOfVoice: (id: number) =>
    get<{ candidate: number; opponent: number; neutral: number }>(`/frames/${id}/share-of-voice`),

  // Recent relevant articles (Dashboard right rail)
  recentArticles: (limit = 10) =>
    get<{ items: SourceItem[] }>(`/articles/recent?limit=${limit}`).then(r => r.items),

  // Review queue
  reviewQueue: () => get<ReviewQueueItem[]>('/review-queue'),
  reviewQueueCount: () => get<{ count: number }>('/review-queue/count'),
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

  // Analytics — endpoints wrap their payload in {spikes|terms|entities: [...]}
  spikes: () =>
    get<{ spikes?: Array<Record<string, number | string>> }>('/analytics/spikes').then(r =>
      (r.spikes ?? []).map((s): Spike => ({
        frame_id: s.frame_id as number,
        frame_name: s.frame_name as string,
        reach_24h: (s.count_24h ?? s.reach_24h ?? 0) as number,
        avg_daily_reach: (s.daily_avg_7d ?? s.avg_daily_reach ?? 0) as number,
        ratio: (s.ratio ?? 0) as number,
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
}
