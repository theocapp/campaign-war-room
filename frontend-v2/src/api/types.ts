export type OwnerType = 'candidate' | 'opponent' | 'media'
// Must match every stage the backend's _narrative_stage() can return — see
// services/narrative_frames.py:_narrative_stage. Missing `resurfacing` and
// `active` here caused TypeScript to silently allow but mis-typecheck the
// Dashboard's stage filter; bug-hunt agent caught two unreachable comparisons.
export type Stage = 'emerging' | 'spreading' | 'resurfacing' | 'active' | 'mainstream' | 'fading' | 'dormant'
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
  // V13.21 — subject_type indicates who the narrative is ABOUT (vs
  // owner_type which is who BENEFITS). Frontend uses both to render
  // the 4-quadrant color scheme. Optional because legacy endpoints
  // may not include it.
  subject_type?: OwnerType
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
  // 30-day daily activity with outlet-tier breakdown. `count` mirrors
  // `total` for legacy consumers; tier fields power the cross-tier
  // transition detectors in lib/featuredFrame.ts.
  activity_30d?: Array<{
    date: string
    count: number
    total?: number
    national?: number
    regional?: number
    local?: number
    blog?: number
    social?: number
    unknown?: number
  }>
  unique_outlets_this_week: number
  unique_outlets_last_week: number
  days_active_last_7: number
  // Saturation signal — how many of the last 7 calendar days this frame
  // has been featured on the dashboard. Drives the saturation penalty
  // in lib/featuredFrame.ts so the panel doesn't become wallpaper.
  // Optional because legacy responses may not include it.
  days_featured_last_7?: number
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
  // Momentum classifier output (see backend/app/services/frame_momentum.py).
  // One of "viral" | "amplified" | "missing_coverage" | "elite_only" |
  // "stable" | "no_trend_signal", or null/undefined when the frame is below
  // MIN_ACTIVE_ARTICLES.
  momentum_signal?: string | null
  // Classifier inputs (parsed JSON). Shape varies per signal — see the
  // services/frame_momentum.py docstring. Used for tooltip text.
  momentum_data?: Record<string, unknown> | null
  // Strategic interpretation of the (momentum_signal, owner_type) pair.
  // posture in {amplify, offensive, defensive, monitor, ignore}.
  // Action is null for "ignore"; urgency in {high, medium, low}.
  // See backend/app/services/strategic_lens.py for the full matrix.
  strategic_lens?: {
    posture: 'amplify' | 'offensive' | 'defensive' | 'monitor' | 'ignore'
    action: string | null
    urgency: 'high' | 'medium' | 'low'
  } | null
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

// One cluster of candidate-frame staging rows that meets promotion
// thresholds. Returned by /api/narrative-frames/candidate-frames/pending.
// Surfaced in the UI as "the AI noticed these emerging narratives —
// promote any into tracked frames?"
export interface CandidateFrameCluster {
  suggested_name: string
  suggested_description: string
  owner_type_hint: 'candidate' | 'opponent' | 'media'
  // Inferred subject (who the narrative is ABOUT) from the cluster's
  // representative name. Combines with owner_type_hint to produce a
  // 4-quadrant label. Falls back to "media" when the heuristic can't
  // pin a side. Optional because the field was added later — older
  // cached responses may still arrive without it.
  subject_type_hint?: 'candidate' | 'opponent' | 'media'
  n_rows: number
  n_articles: number
  n_outlets: number
  outlet_names: string[]
  evidence_quotes: string[]
  candidate_frame_ids: number[]
  first_seen?: string | null
  last_seen?: string | null
}

// 2D narrative landscape data — projection of pending candidate_frames
// for visual cluster inspection. See `services/narrative_landscape.py`.
export interface NarrativeLandscapePoint {
  candidate_frame_id: number
  x: number
  y: number
  cluster_id: number          // -1 = noise singleton
  suggested_name: string
  evidence_quote: string
  owner_type_hint: 'candidate' | 'opponent' | 'media'
  source_item_id: number | null
  source_name: string | null
  source_title: string | null
  outlet_id: number | null
  outlet_name: string | null
  outlet_type: string | null
}

