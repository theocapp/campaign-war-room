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
    # Who actually posted/authored the content (page, account, byline).
    # Distinct from source_name (outlet) and from who the content is *about*.
    source_author = Column(String, nullable=True)
    raw_text = Column(Text)
    summary = Column(Text)
    published_at = Column(DateTime)
    ingested_at = Column(DateTime, default=datetime.utcnow)
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
    # positive | negative | neutral | mixed — how the article's tone affects the candidate
    sentiment = Column(String, nullable=True)

    issue_mentions = relationship("IssueMention", back_populates="source_item", cascade="all, delete-orphan")
    opponent_activities = relationship("OpponentActivity", back_populates="source_item", cascade="all, delete-orphan")


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
    # FEC candidate ID (e.g. "H8PA08123") when the opponent was loaded from
    # the FEC race directory. Used as the primary dedup key so re-imports and
    # name-format changes don't create duplicate rows. Nullable for
    # manually-created opponents.
    fec_candidate_id = Column(String, unique=True)
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


class NarrativeFrame(Base):
    __tablename__ = "narrative_frames"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(Text)
    owner_type = Column(String, default="candidate")  # candidate | opponent | media
    active = Column(Boolean, default=True)
    source = Column(String, default="human")  # human | llm
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    mentions = relationship("NarrativeFrameMention", back_populates="frame", cascade="all, delete-orphan")


class NarrativeFrameMention(Base):
    __tablename__ = "narrative_frame_mentions"
    __table_args__ = (
        UniqueConstraint("frame_id", "source_item_id", name="uq_nfm_frame_source"),
    )
    id = Column(Integer, primary_key=True)
    frame_id = Column(Integer, ForeignKey("narrative_frames.id"), nullable=False)
    source_item_id = Column(Integer, ForeignKey("source_items.id"), nullable=False)
    confidence = Column(Integer, default=70)
    matched_by = Column(String, default="llm")  # llm | human
    created_at = Column(DateTime, default=datetime.utcnow)

    frame = relationship("NarrativeFrame", back_populates="mentions")
    source_item = relationship("SourceItem")
