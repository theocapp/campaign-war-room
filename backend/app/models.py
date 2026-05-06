from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Boolean, UniqueConstraint
from sqlalchemy.orm import relationship
from app.db import Base


class CampaignConfig(Base):
    __tablename__ = "campaign_config"
    id = Column(Integer, primary_key=True)
    candidate_name = Column(String, nullable=False)
    party = Column(String)
    race = Column(String)
    district = Column(String)
    office = Column(String)
    location = Column(String)
    race_level = Column(String)
    election_type = Column(String)
    district_number = Column(String)
    neighborhood_keywords = Column(Text)  # JSON array stored as text
    sparse_race_mode = Column(Boolean, default=False)
    election_date = Column(DateTime, nullable=True)
    campaign_message = Column(Text)
    key_priorities = Column(Text)   # JSON array stored as text
    relevance_keywords = Column(Text)  # JSON array stored as text
    excluded_keywords = Column(Text)   # JSON array stored as text
    geography_keywords = Column(Text)  # JSON array stored as text
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    message_library = relationship("CandidateMessageLibrary", back_populates="campaign", uselist=False)


class RaceDirectory(Base):
    __tablename__ = "race_directory"

    id = Column(Integer, primary_key=True)
    race_key = Column(String, nullable=False, unique=True, index=True)
    race_name = Column(String, nullable=False)
    race_level = Column(String, nullable=False)
    office_name = Column(String, nullable=False)
    state = Column(String, nullable=False)
    district_label = Column(String, nullable=True)
    district_number = Column(String, nullable=True)
    election_type = Column(String, nullable=False)
    election_date = Column(DateTime, nullable=True)
    geography_summary = Column(Text, nullable=True)
    data_source = Column(String, nullable=False, default="manual_seed")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    candidates = relationship("RaceCandidate", back_populates="race", cascade="all, delete-orphan")


class RaceCandidate(Base):
    __tablename__ = "race_candidates"

    id = Column(Integer, primary_key=True)
    race_id = Column(Integer, ForeignKey("race_directory.id"), nullable=False)
    candidate_name = Column(String, nullable=False)
    party = Column(String, nullable=True)
    is_incumbent = Column(Boolean, default=False)
    role = Column(String, default="other")
    campaign_url = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    race = relationship("RaceDirectory", back_populates="candidates")


class SourceItem(Base):
    __tablename__ = "source_items"
    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False)
    source_name = Column(String)
    source_url = Column(String)
    # news | social | public_record | canvassing | opponent_statement | campaign_note
    source_type = Column(String, nullable=False)
    source_owner_type = Column(String, default="unclear")
    source_owner_confidence = Column(String, default="low")
    raw_text = Column(Text)
    summary = Column(Text)
    published_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
    # low | medium | high
    urgency = Column(String, default="low")
    credibility_note = Column(Text)
    reviewed = Column(Boolean, default=False)
    dismissed = Column(Boolean, default=False)
    priority_score = Column(Integer, default=0)
    review_note = Column(Text)
    evidence_score = Column(Integer, default=50)
    credibility_score = Column(Integer, default=50)
    race_relevance_score = Column(Integer, default=0)
    race_relevance_label = Column(String, default="irrelevant")
    relevance_reasons = Column(Text, nullable=True)  # JSON array stored as text
    actionability_score = Column(Integer, default=0)
    actionability_label = Column(String, default="ignore")
    content_category = Column(String, default="irrelevant")
    geo_relevance = Column(String, default="none")
    candidate_mentioned = Column(Boolean, default=False)
    opponent_mentioned = Column(Boolean, default=False)
    district_mentioned = Column(Boolean, default=False)
    priority_issue_mentioned = Column(Boolean, default=False)
    archived_as_irrelevant = Column(Boolean, default=False)
    story_cluster_id = Column(String, nullable=True)
    duplicate_of_source_id = Column(Integer, nullable=True)
    extraction_quality_score = Column(Integer, default=100)
    extraction_quality_label = Column(String, default="good")
    extraction_quality_reasons = Column(Text, nullable=True)  # JSON array stored as text

    issue_mentions = relationship("IssueMention", back_populates="source_item", cascade="all, delete-orphan")
    opponent_activities = relationship("OpponentActivity", back_populates="source_item", cascade="all, delete-orphan")
    narrative_mentions = relationship("NarrativeMention", back_populates="source_item", cascade="all, delete-orphan")