export interface NarrativeLandscapeCluster {
  cluster_id: number
  size: number
  representative_name: string
  owner_type_hint: 'candidate' | 'opponent' | 'media'
  // Inferred subject (who the narrative is ABOUT) from the representative
  // name — combines with owner_type_hint to produce a 4-quadrant label.
  // Optional for forward-compat with older deploys.
  subject_type_hint?: 'candidate' | 'opponent' | 'media'
  outlet_count: number
  outlet_tier_counts: {
    national: number
    regional: number
    local: number
    blog: number
    social: number
    other: number
  }
  outlet_names: string[]
}

export interface NarrativeLandscape {
  points: NarrativeLandscapePoint[]
  clusters: NarrativeLandscapeCluster[]
  computed_at: string
  n_total: number
  n_clustered: number
  n_noise: number
  error: string | null
}

// 2D landscape over ESTABLISHED (already-promoted) narrative frames.
// Companion to the candidate-frames landscape — see
// `services/narrative_landscape_established.py`. Each frame is its own
// point; no clustering (frames are already discrete). Position via UMAP
// over name + description embeddings = topical similarity.
export interface EstablishedLandscapeFrame {
  frame_id: number
  name: string
  description: string | null
  owner_type: 'candidate' | 'opponent' | 'media'
  x: number
  y: number
  mentions_total: number          // drives bubble SIZE in the UI
  mentions_this_week: number
  outlet_count: number
  outlet_tier_counts: {
    national: number
    regional: number
    local: number
    blog: number
    social: number
  }
  stage: string | null            // emerging/spreading/mainstream/fading/dormant
  momentum_signal: string | null  // viral/amplified/missing_coverage/elite_only/stable
}

// One named topic region — a HDBSCAN cluster over established frame
// positions, labeled by LLM (or user-edited). See backend
// `services/topic_regions.py`. Member frame IDs index into
// EstablishedLandscape.frames.
export interface TopicRegion {
  region_id: number          // transient — re-numbered each compute
  persisted_id: number | null  // DB row id for the edit endpoint (null if untracked)
  label: string
  member_frame_ids: number[]
  edited_by_user: boolean    // true = user manually renamed it
  owner_mix: { candidate: number; opponent: number; media: number }
}

export interface EstablishedLandscape {
  frames: EstablishedLandscapeFrame[]
  regions: TopicRegion[]            // labeled topic groupings
  ungrouped_frame_ids: number[]     // HDBSCAN noise — frames not in any region
  computed_at: string
  n_total: number
  error: string | null
}

// One article-extract dot inside a focused established bubble. Lazy-
// loaded via GET /api/narrative-frames/{id}/landscape-detail.
export interface FrameMemberArticle {
  source_item_id: number
  title: string | null
  extracted_text: string | null  // the LLM-pulled quote — what makes the dot meaningful
  source_name: string | null
  outlet_name: string | null
  outlet_type: string | null
  published_at: string | null
}

export interface FrameLandscapeDetail {
  frame_id: number
  articles: FrameMemberArticle[]
}

// V12: dot-level landscape. Atomic unit = one article extract; visible
// grouping emerges from UMAP-projected positions + nested hull overlays
// (narrative-level frame_id, topic-level region_id).
// V13.19 — both owner_type (who BENEFITS) and subject_type (who it's
// ABOUT) ride on every dot/narrative, so the chart can color via the
// 4-quadrant scheme (owner × subject).
export interface ExtractDot {
  id: number                      // NarrativeFrameMention.id
  x: number
  y: number
  frame_id: number                // parent narrative
  owner_type: 'candidate' | 'opponent' | 'media'
  subject_type: 'candidate' | 'opponent' | 'media'
  extracted_text: string
  source_item_id: number
  source_title: string | null
  source_name: string | null
  outlet_name: string | null
  outlet_type: string | null
  published_at: string | null
}

export interface NarrativeGroupInfo {
  frame_id: number
  name: string
  description: string | null
  owner_type: 'candidate' | 'opponent' | 'media'
  subject_type: 'candidate' | 'opponent' | 'media'
  mentions_total: number
  dot_count: number
}

