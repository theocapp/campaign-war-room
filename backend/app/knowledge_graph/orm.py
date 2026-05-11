"""
SQLAlchemy ORM models for the political knowledge graph.

These tables are purely additive — they do not touch any model in app.models.
The kg_ prefix prevents name collisions with the existing issues / narratives tables.

Creation order in this file matches FK dependency order:
  KGEntity → KGEntityAlias
  KGSource → KGEvent → KGNarrative → KGNarrativeClaim
  KGSource → KGClaim → KGClaimEntity, KGClaimIssue
  KGEdge   (polymorphic — no ORM FK relationships to node tables)
"""
from datetime import datetime

from sqlalchemy import (
    Column, Integer, String, Text, Float, DateTime,
    ForeignKey, UniqueConstraint, CheckConstraint, Index,
)
from sqlalchemy.orm import relationship

from app.db import Base


# ── Entities ──────────────────────────────────────────────────────────────────

class KGEntity(Base):
    __tablename__ = "kg_entities"
    __table_args__ = (
        Index("idx_kg_entities_name",           "name"),
        Index("idx_kg_entities_type",           "entity_type"),
        # Partial indexes expressed as plain indexes here (SQLite-compatible)
        Index("idx_kg_entities_merged_into",    "merged_into_entity_id"),
        Index("idx_kg_entities_canonical_name", "canonical_name"),
    )

    id          = Column(Integer, primary_key=True)
    # PERSON | ORG | ISSUE | PLACE
    entity_type = Column(String, nullable=False)
    # As it appeared in source text
    name        = Column(Text, nullable=False)
    # Resolution-confirmed canonical form; None means name is already canonical
    canonical_name        = Column(Text)
    description           = Column(Text)
    # Freeform key/value bag (JSON text; JSONB in Postgres)
    extra_data            = Column(Text, default="{}")
    # JSON float array — placeholder until pgvector
    embedding             = Column(Text)
    # 0.0–1.0; None until resolution runs
    resolution_confidence = Column(Float, CheckConstraint(
        "resolution_confidence IS NULL OR "
        "(resolution_confidence >= 0.0 AND resolution_confidence <= 1.0)",
        name="ck_kg_entity_res_confidence",
    ))
    # Self-FK: points to surviving entity after a merge; None = this is canonical
    merged_into_entity_id = Column(
        Integer, ForeignKey("kg_entities.id", ondelete="SET NULL")
    )

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    aliases     = relationship("KGEntityAlias", back_populates="entity",
                               cascade="all, delete-orphan")
    claim_links = relationship("KGClaimEntity", back_populates="entity",
                               cascade="all, delete-orphan")
    merged_into = relationship("KGEntity", remote_side="KGEntity.id",
                               foreign_keys=[merged_into_entity_id])


class KGEntityAlias(Base):
    __tablename__ = "kg_entity_aliases"
    __table_args__ = (
        UniqueConstraint("entity_id", "alias", name="uq_kg_entity_alias"),
        Index("idx_kg_entity_aliases_alias", "alias"),
    )

    id        = Column(Integer, primary_key=True)
    entity_id = Column(Integer, ForeignKey("kg_entities.id", ondelete="CASCADE"),
                       nullable=False)
    alias     = Column(Text, nullable=False)

    entity = relationship("KGEntity", back_populates="aliases")


# ── Sources ───────────────────────────────────────────────────────────────────

class KGSource(Base):
    __tablename__ = "kg_sources"
    __table_args__ = (
        Index("idx_kg_sources_url",            "url"),
        Index("idx_kg_sources_content_hash",   "content_hash"),
        Index("idx_kg_sources_ingested_at",    "ingested_at"),
        Index("idx_kg_sources_source_type",    "source_type"),
        Index("idx_kg_sources_credibility",    "credibility_score"),
    )

    id             = Column(Integer, primary_key=True)
    url            = Column(Text, nullable=False)
    title          = Column(Text)
    text           = Column(Text)
    published_at   = Column(DateTime)
    ingested_at    = Column(DateTime, default=datetime.utcnow)
    # SHA-256 of (url || text) — dedup key
    content_hash   = Column(Text, nullable=False, unique=True)
    # Soft back-reference to existing ingestion pipeline row (no FK)
    source_item_id = Column(Integer)
    created_at     = Column(DateTime, default=datetime.utcnow)

    # ── Provenance / credibility ───────────────────────────────────────────
    # news | social | public_record | opponent_statement | campaign_note | …
    source_type      = Column(Text)
    # Human-readable outlet name, e.g. "Washington Post"
    source_name      = Column(Text)
    # Registered domain, e.g. "washingtonpost.com"
    domain           = Column(Text)
    # 0.0 – 1.0; heuristic set at ingestion time
    credibility_score  = Column(Float, default=0.5)
    # True when source_owner_type indicates a candidate/opponent/official
    verified_official  = Column(Integer, default=0)   # SQLite BOOLEAN as INTEGER

    claims = relationship("KGClaim", back_populates="source",
                          cascade="all, delete-orphan")
    events = relationship("KGEvent", back_populates="related_source")


