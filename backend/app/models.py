import re
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, DateTime, Date, ForeignKey, Boolean, Float, UniqueConstraint, event
from sqlalchemy.orm import relationship, validates
from app.db import Base


def _humanize_name(name):
    """Normalize a person's name to "First [Middle] Last" form.

    Handles the FEC SHOUTY "LAST, FIRST [MIDDLE]" format ("COGNETTI, PAIGE")
    that the FEC catalog uses everywhere. Also handles already-human names
    idempotently. Used by @validates hooks on CampaignConfig.candidate_name
    and Opponent.name so every consumer downstream sees the clean form,
    regardless of which write path created the row.

    Examples:
      "COGNETTI, PAIGE"       -> "Paige Cognetti"
      "BRESNAHAN, ROBERT P."  -> "Robert P. Bresnahan"
      "Paige Cognetti"        -> "Paige Cognetti" (idempotent)
      "paige  cognetti"       -> "Paige Cognetti" (whitespace + case)
      None / ""               -> None / ""
    """
    if name is None:
        return None
    s = str(name).strip()
    if not s:
        return s
    if "," in s:
        last, _, rest = s.partition(",")
        first = rest.strip()
        last = last.strip()
        if first and last:
            s = f"{first} {last}"
    # Collapse multiple spaces, title-case each component.
    s = re.sub(r"\s+", " ", s).strip()
    # Use a custom title-case so "Robert P." doesn't lose the period and
    # "Mc"/"O'" capitalization isn't mangled — Python's .title() is wrong
    # for "Mcdonald" → "Mcdonald" instead of "McDonald", but that's
    # acceptable trade-off versus the risk of over-cleverness.
    return " ".join(w[:1].upper() + w[1:].lower() if w else w for w in s.split(" "))


class CampaignConfig(Base):
    __tablename__ = "campaign_config"
    id = Column(Integer, primary_key=True)
    candidate_name = Column(String, nullable=False)

    @validates("candidate_name")
    def _normalize_candidate_name(self, _key, value):
        # Storage-time normalization: every write goes through this hook,
        # regardless of which route/service/seed created the row. So
        # downstream consumers (search-query monitors, Bluesky firehose
        # keyword set, Mastodon hashtag derivation, LLM prompts, UI
        # display) always see the clean "Paige Cognetti" form, not the
        # FEC SHOUTY "COGNETTI, PAIGE".
        return _humanize_name(value)
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
    # JSON-encoded list[str] of bare social identifiers (the part AFTER
    # the platform URL prefix, e.g. ["mayorpaigecognetti", "paigegcognetti"]).
    # The RSSHub adapter in source_discovery.py iterates each and emits one
    # feed per handle — politicians routinely run multiple parallel accounts
    # (campaign / office / personal) and we want signal from all of them.
    # NULL or empty list = no social feeds get generated for that platform.
    instagram_handles = Column(Text, nullable=True)
    facebook_pages = Column(Text, nullable=True)
    # Set when the campaign was created via /api/races/{id}/select. Lets the
    # Setup page surface a "reset to FEC default" affordance per field by
    # re-reading the linked RaceDirectory row. NULL = campaign was set up
    # manually (or pre-dates the race-picker flow).
    directory_race_id = Column(
        Integer,
        ForeignKey("race_directory.id", ondelete="SET NULL"),
        nullable=True,
    )
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

    @validates("candidate_name")
    def _normalize_race_candidate_name(self, _key, value):
        return _humanize_name(value)
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
    # Orthogonal to source_type: the social platform a post originated on, if
    # any (twitter|bluesky|reddit|youtube|mastodon|facebook|instagram), else
    # NULL for plain news/web. Derived by services.platform_classify from the
    # URL (primary) + source_name (fallback) — source_type does NOT track this
    # because RSS ingestion flattens every feed item to "news"/"reference".
    platform = Column(String, nullable=True, index=True)
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
    # For aggregator-sourced items (Google News, etc.), the underlying
    # publisher's bare domain extracted from the feed entry. Used by
    # feed_discovery_yield to find outlets worth pulling directly.
    publisher_domain = Column(String, nullable=True, index=True)
    # V13.21 — per-article perspective classification cache. Populated
    # by article_perspective.classify() (cascading outlet bias →
    # attribution → LLM fallback). Drives dot-level color on the
    # narrative landscape; falls back to narrative-level owner_type
    # when NULL. See app/services/article_perspective.py.
    perspective = Column(String, nullable=True)             # pro_candidate | pro_opponent | neutral | NULL
    perspective_method = Column(String, nullable=True)      # existing | outlet_bias | attribution | llm | fallback
    perspective_confidence = Column(String, nullable=True)  # high | medium | low
    perspective_reason = Column(String, nullable=True)      # one-line justification (for debugging)
    # Cached embedding used by the rematch gate (narrative_frames._shortlist_frames_for_article).
    # JSON-encoded float list, populated lazily on first rematch or via the backfill script
    # (app/scripts/backfill_frame_match_embeddings.py). Model column lets the gate detect
    # stale embeddings when the provider changes — see embeddings.py provider policy.
    frame_match_embedding = Column(Text, nullable=True)
    frame_match_embedding_model = Column(String, nullable=True)

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

    @validates("name")
    def _normalize_opponent_name(self, _key, value):
        # See _normalize_candidate_name on CampaignConfig for rationale.
        return _humanize_name(value)
    office = Column(String)
    party = Column(String)
    notes = Column(Text)
    # FEC candidate ID (e.g. "H8PA08123") when the opponent was loaded from
    # the FEC race directory. Used as the primary dedup key so re-imports and
    # name-format changes don't create duplicate rows. Nullable for
    # manually-created opponents.
    fec_candidate_id = Column(String, unique=True)
    # See CampaignConfig.instagram_handles for the storage convention —
    # JSON-encoded list[str] of bare identifiers, no URL prefix.
    instagram_handles = Column(Text, nullable=True)
    facebook_pages = Column(Text, nullable=True)
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


