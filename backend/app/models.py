from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Boolean, Float, UniqueConstraint
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
    trends_keywords = Column(Text)  # JSON array of extra Google Trends terms
    historical_backfill_completed = Column(Boolean, default=False)
    extended_backfill_completed = Column(Boolean, default=False)
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
    gdelt_themes = Column(Text, nullable=True)  # JSON array of GKG theme strings
    extraction_quality_score = Column(Integer, default=100)
    extraction_quality_label = Column(String, default="good")
    extraction_quality_reasons = Column(Text, nullable=True)  # JSON array stored as text
    # positive | negative | neutral | mixed — how the article's tone affects the candidate
    sentiment = Column(String, nullable=True)
    source_credibility = Column(String, default="medium")  # high|medium|low from v2 LLM
    # JSON blob of GDELT's V2Tone field when available (BigQuery-ingested articles):
    # {"avg_tone": -23.5, "positive": 12.1, "negative": 35.6, "polarity": 47.7,
    #  "activity_density": 1.2, "group_density": 0.8, "word_count": 412}
    gdelt_tone = Column(Text, nullable=True)
    # JSON blob of the full LLM analysis result (summary, framing, sentiment,
    # opponent_attacks, etc.) cached so rematch can skip re-reading article text.
    structured_extraction = Column(Text, nullable=True)
    outlet_id = Column(Integer, ForeignKey("outlets.id"), nullable=True)

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