class Issue(Base):
    __tablename__ = "issues"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    summary = Column(Text)
    # low | medium | high
    urgency = Column(String, default="low")
    mention_count = Column(Integer, default=0)
    # rising | stable | falling
    trend = Column(String, default="stable")
    last_seen_at = Column(DateTime)

    mentions = relationship("IssueMention", back_populates="issue", cascade="all, delete-orphan")


class IssueMention(Base):
    __tablename__ = "issue_mentions"
    id = Column(Integer, primary_key=True)
    issue_id = Column(Integer, ForeignKey("issues.id"), nullable=False)
    source_item_id = Column(Integer, ForeignKey("source_items.id"), nullable=False)
    link_strength = Column(Integer, default=0)
    link_reasons = Column(Text, nullable=True)  # JSON array stored as text

    issue = relationship("Issue", back_populates="mentions")
    source_item = relationship("SourceItem", back_populates="issue_mentions")


class Opponent(Base):
    __tablename__ = "opponents"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    office = Column(String)
    party = Column(String)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    activities = relationship("OpponentActivity", back_populates="opponent", cascade="all, delete-orphan")


class OpponentActivity(Base):
    __tablename__ = "opponent_activities"
    id = Column(Integer, primary_key=True)
    opponent_id = Column(Integer, ForeignKey("opponents.id"), nullable=False)
    source_item_id = Column(Integer, ForeignKey("source_items.id"))
    claim = Column(Text)
    attack = Column(Text)
    promise = Column(Text)
    contradiction_note = Column(Text)
    repeated_theme = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

    opponent = relationship("Opponent", back_populates="activities")
    source_item = relationship("SourceItem", back_populates="opponent_activities")
    narrative_mentions = relationship("NarrativeMention", back_populates="opponent_activity", cascade="all, delete-orphan")


class Narrative(Base):
    __tablename__ = "narratives"
    id = Column(Integer, primary_key=True)
    canonical_text = Column(Text, nullable=False)
    short_label = Column(String, nullable=False)
    narrative_type = Column(String, nullable=False)
    owner_type = Column(String, default="unknown")
    direction = Column(String, default="neutral")
    status = Column(String, default="emerging")
    first_seen_at = Column(DateTime)
    last_seen_at = Column(DateTime)
    source_cluster_count = Column(Integer, default=0)
    source_count = Column(Integer, default=0)
    messenger_diversity_count = Column(Integer, default=0)
    geography_count = Column(Integer, default=0)
    traction_score = Column(Integer, default=0)
    evidence_strength = Column(String, default="weak")
    response_status = Column(String, default="no_response")
    owner_confidence = Column(String, default="low")
    attribution_type = Column(String, default="unclear")
    target_confidence = Column(String, default="low")
    candidate_narrative_id = Column(Integer, ForeignKey("candidate_narratives.id"), nullable=True)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    mentions = relationship("NarrativeMention", back_populates="narrative", cascade="all, delete-orphan")
    candidate_narrative = relationship("CandidateNarrative")


class NarrativeMention(Base):
    __tablename__ = "narrative_mentions"
    __table_args__ = (
        # One source per narrative: prevents the same URL appearing twice as
        # evidence.  SQLite treats NULL as distinct in UNIQUE, so activity-only
        # mentions (source_item_id=NULL) are allowed to repeat.
        UniqueConstraint("narrative_id", "source_item_id", name="uq_nm_narrative_source"),
    )
    id = Column(Integer, primary_key=True)
    narrative_id = Column(Integer, ForeignKey("narratives.id"), nullable=False)
    source_item_id = Column(Integer, ForeignKey("source_items.id"), nullable=True)
    opponent_activity_id = Column(Integer, ForeignKey("opponent_activities.id"), nullable=True)
    source_cluster_id = Column(String, nullable=True)
    matched_text = Column(Text, nullable=True)
    mention_role = Column(String, default="repeat")
    confidence_score = Column(Integer, default=50)
    owner_confidence = Column(String, default="low")
    attribution_type = Column(String, default="unclear")
    target_confidence = Column(String, default="low")
    candidate_narrative_id = Column(Integer, ForeignKey("candidate_narratives.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    narrative = relationship("Narrative", back_populates="mentions")
    source_item = relationship("SourceItem", back_populates="narrative_mentions")
    opponent_activity = relationship("OpponentActivity", back_populates="narrative_mentions")
    candidate_narrative = relationship("CandidateNarrative")


class CandidateMessageLibrary(Base):
    __tablename__ = "candidate_message_libraries"
    id = Column(Integer, primary_key=True)
    campaign_config_id = Column(Integer, ForeignKey("campaign_config.id"), nullable=True)
    core_message = Column(Text)
    short_bio_frame = Column(Text)
    tone_guidance = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    campaign = relationship("CampaignConfig", back_populates="message_library")
    narratives = relationship("CandidateNarrative", back_populates="library", cascade="all, delete-orphan")


class CandidateNarrative(Base):
    __tablename__ = "candidate_narratives"
    id = Column(Integer, primary_key=True)
    library_id = Column(Integer, ForeignKey("candidate_message_libraries.id"), nullable=False)
    short_label = Column(String, nullable=False)
    canonical_text = Column(Text, nullable=False)
    narrative_kind = Column(String, nullable=False)
    issue_name = Column(String)
    preferred_phrases = Column(Text)
    avoid_phrases = Column(Text)
    must_mention_points = Column(Text)
    red_lines = Column(Text)
    priority = Column(Integer, default=0)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    library = relationship("CandidateMessageLibrary", back_populates="narratives")


class CanvassingNote(Base):
    __tablename__ = "canvassing_notes"
    id = Column(Integer, primary_key=True)
    voter_name = Column(String)   # optional — privacy
    address = Column(String)      # optional — privacy
    precinct = Column(String, nullable=False)
    issue = Column(String)
    # positive | negative | neutral | mixed
    sentiment = Column(String)
    notes = Column(Text)
    date = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)


class RssFeed(Base):
    __tablename__ = "rss_feeds"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    url = Column(String, nullable=False, unique=True)
    source_type = Column(String, default="news")
    active = Column(Boolean, default=True)
    last_fetched_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)