// 4-quadrant breakdown of a topic's authority-weighted contributions.
// Keys mirror subject_classifier.QUADRANT_* in backend.
export interface QuadrantMix {
  our_defense: number
  our_offense: number
  their_defense: number
  their_offense: number
  media: number
}

export interface TopicGroupInfo {
  region_id: number
  persisted_id: number | null
  label: string
  edited_by_user: boolean
  member_frame_ids: number[]
  owner_mix: { candidate: number; opponent: number; media: number }
  quadrant_mix: QuadrantMix
}

export interface DotLandscape {
  dots: ExtractDot[]
  narratives: NarrativeGroupInfo[]
  topics: TopicGroupInfo[]
  ungrouped_frame_ids: number[]
  computed_at: string
  n_total: number
  error: string | null
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
  /**
   * The social platform a post originated on, if any: 'twitter' | 'bluesky' |
   * 'reddit' | 'youtube' | 'mastodon' | 'facebook' | 'instagram'. Undefined/
   * null for plain news/web. Orthogonal to both source_type and
   * source_category — derived from the item URL on the backend (see
   * backend/app/services/platform_classify.py), because source_type does NOT
   * track platform (RSS ingestion flattens everything to 'news').
   */
  platform?: string
  /**
   * Coarse category derived from outlet metadata + source_name on the
   * backend. One of: 'local_news' | 'national_news' | 'social_media' |
   * 'campaign_source' | 'other'. Drives the Articles-page Source filter.
   * See backend/app/services/source_category.py.
   */
  source_category?: string
  /**
   * Whose "side" the article reads as benefiting: 'pro_candidate' |
   * 'pro_opponent' | 'neutral'. Combined with `sentiment` on the
   * Articles page to derive the 5-bucket pro/anti × candidate/opponent
   * filter.
   */
  perspective?: string
  published_at?: string
  created_at: string
  summary?: string
  race_relevance_score?: number
  /**
   * Coarse bucket derived from race_relevance_score by the backend
   * (critical/high/medium/low/irrelevant). Surface this rather than the
   * raw score — the bucket is what the UI shows so users don't read false
   * precision into the number.
   */
  race_relevance_label?: RelevanceLabel
  actionability_score?: number
  actionability_label?: string
  priority_score?: number
  sentiment?: Sentiment
  reviewed: boolean
  dismissed: boolean
  archived_as_irrelevant?: boolean
  /**
   * Other source rows that the backend judged to be the same wire story
   * (normalized headline matches exactly AND published within 24h). The
   * representative row is the one this SourceItem describes; `duplicates`
   * lists the other versions across other outlets. Only populated on
   * endpoints that explicitly group (currently /articles/recent).
   */
  duplicates?: ArticleDuplicate[]
  /**
   * Active narrative frames this article matches. Only populated by
   * /articles/recent (used to drive the Frame filter on the Articles
   * page).
   */
  frames?: { id: number; name: string }[]
}

export interface ArticleDuplicate {
  id: number
  source_name?: string
  source_url?: string
  published_at?: string
}

export interface ReviewQueueItem extends SourceItem {
  related_issues: Array<{ id: number; name: string }>
  opponent_attack_count: number
}

/**
 * Full article detail — everything we know about a single SourceItem.
 * Returned by GET /api/articles/{id} and rendered by the dashboard's
 * article-detail modal. See backend/app/routes/dashboard.py for the
 * source of truth on field shapes.
 */
export interface ArticleDetail {
  id: number
  title: string

  // Source / authorship
  source_name?: string
  source_url?: string
  source_type?: string
  source_author?: string
  source_owner_type?: string
  source_owner_confidence?: string
  publisher_domain?: string

  // Timestamps (ISO strings)
  published_at?: string
  ingested_at?: string
  created_at?: string

  // Body
  summary?: string
  raw_text?: string

  // Scoring
  race_relevance_score?: number
  race_relevance_label?: string
  relevance_reasons: string[]
  actionability_score?: number
  actionability_label?: string
  priority_score?: number
  urgency?: string
  sentiment?: string
  content_category?: string
  geo_relevance?: string

  // Mention flags
  candidate_mentioned: boolean
  opponent_mentioned: boolean
  district_mentioned: boolean
  priority_issue_mentioned: boolean

