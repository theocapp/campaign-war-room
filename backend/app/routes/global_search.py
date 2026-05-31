"""Universal search endpoints powering the global header search bar.

The legacy `/api/search` route (in sources.py) returns articles via FTS.
This module adds four sibling endpoints so the header dropdown can
surface every NOCTUA primitive in one go:

  GET /api/search/entities     — canonical people, orgs, bills, locations
  GET /api/search/quotes       — verbatim claim_records (v15.0 quote corpus)
  GET /api/search/outlets      — publishers from the outlets table
  GET /api/search/suggestions  — empty-state "try searching" tour: top
                                 entities/outlets/frames + a quote sample
                                 ranked by last-7-day activity

The first three are autocomplete-shaped: small `limit`, fast queries,
no LLM. The suggestions endpoint is called once on dropdown focus.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import (
    ClaimRecord,
    Entity,
    EntityMention,
    NarrativeFrame,
    NarrativeFrameMention,
    Outlet,
    SourceItem,
)
from app.services.source_display import display_source_name, preload_outlets

router = APIRouter()


# ─── Schemas ──────────────────────────────────────────────────────────────

class EntitySearchHit(BaseModel):
    id: int
    canonical_id: str
    name: str
    type: str  # person | organization | bill | event | location | issue
    affiliation: Optional[str] = None
    mention_count: int = 0
    source_count: int = 0


class QuoteSearchHit(BaseModel):
    id: int
    evidence_span: str
    label: Optional[str] = None
    article_id: int
    article_title: Optional[str] = None
    source_name: str


class OutletSearchHit(BaseModel):
    id: int
    name: str
    domain: str
    outlet_type: str
    city: Optional[str] = None
    state: Optional[str] = None
    authority_score: int


# Suggestion sub-shapes carry just enough for a clickable example row.
# They deliberately differ from the *SearchHit shapes above so the
# frontend can render them without forcing a typecast on the union.

class FrameSuggestion(BaseModel):
    id: int
    name: str
    owner_type: Optional[str] = None
    mentions_this_week: int


class EntitySuggestion(BaseModel):
    id: int
    canonical_id: str
    name: str
    type: str
    affiliation: Optional[str] = None
    mentions_this_week: int


class OutletSuggestion(BaseModel):
    id: int
    name: str
    domain: str
    articles_this_week: int


class QuoteSuggestion(BaseModel):
    id: int
    evidence_span: str
    article_id: int
    source_name: str


class SearchSuggestions(BaseModel):
    entities: list[EntitySuggestion]
    outlets: list[OutletSuggestion]
    frames: list[FrameSuggestion]
    quotes: list[QuoteSuggestion]


# ─── Entities ─────────────────────────────────────────────────────────────

@router.get("/search/entities", response_model=list[EntitySearchHit])
def search_entities(
    q: str = Query(..., min_length=1, description="Name or alias substring"),
    limit: int = Query(8, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Substring match against entities.name and the JSON-encoded aliases
    column. Aliases is stored as a JSON list of strings — for the dropdown,
    a plain ILIKE against the raw text is good enough (a hit inside
    `["Paige Cognetti", "Mayor Cognetti"]` still matches on "cognetti").
    """
    term = q.strip()
    if not term:
        return []
    pattern = f"%{term}%"
    rows = (
        db.query(Entity)
        .filter(or_(Entity.name.ilike(pattern), Entity.aliases.ilike(pattern)))
        .order_by(Entity.mention_count.desc().nullslast(), Entity.name)
        .limit(limit)
        .all()
    )
    return [
        EntitySearchHit(
            id=e.id,
            canonical_id=e.canonical_id,
            name=e.name,
            type=e.type,
            affiliation=e.affiliation,
            mention_count=e.mention_count or 0,
            source_count=e.source_count or 0,
        )
        for e in rows
    ]


# ─── Quotes (v15.0 claim_records) ─────────────────────────────────────────

