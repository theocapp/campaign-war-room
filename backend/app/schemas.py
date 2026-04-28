import json
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict, field_validator


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
    election_date: Optional[datetime]
    campaign_message: Optional[str]
    key_priorities: Optional[list[str]] = None
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    @field_validator("key_priorities", mode="before")
    @classmethod
    def _parse_priorities(cls, v):
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
    election_date: Optional[datetime] = None
    campaign_message: Optional[str] = None
    key_priorities: Optional[list[str]] = None


# ── Source Items ──────────────────────────────────────────────────────────────

class SourceItemOut(OrmBase):
    id: int
    title: str
    source_name: Optional[str]
    source_url: Optional[str]
    source_type: str
    summary: Optional[str]
    published_at: Optional[datetime]
    created_at: datetime
    urgency: str
    credibility_note: Optional[str]
    reviewed: bool = False
    dismissed: bool = False
    priority_score: int = 0
    review_note: Optional[str] = None
    evidence_score: int = 50
    credibility_score: int = 50


class SourceItemDetail(SourceItemOut):
    raw_text: Optional[str]
    related_issues: list["IssueOut"] = []


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


# ── Opponents ─────────────────────────────────────────────────────────────────

class OpponentIn(BaseModel):
    name: str
    office: Optional[str] = None
    party: Optional[str] = None
    notes: Optional[str] = None


class OpponentOut(OrmBase):
    id: int
    name: str
    office: Optional[str]
    party: Optional[str]
    notes: Optional[str]
    created_at: datetime


class OpponentActivityOut(OrmBase):
    id: int
    opponent_id: int
    claim: Optional[str]
    attack: Optional[str]
    promise: Optional[str]
    contradiction_note: Optional[str]
    repeated_theme: Optional[str]
    created_at: datetime
    source_item: Optional[SourceItemOut]


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
    last_updated: datetime


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
    related_issue_names: list[str] = []
    related_issue_ids: list[int] = []
    opponent_attack_count: int = 0


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
    cleared_canvassing: int
    cleared_talking_points: int
    cleared_feeds: int
    preserved_feeds: int
    candidate_name: str


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


# ── Talking point history ─────────────────────────────────────────────────────

class GeneratedTalkingPointOut(OrmBase):
    id: int
    issue_name: str
    tone: str
    short_answer: str
    long_answer: str
    debate_answer: str
    social_post: str
    risk_warning: Optional[str]
    evidence_notes: str
    source_titles_used: list[str] = []
    source_urls_used: list[str] = []
    created_at: datetime

    @field_validator("source_titles_used", "source_urls_used", mode="before")
    @classmethod
    def _parse_list(cls, v):
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                return [x for x in parsed if x is not None]
            except Exception:
                return []
        return [x for x in (v or []) if x is not None]
