import type {
  CampaignProfile, DashboardData, DashboardChanges, Issue, IssueDetail,
  SourceItem, SourceItemDetail, SourceTemplate,
  Opponent, OpponentActivity, CanvassingInsights, TalkingPointResponse,
  RssFeed, RssFeedIngestResult, SetupStatus, ReviewQueueItem, GeneratedTalkingPoint,
  ResetWorkspaceRequest, ResetWorkspaceResult,
  SourcePack, SourcePackApplyResult,
  ManualSourceReminder, RaceImportResult,
} from './types'

const BASE = '/api'

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`)
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

async function put<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

async function del(path: string): Promise<void> {
  const res = await fetch(`${BASE}${path}`, { method: 'DELETE' })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
}

export const api = {
  // Campaign
  getCampaign: () => get<CampaignProfile>('/campaign'),
  updateCampaign: (body: Partial<CampaignProfile>) => put<CampaignProfile>('/campaign', body),

  // Dashboard
  getDashboard: () => get<DashboardData>('/dashboard'),
  getDashboardChanges: (hours = 24) => get<DashboardChanges>(`/dashboard/changes?hours=${hours}`),

  // Setup checklist
  getSetupStatus: () => get<SetupStatus>('/setup/status'),

  // Issues
  getIssues: () => get<Issue[]>('/issues'),
  getIssue: (id: number) => get<IssueDetail>(`/issues/${id}`),

  // Sources
  getSources: (params?: { source_type?: string; urgency?: string; limit?: number }) => {
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

  // Race CSV import
  importRaceCSV: async (file: File): Promise<RaceImportResult> => {
    const form = new FormData()
    form.append('file', file)
    const res = await fetch(`${BASE}/race/import-csv`, { method: 'POST', body: form })
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
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
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
    return res.json()
  },

  // Talking Points
  generateTalkingPoints: (body: {
    issue_id?: number; custom_issue_text?: string; tone: string; output_format: string
  }) => post<TalkingPointResponse>('/talking-points', body),
  getTalkingPointsHistory: (limit = 20) =>
    get<GeneratedTalkingPoint[]>(`/talking-points/history?limit=${limit}`),
  getTalkingPointById: (id: number) =>
    get<GeneratedTalkingPoint>(`/talking-points/history/${id}`),
}
