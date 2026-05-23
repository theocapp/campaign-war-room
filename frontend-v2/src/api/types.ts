export type OwnerType = 'candidate' | 'opponent' | 'media'
export type Stage = 'emerging' | 'spreading' | 'mainstream' | 'fading' | 'dormant'
export type Trend = 'up' | 'flat' | 'down'
export type RelevanceLabel = 'critical' | 'high' | 'medium' | 'low' | 'irrelevant'
export type Sentiment = 'positive' | 'negative' | 'neutral' | 'mixed'
export type MonitorType = 'rss' | 'search_query' | 'manual' | 'webpage'
export type ActivityType = 'attack' | 'claim' | 'promise'

export interface NarrativeKeyArticle {
  role: string
  id: number
  title: string
  summary?: string
  source_name?: string
  source_url?: string
  published_at?: string
}

export interface NarrativeFrame {
  id: number
  name: string
  description: string
  owner_type: OwnerType
  source: 'human' | 'llm'
  stage: Stage
  created_at: string
  first_seen_at?: string
  last_seen_at?: string
  mention_count?: number        // legacy fallback
  mentions_total: number
  mentions_this_week: number
  mentions_last_week: number
  articles_last_30d?: number
  activity_30d?: Array<{ date: string; count: number }>
  unique_outlets_this_week: number
  unique_outlets_last_week: number
  days_active_last_7: number
  reach_this_week: number
  reach_last_week: number
  reach_total: number
  outlet_tiers?: {
    national: number
    regional: number
    local: number
    blog: number
    social: number
  }
  key_articles?: NarrativeKeyArticle[]
}

export interface DetailArticle {
  id: number
  title: string
  summary?: string
  source_name?: string
  source_url?: string
  outlet_name?: string
  outlet_type?: string
  outlet_authority?: number
  published_at?: string
  race_relevance_score?: number
  sentiment?: Sentiment
}

export interface DetailQuote {
  text: string
  source_name?: string
  source_url?: string
  outlet_name?: string
  published_at?: string
}

export interface ActivityPoint {
  date: string
  count: number     // total, kept for back-compat with simple sparklines
  total: number
  national: number
  regional: number
  local: number
  blog: number
  social: number
  unknown: number
}

export interface NarrativeFrameDetail {
  id: number
  name: string
  description?: string
  owner_type: OwnerType
  source: 'human' | 'llm'
  created_at?: string
  first_seen_at?: string
  last_seen_at?: string
  articles_total: number
  articles_this_week: number
  outlet_tiers: {
    national: number
    regional: number
    local: number
    blog: number
    social: number
  }
  activity: ActivityPoint[]
  quotes: DetailQuote[]
  articles: DetailArticle[]
}

export interface FrameVariantTimeline {
  id: number
  name: string
  first_seen_at?: string
  last_seen_at?: string
  mention_count: number
  daily_counts: Array<{ date: string; count: number }>
}

export interface NarrativeFrameTimeline {
  frame: {
    id: number
    name: string
    owner_type: OwnerType
    description?: string
    stage?: string | null
    momentum_signal?: string | null
  }
  window_days: number
  variants: FrameVariantTimeline[]
  totals_by_day: Array<{ date: string; count: number }>
  unclustered_mention_count: number
}

export interface TimeseriesPoint {
  date: string
  count: number
  weighted_reach: number
}

export interface SourceItem {
  id: number
  title: string
  source_name?: string
  source_url?: string
  source_type?: string
  published_at?: string
  created_at: string
  summary?: string
  race_relevance_score?: number
  race_relevance_label?: RelevanceLabel
  actionability_score?: number
  actionability_label?: string
  priority_score?: number
  sentiment?: Sentiment
  reviewed: boolean
  dismissed: boolean
  archived_as_irrelevant?: boolean
}

export interface ReviewQueueItem extends SourceItem {
  related_issues: Array<{ id: number; name: string }>
  opponent_attack_count: number
}

export interface Opponent {
  id: number
  name: string
  office?: string
  party?: string
  fec_candidate_id?: string
  created_at: string
}

export interface OpponentActivity {
  id: number
  opponent_id: number
  opponent_name?: string
  claim?: string
  attack?: string
  promise?: string
  contradiction_note?: string
  repeated_theme?: string
  first_seen_at: string
  last_seen_at: string
}

export interface SourceMonitor {
  id: number
  name: string
  monitor_type: MonitorType
  query?: string
  url?: string
  active: boolean
  required_terms?: string[]
  excluded_terms?: string[]
  created_at: string
}

export interface CampaignConfig {
  id?: number
  candidate_name: string
  office?: string
  district?: string
  state?: string
  election_date?: string
  campaign_message?: string
  keywords?: string[]
  priorities?: string[]
}

export interface IngestStatus {
  crawler_last_run?: string
  reddit_last_run?: string
}

export interface Spike {
  frame_id: number
  frame_name: string
  reach_24h: number
  avg_daily_reach: number
  ratio: number
}

export interface TrendDataPoint {
  date: string
  interest: number
}

export interface TrendSeries {
  term: string
  data: TrendDataPoint[]
}

export interface ToneDataPoint {
  date: string
  avg_tone: number
  article_count: number
}

export interface ToneSeries {
  query_label: string
  entity_type: 'candidate' | 'opponent'
  data: ToneDataPoint[]
}

// Morning briefing — backend may vary; we type loosely
export interface MorningBriefingNarrativeCard {
  narrative_id?: number
  frame_id?: number
  id?: number
  frame_name?: string
  short_label?: string
  name?: string
  canonical_text?: string
  owner_type?: OwnerType
  direction?: Trend
  trend?: Trend
  status?: string
  traction_score?: number
  mention_count_24h?: number
  this_week?: number
  last_week?: number
  source_count?: number
  why_it_matters?: string
}

export interface MorningBriefingDevelopment {
  cluster_id?: string
  title: string
  source_count?: number
  why_it_matters?: string
  issue?: string
  recency?: string
}

export interface MorningBriefing {
  candidate_name?: string
  race_situation_memo?: string
  narrative_pulse?: MorningBriefingNarrativeCard[]
  needs_response?: ReviewQueueItem[]
  new_developments?: MorningBriefingDevelopment[]
  review_queue_count?: number
  top_issues?: Array<{ id: number; name: string; urgency: string }>
  suggested_actions?: string[]
  risk_warnings?: string[]
}

export interface SetupStatus {
  campaign_profile: boolean
  opponent_added: boolean
  source_added: boolean
  narrative_frame_added: boolean
}
