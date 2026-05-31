import html as _html
import json
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, field_validator


def _unescape_text(v: Optional[str]) -> Optional[str]:
    """Decode HTML entities in a string field; passthrough for None."""
    if v is None:
        return v
    return _html.unescape(v)


# ── Shared ────────────────────────────────────────────────────────────────────

class OrmBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ── Campaign Profile ──────────────────────────────────────────────────────────

class CampaignProfileOut(OrmBase):
    id: int
    candidate_name: str
    party: Optional[str]
    race: Optional[str]
    district: Optional[str]
    office: Optional[str]
    location: Optional[str]
    race_level: Optional[str] = None
    election_type: Optional[str] = None
    district_number: Optional[str] = None
    neighborhood_keywords: Optional[list[str]] = None
    sparse_race_mode: bool = False
    election_date: Optional[datetime]
    election_date_inferred: bool = False
    campaign_message: Optional[str]
    key_priorities: Optional[list[str]] = None
    relevance_keywords: Optional[list[str]] = None
    excluded_keywords: Optional[list[str]] = None
    geography_keywords: Optional[list[str]] = None
    trends_keywords: Optional[list[str]] = None
    instagram_handles: Optional[list[str]] = None
    facebook_pages: Optional[list[str]] = None
    directory_race_id: Optional[int] = None
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    @field_validator(
        "key_priorities", "relevance_keywords", "excluded_keywords",
        "geography_keywords", "neighborhood_keywords", "trends_keywords",
        "instagram_handles", "facebook_pages",
        mode="before",
    )
    @classmethod
    def _parse_string_list(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return []
        return v


class CampaignProfileIn(BaseModel):
    candidate_name: str
    party: Optional[str] = None
    race: Optional[str] = None
    district: Optional[str] = None
    office: Optional[str] = None
    location: Optional[str] = None
    race_level: Optional[str] = None
    election_type: Optional[str] = None
    district_number: Optional[str] = None
    neighborhood_keywords: Optional[list[str]] = None
    sparse_race_mode: bool = False
    election_date: Optional[datetime] = None
    campaign_message: Optional[str] = None
    key_priorities: Optional[list[str]] = None
    relevance_keywords: Optional[list[str]] = None
    excluded_keywords: Optional[list[str]] = None
    geography_keywords: Optional[list[str]] = None
    trends_keywords: Optional[list[str]] = None
    instagram_handles: Optional[list[str]] = None
    facebook_pages: Optional[list[str]] = None


# ── Race Directory ────────────────────────────────────────────────────────────

class RaceCandidateOut(OrmBase):
    id: int
    race_id: int
    candidate_name: str
    party: Optional[str] = None
    is_incumbent: bool = False
    role: str = "other"
    campaign_url: Optional[str] = None
    notes: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class RaceDirectoryOut(OrmBase):
    id: int
    race_key: str
    race_name: str
    race_level: str
    office_name: str
    state: str
    district_label: Optional[str] = None
    district_number: Optional[str] = None
    election_type: str
    election_date: Optional[datetime] = None
    geography_summary: Optional[str] = None
    data_source: str
    is_active: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    candidates: list[RaceCandidateOut] = []


class RaceSelectRequest(BaseModel):
    candidate_id: Optional[int] = None
    candidate_name: Optional[str] = None


class RaceSelectResult(BaseModel):
    race: RaceDirectoryOut
    campaign: CampaignProfileOut
    selected_candidate_name: str
    opponents_created: int
    opponents_updated: int
    message: str
    init_result: Optional["CampaignInitializeResult"] = None


# ── Source Items ──────────────────────────────────────────────────────────────

class SourceItemOut(OrmBase):
    id: int
    title: str
    source_name: Optional[str]
    source_url: Optional[str]
    source_type: str
    # Social platform the post originated on, if any (twitter|bluesky|reddit|
    # youtube|mastodon|facebook|instagram); None for plain news/web. Orthogonal
    # to source_type — see services.platform_classify.
    platform: Optional[str] = None
    source_owner_type: str = "unclear"
    source_owner_confidence: str = "low"
    source_author: Optional[str] = None
    summary: Optional[str]
    published_at: Optional[datetime]
    ingested_at: Optional[datetime] = None
    created_at: datetime
    urgency: str
    credibility_note: Optional[str]
    reviewed: bool = False
    dismissed: bool = False
    priority_score: int = 0
    review_note: Optional[str] = None
    evidence_score: int = 50
    credibility_score: int = 50
    race_relevance_score: int = 0
    race_relevance_label: str = "irrelevant"
    relevance_reasons: list[str] = []
    actionability_score: int = 0
    actionability_label: str = "ignore"
    content_category: str = "irrelevant"
    geo_relevance: str = "none"
    candidate_mentioned: bool = False
    opponent_mentioned: bool = False
    district_mentioned: bool = False
    priority_issue_mentioned: bool = False
    archived_as_irrelevant: bool = False
    story_cluster_id: Optional[str] = None
    duplicate_of_source_id: Optional[int] = None
    extraction_quality_score: int = 100
    extraction_quality_label: str = "good"
    extraction_quality_reasons: list[str] = []
    issue_link_strength: Optional[int] = None
    issue_link_reasons: list[str] = []
    sentiment: Optional[str] = None
    snapshot: Optional["SourceSnapshot"] = None

    @field_validator("title", "summary", mode="before")
    @classmethod
    def _unescape_text_fields(cls, v):
        return _unescape_text(v)

    @field_validator("relevance_reasons", mode="before")
    @classmethod
    def _parse_reasons(cls, v):
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                return [str(x) for x in parsed]
            except Exception:
                return [v] if v else []
        return [str(x) for x in (v or [])]

    @field_validator("issue_link_reasons", mode="before")
    @classmethod
    def _parse_issue_link_reasons(cls, v):
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                return [str(x) for x in parsed]
            except Exception:
                return [v] if v else []
        return [str(x) for x in (v or [])]

    @field_validator("extraction_quality_reasons", mode="before")
    @classmethod
    def _parse_extraction_reasons(cls, v):
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                return [str(x) for x in parsed]
            except Exception:
                return [v] if v else []
        return [str(x) for x in (v or [])]


class FrameMentionOut(OrmBase):
    frame_id: int
    frame_name: str
    frame_owner_type: str
    confidence: int
    matched_by: str


class SourceItemDetail(SourceItemOut):
    raw_text: Optional[str]
    related_issues: list["IssueOut"] = []
    frame_mentions: list[FrameMentionOut] = []

    @field_validator("raw_text", mode="before")
    @classmethod
    def _unescape_raw_text(cls, v):
        return _unescape_text(v)


class RSSFeedIn(BaseModel):
    url: str
    label: Optional[str] = None


class TextSourceIn(BaseModel):
    title: str
    raw_text: str
    source_name: Optional[str] = "Manual Entry"
    source_type: str = "campaign_note"
    source_url: Optional[str] = None
    published_at: Optional[datetime] = None


class URLSourceIn(BaseModel):
    url: str
    source_type: str = "news"


# ── Issues ────────────────────────────────────────────────────────────────────

class IssueOut(OrmBase):
    id: int
    name: str
    summary: Optional[str]
    urgency: str
    mention_count: int
    trend: str
    last_seen_at: Optional[datetime]


class IssueDetail(IssueOut):
    recent_sources: list[SourceItemOut] = []
    snapshot: Optional["IssueSnapshot"] = None


class SourceSnapshot(BaseModel):
    what_happened: str
    why_it_matters: str
    geography_summary: str
    actors_summary: str
    action_signal: str
    evidence_summary: str
    key_claim_or_quote: Optional[str] = None


class IssueSnapshot(BaseModel):
    issue_snapshot: str
    why_it_matters_now: str
    top_geographies: list[str] = []
    top_actors: list[str] = []
    top_distinct_developments: list[str] = []
    messaging_readiness: str
    evidence_strength: str


# ── Opponents ─────────────────────────────────────────────────────────────────

class OpponentIn(BaseModel):
    name: str
    office: Optional[str] = None
    party: Optional[str] = None
    notes: Optional[str] = None
    instagram_handles: Optional[list[str]] = None
    facebook_pages: Optional[list[str]] = None


class OpponentOut(OrmBase):
    id: int
    name: str
    office: Optional[str]
    party: Optional[str]
    notes: Optional[str]
    instagram_handles: Optional[list[str]] = None
    facebook_pages: Optional[list[str]] = None
    created_at: datetime

    @field_validator("instagram_handles", "facebook_pages", mode="before")
    @classmethod
    def _parse_handle_list(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return []
        return v


class OpponentActivityOut(OrmBase):
    id: int
    opponent_id: int
    claim: Optional[str]
    attack: Optional[str]
    promise: Optional[str]
    source_type: Optional[str]
    first_seen_at: Optional[datetime]
    last_seen_at: Optional[datetime]



class NarrativeComparisonItem(BaseModel):
    narrative_id: int
    short_label: str
    owner_type: str
    narrative_type: str
    status: str
    traction_score: int
    evidence_strength: str
    source_cluster_count: int
    messenger_diversity_count: int
    geography_count: int
    outside_owned_channels: bool
    practical_read: str


class NarrativeComparisonOut(BaseModel):
    top_opponent_narratives: list[NarrativeComparisonItem] = []
    top_candidate_narratives: list[NarrativeComparisonItem] = []
    candidate_owned_only: list[NarrativeComparisonItem] = []
    candidate_broader_spread: list[NarrativeComparisonItem] = []
    opponent_rising_faster: list[NarrativeComparisonItem] = []
    ready_to_amplify: list[NarrativeComparisonItem] = []
    needs_response: list[NarrativeComparisonItem] = []
    summary: str
    generated_at: datetime


# ── Canvassing ────────────────────────────────────────────────────────────────

class PrecinctInsight(BaseModel):
    precinct: str
    contact_count: int
    top_issues: list[str]
    dominant_sentiment: str
    summary: str


class CanvassingInsightsOut(BaseModel):
    total_contacts: int
    precincts: list[PrecinctInsight]
    overall_top_issues: list[str]
    sentiment_breakdown: dict[str, int]


# ── Talking Points ────────────────────────────────────────────────────────────

class TalkingPointRequest(BaseModel):
    issue_id: Optional[int] = None
    custom_issue_text: Optional[str] = None
    # calm | aggressive | policy-focused | debate | social
    tone: str = "calm"
    # short | long | debate | social | all
    output_format: str = "all"


class TalkingPointResponse(BaseModel):
    issue: str
    short_answer: str
    long_answer: str
    debate_answer: str
    social_post: str
    risk_warning: Optional[str]
    evidence_notes: str
    source_titles_used: list[str] = []
    source_urls_used: list[str] = []


# ── Dashboard ─────────────────────────────────────────────────────────────────

class SuggestedAction(BaseModel):
    priority: str   # urgent | high | medium
    action: str
    rationale: str


class RiskWarning(BaseModel):
    source_id: int
    source_title: str
    warning: str
    urgency: str


class SourceCoverageDiagnostic(BaseModel):
    source_coverage_strength: str
    manual_source_dependence: str
    geography_coverage_gaps: list[str] = []
    issue_coverage_gaps: list[str] = []
    race_coverage_mode: str
    reasons: list[str] = []


class DashboardRaceHeader(BaseModel):
    candidate_name: str
    race: str
    office: Optional[str] = None
    district: Optional[str] = None
    election_type: Optional[str] = None
    opponents: list[str] = []
    race_mode: str
    source_coverage_strength: str


class DashboardAttentionCard(BaseModel):
    card_type: str
    priority: str
    title: str
    explanation: str
    action_label: str
    destination: Optional[str] = None


class DashboardReviewQueueItem(BaseModel):
    source_id: int
    title: str
    issue: Optional[str] = None
    relevance_label: str
    relevance_score: int
    actionability_label: str
    source_type: str
    geography: Optional[str] = None


class DashboardReviewSnapshot(BaseModel):
    review_worthy_count: int
    respond_now_count: int
    top_items: list[DashboardReviewQueueItem] = []


class DashboardPriorityIssue(BaseModel):
    issue_id: int
    name: str
    distinct_development_count: int
    trend: str
    evidence_confidence: str
    geography_concentration: Optional[str] = None
    why_now: str


class DashboardOpponentWatch(BaseModel):
    repeated_themes: list[str] = []
    latest_attack: Optional[str] = None
    source_item_id: Optional[int] = None
    source_title: Optional[str] = None
    source_name: Optional[str] = None
    source_url: Optional[str] = None
    source_created_at: Optional[datetime] = None
    response_status: str
    summary: str


class DashboardReadiness(BaseModel):
    coverage_strength: str
    manual_source_dependence: str
    geography_gaps: list[str] = []
    issue_gaps: list[str] = []
    ready_to_message_issues: list[str] = []
    thin_evidence_issues: list[str] = []
    sparse_race_note: Optional[str] = None
    reasons: list[str] = []


class DashboardDevelopment(BaseModel):
    cluster_id: str
    title: str
    issue: Optional[str] = None
    why_it_matters: str
    source_count: int
    recency: Optional[datetime] = None
    source_id: int


class DashboardNarrativeCard(BaseModel):
    narrative_id: int
    short_label: str
    canonical_text: str
    narrative_type: str
    owner_type: str
    direction: str
    status: str
    traction_score: int
    evidence_strength: str
    response_status: str
    owner_confidence: str
    attribution_type: str
    target_confidence: str
    source_count: int
    source_cluster_count: int
    messenger_diversity_count: int
    geography_count: int
    why_it_matters: str
    source_item_id: Optional[int] = None
    source_title: Optional[str] = None
    source_url: Optional[str] = None
    # Narrative-brief specific fields (added for narrative-first pivot)
    what_changed: Optional[str] = None
    why_it_matters_now: Optional[str] = None
    spread_summary: Optional[str] = None
    risk_or_opportunity: Optional[str] = None
    action: Optional[str] = None
    confidence: Optional[str] = None
    evidence_summary: Optional[str] = None
    top_supporting_sources: list[SourceItemOut] = []
    verify_links: list[str] = []
    # Timeline / change-detection MVP fields
    change_summary: Optional[str] = None
    new_messenger_types: list[str] = []
    new_source_clusters_count: int = 0
    new_geographies: list[str] = []
    escaped_owned_recently: bool = False
    momentum_shift: Optional[str] = None  # stronger | weaker | unchanged
    recent_window_summary: Optional[str] = None
    last_seen_at: Optional[datetime] = None


class DashboardNarrativeComparison(BaseModel):
    top_opponent: list[NarrativeComparisonItem] = []
    top_candidate: list[NarrativeComparisonItem] = []
    candidate_owned_only: list[NarrativeComparisonItem] = []
    candidate_broader_spread: list[NarrativeComparisonItem] = []
    needs_response: list[NarrativeComparisonItem] = []
    ready_to_amplify: list[NarrativeComparisonItem] = []
    summary: str


class DashboardOut(BaseModel):
    candidate_name: str
    race: str
    top_issues: list[IssueOut]
    recent_sources: list[SourceItemOut]
    opponent_activity: list[OpponentActivityOut]
    suggested_actions: list[SuggestedAction]
    risk_warnings: list[RiskWarning]
    canvassing_summary: Optional[str]
    review_queue_count: int = 0
    source_coverage: SourceCoverageDiagnostic
    race_header: Optional[DashboardRaceHeader] = None
    attention_now: list[DashboardAttentionCard] = []
    review_snapshot: Optional[DashboardReviewSnapshot] = None
    priority_issues: list[DashboardPriorityIssue] = []
    opponent_watch: Optional[DashboardOpponentWatch] = None
    narrative_briefing: list[DashboardNarrativeCard] = []
    narrative_comparison: Optional[DashboardNarrativeComparison] = None
    coverage_readiness: Optional[DashboardReadiness] = None
    recent_developments: list[DashboardDevelopment] = []
    last_updated: datetime


# ── Campaign initialization ───────────────────────────────────────────────────

class CampaignInitializeStep(BaseModel):
    step: int
    label: str
    status: Literal["ok", "skipped", "error"]
    detail: str


class CampaignInitializeResult(BaseModel):
    steps: list[CampaignInitializeStep]
    monitors_created: int
    monitors_skipped: int
    sources_ingested: int
    narratives_refreshed: int
    message: str
    initialized_at: datetime


# ── Setup checklist ───────────────────────────────────────────────────────────

class SetupChecklistItem(BaseModel):
    id: str
    label: str
    complete: bool
    helper_text: str
    action_path: str


class SetupStatusOut(BaseModel):
    complete: bool
    items: list[SetupChecklistItem]


# ── RSS Feeds ─────────────────────────────────────────────────────────────────

class RssFeedOut(OrmBase):
    id: int
    name: str
    url: str
    source_type: str
    active: bool
    last_fetched_at: Optional[datetime]
    created_at: datetime


class RssFeedCreate(BaseModel):
    name: str
    url: str
    source_type: str = "news"


class RssFeedUpdate(BaseModel):
    name: Optional[str] = None
    active: Optional[bool] = None
    source_type: Optional[str] = None


class RssFeedIngestResult(BaseModel):
    feed_id: int
    added_count: int
    skipped_count: int
    error_count: int
    added_items: list[SourceItemOut] = []


# ── Source monitors ───────────────────────────────────────────────────────────

class SourceMonitorBase(BaseModel):
    name: str
    monitor_type: str
    query: Optional[str] = None
    url: Optional[str] = None
    source_type: str = "news"
    category: Optional[str] = None
    active: bool = True
    required_terms: Optional[list[str]] = None
    excluded_terms: Optional[list[str]] = None
    relevance_hint: Optional[str] = None


class SourceMonitorCreate(SourceMonitorBase):
    pass


class SourceMonitorUpdate(BaseModel):
    name: Optional[str] = None
    monitor_type: Optional[str] = None
    query: Optional[str] = None
    url: Optional[str] = None
    source_type: Optional[str] = None
    category: Optional[str] = None
    active: Optional[bool] = None
    required_terms: Optional[list[str]] = None
    excluded_terms: Optional[list[str]] = None
    relevance_hint: Optional[str] = None


class SourceMonitorOut(OrmBase):
    id: int
    name: str
    monitor_type: str
    query: Optional[str]
    url: Optional[str]
    source_type: str
    category: Optional[str]
    active: bool
    required_terms: Optional[list[str]] = None
    excluded_terms: Optional[list[str]] = None
    relevance_hint: Optional[str]
    last_checked_at: Optional[datetime]
    created_at: datetime
    updated_at: Optional[datetime]

    @field_validator("required_terms", "excluded_terms", mode="before")
    @classmethod
    def _parse_terms(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return []
        return v


class GenerateMonitorsRequest(BaseModel):
    apply: bool = False
    replace_existing: bool = False


class GenerateMonitorsResult(BaseModel):
    suggestions: list[SourceMonitorBase]
    created_count: int = 0
    skipped_duplicates: int = 0
    monitors: list[SourceMonitorOut] = []


class MonitorIngestItem(BaseModel):
    title: Optional[str] = None
    url: Optional[str] = None
    status: str
    source_id: Optional[int] = None
    reason: Optional[str] = None
    relevance_label: Optional[str] = None
    relevance_score: Optional[int] = None
    archived_as_irrelevant: Optional[bool] = None


class MonitorIngestResult(BaseModel):
    monitor_id: int
    monitor_name: str
    monitor_type: str
    provider: Optional[str] = None
    message: Optional[str] = None
    added_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    results: list[MonitorIngestItem] = []


class IngestSearchMonitorsResult(BaseModel):
    monitor_count: int
    added_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    results: list[MonitorIngestResult] = []


# ── Review queue ──────────────────────────────────────────────────────────────

class ReviewQueueItemOut(OrmBase):
    id: int
    title: str
    source_name: Optional[str]
    source_url: Optional[str]
    source_type: str
    summary: Optional[str]
    urgency: str
    credibility_note: Optional[str]
    reviewed: bool = False
    dismissed: bool = False
    priority_score: int = 0
    review_note: Optional[str] = None
    published_at: Optional[datetime]
    created_at: datetime
    evidence_score: int = 50
    credibility_score: int = 50
    race_relevance_score: int = 0
    race_relevance_label: str = "irrelevant"
    relevance_reasons: list[str] = []
    actionability_score: int = 0
    actionability_label: str = "ignore"
    content_category: str = "irrelevant"
    geo_relevance: str = "none"
    candidate_mentioned: bool = False
    opponent_mentioned: bool = False
    district_mentioned: bool = False
    priority_issue_mentioned: bool = False
    archived_as_irrelevant: bool = False
    related_issue_names: list[str] = []
    related_issue_ids: list[int] = []
    opponent_attack_count: int = 0
    story_cluster_id: Optional[str] = None
    duplicate_of_source_id: Optional[int] = None
    extraction_quality_score: int = 100
    extraction_quality_label: str = "good"
    extraction_quality_reasons: list[str] = []
    extraction_quality_score: int = 100
    extraction_quality_label: str = "good"
    extraction_quality_reasons: list[str] = []

    @field_validator("relevance_reasons", mode="before")
    @classmethod
    def _parse_reasons(cls, v):
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                return [str(x) for x in parsed]
            except Exception:
                return [v] if v else []
        return [str(x) for x in (v or [])]

    @field_validator("extraction_quality_reasons", mode="before")
    @classmethod
    def _parse_extraction_reasons(cls, v):
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                return [str(x) for x in parsed]
            except Exception:
                return [v] if v else []
        return [str(x) for x in (v or [])]


class ReviewAction(BaseModel):
    review_note: Optional[str] = None


class PriorityUpdate(BaseModel):
    priority_score: int


class BulkReviewAction(BaseModel):
    source_ids: list[int]
    review_note: Optional[str] = None


# ── Source templates ──────────────────────────────────────────────────────────

class SourceTemplate(BaseModel):
    id: str
    name: str
    category: str
    description: str
    example_url: Optional[str]
    url_pattern: Optional[str]
    source_type: str
    setup_note: Optional[str] = None


# ── Dashboard changes ─────────────────────────────────────────────────────────

class DashboardChange(BaseModel):
    type: str        # new_source | new_attack | new_issue | review_completed
    title: str
    detail: Optional[str]
    urgency: Optional[str]
    created_at: datetime


class DashboardChangesOut(BaseModel):
    since_hours: int
    changes: list[DashboardChange]
    new_source_count: int
    new_attack_count: int


# ── Admin / workspace ─────────────────────────────────────────────────────────

class ResetWorkspaceRequest(BaseModel):
    confirm: str                               # must equal "RESET WORKSPACE"
    candidate_name: str
    office: str
    district: Optional[str] = None
    party: Optional[str] = None
    location: Optional[str] = None
    election_date: Optional[datetime] = None
    campaign_message: Optional[str] = None
    key_priorities: Optional[list[str]] = None
    preserve_feeds: bool = False


class ResetWorkspaceResult(BaseModel):
    cleared_sources: int
    cleared_issues: int
    cleared_opponents: int
    cleared_narrative_frames: int
    cleared_feeds: int
    preserved_feeds: int
    candidate_name: str


class ReanalyzeSourcesRequest(BaseModel):
    confirm: str
    limit: Optional[int] = None
    source_id: Optional[int] = None
    include_reviewed: bool = False
    include_dismissed: bool = False
    include_archived: bool = True
    dry_run: bool = False


class RescoreArticlesRequest(BaseModel):
    """Body for POST /admin/rescore-articles.

    A full rescore (only_unscored=False) runs the LLM scoring pipeline over
    every article in the corpus — currently 21k+ items at ~2/min, i.e. a
    multi-day, real-money job. Require an explicit confirm string for that
    case so a single misclick doesn't trigger it. The `only_unscored=True`
    case is the safe "resume" path — it only touches articles that have
    never been scored — so no confirm is required there.
    """
    confirm: Optional[str] = None  # required when only_unscored=False
    only_unscored: bool = False
    auto_rematch: bool = False
    max_workers: Optional[int] = None


class ReanalyzeSourceResult(BaseModel):
    source_id: int
    title: str
    dry_run: bool
    changed: bool
    changes: dict = {}
    issue_names: list[str] = []


class ReanalyzeSourcesResult(BaseModel):
    dry_run: bool
    matched_count: int
    updated_count: int
    results: list[ReanalyzeSourceResult] = []


# ── Source packs ──────────────────────────────────────────────────────────────

class SourcePackItemOut(OrmBase):
    id: int
    source_pack_id: int
    name: str
    category: Optional[str]
    source_type: str
    url: Optional[str]
    setup_note: Optional[str]
    active: bool


class SourcePackOut(OrmBase):
    id: int
    name: str
    description: Optional[str]
    race_level: Optional[str]
    geography: Optional[str]
    created_at: datetime
    items: list[SourcePackItemOut] = []


class SourcePackCreate(BaseModel):
    name: str
    description: Optional[str] = None
    race_level: Optional[str] = None
    geography: Optional[str] = None
    items: list[dict] = []


class SourcePackApplyResult(BaseModel):
    pack_name: str
    feeds_created: int
    reminders_created: int
    skipped_duplicate_feeds: int


# ── Manual source reminders ───────────────────────────────────────────────────

class ManualSourceReminderOut(OrmBase):
    id: int
    name: str
    category: Optional[str]
    source_type: str
    url: Optional[str]
    setup_note: Optional[str]
    active: bool
    last_checked_at: Optional[datetime]
    created_at: datetime


class ManualSourceReminderIn(BaseModel):
    name: str
    category: Optional[str] = None
    source_type: str = "news"
    url: Optional[str] = None
    setup_note: Optional[str] = None


class ManualSourceReminderUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    source_type: Optional[str] = None
    url: Optional[str] = None
    setup_note: Optional[str] = None
    active: Optional[bool] = None


# ── Race CSV import ───────────────────────────────────────────────────────────

class RaceImportResult(BaseModel):
    campaign_updated: bool
    opponents_created: int
    feeds_created: int
    reminders_created: int
    skipped: int
    errors: list[str]


# ── Race Sentiment (markets + forecaster ratings) ─────────────────────────────

class RaceSentimentOut(OrmBase):
    id: int
    source: str
    source_type: Literal["market", "rating"]
    display_name: str
    candidate_pct: Optional[float] = None
    opponent_pct: Optional[float] = None
    delta_7d: Optional[float] = None
    rating_label: Optional[str] = None
    rating_min_pct: Optional[float] = None
    rating_max_pct: Optional[float] = None
    favors: Optional[Literal["candidate", "opponent", "tossup"]] = None
    source_url: Optional[str] = None
    as_of: Optional[datetime] = None
    notes: Optional[str] = None
    external_id: Optional[str] = None
    external_metadata: Optional[dict] = None
    last_synced_at: Optional[datetime] = None
    last_sync_error: Optional[str] = None
    updated_at: Optional[datetime] = None

    @field_validator("external_metadata", mode="before")
    @classmethod
    def _parse_external_metadata(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except Exception:
                return None
        return v


class RaceSentimentUpdate(BaseModel):
    """Partial-update payload. Every field is optional so the UI can patch
    one cell at a time without resending the whole row.

    The market *pointers* (external_id / external_metadata) are deliberately
    NOT editable here. They identify which external market/rating a source
    tracks and are auto-discovered + maintained by race_sentiment_sync. Letting
    a request repoint a source at an arbitrary external feed was a
    confused-deputy hole (the server fetches whatever id it's handed), so the
    pointers are now sync-owned and read-only. RaceSentimentOut still exposes
    them so the UI can show what a source is wired to."""
    candidate_pct: Optional[float] = None
    opponent_pct: Optional[float] = None
    delta_7d: Optional[float] = None
    rating_label: Optional[str] = None
    rating_min_pct: Optional[float] = None
    rating_max_pct: Optional[float] = None
    favors: Optional[Literal["candidate", "opponent", "tossup"]] = None
    source_url: Optional[str] = None
    as_of: Optional[datetime] = None
    notes: Optional[str] = None


class RaceSentimentSnapshotOut(OrmBase):
    id: int
    source: str
    source_type: Literal["market", "rating"]
    candidate_pct: Optional[float] = None
    opponent_pct: Optional[float] = None
    rating_label: Optional[str] = None
    rating_min_pct: Optional[float] = None
    rating_max_pct: Optional[float] = None
    favors: Optional[str] = None
    captured_at: datetime
    source_as_of: Optional[datetime] = None
    # Data-quality flag — see race_sentiment_sync._flag_previous_if_isolated
    # and the suspect-snapshots audit endpoint. The history endpoint filters
    # suspect rows out by default; when callers pass ?include_suspect=true
    # they need to know which rows were flagged.
    suspect: bool = False
    suspect_reason: Optional[str] = None