  // Perspective classifier (article_perspective.py — 3-bucket signal used
  // primarily for landscape dot color, not as the headline framing label).
  perspective?: string | null         // pro_candidate | pro_opponent | neutral | null
  perspective_method?: string | null
  perspective_confidence?: string | null
  perspective_reason?: string | null

  // LLM-judged framing — finer-grained than perspective. Tells the user what
  // this article DOES to their candidate (helps / hurts) rather than which
  // side is pushing it (pro_candidate / pro_opponent). Sourced from the
  // single combined campaign_analysis call.
  framing?: string | null             // helps_candidate | hurts_candidate | opponent_news | background | irrelevant

  // Credibility / quality
  credibility_score?: number
  source_credibility?: string
  credibility_note?: string
  extraction_quality_score?: number
  extraction_quality_label?: string
  extraction_quality_reasons: string[]

  // GDELT data
  gdelt_themes: string[]
  gdelt_tone?: {
    avg_tone?: number
    positive?: number
    negative?: number
    polarity?: number
    activity_density?: number
    group_density?: number
    word_count?: number
  } | null

  // Full LLM analysis (shape varies — see structured_extraction in models)
  structured_extraction?: Record<string, unknown> | null

  // Lifecycle flags
  reviewed: boolean
  dismissed: boolean
  archived_as_irrelevant?: boolean
  review_note?: string

  // Relationships
  issue_mentions: Array<{
    issue_id: number
    name?: string
    summary?: string
    link_strength?: number
    link_reasons: string[]
  }>
  opponent_activities: Array<{
    id: number
    opponent_id: number
    opponent_name?: string
    claim?: string
    attack?: string
    promise?: string
    contradiction_note?: string
    repeated_theme?: string
    created_at?: string
  }>
}

export interface Opponent {
  id: number
  name: string
  office?: string
  party?: string
  fec_candidate_id?: string
  /** Bare IG usernames — politicians often run several parallel accounts
   *  (campaign / office / personal) and we track all of them. */
  instagram_handles?: string[] | null
  /** Bare FB page slugs — see instagram_handles. */
  facebook_pages?: string[] | null
  created_at: string
}

/** One handle candidate surfaced by /api/setup/discover-handles. */
export interface DiscoveredHandle {
  handle: string
  url: string
  snippet?: string | null
  confidence: 'high' | 'medium' | 'low'
  score: number
}

export interface HandleDiscoveryResult {
  name: string
  location?: string | null
  instagram: DiscoveredHandle[]
  facebook: DiscoveredHandle[]
}

/** Platforms surfaced by Phase 2 third-party discovery. Matches the
 *  backend's sub_platform vocab. */
export type ThirdPartyPlatform =
  | 'instagram' | 'facebook' | 'bluesky'
  | 'reddit_subreddit' | 'reddit_user' | 'youtube'

/** A third-party account candidate surfaced by /api/setup/discover-third-party.
 *  These are accounts/pages that MENTION the race — local news outlets,
 *  county committees, PACs, statewide subreddits — not the candidate's
 *  or opponent's own accounts.
 */
export interface DiscoveredThirdPartyAccount {
  platform: ThirdPartyPlatform
  identifier: string
  display_name?: string | null
  url: string
  snippet?: string | null
  score: number
  confidence: 'high' | 'medium' | 'low'
  inferred_role: string
  /** When non-null we have a usable RSS feed URL for this platform —
   *  i.e. ingestable today. Null for IG/FB (paused) or YouTube @handles
   *  that need a channel-id lookup before RSS is buildable. */
  rss_url?: string | null
  matched_queries?: string[]
  /** Bare anchor names that surfaced this account. e.g. ["Paige Cognetti"]
   *  for results that only show up in candidate-anchored searches,
   *  ["Rob Bresnahan"] for opponent-only, both for results that appear
   *  in dedup'd searches across both names. UI uses this to render
   *  "via X" pills so it's clear whose search produced each result. */
  matched_anchors?: string[]
}

export interface ThirdPartyDiscoveryResult {
  candidate_name: string
  location?: string | null
  accounts_by_platform: Record<ThirdPartyPlatform, DiscoveredThirdPartyAccount[]>
  /** Map of platform → list of identifiers the user already confirmed.
   *  UI uses this to hide or mark already-tracked candidates. */
  already_tracked: Partial<Record<ThirdPartyPlatform, string[]>>
}