class TrackedThirdPartyAccount(Base):
    """User-confirmed third-party accounts that talk about this race.

    Distinct from the candidate's own and opponents' own social handles
    (those live as JSON-list columns on CampaignConfig and Opponent).
    These are local news on FB, county committees, PACs, statewide
    subreddits, journalists covering the race — anyone whose posts the
    user wants ingested even though they're not the candidate or
    opponent themselves.

    Each row preserves the discovery context (snippet, role) so the
    user remembers why they added it. The (platform, identifier) unique
    constraint prevents re-runs of the discovery flow from creating
    duplicate rows.
    """
    __tablename__ = "tracked_third_party_accounts"
    id = Column(Integer, primary_key=True)
    # Matches the discovery service's sub_platform vocab:
    # instagram | facebook | bluesky | reddit_subreddit | reddit_user | youtube
    platform = Column(String, nullable=False)
    # Bare identifier — handle / page slug / subreddit name / @handle / UCxxxx
    identifier = Column(String, nullable=False)
    display_name = Column(String, nullable=True)
    url = Column(String, nullable=False)
    inferred_role = Column(String, nullable=True)
    snippet = Column(Text, nullable=True)
    # NULL when the platform needs IG/FB gating OR a second lookup to
    # resolve a channel_id (YouTube @handle form).
    rss_url = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    added_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("platform", "identifier", name="uq_tracked_account"),
    )


class SearchResultCache(Base):
    """Disk-backed cache for web-search results.

    Wraps every `search_provider.search()` call so dev iteration and
    repeated user clicks don't burn through the Tavily free-tier quota
    (1000 credits/month per key). TTL defaults to 7 days — handles and
    page slugs change rarely, and the only real cost of staleness is
    that a brand-new account created in the last week wouldn't surface
    yet in cached results.

    Key: (provider, query, limit_n). The `limit_n` is part of the key
    because the same query at limit=4 and limit=8 returns different
    result sets — truncating limit=8 to a cached limit=4 would silently
    drop hits.
    """
    __tablename__ = "search_result_cache"
    id = Column(Integer, primary_key=True)
    provider = Column(String, nullable=False)
    query = Column(Text, nullable=False)
    limit_n = Column(Integer, nullable=False)
    cached_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    # JSON-encoded list of SearchResult records.
    results_json = Column(Text, nullable=False)
    # Inner provider's status message if any (e.g. "all keys exhausted").
    message = Column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("provider", "query", "limit_n", name="uq_search_cache_key"),
    )


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
    # Source reliability tagging (GKG principle #1 — provenance).
    # bias_label: 'left' | 'center-left' | 'center' | 'center-right' | 'right' | null
    # reliability_score: 0–100, Ad Fontes-style. 64+ = good factual reporting,
    # 32–63 = analysis/opinion mix, <32 = inaccurate or fabricated content.
    bias_label = Column(String, nullable=True)
    reliability_score = Column(Integer, nullable=True)
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
    # Explicit subject classification — who the frame is ABOUT (vs owner_type
    # which is who BENEFITS). NULL = fall back to the name-based heuristic in
    # subject_classifier.py. Set explicitly when the user picks a quadrant in
    # the UI (e.g. "Cognetti's Offense" = owner=candidate, subject=opponent).
    subject_type = Column(String, nullable=True)  # candidate | opponent | media | NULL
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


