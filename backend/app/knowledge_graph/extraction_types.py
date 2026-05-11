"""
Pydantic models for the knowledge graph extraction pipeline.

Two layers:
  Raw*     — mirrors what the LLM is asked to return (permissive, lenient validators)
  Validated* / ExtractionResult — post-validation, safe to map into the kg_* DB schema
"""
from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel, Field, field_validator


# ── Enums (string literals, not Python Enum, so Pydantic coerces them cheaply) ─

VALID_ENTITY_TYPES = {"PERSON", "ORG", "ISSUE", "PLACE"}
VALID_EVENT_TYPES  = {"DEBATE", "SCANDAL", "POLICY", "SPEECH", "VOTE"}
VALID_STANCES      = {"support", "oppose", "neutral", "unknown"}


# ── Raw LLM output models ─────────────────────────────────────────────────────
# Lenient validators: coerce where safe, drop/default where invalid.

class RawExtractedEntity(BaseModel):
    type:                     str
    name:                     str
    canonical_name_candidate: Optional[str] = None

    @field_validator("type", mode="before")
    @classmethod
    def _normalize_type(cls, v: object) -> str:
        s = str(v).strip().upper()
        return s if s in VALID_ENTITY_TYPES else "PERSON"

    @field_validator("name", "canonical_name_candidate", mode="before")
    @classmethod
    def _strip(cls, v: object) -> Optional[str]:
        return str(v).strip() if v is not None else None


class RawExtractedIssue(BaseModel):
    slug:         str
    display_name: str

    @field_validator("slug", mode="before")
    @classmethod
    def _normalize_slug(cls, v: object) -> str:
        s = re.sub(r"[^a-z0-9]+", "_", str(v).strip().lower()).strip("_")
        return s or "unknown_issue"

    @field_validator("display_name", mode="before")
    @classmethod
    def _strip(cls, v: object) -> str:
        return str(v).strip()


class RawExtractedEvent(BaseModel):
    name:            str
    type:            str
    event_timestamp: Optional[str] = None
    description:     Optional[str] = None

    @field_validator("type", mode="before")
    @classmethod
    def _normalize_type(cls, v: object) -> str:
        s = str(v).strip().upper()
        return s if s in VALID_EVENT_TYPES else "POLICY"

    @field_validator("name", "description", mode="before")
    @classmethod
    def _strip(cls, v: object) -> Optional[str]:
        return str(v).strip() if v is not None else None

    @field_validator("event_timestamp", mode="before")
    @classmethod
    def _coerce_null(cls, v: object) -> Optional[str]:
        if v in (None, "null", "None", ""):
            return None
        return str(v).strip()


class RawExtractedClaim(BaseModel):
    text:         str
    stance:       str   = "unknown"
    confidence:   float = Field(0.5, ge=0.0, le=1.0)
    entity_names: list[str] = Field(default_factory=list)
    issue_names:  list[str] = Field(default_factory=list)
    event_names:  list[str] = Field(default_factory=list)

    @field_validator("text", mode="before")
    @classmethod
    def _strip_text(cls, v: object) -> str:
        return str(v).strip()

    @field_validator("stance", mode="before")
    @classmethod
    def _normalize_stance(cls, v: object) -> str:
        s = str(v).strip().lower()
        return s if s in VALID_STANCES else "unknown"

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp(cls, v: object) -> float:
        try:
            f = float(v)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            return 0.5
        return max(0.0, min(1.0, f))

    @field_validator("entity_names", "issue_names", "event_names", mode="before")
    @classmethod
    def _coerce_str_list(cls, v: object) -> list[str]:
        if not isinstance(v, list):
            return []
        return [str(x).strip() for x in v if x]


class RawExtractionPayload(BaseModel):
    """Direct representation of the LLM's JSON response."""
    claims:   list[RawExtractedClaim]  = Field(default_factory=list)
    entities: list[RawExtractedEntity] = Field(default_factory=list)
    issues:   list[RawExtractedIssue]  = Field(default_factory=list)
    events:   list[RawExtractedEvent]  = Field(default_factory=list)


# ── Validated output (post groundedness + cross-reference checks) ─────────────

class ValidatedClaim(BaseModel):
    text:         str
    stance:       str
    confidence:   float
    entity_names: list[str]  # names confirmed present in entities list
    issue_slugs:  list[str]  # normalized slugs, confirmed present in issues list
    event_names:  list[str]  # names confirmed present in events list


class ExtractionResult(BaseModel):
    """
    Clean extraction result, safe to map directly into the kg_* DB schema.

    Guarantees:
    - Every entity_name in claims exists in the entities array
    - Every issue_slug in claims corresponds to an issue in the issues array
    - Every event_name in claims exists in the events array
    - All entity names passed the groundedness check (present in source text)
    """
    claims:           list[ValidatedClaim]     = Field(default_factory=list)
    entities:         list[RawExtractedEntity] = Field(default_factory=list)
    issues:           list[RawExtractedIssue]  = Field(default_factory=list)
    events:           list[RawExtractedEvent]  = Field(default_factory=list)
    # Diagnostic counters (not stored in DB)
    dropped_claims:   int = 0
    dropped_entities: int = 0