class SourceMonitor(Base):
    __tablename__ = "source_monitors"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    # rss | search_query | manual | webpage
    monitor_type = Column(String, nullable=False)
    query = Column(Text, nullable=True)
    url = Column(String, nullable=True)
    source_type = Column(String, default="news")
    category = Column(String, nullable=True)
    active = Column(Boolean, default=True)
    required_terms = Column(Text, nullable=True)  # JSON array stored as text
    excluded_terms = Column(Text, nullable=True)  # JSON array stored as text
    relevance_hint = Column(Text, nullable=True)
    last_checked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SourcePack(Base):
    __tablename__ = "source_packs"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(Text)
    race_level = Column(String)   # federal | state | local
    geography = Column(String)    # generic | us_house | us_senate | state_leg | city_council
    created_at = Column(DateTime, default=datetime.utcnow)

    items = relationship("SourcePackItem", back_populates="pack", cascade="all, delete-orphan")


class SourcePackItem(Base):
    __tablename__ = "source_pack_items"
    id = Column(Integer, primary_key=True)
    source_pack_id = Column(Integer, ForeignKey("source_packs.id"), nullable=False)
    name = Column(String, nullable=False)
    category = Column(String)
    source_type = Column(String, default="news")
    url = Column(String)        # may be a template placeholder or None
    setup_note = Column(Text)
    active = Column(Boolean, default=True)

    pack = relationship("SourcePack", back_populates="items")


class ManualSourceReminder(Base):
    __tablename__ = "manual_source_reminders"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    category = Column(String)
    source_type = Column(String, default="news")
    url = Column(String)
    setup_note = Column(Text)
    active = Column(Boolean, default=True)
    last_checked_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)


class ManualCapture(Base):
    __tablename__ = "manual_captures"
    id = Column(Integer, primary_key=True)
    source_item_id = Column(Integer, ForeignKey("source_items.id"), nullable=False)
    title = Column(String, nullable=False)
    source_name = Column(String)
    source_type = Column(String, default="campaign_note")
    source_url = Column(String)
    capture_type = Column(String, default="pasted_text")
    raw_text = Column(Text, nullable=False)
    geography_tags = Column(Text, nullable=True)  # JSON array stored as text
    issue_tags = Column(Text, nullable=True)      # JSON array stored as text
    candidate_related = Column(Boolean, default=False)
    opponent_related = Column(Boolean, default=False)
    notes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    source_item = relationship("SourceItem")


class GeneratedTalkingPoint(Base):
    __tablename__ = "generated_talking_points"
    id = Column(Integer, primary_key=True)
    issue_id = Column(Integer, ForeignKey("issues.id"), nullable=True)
    custom_issue_text = Column(String)
    issue_name = Column(String, nullable=False)
    tone = Column(String, nullable=False)
    short_answer = Column(Text)
    long_answer = Column(Text)
    debate_answer = Column(Text)
    social_post = Column(Text)
    risk_warning = Column(Text)
    evidence_notes = Column(Text)
    source_titles_used = Column(Text)  # JSON array
    source_urls_used = Column(Text)    # JSON array
    created_at = Column(DateTime, default=datetime.utcnow)
