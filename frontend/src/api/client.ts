import type {
  CampaignProfile, DashboardData, DashboardChanges, DashboardNarrativeCard, Issue, IssueDetail,
  SourceItem, SourceItemDetail, SourceTemplate,
  Opponent, OpponentActivity, CanvassingInsights, TalkingPointResponse,
  RssFeed, RssFeedIngestResult, SetupStatus, ReviewQueueItem, GeneratedTalkingPoint,
  ResetWorkspaceRequest, ResetWorkspaceResult,
  RaceDirectory, RaceSelectResult,
  SourcePack, SourcePackApplyResult,
  ManualSourceReminder, RaceImportResult, SourceMonitor, GenerateMonitorsResult,
  MonitorIngestResult, IngestSearchMonitorsResult,
  ManualCapture, ManualCaptureResult,
  CandidateMessageLibrary, CandidateNarrative,
  NarrativeDetail, NarrativeComparisonOut,
} from './types'

const BASE = '/api'

// Global toast hook — set by ToastProvider after mount so the API client can
// show notifications without needing React context at the call site.
type ToastFn = (message: string, type?: 'error' | 'warning' | 'info') => void
let _toastFn: ToastFn | null = null
export function registerToast(fn: ToastFn) { _toastFn = fn }
export function apiToast(message: string, type: 'error' | 'warning' | 'info' = 'error') {
  _toastFn?.(message, type)
}

async function extractError(res: Response): Promise<string> {
  try {
    const data = await res.clone().json()
    if (typeof data?.detail === 'string') return data.detail
    if (Array.isArray(data?.detail)) return data.detail.map((d: { msg?: string }) => d.msg ?? String(d)).join('; ')
  } catch { /* not JSON */ }
  return `${res.status} ${res.statusText}`
}