/** A confirmed tracked third-party account stored in the DB. */
export interface TrackedThirdPartyAccount {
  id: number
  platform: ThirdPartyPlatform
  identifier: string
  display_name?: string | null
  url: string
  inferred_role?: string | null
  snippet?: string | null
  rss_url?: string | null
  notes?: string | null
  added_at: string
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
  last_checked_at?: string | null
}

export interface CampaignConfig {
  id?: number
  candidate_name: string
  office?: string
  district?: string
  state?: string
  // Backend CampaignConfig stores location free-form ("Scranton, PA" or
  // "Scranton/Wilkes-Barre, PA-08"). Used as a fallback source for state
  // derivation when district doesn't encode it.
  location?: string
  party?: string
  race?: string
  race_level?: string
  election_type?: string
  district_number?: string
  election_date?: string
  /** True when the stored election_date matches the auto-inferred date for
   *  this race level + state + year. UI shows a subtle "auto" badge when
   *  true so the user knows they can override without "losing" anything. */
  election_date_inferred?: boolean
  campaign_message?: string
  /** Legacy alias for relevance_keywords used by the current Setup form. */
  keywords?: string[]
  /** Legacy alias for key_priorities used by the current Setup form. */
  priorities?: string[]
  key_priorities?: string[]
  relevance_keywords?: string[]
  excluded_keywords?: string[]
  geography_keywords?: string[]
  trends_keywords?: string[]
  neighborhood_keywords?: string[]
  sparse_race_mode?: boolean
  /** Bare IG usernames — see Opponent.instagram_handles. */
  instagram_handles?: string[] | null
  /** Bare FB page slugs — see Opponent.facebook_pages. */
  facebook_pages?: string[] | null
  /** Set when the campaign was created via /api/races/{id}/select. Null for
   *  campaigns set up manually or pre-dating the race-picker flow. */
  directory_race_id?: number | null
  created_at?: string
  updated_at?: string
}

// ── Race Directory ───────────────────────────────────────────────────────
// Mirrors backend RaceDirectoryOut / RaceCandidateOut from schemas.py.
// Source: FEC Candidate Master snapshot, seeded into the race_directory
// table at boot. Powers the "Pick your race" picker in Setup.
export interface RaceCandidate {
  id: number
  race_id: number
  candidate_name: string
  party?: string | null
  is_incumbent: boolean
  /** "candidate" for the row representing the user's pick, "other" for
   *  everyone else (they become Opponents on select). */
  role: string
  campaign_url?: string | null
  notes?: string | null
}

export interface RaceDirectory {
  id: number
  race_key: string
  race_name: string
  race_level: string
  office_name: string
  state: string
  district_label?: string | null
  district_number?: string | null
  election_type: string
  election_date?: string | null
  geography_summary?: string | null
  data_source: string
  is_active: boolean
  candidates: RaceCandidate[]
}

