"""
Pydantic models for the political knowledge graph.

Convention:
  *Create  — write payload (input)
  *Out     — full DB row (API response)
  *Detail  — extended response with nested objects (heavier; single-record views only)
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ── Config shared by all ORM-backed response models ───────────────────────────

class _OrmBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ── Enums ─────────────────────────────────────────────────────────────────────

class EntityType(str, Enum):
    PERSON = "PERSON"
    ORG    = "ORG"
    ISSUE  = "ISSUE"
    PLACE  = "PLACE"


class Stance(str, Enum):
    support = "support"
    oppose  = "oppose"
    neutral = "neutral"
    unknown = "unknown"


class EventType(str, Enum):
    DEBATE  = "DEBATE"
    SCANDAL = "SCANDAL"
    POLICY  = "POLICY"
    SPEECH  = "SPEECH"
    VOTE    = "VOTE"


class RelationshipType(str, Enum):
    SUPPORTS    = "SUPPORTS"
    REFUTES     = "REFUTES"
    MENTIONS    = "MENTIONS"
    RELATES_TO  = "RELATES_TO"
    OCCURRED_IN = "OCCURRED_IN"


class NodeType(str, Enum):
    entity    = "entity"
    claim     = "claim"
    issue     = "issue"
    source    = "source"
    event     = "event"
    narrative = "narrative"


# ── Entity ────────────────────────────────────────────────────────────────────

class EntityAliasOut(_OrmBase):
    id:    int
    alias: str

class EntityCreate(BaseModel):
    entity_type:           EntityType
    name:                  str
    canonical_name:        Optional[str]  = None
    description:           Optional[str]  = None
    extra_data:            Optional[str]  = None   # JSON string
    embedding:             Optional[str]  = None   # JSON float array
    resolution_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    merged_into_entity_id: Optional[int]  = None
    aliases:               list[str]      = Field(default_factory=list)

class EntityOut(_OrmBase):
    id:                    int
    entity_type:           EntityType
    name:                  str
    canonical_name:        Optional[str]
    description:           Optional[str]
    extra_data:            Optional[str]
    embedding:             Optional[str]
    resolution_confidence: Optional[float]
    merged_into_entity_id: Optional[int]
    aliases:               list[EntityAliasOut] = Field(default_factory=list)
    created_at:            datetime
    updated_at:            datetime


# ── Source ────────────────────────────────────────────────────────────────────

class SourceCreate(BaseModel):
    url:            str
    title:          Optional[str]      = None
    text:           Optional[str]      = None
    published_at:   Optional[datetime] = None
    content_hash:   str                       # SHA-256 of (url || text)
    source_item_id: Optional[int]      = None # back-ref to existing source_items.id

class SourceOut(_OrmBase):
    id:             int
    url:            str
    title:          Optional[str]
    text:           Optional[str]
    published_at:   Optional[datetime]
    ingested_at:    datetime
    content_hash:   str
    source_item_id: Optional[int]
    created_at:     datetime


# ── Issue ─────────────────────────────────────────────────────────────────────

class IssueCreate(BaseModel):
    name:         str            # normalized slug, e.g. "housing_affordability"
    display_name: str
    description:  Optional[str] = None
    embedding:    Optional[str] = None   # JSON float array

class IssueOut(_OrmBase):
    id:           int
    name:         str
    display_name: str
    description:  Optional[str]
    embedding:    Optional[str]
    created_at:   datetime
    updated_at:   datetime


# ── Event ─────────────────────────────────────────────────────────────────────

class EventCreate(BaseModel):
    name:              str
    event_type:        EventType
    event_timestamp:   Optional[datetime] = None
    description:       Optional[str]      = None
    related_source_id: Optional[int]      = None

class EventOut(_OrmBase):
    id:                int
    name:              str
    event_type:        EventType
    event_timestamp:   Optional[datetime]
    description:       Optional[str]
    related_source_id: Optional[int]
    created_at:        datetime
    updated_at:        datetime


# ── Claim ─────────────────────────────────────────────────────────────────────

class ClaimCreate(BaseModel):
    text:        str
    stance:      Stance = Stance.unknown
    confidence:  float  = Field(0.0, ge=0.0, le=1.0)
    source_id:   int
    semantic_id: Optional[str] = None
    entity_ids:  list[int]     = Field(default_factory=list)
    issue_ids:   list[int]     = Field(default_factory=list)

class ClaimOut(_OrmBase):
    id:           int
    text:         str
    stance:       Stance
    confidence:   float
    source_id:    int
    semantic_id:  Optional[str]
    extracted_at: datetime
    created_at:   datetime
    # Resolved IDs — populated by the query layer
    entity_ids:   list[int] = Field(default_factory=list)
    issue_ids:    list[int] = Field(default_factory=list)

class ClaimDetail(ClaimOut):
    """Extended response with nested objects (use sparingly — heavier query)."""
    source:   Optional[SourceOut] = None
    entities: list[EntityOut]     = Field(default_factory=list)
    issues:   list[IssueOut]      = Field(default_factory=list)


# ── Narrative ─────────────────────────────────────────────────────────────────

class NarrativeCreate(BaseModel):
    label:             str
    description:       Optional[str]      = None
    embedding:         Optional[str]      = None   # JSON float array
    clustering_method: Optional[str]      = None
    velocity_score:    float              = 0.0
    first_seen_at:     Optional[datetime] = None
    last_seen_at:      Optional[datetime] = None
    trigger_event_id:  Optional[int]      = None
    claim_ids:         list[int]          = Field(default_factory=list)

class NarrativeOut(_OrmBase):
    id:                int
    label:             str
    description:       Optional[str]
    embedding:         Optional[str]
    clustering_method: Optional[str]
    velocity_score:    float
    first_seen_at:     Optional[datetime]
    last_seen_at:      Optional[datetime]
    trigger_event_id:  Optional[int]
    created_at:        datetime
    updated_at:        datetime
    # Summary count only — avoid loading all claims in list views
    claim_count:       int = 0

class NarrativeDetail(NarrativeOut):
    """Extended response with nested claims (use for single-narrative views)."""
    trigger_event: Optional[EventOut] = None
    claims:        list[ClaimOut]     = Field(default_factory=list)


# ── Edge ──────────────────────────────────────────────────────────────────────

class EdgeCreate(BaseModel):
    from_type:         NodeType
    from_id:           int
    to_type:           NodeType
    to_id:             int
    relationship_type: RelationshipType
    confidence_score:  Optional[float] = Field(None, ge=0.0, le=1.0)

class EdgeOut(_OrmBase):
    id:                int
    from_type:         NodeType
    from_id:           int
    to_type:           NodeType
    to_id:             int
    relationship_type: RelationshipType
    confidence_score:  Optional[float]
    created_at:        datetime
