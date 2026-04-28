export interface CampaignProfile {
  id: number
  candidate_name: string
  party: string | null
  race: string | null
  district: string | null
  office: string | null
  location: string | null
  election_date: string | null
  campaign_message: string | null
  key_priorities: string[] | null
  created_at: string | null
  updated_at: string | null
}

export interface Issue {
  id: number
  name: string
  summary: string | null
  urgency: 'low' | 'medium' | 'high'
  mention_count: number
  trend: 'rising' | 'stable' | 'falling'
  last_seen_at: string | null
}

export interface IssueDetail extends Issue {
  recent_sources: SourceItem[]
}

export interface SourceItem {
  id: number
  title: string
  source_name: string | null
  source_url: string | null
  source_type: string
  summary: string | null
  published_at: string | null
  created_at: string
  urgency: 'low' | 'medium' | 'high'
  credibility_note: string | null
  reviewed: boolean
  dismissed: boolean
  priority_score: number
  review_note: string | null
  evidence_score: number
  credibility_score: number
}

export interface SourceItemDetail extends SourceItem {
  raw_text: string | null
  related_issues: Issue[]
}

export interface Opponent {
  id: number
  name: string
  office: string | null
  party: string | null
  notes: string | null
  created_at: string
}

export interface OpponentActivity {
  id: number
  opponent_id: number
  claim: string | null
  attack: string | null
  promise: string | null
  contradiction_note: string | null
  repeated_theme: string | null
  created_at: string
  source_item: SourceItem | null
}

export interface SuggestedAction {
  priority: 'urgent' | 'high' | 'medium'
  action: string
  rationale: string
}

export interface RiskWarning {
  source_id: number
  source_title: string
  warning: string
  urgency: string
}

export interface DashboardData {
  candidate_name: string
  race: string
  top_issues: Issue[]
  recent_sources: SourceItem[]
  opponent_activity: OpponentActivity[]
  suggested_actions: SuggestedAction[]
  risk_warnings: RiskWarning[]
  canvassing_summary: string | null
  review_queue_count: number
  last_updated: string
}

export interface PrecinctInsight {
  precinct: string
  contact_count: number
  top_issues: string[]
  dominant_sentiment: string
  summary: string
}

export interface CanvassingInsights {
  total_contacts: number
  precincts: PrecinctInsight[]
  overall_top_issues: string[]
  sentiment_breakdown: Record<string, number>
}

export interface TalkingPointResponse {
  issue: string
  short_answer: string
  long_answer: string
  debate_answer: string
  social_post: string
  risk_warning: string | null
  evidence_notes: string
  source_titles_used: string[]
  source_urls_used: string[]
}

export interface RssFeed {
  id: number
  name: string
  url: string
  source_type: string
  active: boolean
  last_fetched_at: string | null
  created_at: string
}

export interface RssFeedIngestResult {
  feed_id: number
  added_count: number
  skipped_count: number
  error_count: number
  added_items: SourceItem[]
}

export interface SetupChecklistItem {
  id: string
  label: string
  complete: boolean
  helper_text: string
  action_path: string
}

export interface SetupStatus {
  complete: boolean
  items: SetupChecklistItem[]
}

export interface ReviewQueueItem extends SourceItem {
  related_issue_names: string[]
  related_issue_ids: number[]
  opponent_attack_count: number
}

export interface DashboardChange {
  type: string
  title: string
  detail: string | null
  urgency: string | null
  created_at: string
}

export interface DashboardChanges {
  since_hours: number
  changes: DashboardChange[]
  new_source_count: number
  new_attack_count: number
}

export interface ResetWorkspaceRequest {
  confirm: string
  candidate_name: string
  office: string
  district?: string
  party?: string
  location?: string
  election_date?: string
  campaign_message?: string
  key_priorities?: string[]
  preserve_feeds?: boolean
}

export interface ResetWorkspaceResult {
  cleared_sources: number
  cleared_issues: number
  cleared_opponents: number
  cleared_canvassing: number
  cleared_talking_points: number
  cleared_feeds: number
  preserved_feeds: number
  candidate_name: string
}

export interface SourcePackItem {
  id: number
  source_pack_id: number
  name: string
  category: string | null
  source_type: string
  url: string | null
  setup_note: string | null
  active: boolean
}

export interface SourcePack {
  id: number
  name: string
  description: string | null
  race_level: string | null
  geography: string | null
  created_at: string
  items: SourcePackItem[]
}

export interface SourcePackApplyResult {
  pack_name: string
  feeds_created: number
  reminders_created: number
  skipped_duplicate_feeds: number
}

export interface ManualSourceReminder {
  id: number
  name: string
  category: string | null
  source_type: string
  url: string | null
  setup_note: string | null
  active: boolean
  last_checked_at: string | null
  created_at: string
}

export interface RaceImportResult {
  campaign_updated: boolean
  opponents_created: number
  feeds_created: number
  reminders_created: number
  skipped: number
  errors: string[]
}

export interface SourceTemplate {
  id: string
  name: string
  category: string
  description: string
  example_url: string | null
  url_pattern: string | null
  source_type: string
  setup_note: string | null
}

export interface GeneratedTalkingPoint {
  id: number
  issue_name: string
  tone: string
  short_answer: string
  long_answer: string
  debate_answer: string
  social_post: string
  risk_warning: string | null
  evidence_notes: string
  source_titles_used: string[]
  source_urls_used: string[]
  created_at: string
}