class FeaturedAppearance(Base):
    """Append-only log of dashboard "Featured Narratives" appearances.

    Written when the Dashboard renders a featured card for a frame on a
    given calendar day (frontend POSTs after the cards mount; the unique
    constraint makes repeat-render writes idempotent).

    Read by get_frames_with_counts() to populate the
    `days_featured_last_7` field on each frame. That field feeds the
    saturation penalty in lib/featuredFrame.ts:multiObjectiveScore() —
    frames that have already been featured 3+ days in a row get their
    rank decayed so the panel doesn't become wallpaper.

    `appeared_on` is a Date (not DateTime). We don't care about the
    moment of appearance, only the calendar day.
    """
    __tablename__ = "featured_appearances"
    __table_args__ = (
        UniqueConstraint("frame_id", "appeared_on", name="uq_featured_frame_day"),
    )
    id = Column(Integer, primary_key=True)
    frame_id = Column(Integer, ForeignKey("narrative_frames.id", ondelete="CASCADE"), nullable=False)
    appeared_on = Column(Date, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class IngestionHealthAlert(Base):
    """Tracks per-source ingestion-quality regressions.

    Motivated by the 2026-05-26 Google News body-excerpt collapse going
    unnoticed for 3 days: half our feeds silently dropped from ~2000-char
    article bodies to ~70-char title stubs, and nothing surfaced until
    the spike-detector visibly dried up days later.

    Two alert `kind` values today, both written by `ingestion_health.py`'s
    daily detection job:

    - `short_body`: trailing-24h avg `raw_text` length for this source is
      < 50% of the trailing-7d baseline AND below ~300 chars in absolute
      terms. Catches the Google-News-style collapse where a feed switches
      from full body to title-only.
    - `silent`: a source that historically posted ≥1 item/day has gone
      silent for 24h+. Catches feed misconfiguration, IP bans, or upstream
      outages.

    One active+resolved row per (source_name, kind) — re-running the
    detection job updates the existing row in place. `resolved_at`
    transitions from NULL to a timestamp when the source recovers; the
    row stays around so the frontend can render "Citizens' Voice:
    recovered 2h ago" instead of just dropping the alert without
    explanation. If we ever want a full audit trail we'd swap this for
    an append-only log.

    Surfaced to the dashboard via the notifications bell — see
    `routes/health.py:ingestion_alerts` and `lib/notifications.ts`'s
    `ingestion_quality` kind.
    """
    __tablename__ = "ingestion_health_alerts"
    __table_args__ = (
        UniqueConstraint("source_name", "kind", name="uq_ingestion_health_source_kind"),
    )
    id = Column(Integer, primary_key=True)
    source_name = Column(String(256), nullable=False)
    kind = Column(String(32), nullable=False)
    detected_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)
    # Metrics snapshot at detection time so the notification can render
    # "Citizens' Voice: avg body dropped 1800 → 90 chars" without
    # re-computing.
    baseline_avg_len = Column(Float, nullable=True)
    current_avg_len = Column(Float, nullable=True)
    sample_count_24h = Column(Integer, nullable=True)
    sample_count_7d = Column(Integer, nullable=True)
    last_checked_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class TextOverride(Base):
    """Admin-authored manual overrides for AI-generated text.

    First consumer is the morning briefing — the admin can pencil-edit the
    headline or body when the LLM produces a bad take, and the edit applies
    for every user until the underlying input materially changes.

    `key` is a stable string the consumer chooses (e.g. `briefing.memo.headline`,
    `briefing.memo.text`). `input_hash` pins the override to the inputs the
    LLM was working from when the admin made the edit. When the consumer
    detects a hash mismatch on read it deletes the row — the news has moved
    on and the override has nothing to say about the new state. See
    briefing_summary.get_or_generate_grounded for the consumer pattern.
    """
    __tablename__ = "text_overrides"
    id = Column(Integer, primary_key=True)
    key = Column(String(128), nullable=False, unique=True, index=True)
    value = Column(Text, nullable=False)
    # Stable hash of the inputs that produced the AI text being overridden.
    # Nullable so a future consumer can pin an override that has no upstream
    # hash (e.g. a static label). The briefing path always sets it.
    input_hash = Column(String(128), nullable=True)
    created_by_name = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


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
    # SHA1 of the frame's name+description at the time this match was made.
    # When the frame is edited, the hash changes and this match becomes stale —
    # callers can query for matches whose frame_content_hash doesn't equal
    # the frame's current hash to find/clean stale data.
    frame_content_hash = Column(String, nullable=True)


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