@router.get("/search/quotes", response_model=list[QuoteSearchHit])
def search_quotes(
    q: str = Query(..., min_length=1, description="Phrase to find inside quote spans"),
    limit: int = Query(8, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Substring match against the verbatim quote text. ClaimRecord has
    ~3.8k rows today — ILIKE is fast enough without a dedicated tsvector
    index. If/when the corpus grows past ~50k records, swap this for a
    GIN-indexed tsvector column via an Alembic migration.

    Results are joined with the source article + outlet so the dropdown
    can render `"<quote snippet>" — Article Title · Publisher`.
    """
    term = q.strip()
    if not term:
        return []
    pattern = f"%{term}%"

    rows = (
        db.query(ClaimRecord, SourceItem)
        .join(SourceItem, ClaimRecord.article_id == SourceItem.id)
        .filter(ClaimRecord.evidence_span.ilike(pattern))
        .order_by(ClaimRecord.created_at.desc().nullslast())
        .limit(limit)
        .all()
    )

    # Batch-load outlets so display_source_name() doesn't do N+1 queries.
    items = [si for _, si in rows]
    outlet_map = preload_outlets(db, items)

    return [
        QuoteSearchHit(
            id=cr.id,
            evidence_span=cr.evidence_span,
            label=cr.label,
            article_id=si.id,
            article_title=si.title,
            source_name=display_source_name(si, outlet_map.get(si.outlet_id) if si.outlet_id else None),
        )
        for cr, si in rows
    ]


# ─── Outlets ──────────────────────────────────────────────────────────────

@router.get("/search/outlets", response_model=list[OutletSearchHit])
def search_outlets(
    q: str = Query(..., min_length=1, description="Outlet name or domain substring"),
    limit: int = Query(8, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Substring match against outlets.name and outlets.domain. Returns
    active outlets only, ranked by authority_score so e.g. "Times" surfaces
    the New York Times before a one-blogger Pennsylvania outlet."""
    term = q.strip()
    if not term:
        return []
    pattern = f"%{term}%"
    rows = (
        db.query(Outlet)
        .filter(Outlet.active == True)  # noqa: E712
        .filter(or_(Outlet.name.ilike(pattern), Outlet.domain.ilike(pattern)))
        .order_by(Outlet.authority_score.desc(), Outlet.name)
        .limit(limit)
        .all()
    )
    return [
        OutletSearchHit(
            id=o.id,
            name=o.name,
            domain=o.domain,
            outlet_type=o.outlet_type,
            city=o.city,
            state=o.state,
            authority_score=o.authority_score,
        )
        for o in rows
    ]


# ─── Empty-state suggestions ──────────────────────────────────────────────

@router.get("/search/suggestions", response_model=SearchSuggestions)
def search_suggestions(
    per_type: int = Query(3, ge=1, le=10),
    db: Session = Depends(get_db),
):
    """One-shot bundle for the empty-state dropdown — "try searching" tour.

    Returns top-N (default 3) entities, outlets, narrative frames, and a
    sample of recent quotes, ranked by last-7-day activity so the
    suggestions feel fresh on every page load. Designed to be cheap
    enough to call on every focus — no LLM, just aggregate queries.

    Ranking signal: for entities and outlets we count their joined
    SourceItem rows where `published_at` falls in the last 7 days; for
    frames we count their NarrativeFrameMention rows in that window;
    for quotes we pull the most recent N records that have an attached
    article (so the row is clickable). All ties broken by all-time
    mention_count so we never return rows with all-zero ranking values.
    """
    cutoff = datetime.utcnow() - timedelta(days=7)

    # Top entities by 7-day article count.
    entity_counts = (
        db.query(
            EntityMention.entity_id.label("entity_id"),
            func.count(func.distinct(EntityMention.article_id)).label("week_count"),
        )
        .join(SourceItem, EntityMention.article_id == SourceItem.id)
        .filter(SourceItem.published_at >= cutoff)
        .group_by(EntityMention.entity_id)
        .subquery()
    )
    entity_rows = (
        db.query(Entity, entity_counts.c.week_count)
        .outerjoin(entity_counts, Entity.id == entity_counts.c.entity_id)
        .order_by(
            entity_counts.c.week_count.desc().nullslast(),
            Entity.mention_count.desc().nullslast(),
        )
        .limit(per_type)
        .all()
    )
    entities = [
        EntitySuggestion(
            id=e.id, canonical_id=e.canonical_id, name=e.name, type=e.type,
            affiliation=e.affiliation, mentions_this_week=int(wc or 0),
        )
        for e, wc in entity_rows
    ]

    # Top outlets by 7-day article count. Active outlets only. We
    # intentionally restrict to local_news + regional_news because the
    # campaign user cares about the press covering THEIR race — national
    # aggregators (Independent UK, Fox News, etc.) dominate purely by
    # volume and crowd out the publications that matter for narrative
    # tracking. If a future product wants the national view it should
    # be a separate endpoint or a query parameter.
    outlet_counts = (
        db.query(
            SourceItem.outlet_id.label("outlet_id"),
            func.count(SourceItem.id).label("week_count"),
        )
        .filter(SourceItem.outlet_id.isnot(None))
        .filter(SourceItem.published_at >= cutoff)
        .group_by(SourceItem.outlet_id)
        .subquery()
    )
    outlet_rows = (
        db.query(Outlet, outlet_counts.c.week_count)
        .join(outlet_counts, Outlet.id == outlet_counts.c.outlet_id)
        .filter(Outlet.active == True)  # noqa: E712
        .filter(Outlet.outlet_type.in_(["local_news", "regional_news"]))
        .order_by(
            outlet_counts.c.week_count.desc().nullslast(),
            Outlet.authority_score.desc(),
        )
        .limit(per_type)
        .all()
    )
    outlets = [
        OutletSuggestion(
            id=o.id, name=o.name, domain=o.domain, articles_this_week=int(wc or 0),
        )
        for o, wc in outlet_rows
    ]

    # Top narrative frames by 7-day mention count.
    frame_counts = (
        db.query(
            NarrativeFrameMention.frame_id.label("frame_id"),
            func.count(NarrativeFrameMention.id).label("week_count"),
        )
        .join(SourceItem, NarrativeFrameMention.source_item_id == SourceItem.id)
        .filter(SourceItem.published_at >= cutoff)
        .group_by(NarrativeFrameMention.frame_id)
        .subquery()
    )
    frame_rows = (
        db.query(NarrativeFrame, frame_counts.c.week_count)
        .join(frame_counts, NarrativeFrame.id == frame_counts.c.frame_id)
        .order_by(frame_counts.c.week_count.desc())
        .limit(per_type)
        .all()
    )
    frames = [
        FrameSuggestion(
            id=f.id, name=f.name, owner_type=f.owner_type,
            mentions_this_week=int(wc or 0),
        )
        for f, wc in frame_rows
    ]

    # Sample quotes — most-recent N records that have an article AND a
    # non-null label. The label gate filters out plain "statement" /
    # quote-from-source rows in favor of attack / endorsement / vote /
    # policy_position records — the ones that actually look interesting
    # in a one-line preview. Recency is the closest meaningful "trending"
    # signal for individual quotes without a quote-clustering layer.
    quote_rows = (
        db.query(ClaimRecord, SourceItem)
        .join(SourceItem, ClaimRecord.article_id == SourceItem.id)
        .filter(ClaimRecord.label.isnot(None))
        .filter(ClaimRecord.label != "statement")
        .order_by(ClaimRecord.created_at.desc().nullslast())
        .limit(per_type)
        .all()
    )
    # If labeled-quote pool is empty (small DBs / fresh deployments),
    # fall back to any recent quote so the suggestions section isn't blank.
    if not quote_rows:
        quote_rows = (
            db.query(ClaimRecord, SourceItem)
            .join(SourceItem, ClaimRecord.article_id == SourceItem.id)
            .order_by(ClaimRecord.created_at.desc().nullslast())
            .limit(per_type)
            .all()
        )
    quote_outlets = preload_outlets(db, [si for _, si in quote_rows])
    quotes = [
        QuoteSuggestion(
            id=cr.id,
            evidence_span=cr.evidence_span,
            article_id=si.id,
            source_name=display_source_name(si, quote_outlets.get(si.outlet_id) if si.outlet_id else None),
        )
        for cr, si in quote_rows
    ]

    return SearchSuggestions(
        entities=entities, outlets=outlets, frames=frames, quotes=quotes,
    )