async function throwIfNotOk(res: Response): Promise<void> {
  if (!res.ok) {
    const msg = await extractError(res)
    throw new Error(msg)
  }
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`)
  await throwIfNotOk(res)
  return res.json()
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  await throwIfNotOk(res)
  return res.json()
}

async function put<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  await throwIfNotOk(res)
  return res.json()
}

async function del(path: string): Promise<void> {
  const res = await fetch(`${BASE}${path}`, { method: 'DELETE' })
  await throwIfNotOk(res)
}

export const api = {
  // Campaign
  getCampaign: () => get<CampaignProfile>('/campaign'),
  updateCampaign: (body: Partial<CampaignProfile>) => put<CampaignProfile>('/campaign', body),

  // Race Directory
  getRaces: (params?: { q?: string; race_level?: string; state?: string; limit?: number }) => {
    const qs = new URLSearchParams()
    if (params?.q) qs.set('q', params.q)
    if (params?.race_level) qs.set('race_level', params.race_level)
    if (params?.state) qs.set('state', params.state)
    if (params?.limit) qs.set('limit', String(params.limit))
    return get<RaceDirectory[]>(`/races${qs.toString() ? '?' + qs.toString() : ''}`)
  },
  searchRaces: (q: string) => get<RaceDirectory[]>(`/races/search?q=${encodeURIComponent(q)}`),
  getRace: (id: number) => get<RaceDirectory>(`/races/${id}`),
  selectRace: (id: number, body?: { candidate_id?: number; candidate_name?: string }) =>
    post<RaceSelectResult>(`/races/${id}/select`, body || {}),

  // Dashboard
  getDashboard: () => get<DashboardData>('/dashboard'),
  getDashboardChanges: (hours = 24) => get<DashboardChanges>(`/dashboard/changes?hours=${hours}`),

  // Candidate message library
  getMessageLibrary: () => get<CandidateMessageLibrary>('/message-library'),
  updateMessageLibrary: (body: Partial<CandidateMessageLibrary>) =>
    put<CandidateMessageLibrary>('/message-library', body),
  getCandidateNarratives: () => get<CandidateNarrative[]>('/message-library/narratives'),
  createCandidateNarrative: (body: Partial<CandidateNarrative>) =>
    post<CandidateNarrative>('/message-library/narratives', body),
  updateCandidateNarrative: (id: number, body: Partial<CandidateNarrative>) =>
    put<CandidateNarrative>(`/message-library/narratives/${id}`, body),
  deleteCandidateNarrative: (id: number) => del(`/message-library/narratives/${id}`),

  // Setup checklist
  getSetupStatus: () => get<SetupStatus>('/setup/status'),

  // Issues
  getIssues: () => get<Issue[]>('/issues'),
  getIssue: (id: number) => get<IssueDetail>(`/issues/${id}`),

  // Sources
  getSources: (params?: { source_type?: string; urgency?: string; source_filter?: string; limit?: number }) => {
    const qs = new URLSearchParams(params as Record<string, string>).toString()
    return get<SourceItem[]>(`/sources${qs ? '?' + qs : ''}`)
  },
  getSource: (id: number) => get<SourceItemDetail>(`/sources/${id}`),
  addTextSource: (body: {
    title: string; raw_text: string; source_name?: string
    source_type?: string; source_url?: string
  }) => post<SourceItem>('/sources/text', body),
  addRssOnce: (url: string, label?: string) =>
    post<SourceItem[]>('/sources/rss', { url, label }),
  addUrlSource: (url: string, source_type?: string) =>
    post<SourceItem>('/sources/url', { url, source_type: source_type || 'news' }),
  getManualCaptures: () => get<ManualCapture[]>('/manual-captures'),
  createManualCapture: (body: {
    title: string
    raw_text: string
    source_name?: string
    source_type?: string
    source_url?: string
    capture_type?: string
    geography_tags?: string[]
    issue_tags?: string[]
    candidate_related?: boolean
    opponent_related?: boolean
    notes?: string
  }) => post<ManualCaptureResult>('/manual-captures', body),

  // RSS Feeds (persistent)
  getRssFeeds: () => get<RssFeed[]>('/rss-feeds'),
  createRssFeed: (body: { name: string; url: string; source_type?: string }) =>
    post<RssFeed>('/rss-feeds', body),
  updateRssFeed: (id: number, body: { name?: string; active?: boolean; source_type?: string }) =>
    put<RssFeed>(`/rss-feeds/${id}`, body),
  deleteRssFeed: (id: number) => del(`/rss-feeds/${id}`),
  ingestRssFeed: (id: number) => post<RssFeedIngestResult>(`/rss-feeds/${id}/ingest`, {}),
  ingestAllFeeds: () =>
    post<{ feeds_processed: number; results: unknown[] }>('/rss-feeds/ingest-all', {}),

  // Review Queue
  getReviewQueue: () => get<ReviewQueueItem[]>('/review-queue'),
  reviewSource: (id: number, review_note?: string) =>
    post<ReviewQueueItem>(`/review-queue/${id}/review`, { review_note: review_note ?? null }),
  dismissSource: (id: number, review_note?: string) =>
    post<ReviewQueueItem>(`/review-queue/${id}/dismiss`, { review_note: review_note ?? null }),
  setSourcePriority: (id: number, priority_score: number) =>
    post<ReviewQueueItem>(`/review-queue/${id}/priority`, { priority_score }),
  bulkReviewSources: (source_ids: number[], review_note?: string) =>
    post<{ updated: number }>('/review-queue/bulk/review', { source_ids, review_note: review_note ?? null }),
  bulkDismissSources: (source_ids: number[], review_note?: string) =>
    post<{ updated: number }>('/review-queue/bulk/dismiss', { source_ids, review_note: review_note ?? null }),

  // Source templates
  getSourceTemplates: () => get<SourceTemplate[]>('/source-templates'),

  // Admin / workspace
  resetWorkspace: (body: ResetWorkspaceRequest) =>
    post<ResetWorkspaceResult>('/admin/reset-workspace', body),

  // Source packs
  getSourcePacks: () => get<SourcePack[]>('/source-packs'),
  applySourcePack: (id: number) => post<SourcePackApplyResult>(`/source-packs/${id}/apply`, {}),

  // Source reminders
  getSourceReminders: () => get<ManualSourceReminder[]>('/source-reminders'),
  createSourceReminder: (body: {
    name: string; category?: string; source_type?: string; url?: string; setup_note?: string
  }) => post<ManualSourceReminder>('/source-reminders', body),
  updateSourceReminder: (id: number, body: {
    name?: string; category?: string; source_type?: string; url?: string; setup_note?: string; active?: boolean
  }) => put<ManualSourceReminder>(`/source-reminders/${id}`, body),
  deleteSourceReminder: (id: number) => del(`/source-reminders/${id}`),
  markReminderChecked: (id: number) =>
    post<ManualSourceReminder>(`/source-reminders/${id}/mark-checked`, {}),

  // Monitors
  getMonitors: (monitor_type?: string) =>
    get<SourceMonitor[]>(`/monitors${monitor_type && monitor_type !== 'all' ? `?monitor_type=${monitor_type}` : ''}`),
  createMonitor: (body: Partial<SourceMonitor>) => post<SourceMonitor>('/monitors', body),
  updateMonitor: (id: number, body: Partial<SourceMonitor>) => put<SourceMonitor>(`/monitors/${id}`, body),
  deleteMonitor: (id: number) => del(`/monitors/${id}`),
  generateMonitors: (body: { apply?: boolean; replace_existing?: boolean }) =>
    post<GenerateMonitorsResult>('/monitors/generate', body),
  markMonitorChecked: (id: number) => post<SourceMonitor>(`/monitors/${id}/mark-checked`, {}),
  ingestMonitor: (id: number) => post<MonitorIngestResult>(`/monitors/${id}/ingest`, {}),
  ingestSearchMonitors: () => post<IngestSearchMonitorsResult>('/monitors/ingest-search', {}),

  // Race CSV import
  importRaceCSV: async (file: File): Promise<RaceImportResult> => {
    const form = new FormData()
    form.append('file', file)
    const res = await fetch(`${BASE}/race/import-csv`, { method: 'POST', body: form })
    await throwIfNotOk(res)
    return res.json()
  },

  // Opponents
  getOpponents: () => get<Opponent[]>('/opponents'),
  getOpponentActivity: (id: number) => get<OpponentActivity[]>(`/opponents/${id}/activity`),
  addOpponent: (body: { name: string; office?: string; party?: string; notes?: string }) =>
    post<Opponent>('/opponents', body),

  // Canvassing
  getCanvassingInsights: () => get<CanvassingInsights>('/canvassing/insights'),
  uploadCanvassing: async (file: File): Promise<{ imported: number; message: string }> => {
    const form = new FormData()
    form.append('file', file)
    const res = await fetch(`${BASE}/canvassing/upload`, { method: 'POST', body: form })
    await throwIfNotOk(res)
    return res.json()
  },

  // Narratives
  getNarrativeBriefs: (limit = 50) => get<DashboardNarrativeCard[]>(`/narratives/briefs?limit=${limit}`),
  getNarrativeDetail: (id: number) => get<NarrativeDetail>(`/narratives/${id}`),
  getNarrativeComparison: () => get<NarrativeComparisonOut>('/narratives/compare'),

  // Talking Points
  generateTalkingPoints: (body: {
    issue_id?: number; custom_issue_text?: string; tone: string; output_format: string
  }) => post<TalkingPointResponse>('/talking-points', body),
  getTalkingPointsHistory: (limit = 20) =>
    get<GeneratedTalkingPoint[]>(`/talking-points/history?limit=${limit}`),
  getTalkingPointById: (id: number) =>
    get<GeneratedTalkingPoint>(`/talking-points/history/${id}`),
}