class TopicRegionLabel(Base):
    """Persistent topic-region label for the Landscape page.

    A "topic region" is a HDBSCAN cluster over established narrative-frame
    UMAP positions (see services/topic_regions.py). The label is an LLM-
    generated short phrase like "Healthcare" or "Insider Trading" — or a
    user-edited override.

    Identity problem: HDBSCAN cluster IDs are not stable across recomputes.
    Frame membership IS stable. So we identify a region by its sorted set
    of member frame IDs; on recompute we fuzzy-match new clusters against
    persisted rows by Jaccard overlap.

    Why a separate table (not a column on NarrativeFrame):
      - A frame can belong to a region; a region is a SET of frames. 1:many.
      - User-edited labels need to survive frame add/remove with only minor
        membership shift — Jaccard ≥ 0.5 preserves the label.
      - Cheap to wipe and rebuild if HDBSCAN params change.
    """
    __tablename__ = "topic_region_labels"
    id = Column(Integer, primary_key=True)
    # JSON-encoded sorted list of frame_ids that defined this region at
    # creation time. SQLite can't enforce list ordering, so we sort + JSON
    # at write time and parse at read time.
    member_frame_ids_json = Column(Text, nullable=False)
    label = Column(String, nullable=False)
    # True if a user edited this label. When true, the label survives all
    # recomputes via Jaccard match and never gets overwritten by the LLM.
    edited_by_user = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ProposedClusterTriage(Base):
    """AI triage verdict for a proposed HDBSCAN cluster.

    Per Phase B of the proposed-narrative auto-triage work. The
    narrative_triage service walks the current proposed clusters and,
    for each one, decides one of:

      - auto_reject           Noise heuristic (single outlet, tiny cluster).
                              Hides it from the review queue.
      - auto_merge            LLM judged this cluster IS a tracked narrative
                              already. suggested_merge_frame_id points at it.
      - auto_promote_suggested LLM judged it's clearly worth tracking. The UI
                              should pre-fill the Promote modal with the
                              suggested name/description/owner so the human
                              just hits Confirm.
      - human_review          Ambiguous. The UI shows it un-pre-filled.

    Same identity problem as TopicRegionLabel: HDBSCAN cluster IDs reshuffle
    every run, but the SET of candidate_frame_ids in a cluster is stable
    across runs unless the AI re-scores something. We fingerprint by sorted
    member candidate_frame_ids (sha256 hex) so a cluster keeps its triage
    verdict across landscape recomputes.

    Additive-only — no changes to existing tables. SQLite auto-creates via
    Base.metadata.create_all() in db.init_db().
    """
    __tablename__ = "proposed_cluster_triage"
    id = Column(Integer, primary_key=True)
    # sha256 hex of "|".join(sorted str(candidate_frame_ids)) — the cluster's
    # stable identity across landscape recomputes.
    cluster_fingerprint = Column(String, nullable=False, unique=True, index=True)
    # JSON-encoded sorted list of member candidate_frame_ids, kept for
    # debugging + future "did the membership drift?" checks.
    member_candidate_frame_ids_json = Column(Text, nullable=False)
    # auto_reject | auto_merge | auto_promote_suggested | human_review
    verdict = Column(String, nullable=False, index=True)
    # 0.0–1.0 model-reported confidence. For auto_reject (heuristic) we use
    # 1.0 to signal "decided without LLM."
    confidence = Column(Float, nullable=False, default=0.0)
    # Free-text explanation from the LLM (or "noise heuristic" for auto_reject)
    reasoning = Column(Text, nullable=True)
    # Set when verdict == auto_merge. FK is loose (no constraint) so deleting
    # a tracked frame doesn't orphan-error this row; the consumer should
    # re-validate.
    suggested_merge_frame_id = Column(Integer, nullable=True, index=True)
    # Set when verdict == auto_promote_suggested. The LLM's improved
    # naming/description; the UI pre-fills the Promote modal with these.
    suggested_name = Column(String, nullable=True)
    suggested_description = Column(Text, nullable=True)
    suggested_owner_type = Column(String, nullable=True)  # candidate|opponent|media
    # User actions: applying or overriding the verdict.
    # dismissed_at: user said "ignore this proposal" (acts like auto_reject).
    # applied_at:   user accepted the verdict (e.g. confirmed a suggested
    #               promote/merge). Triage row stays so we can audit later.
    dismissed_at = Column(DateTime, nullable=True)
    applied_at = Column(DateTime, nullable=True)
    # Which LLM made the verdict (e.g. "gpt-4o", "claude-sonnet-4-5"). Useful
    # for the Phase C A/B test bookkeeping.
    judged_by_model = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ProposedClusterSnapshot(Base):
    """Persistent snapshot of a proposed-narrative cluster.

    Stops the Review Queue's Proposed Narratives list from mutating between
    user visits. Previously the list was a live HDBSCAN compute over a 21-day
    rolling window — clusters appeared, disappeared, and reshuffled as new
    articles arrived. The user reported (correctly) that this made the
    workflow untrustworthy: promote a cluster, come back later, and the
    list has changed without action.

    This table persists the most recent snapshot the user has seen. The
    Review Queue reads from here; new HDBSCAN runs only insert/update rows
    when explicitly triggered (manual refresh or scheduled job). User
    actions (promote / merge / dismiss) stamp the snapshot row so it
    disappears from the open list but stays in the table for audit.

    Identity is by cluster_fingerprint (same sha256 the triage layer uses),
    so a re-snapshot finds existing rows by overlap of member ids rather
    than creating duplicates.
    """
    __tablename__ = "proposed_cluster_snapshots"
    id = Column(Integer, primary_key=True)
    # sha256 of "|".join(sorted candidate_frame_ids). Same shape as triage.
    cluster_fingerprint = Column(String, nullable=False, unique=True, index=True)
    # Original HDBSCAN cluster_id from the snapshot pass that wrote this row.
    # Not stable across recomputes — kept for debugging.
    cluster_id = Column(Integer, nullable=False)
    representative_name = Column(String, nullable=False)
    size = Column(Integer, nullable=False)
    outlet_count = Column(Integer, nullable=False)
    # JSON: list[str] of outlet names.
    outlet_names_json = Column(Text, nullable=False)
    # JSON: dict with national/regional/local/blog/social int counts.
    outlet_tier_counts_json = Column(Text, nullable=False)
    owner_type_hint = Column(String, nullable=False)
    subject_type_hint = Column(String, nullable=True)
    # JSON: sorted list[int] of contributing candidate_frame_ids.
    member_candidate_frame_ids_json = Column(Text, nullable=False)
    # JSON: list of NarrativeLandscapePoint dicts (x/y/cluster_id/quote/etc).
    # Kept so the frontend's existing rendering code works unchanged — we
    # serve the same shape the live landscape produces.
    points_json = Column(Text, nullable=False)
    # Cluster centroid in UMAP space — used by the landscape map view.
    x = Column(Float, nullable=True)
    y = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    # Updated when a re-snapshot pass finds this cluster again (size may
    # have grown, etc.). The user-visible "this cluster has been here since"
    # is `created_at`; `refreshed_at` is just bookkeeping.
    refreshed_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    # User-action stamps. A row with either non-null disappears from the
    # open snapshot list. Both null = still pending in the queue.
    dismissed_at = Column(DateTime, nullable=True)
    applied_at = Column(DateTime, nullable=True)
    # When applied: which narrative frame the user promoted/merged this into.
    applied_to_frame_id = Column(Integer, nullable=True, index=True)