export interface RaceSelectResult {
  race: RaceDirectory
  campaign: CampaignConfig
  selected_candidate_name: string
  opponents_created: number
  opponents_updated: number
  message: string
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
  /** ISO timestamp of the latest article in the spike — when the burst
   *  actually peaked. Drives Timeline view event placement. */
  peak_at?: string | null
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
// These types match what /api/briefing/morning ACTUALLY returns
// (verified against the live backend on 2026-05-24). The previous shape
// included `mention_count_24h`, `why_it_matters`, `risk_warnings`,
// `suggested_actions`, etc. — none of which the backend sends. The frontend
// silently failed to render the LLM race-memo and the new-articles list
// because of the field-name mismatch (`race_situation_memo` vs `race_memo`,
// `new_developments` vs `new_articles`). Keep this aligned with
// routes/dashboard.py:get_morning_briefing + _item_dict.
export interface MorningBriefingNarrativeCard {
  id: number
  name: string
  owner_type?: OwnerType
  this_week: number
  last_week: number
}

// Subset of SourceItem returned by routes/dashboard.py:_item_dict.
// Smaller than the full SourceItem — only includes the briefing-relevant fields.
export interface BriefingArticle {
  id: number
  title: string
  summary?: string | null
  source_name?: string | null
  source_url?: string | null
  published_at?: string | null
  race_relevance_score?: number | null
  actionability_label?: string | null
  framing?: string | null
}

// v2 grounded-memo shapes. Returned only when /briefing/morning?v=2.
// Default (?v=1) keeps `race_memo` as a plain string for backward compat
// — see GroundedMemo vs string union in MorningBriefing below.
export interface BriefingCitation {
  marker: string       // "C1", "C2", ...
  claim_id: number
  article_id: number
}

export interface BriefingClaimEntity {
  id: string           // canonical_id
  name: string
  affiliation?: string | null
}

export interface BriefingClaim {
  claim_id: number
  quote: string
  label: string        // attack | endorsement | vote | commitment | policy_position | defense
  entities: BriefingClaimEntity[]
  outlet: string
  reliability_score: number | null
  published_at: string | null
  article_id: number
  article_url: string | null
}

export interface GroundedMemo {
  headline: string | null         // 1-line LLM-generated summary ("Linear-style"
                                  // headline). Null if the model didn't produce
                                  // one in the expected format — the frontend
                                  // renders the body alone in that case.
  text: string                    // prose with [C1] [C2] markers
  citations: BriefingCitation[]
  sources_used: BriefingClaim[]   // also drives the "Sources Used" expandable
  // Admin manual-override metadata. `input_hash` pins an override to the
  // inputs the LLM was working from when the admin saved — the frontend
  // echoes it back on the next PUT so the override is auto-cleared when
  // the news materially changes. `overridden_*` flag which fields the
  // admin manually edited; `overridden_by` / `overridden_at` drive a small
  // "Edited by X · 5m ago" indicator for non-admin viewers.
  input_hash?: string
  overridden_headline?: boolean
  overridden_text?: boolean
  overridden_by?: string | null
  overridden_at?: string | null
}

export interface BriefingEntity {
  id: string                      // canonical_id, e.g. "person:bresnahan"
  name: string
  type: 'person' | 'organization' | 'bill' | 'event' | 'location'
  affiliation: 'D' | 'R' | 'I' | null
  mentions_this_week: number      // race-context-gated for context entities; raw for always-show candidates
  mentions_last_week: number
  delta: number
  // gated/raw ratio for context entities (e.g. 0.15 = 15% race-focused).
  // null for always-show candidates (gate is meaningless — they ARE the race)
  // and when raw count is zero (no denominator).
  race_share: number | null
  sample_recent_titles: string[]
}

// Frames whose articles are cited in the v2 grounded memo. Powers
// Featured Narratives pinning — every frame in this list is guaranteed
// a slot in the dashboard panel so the memo and panel always agree.
// Top-confidence frame per article, ties included.
export interface BriefingCitedFrame {
  frame_id: number
  frame_name: string
  confidence: number
  cited_article_ids: number[]
}

export interface MorningBriefing {
  generated_at: string
  meta: {
    total_articles_today: number
    relevant_articles_today: number
  }
  race_memo?: string | GroundedMemo | null
  narrative_pulse: MorningBriefingNarrativeCard[]
  needs_response: BriefingArticle[]
  new_articles: BriefingArticle[]
  spike_alerts: Array<{
    frame_id: number
    name: string
    score: number
    reach: number
  }>
  // Only present when ?v=2
  top_entities?: BriefingEntity[]
  // Only present when ?v=2 — last 48h of candidate-specific labeled claims.
  // May be empty in quiet windows; render conditionally.
  overnight_changes?: BriefingClaim[]
  // Only present when ?v=2 — frames cited in the memo (top-confidence
  // per article, ties included). Drives Featured Narratives pinning.
  cited_frames?: BriefingCitedFrame[]
}

// Backend returns the checklist as a list of items so the labels, help text,
// and click-through paths live on the server side. The Setup page renders
// `items[].complete` for each row and uses `complete` for the overall banner.
export interface SetupChecklistItem {
  id: string
  label: string
  complete: boolean
  helper_text: string
  /** Frontend route the user should visit to complete this step. */
  action_path: string
}

export interface SetupStatus {
  complete: boolean
  items: SetupChecklistItem[]
}

// AI triage verdict for a proposed (HDBSCAN-clustered) cluster of candidate
// frames. One row per cluster_fingerprint (sha256 of sorted member ids)
// so verdicts survive HDBSCAN cluster_id reshuffles. See backend
// services/narrative_triage.py.
export type NarrativeTriageVerdictKind =
  | 'auto_reject'              // noise heuristic — hide from UI by default
  | 'auto_merge'                // AI: this IS an existing tracked narrative
  | 'auto_promote_suggested'    // AI: clearly worth tracking — pre-fill modal
  | 'human_review'              // AI uncertain — user decides un-pre-filled

export interface NarrativeTriageVerdict {
  id: number
  cluster_fingerprint: string
  member_candidate_frame_ids: number[]
  verdict: NarrativeTriageVerdictKind
  confidence: number
  reasoning: string | null
  // Set when verdict === 'auto_merge'
  suggested_merge_frame_id: number | null
  // Set when verdict === 'auto_promote_suggested' (or 'human_review' as a head-start)
  suggested_name: string | null
  suggested_description: string | null
  suggested_owner_type: string | null
  dismissed_at: string | null
  applied_at: string | null
  judged_by_model: string | null
  created_at: string | null
  updated_at: string | null
}

// ── Race sentiment (markets + forecaster ratings) ─────────────────────────────
// Phase 1: every source row may have only some fields populated. Markets fill
// the *_pct + delta_7d fields. Forecasters fill rating_label, rating_min_pct,
// rating_max_pct, favors. No single row blends both — by design.

export type RaceSentimentSourceType = 'market' | 'rating'
export type RaceSentimentFavors = 'candidate' | 'opponent' | 'tossup'

export interface RaceSentiment {
  id: number
  source: string                // slug: polymarket | kalshi | cook | sabato | inside_elections | ddhq
  source_type: RaceSentimentSourceType
  display_name: string
  // Markets
  candidate_pct: number | null
  opponent_pct: number | null
  delta_7d: number | null
  // Ratings (as a band, not a fake percent)
  rating_label: string | null
  rating_min_pct: number | null
  rating_max_pct: number | null
  favors: RaceSentimentFavors | null
  // Common
  source_url: string | null
  as_of: string | null          // ISO datetime — when the source itself published this
  notes: string | null
  // Phase 2: connector config + sync state
  external_id: string | null
  external_metadata: Record<string, unknown> | null
  last_synced_at: string | null
  last_sync_error: string | null
  updated_at: string | null     // ISO datetime — when our DB row was last touched
}

// Market pointers (external_id / external_metadata) are sync-owned and
// read-only — they're omitted here to match the backend RaceSentimentUpdate,
// which no longer accepts them (repointing a source at an arbitrary external
// feed was a confused-deputy hole).
export type RaceSentimentUpdate = Partial<Omit<RaceSentiment, 'id' | 'source' | 'source_type' | 'display_name' | 'updated_at' | 'last_synced_at' | 'last_sync_error' | 'external_id' | 'external_metadata'>>

export interface RaceSentimentSnapshot {
  id: number
  source: string
  source_type: RaceSentimentSourceType
  candidate_pct: number | null
  opponent_pct: number | null
  rating_label: string | null
  rating_min_pct: number | null
  rating_max_pct: number | null
  favors: string | null
  captured_at: string           // ISO datetime
  source_as_of: string | null
}

// Phase 3: timeline events overlaid on the forecast chart.
// Each event is one of three types — used to color/icon them.
export type TimelineEventType = 'frame_created' | 'frame_stage_change' | 'top_article'

export interface TimelineEvent {
  type: TimelineEventType
  timestamp: string             // ISO datetime, Z-suffixed
  label: string
  frame_id?: number
  owner_type?: string
  subject_type?: string | null
  from_stage?: string
  to_stage?: string
  article_id?: number
  score?: number
  source_name?: string
}

// Per-frame lifecycle events derived from when articles actually matched
// the frame — NOT when the frame was promoted in the database. See
// /api/race-sentiment/narrative-lifecycle.
export type NarrativeLifecycleEventType =
  | 'narrative_emerged'
  | 'narrative_peaked'
  | 'narrative_faded'

export interface NarrativeLifecycleEvent {
  type: NarrativeLifecycleEventType
  timestamp: string             // ISO datetime, Z-suffixed
  label: string                 // frame name
  frame_id: number
  owner_type: string | null
  // subject_type: who the narrative is ABOUT (resolved server-side via the
  // name-based heuristic when the frame's own field is NULL). Combined with
  // owner_type to drive the 4-quadrant color scheme on the Timeline.
  subject_type: string | null
  // Lifetime article-match count for the frame — drives the Timeline pin
  // SIZE so a narrative's reach is visible at a glance. Stable across all
  // event types for the same frame, so the Emerged / Peaked / Faded pins
  // for one narrative are sized comparably.
  total_mentions: number
  peak_count?: number           // only on narrative_peaked
}

// ─── Global search (header dropdown) ─────────────────────────────────────
// Returned by /api/search/entities · /search/quotes · /search/outlets.
// These power the universal search bar — articles and narrative frames
// already have their own shapes.
export interface EntitySearchHit {
  id: number
  canonical_id: string
  name: string
  type: string                  // person | organization | bill | event | location | issue
  affiliation: string | null    // D | R | I | null
  mention_count: number
  source_count: number
}

// ─── Entity detail page (/entities/:id) ───────────────────────────────
// Powers the `/entities/:id` page that Activity-This-Week cards link to.
// Backend: GET /api/entities/{canonical_id}
export interface EntityDetail {
  entity: {
    id: string                  // canonical_id, e.g. "person:bresnahan"
    name: string
    type: 'person' | 'organization' | 'bill' | 'event' | 'location' | 'issue'
    affiliation: 'D' | 'R' | 'I' | null
    description: string | null
    mention_count: number       // counter on entities table — extractor-maintained, possibly stale
    source_count: number
    first_seen: string | null
    last_seen: string | null
  }
  stats: {
    window_days: number
    mentions_this_week: number  // raw article-distinct count in last `window_days`
    mentions_last_week: number  // same metric for the preceding `window_days`
    delta: number
    total_articles: number      // articles mentioning this entity (any time)
    total_quotes: number        // v15.0 claim_records mentioning this entity (any time)
  }
  recent_articles: Array<{
    id: number
    title: string
    source_url: string
    source_name: string
    published_at: string | null
    summary: string | null
    sentiment: string | null
    race_relevance_score: number
  }>
  supporting_quotes: Array<{
    id: number
    evidence_span: string
    label: string | null
    confidence: string
    article: {
      id: number
      title: string
      source_url: string
      source_name: string
      published_at: string | null
    } | null
  }>
  narrative_frames: Array<{
    id: number
    name: string
    owner_type: 'candidate' | 'opponent' | 'media'
    stage: string | null
    article_count: number
  }>
}

export interface QuoteSearchHit {
  id: number
  evidence_span: string
  label: string | null          // statement | attack | endorsement | …
  article_id: number
  article_title: string | null
  source_name: string           // already publisher-resolved server-side
}

export interface OutletSearchHit {
  id: number
  name: string
  domain: string
  outlet_type: string
  city: string | null
  state: string | null
  authority_score: number
}

// Empty-state "Try searching" tour. Returned by /api/search/suggestions.
// Each sub-shape is intentionally narrower than the corresponding
// SearchHit above — suggestions only need enough to render one example
// row, not full search-result detail.
export interface EntitySuggestion {
  id: number
  canonical_id: string
  name: string
  type: string
  affiliation: string | null
  mentions_this_week: number
}

export interface OutletSuggestion {
  id: number
  name: string
  domain: string
  articles_this_week: number
}

export interface FrameSuggestion {
  id: number
  name: string
  owner_type: string | null
  mentions_this_week: number
}

export interface QuoteSuggestion {
  id: number
  evidence_span: string
  article_id: number
  source_name: string
}

export interface SearchSuggestions {
  entities: EntitySuggestion[]
  outlets: OutletSuggestion[]
  frames: FrameSuggestion[]
  quotes: QuoteSuggestion[]
}

