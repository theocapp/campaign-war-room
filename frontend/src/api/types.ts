export interface CampaignProfile {
  id: number
  candidate_name: string
  party: string | null
  race: string | null
  district: string | null
  office: string | null
  location: string | null
  race_level: string | null
  election_type: string | null
  district_number: string | null
  neighborhood_keywords: string[] | null
  sparse_race_mode: boolean
  election_date: string | null
  election_date_inferred?: boolean
  campaign_message: string | null
  key_priorities: string[] | null
  relevance_keywords: string[] | null
  excluded_keywords: string[] | null
  geography_keywords: string[] | null
  created_at: string | null
  updated_at: string | null
}

export interface RaceCandidate {
  id: number
  race_id: number
  candidate_name: string
  party: string | null
  is_incumbent: boolean
  role: 'candidate' | 'opponent' | 'other' | string
  campaign_url: string | null
  notes: string | null
  created_at: string | null
  updated_at: string | null
}

export interface RaceDirectory {
  id: number
  race_key: string
  race_name: string
  race_level: 'federal' | 'state' | 'local' | 'other' | string
  office_name: string
  state: string
  district_label: string | null
  district_number: string | null
  election_type: 'general' | 'primary' | 'special' | 'runoff' | 'other' | string
  election_date: string | null
  geography_summary: string | null
  data_source: 'fec' | 'openstates' | 'manual_seed' | 'other' | string
  is_active: boolean
  created_at: string | null
  updated_at: string | null
  candidates: RaceCandidate[]
}

export interface CampaignInitializeStep {
  step: number
  label: string
  status: 'ok' | 'skipped' | 'error'
  detail: string
}

export interface CampaignInitializeResult {
  steps: CampaignInitializeStep[]
  monitors_created: number
  monitors_skipped: number
  sources_ingested: number
  narratives_refreshed: number
  message: string
  initialized_at: string
}

export interface RaceSelectResult {
  race: RaceDirectory
  campaign: CampaignProfile
  selected_candidate_name: string
  opponents_created: number
  opponents_updated: number
  message: string
  init_result?: CampaignInitializeResult
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
  snapshot: IssueSnapshot | null
}

export interface SourceSnapshot {
  what_happened: string
  why_it_matters: string
  geography_summary: string
  actors_summary: string
  action_signal: string
  evidence_summary: string
  key_claim_or_quote: string | null
}

export interface SourceItem {
  id: number
  title: string
  source_name: string | null
  source_url: string | null
  source_type: string
  source_owner_type: string
  source_owner_confidence: string
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
  race_relevance_score: number
  race_relevance_label: string
  relevance_reasons: string[]
  actionability_score: number
  actionability_label: string
  content_category: string
  geo_relevance: string
  candidate_mentioned: boolean
  opponent_mentioned: boolean
  district_mentioned: boolean
  priority_issue_mentioned: boolean
  archived_as_irrelevant: boolean
  story_cluster_id: string | null
  duplicate_of_source_id: number | null
  extraction_quality_score: number
  extraction_quality_label: 'poor' | 'mixed' | 'good'
  extraction_quality_reasons: string[]
  issue_link_strength: number | null
  issue_link_reasons: string[]
  snapshot: SourceSnapshot | null
}

export interface SourceItemDetail extends SourceItem {
  raw_text: string | null
  related_issues: Issue[]
}

export interface IssueSnapshot {
  issue_snapshot: string
  why_it_matters_now: string
  top_geographies: string[]
  top_actors: string[]
  top_distinct_developments: string[]
  messaging_readiness: string
  evidence_strength: string
}

export interface ManualCapture {
  id: number
  source_item_id: number
  title: string
  source_name: string | null
  source_type: string
  source_url: string | null
  capture_type: string
  raw_text: string
  geography_tags: string[]
  issue_tags: string[]
  candidate_related: boolean
  opponent_related: boolean
  notes: string | null
  created_at: string
  source_item: SourceItem | null
}