# ─── Feature A: Knowledge-graph entity extraction (V14 — Phase 2 schema) ─────
# Per backend/docs/entity_schema.md — 6 entity types, 11 relationship verbs,
# per-race seeded canonical entities. These tables are write-empty until the
# extraction pipeline (Phase 3) runs over the article corpus. Existing data is
# untouched; the new tables add to the schema, they don't modify it.

class Entity(Base):
    """Canonical entity — a person, organization, bill, event, location, or
    issue tracked across articles. Pre-seeded from
    backend/data/canonical_entities.<DISTRICT>.json; the extraction layer
    auto-discovers new ones and adds them with a generated canonical_id."""
    __tablename__ = "entities"
    id = Column(Integer, primary_key=True)
    # Stable string ID like "person:cognetti" or auto-generated "person:auto:<n>".
    # UNIQUE — drives canonicalization and is the FK target from relations.
    canonical_id = Column(String, unique=True, nullable=False, index=True)
    type = Column(String, nullable=False, index=True)  # person|organization|bill|event|location|issue
    name = Column(String, nullable=False)
    # JSON list of strings — every other surface form this entity is known by.
    # Used by the extractor's seed-match step before falling back to embedding.
    aliases = Column(Text, nullable=True)  # JSON-encoded list
    description = Column(Text, nullable=True)
    affiliation = Column(String, nullable=True)  # D|R|I|null
    # Optional type-specific metadata (JSON-encoded). Avoids per-type tables.
    # Examples: person → {"role": "mayor", "city": "Scranton"}
    #           bill → {"status": "pending", "congress_session": "119"}
    metadata_json = Column(Text, nullable=True)
    # Auto-updated by the extraction layer:
    mention_count = Column(Integer, default=0)
    source_count = Column(Integer, default=0)  # distinct articles
    first_seen = Column(DateTime, nullable=True)
    last_seen = Column(DateTime, nullable=True)
    # Seed-vs-discovered:
    seeded = Column(Boolean, default=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class EntityMention(Base):
    """One row per (article, entity) pair. Records the surface text the
    entity appeared as in that article, plus the extractor's confidence.
    Used to compute mention counts and to find supporting evidence for
    relations."""
    __tablename__ = "entity_mentions"
    id = Column(Integer, primary_key=True)
    article_id = Column(Integer, ForeignKey("source_items.id"), index=True, nullable=False)
    entity_id = Column(Integer, ForeignKey("entities.id"), index=True, nullable=False)
    surface_text = Column(String, nullable=True)  # what the article actually said
    confidence = Column(String, nullable=False, default="medium")  # high|medium|low
    extraction_method = Column(String, nullable=False, default="llm")  # seed|alias|embedding|llm
    created_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (
        UniqueConstraint("article_id", "entity_id", name="uq_entity_mention_article_entity"),
    )


class Claim(Base):
    """⚠️ LEGACY — DEPRECATED AFTER v15.0 ⚠️

    Triple-shaped claim layer from extractor versions v14.1 → v14.7.
    DO NOT write to this table going forward. New extractions write to
    ClaimRecord (claim_records) instead — a quote-anchored shape that
    avoids the structural hallucination problems we hit forcing the LLM
    to emit (subject, predicate, object) triples.

    Failure mode that retired this design (documented at v14.7):
      The LLM systematically projects triple-shaped content onto articles
      whether or not the article contains it — e.g. tagging "Cognetti's
      office did not respond" as Cognetti→attended→rally because the
      schema demands a subject-event edge. Tightening the prompt makes
      this worse, not better. The shape itself fights the corpus.

    Original docstring follows (for historical context only).
    ────────────────────────────────────────────────────────────────────

    A single, identifiable assertion about (subject, predicate, object).

    The claim is the unit between extraction and the entity-relation graph.
    Articles support (or contest) claims; the entity_relations row for the
    same (subject, predicate, object) is now a derived denormalization of
    the underlying claim — weight = count of supporting articles, evidence
    aggregated from supporting articles, etc.

    Lifecycle (`status`):
      - active     : the claim stands; supported by ≥1 article
      - contested  : at least one article disputes the claim
      - retracted  : human review marked this as wrong / extraction error

    The (subject_id, predicate, object_id) triple is unique — same logical
    fact = same claim, regardless of how many articles support it.
    """
    __tablename__ = "claims"
    id = Column(Integer, primary_key=True)
    subject_id = Column(Integer, ForeignKey("entities.id"), index=True, nullable=False)
    predicate = Column(String, nullable=False, index=True)
    object_id = Column(Integer, ForeignKey("entities.id"), index=True, nullable=False)
    # Dimensional decomposition derived from predicate (stance.py).
    # Stored explicitly so contradictions can be queried directly.
    procedural = Column(String, nullable=True)
    rhetorical = Column(String, nullable=True)
    ideological = Column(String, nullable=True)
    # Lifecycle
    status = Column(String, nullable=False, default="active")  # active | contested | retracted
    retracted_at = Column(DateTime, nullable=True)
    retracted_by = Column(String, nullable=True)
    retracted_reason = Column(Text, nullable=True)
    # Provenance — first/last article that asserted this claim
    first_seen = Column(DateTime, nullable=True)
    last_seen = Column(DateTime, nullable=True)
    # Best representative quote (UI-facing); per-article quotes live in claim_supports
    sample_quote = Column(Text, nullable=True)
    confidence = Column(String, nullable=False, default="medium")
    extractor_version = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (
        UniqueConstraint("subject_id", "predicate", "object_id", name="uq_claim_triple"),
    )


class ClaimSupport(Base):
    """⚠️ LEGACY — DEPRECATED AFTER v15.0 (paired with Claim) ⚠️

    Per-article stance rows for the triple-shaped Claim layer.
    Replaced by ClaimRecord (claim_records), which collapses
    "claim + per-article support" into a single quote-anchored row.

    N-to-N between claims and articles. Each row = one article either
    supporting or contesting a claim.

    `stance` = 'supporting' (default) — article asserts the claim
    `stance` = 'contesting' — article disputes the claim (e.g., fact-check)
    """
    __tablename__ = "claim_supports"
    id = Column(Integer, primary_key=True)
    claim_id = Column(Integer, ForeignKey("claims.id"), index=True, nullable=False)
    article_id = Column(Integer, ForeignKey("source_items.id"), index=True, nullable=False)
    stance = Column(String, nullable=False, default="supporting")  # supporting | contesting
    sample_quote = Column(Text, nullable=True)
    confidence = Column(String, nullable=False, default="medium")
    extractor_version = Column(String, nullable=True)
    extracted_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (
        UniqueConstraint("claim_id", "article_id", name="uq_claim_support_pair"),
    )


class ClaimRecord(Base):
    """v15.0+ — Quote-anchored claim record. The new source-of-truth
    extraction output, replacing the triple-shaped Claim/ClaimSupport
    pair.

    DESIGN PRINCIPLE: every claim is grounded in a verbatim text span
    from a single article. There is NO inferred predicate, NO synthesized
    subject/object edge. The LLM's job is reduced to selecting a span
    of text and identifying which canonical entities appear in it.

    Quality invariants enforced at persist time:
      - evidence_span must be a verbatim substring of source article text
      - every entity in claim_record_entities must appear in evidence_span
        (by name OR alias)
      - evidence_start_char / evidence_end_char locate the span in
        source_items.raw_text for deterministic UI highlighting + audit

    Quote deduplication: evidence_hash is sha1(normalized_evidence_span),
    UNIQUE across the table. Identical AP-style syndicated phrasing
    naturally collapses to one row, regardless of which outlets repeated
    it (article_id of the FIRST observation wins; later observations
    add an entity_mention but don't duplicate the quote claim).

    Labels are SHALLOW (statement, attack, defense, endorsement,
    policy_position, vote, announcement, commitment) and OPTIONAL. The
    model is encouraged to leave label=NULL when uncertain. We deliberately
    avoid relational predicates here — if you find yourself adding "target"
    or "directionality" fields, you are reintroducing the v14.x failure
    mode. RESIST.

    Future clustering layer (post-v15.0): claim_record embeddings get
    HDBSCAN-clustered the same way frame_variants are. Each cluster
    becomes a "narrative assertion" — the real intelligence object.
    Until then, individual claim_records are the unit of evidence.
    """
    __tablename__ = "claim_records"
    id = Column(Integer, primary_key=True)
    article_id = Column(Integer, ForeignKey("source_items.id"), index=True, nullable=False)
    # The verbatim quote span from source_items.raw_text. Must be a substring.
    evidence_span = Column(Text, nullable=False)
    # Character offsets locating evidence_span in source_items.raw_text.
    # Used for exact UI highlighting + provenance audit + future span-based
    # training/eval. Computed at persist time, not by the LLM.
    evidence_start_char = Column(Integer, nullable=True)
    evidence_end_char = Column(Integer, nullable=True)
    # SHA1 of normalized evidence_span (lowercased, whitespace-collapsed,
    # quote-marks-unified). UNIQUE — natural dedup of syndicated phrasing.
    evidence_hash = Column(String(40), nullable=False, index=True)
    # Optional shallow label. One of {statement, attack, defense, endorsement,
    # policy_position, vote, announcement, commitment} or NULL.
    label = Column(String, nullable=True, index=True)
    confidence = Column(String, nullable=False, default="medium")  # high|medium|low
    extractor_version = Column(String, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (
        UniqueConstraint("evidence_hash", name="uq_claim_record_evidence_hash"),
    )


class ClaimRecordEntity(Base):
    """v15.0+ — Many-to-many between claim_records and entities.

    Each row says: this entity appears in this claim's evidence_span.
    The role/directionality of that appearance is NOT represented —
    we deliberately do not record "this entity is the subject" or
    "this entity is the target". If you need that, the quote itself
    is the source of truth; do not re-derive a predicate.
    """
    __tablename__ = "claim_record_entities"
    id = Column(Integer, primary_key=True)
    claim_record_id = Column(Integer, ForeignKey("claim_records.id"), index=True, nullable=False)
    entity_id = Column(Integer, ForeignKey("entities.id"), index=True, nullable=False)
    # The surface text the entity appeared as in this quote (e.g. "the mayor"
    # for entity_id pointing at Cognetti). Useful for span-level highlighting.
    surface_text = Column(String, nullable=True)
    __table_args__ = (
        UniqueConstraint("claim_record_id", "entity_id", name="uq_claim_record_entity_pair"),
    )


class EntityReviewDecision(Base):
    """Human review decisions on entity-level review-queue items.

    item_type identifies the surfaced pattern (contradiction, canonicalization
    match, partisan suspect, domain/range violation, etc.). item_key is a
    deterministic identifier for the specific item — e.g. for a contradiction
    it might be "contradiction-{subj_id}-{obj_id}". Decisions are sticky:
    once recorded, the listing endpoint stops surfacing that item.

    decision values: "approve", "reject", "skip"
      - approve: the surfaced item is correct, leave it / apply the action
      - reject: the surfaced item is wrong, action should not be applied
      - skip: user deferred — may surface again later
    """
    __tablename__ = "entity_review_decisions"
    id = Column(Integer, primary_key=True)
    item_type = Column(String, nullable=False, index=True)
    item_key = Column(String, nullable=False, index=True)
    decision = Column(String, nullable=False)  # approve|reject|skip
    notes = Column(Text, nullable=True)
    decided_at = Column(DateTime, default=datetime.utcnow)
    decided_by = Column(String, nullable=True)
    __table_args__ = (
        UniqueConstraint("item_type", "item_key", name="uq_entity_review_decision_item"),
    )


class EntityRelation(Base):
    """⚠️ DEPRECATED FOR ACTION PREDICATES AFTER v15.0 ⚠️

    After v15.0 this table is FROZEN for the action predicates
    (endorses, criticizes, attacks, voted_for, voted_against, co_sponsored,
    attended). New LLM extractions do NOT write action-predicate rows here.

    Structural predicates (`represents`, `member_of`, `predecessor_of`)
    sourced from curated seed files (e.g. role_transitions.PA-08.json)
    continue to land here — those are stable, auditable facts that don't
    suffer the LLM-extraction failure mode.

    All 1,786 existing rows remain readable; the EntityNetwork canvas
    still displays them as edges. The triage status is:
      - row written by seed/curated source → live
      - row written by v14.x LLM extraction → frozen, may decay in
        usefulness as the claim_records system supersedes it

    Original docstring follows (for historical context only).
    ────────────────────────────────────────────────────────────────────

    A subject-predicate-object fact extracted from articles. Weight is
    incremented as more articles support the same relation. sample_quote
    holds the best supporting excerpt; source_articles holds the full list.

    Temporal validity (GKG principle #8 — facts decay):
      - For role-type predicates (`represents`, `member_of`, `predecessor_of`):
        valid_from / valid_to bracket the duration of the role.
      - For event-type predicates (`voted_for`, `endorses`, etc.): valid_from
        is the event date; valid_to is normally NULL (events don't expire).
      - NULL valid_from means "validity unknown" (most extracted relations).
      - NULL valid_to means "still valid as of last observation."
    """
    __tablename__ = "entity_relations"
    id = Column(Integer, primary_key=True)
    subject_id = Column(Integer, ForeignKey("entities.id"), index=True, nullable=False)
    predicate = Column(String, nullable=False, index=True)  # endorses|criticizes|attacks|voted_for|voted_against|co_sponsored|represents|member_of|attended|donated_to|predecessor_of
    object_id = Column(Integer, ForeignKey("entities.id"), index=True, nullable=False)
    weight = Column(Integer, default=1)  # # supporting articles
    first_seen = Column(DateTime, nullable=True)
    last_seen = Column(DateTime, nullable=True)
    valid_from = Column(DateTime, nullable=True)  # role start / event date
    valid_to = Column(DateTime, nullable=True)    # role end (NULL = current)
    sample_quote = Column(Text, nullable=True)
    # JSON list of source_item ids supporting this relation, capped to 50
    # most-recent. Beyond 50 we just rely on weight.
    source_articles = Column(Text, nullable=True)  # JSON list of int
    # GKG principle #6 — per-edge provenance. JSON array of evidence dicts:
    # [{article_id, sample_quote, confidence, extracted_at, extractor_version}].
    # Capped at 50 most-recent entries. New persists write here; old persists
    # (pre-V14.2) populated only source_articles + sample_quote — those rows
    # are migrated lossy by scripts/entity_evidence_migrate.py.
    evidence_json = Column(Text, nullable=True)  # JSON list of dicts
    confidence = Column(String, nullable=False, default="medium")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (
        UniqueConstraint("subject_id", "predicate", "object_id", name="uq_entity_relation_triple"),
    )


class RaceSentiment(Base):
    """External 'how the race is going' signals: prediction markets + forecaster ratings.

    Stores the CURRENT value per source (one row per source). History lives
    in `race_sentiment_snapshots` so this row can be read cheaply by the
    Dashboard without scanning a time-series. Kept deliberately flat:
    markets populate (candidate_pct, opponent_pct, delta_7d) and leave the
    rating fields null; forecasters populate (rating_label, rating_min_pct,
    rating_max_pct, favors) and leave the market fields null. The card
    renders by inspecting source_type.

    No blended/composite number is ever stored — that's a deliberate
    design choice. Markets and forecasters measure different things.

    `external_id` + `external_metadata`: connector configuration. For
    Polymarket: external_id is the event slug ("pa-08-house-election-winner");
    metadata is a JSON blob holding the per-side market IDs / token IDs so the
    fetcher knows which Yes-token corresponds to the candidate vs opponent.
    For Cook etc.: external_id is the ratings-page URL; metadata can hold
    any scraper hints. NULL when the row is manually-entered only.
    """
    __tablename__ = "race_sentiment"
    id = Column(Integer, primary_key=True)
    source = Column(String, nullable=False, unique=True)  # slug: polymarket | kalshi | cook | sabato | inside_elections | ddhq
    source_type = Column(String, nullable=False)          # 'market' | 'rating'
    display_name = Column(String, nullable=False)

    # Markets — numeric percentages from the contract prices.
    candidate_pct = Column(Float, nullable=True)
    opponent_pct = Column(Float, nullable=True)
    delta_7d = Column(Float, nullable=True)   # change in candidate_pct vs ~7 days ago

    # Ratings — categorical band. min/max bracket the rating's implied range
    # (e.g. Lean R ≈ 55–65). Stored as a band, never as a single fake percent.
    rating_label = Column(String, nullable=True)     # "Toss-up" | "Lean R" | "Likely D" | etc.
    rating_min_pct = Column(Float, nullable=True)
    rating_max_pct = Column(Float, nullable=True)
    favors = Column(String, nullable=True)           # 'candidate' | 'opponent' | 'tossup'

    # Common metadata
    source_url = Column(String, nullable=True)
    as_of = Column(DateTime, nullable=True)          # when the source itself published this value
    notes = Column(Text, nullable=True)

    # Connector configuration (Phase 2). NULL = manual-entry source.
    external_id = Column(String, nullable=True)        # event slug / ratings URL / etc.
    external_metadata = Column(Text, nullable=True)    # JSON: per-connector config
    last_synced_at = Column(DateTime, nullable=True)   # last successful auto-sync
    last_sync_error = Column(Text, nullable=True)      # null on success, else short error string

    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class RaceSentimentSnapshot(Base):
    """Time-series history of race-sentiment values.

    Written by the daily scheduler job AND by the one-shot history backfill
    that runs when a connector is first configured. The chart on the
    forecast page reads from here. The Dashboard card reads from the
    `race_sentiment` current-value row instead, since it doesn't need history.

    Snapshots are NEVER updated in place — even if the source publishes a
    correction. That preserves a faithful log of what we observed when.
    """
    __tablename__ = "race_sentiment_snapshots"
    id = Column(Integer, primary_key=True)
    source = Column(String, nullable=False, index=True)
    source_type = Column(String, nullable=False)

    # Market shape
    candidate_pct = Column(Float, nullable=True)
    opponent_pct = Column(Float, nullable=True)

    # Rating shape
    rating_label = Column(String, nullable=True)
    rating_min_pct = Column(Float, nullable=True)
    rating_max_pct = Column(Float, nullable=True)
    favors = Column(String, nullable=True)

    captured_at = Column(DateTime, default=datetime.utcnow, index=True)
    source_as_of = Column(DateTime, nullable=True)   # source's own timestamp, if known
    raw_response = Column(Text, nullable=True)        # JSON dump of the upstream payload (debug)

    # Data-quality flag set at write time. True when the snapshot fails the
    # coherence check (candidate_pct + opponent_pct outside ~100% ± spread).
    # Real catastrophic market moves keep the two sides in sync — the only
    # way to break coherence is desynchronized / stale / glitched scrape
    # data. Suspect rows stay in the DB for audit but are filtered out of
    # charts and impact computations by default.
    suspect = Column(Boolean, default=False, nullable=False, index=True)
    # Human-readable reason for the suspect flag, e.g. "incoherent pricing:
    # cand+opp=103.5". Helps audit later.
    suspect_reason = Column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint("source", "captured_at", name="uq_race_sentiment_snapshot"),
    )


# --- derived columns ---------------------------------------------------------
# Keep source_items.platform in sync with (source_url, source_name) on every
# ORM write. platform is a pure function of those two columns (see
# services.platform_classify.derive_platform), so computing it from a single
# mapper event — rather than in each of the ~6 ingestion call sites (RSS,
# firehose, Reddit/Mastodon/Twitter scrapers, manual inserts) — guarantees a
# consistent tag no matter which path created the row. A before_insert/
# before_update listener fires once the object is fully populated, which is
# why this is an event and not a @validates hook (platform needs BOTH columns
# and we don't control the order they're set in).
#
# Idempotent: re-deriving on update yields the same value when url/name are
# unchanged, so this is safe to fire on every flush (e.g. rescore). The raw-SQL
# backfill (scripts/backfill_platform.py) uses Core UPDATEs, which do NOT
# trigger this ORM event — so the two coexist without clobbering each other.
from app.services.platform_classify import derive_platform  # noqa: E402


@event.listens_for(SourceItem, "before_insert")
@event.listens_for(SourceItem, "before_update")
def _set_source_item_platform(mapper, connection, target: "SourceItem") -> None:
    target.platform = derive_platform(target.source_url, target.source_name)
