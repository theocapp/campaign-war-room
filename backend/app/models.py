from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Boolean
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
    election_date = Column(DateTime, nullable=True)
    campaign_message = Column(Text)
    key_priorities = Column(Text)   # JSON array stored as text
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class SourceItem(Base):
    __tablename__ = "source_items"
    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False)
    source_name = Column(String)
    source_url = Column(String)
    # news | social | public_record | canvassing | opponent_statement | campaign_note
    source_type = Column(String, nullable=False)
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