# ── Issues ────────────────────────────────────────────────────────────────────

class KGIssue(Base):
    __tablename__ = "kg_issues"
    __table_args__ = (
        Index("idx_kg_issues_name", "name"),
    )

    id           = Column(Integer, primary_key=True)
    # Normalized slug, e.g. "housing_affordability"
    name         = Column(Text, nullable=False, unique=True)
    # Human-readable label
    display_name = Column(Text, nullable=False)
    description  = Column(Text)
    # JSON float array placeholder
    embedding    = Column(Text)
    created_at   = Column(DateTime, default=datetime.utcnow)
    updated_at   = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    claim_links = relationship("KGClaimIssue", back_populates="issue",
                               cascade="all, delete-orphan")


# ── Events ────────────────────────────────────────────────────────────────────

class KGEvent(Base):
    __tablename__ = "kg_events"
    __table_args__ = (
        Index("idx_kg_events_type",      "event_type"),
        Index("idx_kg_events_timestamp", "event_timestamp"),
    )

    id                = Column(Integer, primary_key=True)
    name              = Column(Text, nullable=False)
    # DEBATE | SCANDAL | POLICY | SPEECH | VOTE
    event_type        = Column(String, nullable=False)
    # When the event actually occurred
    event_timestamp   = Column(DateTime)
    description       = Column(Text)
    related_source_id = Column(Integer,
                               ForeignKey("kg_sources.id", ondelete="SET NULL"))
    created_at        = Column(DateTime, default=datetime.utcnow)
    updated_at        = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    related_source = relationship("KGSource", back_populates="events")
    narratives     = relationship("KGNarrative", back_populates="trigger_event")


# ── Claims ────────────────────────────────────────────────────────────────────

class KGClaim(Base):
    __tablename__ = "kg_claims"
    __table_args__ = (
        CheckConstraint("confidence >= 0.0 AND confidence <= 1.0",
                        name="ck_kg_claim_confidence"),
        Index("idx_kg_claims_semantic_id", "semantic_id"),
        Index("idx_kg_claims_stance",      "stance"),
        Index("idx_kg_claims_confidence",  "confidence"),
    )

    id           = Column(Integer, primary_key=True)
    text         = Column(Text, nullable=False)
    # Canonical human-readable form after phrase normalisation (set by claim_normalizer)
    normalized_text = Column(Text)
    # support | oppose | neutral | unknown
    stance       = Column(String, nullable=False, default="unknown")
    # 0.0 – 1.0
    confidence   = Column(Float, nullable=False, default=0.0)
    source_id    = Column(Integer, ForeignKey("kg_sources.id", ondelete="CASCADE"),
                          nullable=False)
    # SHA-256[:16] of normalized token bag + stance + entities + issues.
    # Used to detect semantically equivalent claims within the same source.
    semantic_id  = Column(Text)
    # JSON float array embedding vector (stored as text; swap for VECTOR with pgvector)
    embedding    = Column(Text)
    extracted_at = Column(DateTime, default=datetime.utcnow)
    created_at   = Column(DateTime, default=datetime.utcnow)

    source          = relationship("KGSource",       back_populates="claims")
    entity_links    = relationship("KGClaimEntity",  back_populates="claim",
                                   cascade="all, delete-orphan")
    issue_links     = relationship("KGClaimIssue",   back_populates="claim",
                                   cascade="all, delete-orphan")
    narrative_links = relationship("KGNarrativeClaim", back_populates="claim",
                                   cascade="all, delete-orphan")


# ── Join: Claim ↔ Entity ──────────────────────────────────────────────────────

class KGClaimEntity(Base):
    __tablename__ = "kg_claim_entities"
    __table_args__ = (
        Index("idx_kg_claim_entities_entity", "entity_id"),
    )

    claim_id  = Column(Integer, ForeignKey("kg_claims.id",   ondelete="CASCADE"),
                       primary_key=True)
    entity_id = Column(Integer, ForeignKey("kg_entities.id", ondelete="CASCADE"),
                       primary_key=True)

    claim  = relationship("KGClaim",  back_populates="entity_links")
    entity = relationship("KGEntity", back_populates="claim_links")


# ── Join: Claim ↔ Issue ───────────────────────────────────────────────────────

class KGClaimIssue(Base):
    __tablename__ = "kg_claim_issues"
    __table_args__ = (
        Index("idx_kg_claim_issues_issue", "issue_id"),
    )

    claim_id = Column(Integer, ForeignKey("kg_claims.id",  ondelete="CASCADE"),
                      primary_key=True)
    issue_id = Column(Integer, ForeignKey("kg_issues.id",  ondelete="CASCADE"),
                      primary_key=True)

    claim = relationship("KGClaim",  back_populates="issue_links")
    issue = relationship("KGIssue",  back_populates="claim_links")


# ── Narratives ────────────────────────────────────────────────────────────────