class Outlet(Base):
    __tablename__ = "outlets"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    domain = Column(String, nullable=False, unique=True)
    # local_news | regional_news | national | blog | social | broadcast
    outlet_type = Column(String, nullable=False, default="local_news")
    state = Column(String, nullable=True)   # e.g. "PA"
    city = Column(String, nullable=True)    # e.g. "Scranton"
    # 1 (blog with no editorial staff) to 10 (major regional daily)
    authority_score = Column(Integer, nullable=False, default=5)
    # RSS feed URL for this outlet (used when creating monitors)
    rss_url = Column(String, nullable=True)
    # JSON array of district codes this outlet covers, e.g. ["PA-08", "PA-07"]
    # Used so user-added outlets are picked up by get_local_outlets() for future campaigns.
    districts = Column(Text, nullable=True)
    # Estimated monthly unique visitors (from LLM knowledge or manual entry).
    # Used to calculate reach: reach = monthly_visitors * per_article_factor (default 0.003).
    # NULL means fall back to authority_score-based weighting.
    monthly_visitors = Column(Integer, nullable=True)
    active = Column(Boolean, default=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


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
    # Most recent stage observed for this frame. NULL when never computed.
    # Compared against the freshly-computed stage in get_frames_with_counts;
    # any difference triggers a FrameStageHistory row + update of this field.
    last_known_stage = Column(String, nullable=True)
    last_stage_check_at = Column(DateTime, nullable=True)
    # Cross-signal classification from Trend×Narrative correlation:
    #   viral             — article spike + matching trend spike
    #   missing_coverage  — trend spike without matching article volume
    #   elite_only        — article spike without matching trend interest
    #   stable            — neither metric spiking
    momentum_signal = Column(String, nullable=True)
    momentum_signal_at = Column(DateTime, nullable=True)
    # JSON snapshot of supporting evidence:
    # {"article_velocity": 3.4, "trend_velocity": 2.1, "matched_terms": [...]}
    momentum_data = Column(Text, nullable=True)

    mentions = relationship("NarrativeFrameMention", back_populates="frame", cascade="all, delete-orphan")


class FrameVariant(Base):
    """A messaging variant within a narrative frame.

    A variant is a specific phrasing/argument of the broader frame:
      Frame: "Bresnahan's Healthcare Record"
        Variant 1: "Bresnahan voted against ACA expansion"
        Variant 2: "Bresnahan blocked Medicaid for seniors"
        Variant 3: "Bresnahan killed healthcare"  (newer, more aggressive)

    Variants are computed by clustering NarrativeFrameMention.extracted_text
    quotes per frame (see app/services/frame_variants.py). Each NFM gets
    assigned a variant_id pointing to its cluster.

    Naming convention: variants get an LLM-generated short name from the
    dominant phrasing in the cluster. Frame name describes the underlying
    claim (stable); variant names describe specific phrasings (mutable as
    the cluster shifts).
    """
    __tablename__ = "frame_variants"
    id = Column(Integer, primary_key=True)
    frame_id = Column(Integer, ForeignKey("narrative_frames.id"), nullable=False)
    name = Column(String, nullable=False)
    # JSON array of floats — centroid of member-quote embeddings.
    # Used for fast nearest-variant lookup when new mentions arrive.
    centroid_embedding = Column(Text, nullable=True)
    first_seen_at = Column(DateTime, nullable=False)
    last_seen_at = Column(DateTime, nullable=False)
    mention_count = Column(Integer, default=0)
    # Generation marker — every full re-cluster bumps this. Useful when
    # we want to invalidate old assignments without dropping the row.
    generation = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class FrameStageHistory(Base):
    """Append-only log of frame stage transitions.

    Stage is normally computed on-read from current mention counts (see
    _narrative_stage), but transitions are the interesting events. This table
    captures each transition so we can answer:
      - When did frame X go from "emerging" to "spreading"?
      - Which frames moved to "fading" this week?
      - How long was frame X stuck at "mainstream"?

    Populated by get_frames_with_counts when it detects a change from
    NarrativeFrame.last_known_stage.
    """
    __tablename__ = "frame_stage_history"
    id = Column(Integer, primary_key=True)
    frame_id = Column(Integer, ForeignKey("narrative_frames.id"), nullable=False)
    from_stage = Column(String, nullable=True)  # NULL = first observation
    to_stage = Column(String, nullable=False)
    transitioned_at = Column(DateTime, default=datetime.utcnow)
    # JSON snapshot of supporting metrics at transition time:
    # {"art_total": 47, "art_this_week": 12, "baseline_weekly": 3, "days_since_article": 1}
    metrics_snapshot = Column(Text, nullable=True)


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
    extracted_text = Column(Text, nullable=True)  # specific claim/quote from article for this frame
    # JSON blob: full extracted-claim metadata (claim_type, actor, intensity,
    # temporal, attribution, rebuttal_quote). See campaign_analysis._validate_v2_claim.
    claim_meta = Column(Text, nullable=True)
    # Which variant of the parent frame this quote belongs to. NULL until
    # the variant clustering job runs. See frame_variants.py.
    variant_id = Column(Integer, ForeignKey("frame_variants.id"), nullable=True)
    # Cached embedding of extracted_text (JSON array of floats). Optional —
    # variant clustering can re-compute on demand if missing.
    quote_embedding = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    frame = relationship("NarrativeFrame", back_populates="mentions")
    source_item = relationship("SourceItem")


# ─── Cluster-native tables (Phase A: schema + dual-write) ─────────────────────
# A StoryCluster is a temporal aggregation unit — "what happened" across N
# articles. FrameClusterMatch / ClusterOpponentActivity attach interpretation
# events to the cluster rather than the article. Phase A populates these in
# parallel with the legacy NarrativeFrameMention / OpponentActivity tables;
# analytics still reads legacy until Phase C.

class StoryCluster(Base):
    __tablename__ = "story_clusters"
    # Matches SourceItem.story_cluster_id format ("source-{N}") so legacy and
    # cluster-native rows can be joined without translation.
    id = Column(String, primary_key=True)
    # Provenance — first article that ever attached to this cluster.
    seed_source_item_id = Column(Integer, ForeignKey("source_items.id"), nullable=False)
    # UI-facing best representative; recomputed on every attach.
    representative_source_item_id = Column(Integer, ForeignKey("source_items.id"), nullable=False)
    # Frozen at each LLM run; the article whose text grounded the most recent
    # cluster-level analysis. Stays put on plain attaches.
    analysis_anchor_source_item_id = Column(Integer, ForeignKey("source_items.id"), nullable=True)
    analysis_anchor_updated_at = Column(DateTime, nullable=True)
    last_llm_analysis_at = Column(DateTime, nullable=True)
    title_representative = Column(String, nullable=True)
    summary_representative = Column(Text, nullable=True)
    simhash_64 = Column(String, nullable=True)  # hex-encoded 64-bit SimHash of representative body
    first_seen_at = Column(DateTime, nullable=False)
    last_seen_at = Column(DateTime, nullable=False)
    article_count = Column(Integer, default=1)
    outlet_count = Column(Integer, default=1)
    source_diversity_score = Column(Float, default=0.0)
    known_entities = Column(Text, nullable=True)  # JSON list of normalized entity strings
    dormant_since = Column(DateTime, nullable=True)
    # Cached JSON of the most recent cluster-level LLM extraction so rematch
    # can skip re-reading article text.
    structured_extraction = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class FrameClusterMatch(Base):
    __tablename__ = "frame_cluster_matches"
    __table_args__ = (
        UniqueConstraint("frame_id", "story_cluster_id", name="uq_frame_cluster"),
    )
    id = Column(Integer, primary_key=True)
    frame_id = Column(Integer, ForeignKey("narrative_frames.id"), nullable=False)
    story_cluster_id = Column(String, ForeignKey("story_clusters.id"), nullable=False)
    confidence = Column(Integer, default=75)
    matched_by = Column(String, default="llm")  # llm | human
    # cluster_runtime | cluster_backfill | cluster_retrigger
    source_type = Column(String, default="cluster_runtime")
    # Timestamp of the LLM run that produced this match. The representative
    # article is resolved dynamically via StoryCluster.representative_source_item_id.
    representative_snapshot_ts = Column(DateTime, nullable=True)
    first_seen_at = Column(DateTime, default=datetime.utcnow)
    last_seen_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ClusterOpponentActivity(Base):
    __tablename__ = "cluster_opponent_activities"
    __table_args__ = (
        UniqueConstraint(
            "opponent_id", "story_cluster_id", "fingerprint",
            name="uq_cluster_opponent_fp",
        ),
    )
    id = Column(Integer, primary_key=True)
    opponent_id = Column(Integer, ForeignKey("opponents.id"), nullable=False)
    story_cluster_id = Column(String, ForeignKey("story_clusters.id"), nullable=False)
    claim = Column(Text, nullable=True)
    attack = Column(Text, nullable=True)
    promise = Column(Text, nullable=True)
    # Hash of normalized quote text — dedup key within (opponent, cluster).
    fingerprint = Column(String, nullable=False)
    source_type = Column(String, default="cluster_runtime")
    first_seen_at = Column(DateTime, default=datetime.utcnow)
    last_seen_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class GoogleTrendSnapshot(Base):
    """Daily Google Trends interest score (0–100) for a tracked search term.

    Pulled via pytrends. The same term/date is stored once per geography
    (statewide US-PA and the Scranton/Wilkes-Barre DMA US-PA-577), so the
    Analytics page can toggle between them.
    Interest is relative within the query batch — 100 = peak interest for that
    term over the trailing 90 days. Stored per term per day for sparkline display.
    """
    __tablename__ = "google_trend_snapshots"
    __table_args__ = (
        UniqueConstraint("term", "snapshot_date", "geo", name="uq_google_trend_daily"),
    )
    id = Column(Integer, primary_key=True)
    term = Column(String, nullable=False)
    snapshot_date = Column(DateTime, nullable=False)
    interest = Column(Integer, nullable=True)  # 0–100, None if Google returned no data
    geo = Column(String, nullable=False, default="US-PA")
    created_at = Column(DateTime, default=datetime.utcnow)


class GdeltToneSnapshot(Base):
    """Daily aggregate tone snapshot from GDELT timelinetone API.

    Tracks how positive/negative media coverage is for the candidate and each
    opponent over time. GDELT tone ranges from -100 (most negative) to +100
    (most positive); neutral coverage sits near 0.
    """
    __tablename__ = "gdelt_tone_snapshots"
    __table_args__ = (
        UniqueConstraint("query_label", "snapshot_date", name="uq_gdelt_tone_daily"),
    )
    id = Column(Integer, primary_key=True)
    # Human-readable label for what was searched, e.g. "Matt Cartwright" or "Rob Bresnahan"
    query_label = Column(String, nullable=False)
    # candidate | opponent
    entity_type = Column(String, nullable=False, default="candidate")
    # Date this snapshot covers (truncated to day, UTC)
    snapshot_date = Column(DateTime, nullable=False)
    # Average GDELT tone score for this day (-100 to +100)
    avg_tone = Column(Float, nullable=True)
    # Number of articles in the GDELT index that day
    article_count = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class CandidateFrame(Base):
    """Staging table for emerging narratives the LLM proposed during scoring
    but which don't match any existing NarrativeFrame.

    Populated per-claim during analysis. A periodic auto-promotion job clusters
    semantically similar candidate frames; clusters with enough cross-article
    and cross-outlet support get promoted into real NarrativeFrames.

    Lifecycle:
      - inserted when extracted_claims[i].candidate_new_frame is set
      - resolved_to_frame_id set when auto-promoted (or merged into existing)
      - rows are kept indefinitely as audit trail
    """
    __tablename__ = "candidate_frames"
    id = Column(Integer, primary_key=True)
    source_item_id = Column(Integer, ForeignKey("source_items.id"), nullable=False)
    suggested_name = Column(String, nullable=False)
    owner_type_hint = Column(String, nullable=False, default="media")
    evidence_quote = Column(Text, nullable=False)
    reasoning = Column(Text, nullable=True)
    # Set when the auto-promoter merges this candidate into a real frame
    # (either newly created or matched to an existing one).
    resolved_to_frame_id = Column(Integer, ForeignKey("narrative_frames.id"), nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