export interface ManualCaptureResult {
  capture: ManualCapture
  source_item: SourceItem
  related_issues: Issue[]
  message: string
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

export interface SourceCoverageDiagnostic {
  source_coverage_strength: 'weak' | 'moderate' | 'strong'
  manual_source_dependence: 'low' | 'medium' | 'high'
  geography_coverage_gaps: string[]
  issue_coverage_gaps: string[]
  race_coverage_mode: string
  reasons: string[]
}

export interface DashboardRaceHeader {
  candidate_name: string
  race: string
  office: string | null
  district: string | null
  election_type: string | null
  opponents: string[]
  race_mode: string
  source_coverage_strength: string
}

export interface DashboardAttentionCard {
  card_type: string
  priority: 'urgent' | 'high' | 'medium'
  title: string
  explanation: string
  action_label: string
  destination: string | null
}

export interface DashboardReviewQueueItem {
  source_id: number
  title: string
  issue: string | null
  relevance_label: string
  relevance_score: number
  actionability_label: string
  source_type: string
  geography: string | null
}

export interface DashboardReviewSnapshot {
  review_worthy_count: number
  respond_now_count: number
  top_items: DashboardReviewQueueItem[]
}

export interface DashboardPriorityIssue {
  issue_id: number
  name: string
  distinct_development_count: number
  trend: 'rising' | 'stable' | 'falling'
  evidence_confidence: string
  geography_concentration: string | null
  why_now: string
}

export interface DashboardOpponentWatch {
  repeated_themes: string[]
  latest_attack: string | null
  source_item_id: number | null
  source_title: string | null
  source_name: string | null
  source_url: string | null
  source_created_at: string | null
  response_status: string
  summary: string
}

export interface DashboardReadiness {
  coverage_strength: string
  manual_source_dependence: string
  geography_gaps: string[]
  issue_gaps: string[]
  ready_to_message_issues: string[]
  thin_evidence_issues: string[]
  sparse_race_note: string | null
  reasons: string[]
}

export interface DashboardDevelopment {
  cluster_id: string
  title: string
  issue: string | null
  why_it_matters: string
  source_count: number
  recency: string | null
  source_id: number
}

export interface DashboardNarrativeCard {
  narrative_id: number
  short_label: string
  canonical_text: string
  narrative_type: string
  owner_type: string
  direction: string
  status: 'emerging' | 'rising' | 'stable' | 'fading'
  traction_score: number
  evidence_strength: 'weak' | 'moderate' | 'strong'
  response_status: string
  owner_confidence: 'low' | 'medium' | 'high'
  attribution_type: string
  target_confidence: 'low' | 'medium' | 'high'
  source_count: number
  source_cluster_count: number
  messenger_diversity_count: number
  geography_count: number
  why_it_matters: string
  source_item_id: number | null
  source_title: string | null
  source_url: string | null
  // Narrative-brief additions
  what_changed?: string
  why_it_matters_now?: string
  spread_summary?: string
  risk_or_opportunity?: string
  action?: 'ignore' | 'monitor' | 'respond' | 'amplify' | string
  confidence?: string
  evidence_summary?: string
  top_supporting_sources?: SourceItem[]
  verify_links?: string[]
  change_summary?: string | null
  new_messenger_types?: string[] | null
  new_source_clusters_count?: number | null
  new_geographies?: string[] | null
  escaped_owned_recently?: boolean | null
  momentum_shift?: 'stronger' | 'weaker' | 'unchanged' | string | null
  recent_window_summary?: string | null
}

export interface NarrativeComparisonItem {
  narrative_id: number
  short_label: string
  owner_type: string
  narrative_type: string
  status: string
  traction_score: number
  evidence_strength: string
  source_cluster_count: number
  messenger_diversity_count: number
  geography_count: number
  outside_owned_channels: boolean
  practical_read: string
}

export interface NarrativeComparisonOut {
  top_opponent_narratives: NarrativeComparisonItem[]
  top_candidate_narratives: NarrativeComparisonItem[]
  candidate_owned_only: NarrativeComparisonItem[]
  candidate_broader_spread: NarrativeComparisonItem[]
  opponent_rising_faster: NarrativeComparisonItem[]
  ready_to_amplify: NarrativeComparisonItem[]
  needs_response: NarrativeComparisonItem[]
  summary: string
  generated_at: string
}

export interface NarrativeMention {
  id: number
  narrative_id: number
  source_item_id: number | null
  opponent_activity_id: number | null
  source_cluster_id: string | null
  matched_text: string | null
  mention_role: string
  confidence_score: number
  owner_confidence: string
  attribution_type: string
  target_confidence: string
  candidate_narrative_id: number | null
  created_at: string
  source_item: SourceItem | null
}

export interface NarrativeDetail {
  id: number
  canonical_text: string
  short_label: string
  narrative_type: string
  owner_type: string
  direction: string
  status: string
  first_seen_at: string | null
  last_seen_at: string | null
  source_cluster_count: number
  source_count: number
  messenger_diversity_count: number
  geography_count: number
  traction_score: number
  evidence_strength: string
  response_status: string
  owner_confidence: string
  attribution_type: string
  target_confidence: string
  notes: string | null
  what_changed: string | null
  why_it_matters: string | null
  spread_summary: string | null
  risk_or_opportunity: string | null
  action: string | null
  momentum_shift: string | null
  recent_window_summary: string | null
  mentions: NarrativeMention[]
}

export interface DashboardNarrativeComparison {
  top_opponent: NarrativeComparisonItem[]
  top_candidate: NarrativeComparisonItem[]
  candidate_owned_only: NarrativeComparisonItem[]
  candidate_broader_spread: NarrativeComparisonItem[]
  needs_response: NarrativeComparisonItem[]
  ready_to_amplify: NarrativeComparisonItem[]
  summary: string
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
  source_coverage: SourceCoverageDiagnostic
  race_header: DashboardRaceHeader | null
  attention_now: DashboardAttentionCard[]
  review_snapshot: DashboardReviewSnapshot | null
  priority_issues: DashboardPriorityIssue[]
  opponent_watch: DashboardOpponentWatch | null
  narrative_briefing: DashboardNarrativeCard[]
  narrative_comparison: DashboardNarrativeComparison | null
  coverage_readiness: DashboardReadiness | null
  recent_developments: DashboardDevelopment[]
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

export interface CandidateMessageLibrary {
  id: number
  campaign_config_id: number | null
  core_message: string | null
  short_bio_frame: string | null
  tone_guidance: string | null
  created_at: string | null
  updated_at: string | null
}

export interface CandidateNarrative {
  id: number
  library_id: number
  short_label: string
  canonical_text: string
  narrative_kind: 'self_definition' | 'issue_frame' | 'contrast' | 'rebuttal'
  issue_name: string | null
  preferred_phrases: string[]
  avoid_phrases: string[]
  must_mention_points: string[]
  red_lines: string[]
  priority: number
  active: boolean
  created_at: string | null
  updated_at: string | null
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

export interface SourceMonitor {
  id: number
  name: string
  monitor_type: 'rss' | 'search_query' | 'manual' | 'webpage'
  query: string | null
  url: string | null
  source_type: string
  category: string | null
  active: boolean
  required_terms: string[] | null
  excluded_terms: string[] | null
  relevance_hint: string | null
  last_checked_at: string | null
  created_at: string
  updated_at: string | null
}

export interface GenerateMonitorsResult {
  suggestions: Omit<SourceMonitor, 'id' | 'last_checked_at' | 'created_at' | 'updated_at'>[]
  created_count: number
  skipped_duplicates: number
  monitors: SourceMonitor[]
}

export interface MonitorIngestItem {
  title: string | null
  url: string | null
  status: string
  source_id: number | null
  reason: string | null
  relevance_label: string | null
  relevance_score: number | null
  archived_as_irrelevant: boolean | null
}

export interface MonitorIngestResult {
  monitor_id: number
  monitor_name: string
  monitor_type: string
  provider: string | null
  message: string | null
  added_count: number
  skipped_count: number
  failed_count: number
  results: MonitorIngestItem[]
}

export interface IngestSearchMonitorsResult {
  monitor_count: number
  added_count: number
  skipped_count: number
  failed_count: number
  results: MonitorIngestResult[]
}