class KGNarrative(Base):
    __tablename__ = "kg_narratives"
    __table_args__ = (
        Index("idx_kg_narratives_last_seen",     "last_seen_at"),
        Index("idx_kg_narratives_velocity",      "velocity_score"),
        Index("idx_kg_narratives_trigger_event", "trigger_event_id"),
        Index("idx_kg_narratives_status",        "status"),
    )

    id                = Column(Integer, primary_key=True)
    label             = Column(Text, nullable=False)
    description       = Column(Text)
    # Narrative centroid embedding (JSON float array placeholder)
    embedding         = Column(Text)
    # Algorithm that produced this cluster, e.g. "hdbscan", "llm_grouping"
    clustering_method = Column(Text)
    # New supporting claims per day; updated by clustering job
    velocity_score    = Column(Float, default=0.0)
    first_seen_at     = Column(DateTime)
    last_seen_at      = Column(DateTime)
    # Lifecycle: active | inactive | merged
    status            = Column(String, nullable=False, default="active")
    # When status="merged", points to the surviving narrative
    merged_into_id    = Column(Integer, ForeignKey("kg_narratives.id", ondelete="SET NULL"))
    # Event that initiated or significantly accelerated this narrative
    trigger_event_id  = Column(Integer,
                               ForeignKey("kg_events.id", ondelete="SET NULL"))
    created_at        = Column(DateTime, default=datetime.utcnow)
    updated_at        = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    trigger_event = relationship("KGEvent", back_populates="narratives")
    claim_links   = relationship("KGNarrativeClaim", back_populates="narrative",
                                 cascade="all, delete-orphan")


# ── Join: Narrative ↔ Claim ───────────────────────────────────────────────────

class KGNarrativeClaim(Base):
    __tablename__ = "kg_narrative_claims"
    __table_args__ = (
        Index("idx_kg_narrative_claims_claim", "claim_id"),
    )

    narrative_id = Column(Integer, ForeignKey("kg_narratives.id", ondelete="CASCADE"),
                          primary_key=True)
    claim_id     = Column(Integer, ForeignKey("kg_claims.id",     ondelete="CASCADE"),
                          primary_key=True)
    added_at     = Column(DateTime, default=datetime.utcnow)

    narrative = relationship("KGNarrative", back_populates="claim_links")
    claim     = relationship("KGClaim",     back_populates="narrative_links")


# ── Alerts ───────────────────────────────────────────────────────────────────

class KGAlert(Base):
    __tablename__ = "kg_alerts"
    __table_args__ = (
        Index("idx_kg_alerts_narrative",    "narrative_id"),
        Index("idx_kg_alerts_type",         "alert_type"),
        Index("idx_kg_alerts_created_at",   "created_at"),
        Index("idx_kg_alerts_resolved_at",  "resolved_at"),
    )

    id             = Column(Integer, primary_key=True)
    narrative_id   = Column(Integer, ForeignKey("kg_narratives.id", ondelete="CASCADE"),
                            nullable=False)
    # velocity_spike | opponent_attack | entity_surge | source_surge | new_narrative
    alert_type     = Column(String, nullable=False)
    # 0.0 – 1.0
    severity_score = Column(Float, nullable=False)
    message        = Column(Text, nullable=False)
    created_at     = Column(DateTime, default=datetime.utcnow, nullable=False)
    resolved_at    = Column(DateTime)

    narrative = relationship("KGNarrative")


# ── Edges (explicit typed graph relationships) ────────────────────────────────
# Polymorphic: from_type/to_type name the node table; from_id/to_id are PKs
# within that table.  No ORM relationships here — join dynamically in queries.

class KGEdge(Base):
    __tablename__ = "kg_edges"
    __table_args__ = (
        # Prevent duplicate edges of the same typed relationship.
        UniqueConstraint(
            "from_type", "from_id", "to_type", "to_id", "relationship_type",
            name="uq_kg_edge",
        ),
        # Outbound traversal: "what does node X point to?"
        Index("idx_kg_edges_from", "from_type", "from_id"),
        # Inbound traversal: "what points at node Y?"
        Index("idx_kg_edges_to",   "to_type",   "to_id"),
        Index("idx_kg_edges_rel",  "relationship_type"),
    )

    id                = Column(Integer, primary_key=True)
    # entity | claim | issue | source | event | narrative
    from_type         = Column(String, nullable=False)
    from_id           = Column(Integer, nullable=False)
    to_type           = Column(String, nullable=False)
    to_id             = Column(Integer, nullable=False)
    # SUPPORTS | REFUTES | MENTIONS | RELATES_TO | OCCURRED_IN
    relationship_type = Column(String, nullable=False)
    confidence_score  = Column(Float, CheckConstraint(
        "confidence_score IS NULL OR "
        "(confidence_score >= 0.0 AND confidence_score <= 1.0)",
        name="ck_kg_edge_confidence",
    ))
    created_at        = Column(DateTime, default=datetime.utcnow)
