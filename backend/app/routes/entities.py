"""Entity detail API — read-only view of a canonical entity.

Powers the frontend `/entities/:id` page that the Activity-This-Week
cards (and any other "click on an entity" surface) link to. Keeps the
KG-policy contract: this is an evidence side-panel, not a graph.

One endpoint:
  GET /api/entities/{canonical_id}
    → entity profile + 7d-vs-14d stats + recent articles + supporting
      quote-anchored claim_records (v15.0+) + narrative frames the
      entity's articles match into.

Alias merging is consistent with `briefing_retrieval.top_entities_for_briefing`:
`person:bresnahan` + `person:auto:rob-bresnahan-jr` collapse to one entity
for counting and listing.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import (
    ClaimRecord,
    ClaimRecordEntity,
    Entity,
    EntityMention,
    NarrativeFrame,
    NarrativeFrameMention,
    Outlet,
    SourceItem,
)
from app.services.briefing_retrieval import _entity_ids_for_canonical
from app.services.source_display import display_source_name, preload_outlets


router = APIRouter(prefix="/entities", tags=["entities"])


def _entity_profile(e: Entity) -> dict:
    return {
        "id": e.canonical_id,
        "name": e.name,
        "type": e.type,
        "affiliation": e.affiliation,
        "description": e.description,
        "mention_count": e.mention_count or 0,
        "source_count": e.source_count or 0,
        "first_seen": e.first_seen.isoformat() if e.first_seen else None,
        "last_seen": e.last_seen.isoformat() if e.last_seen else None,
    }


@router.get("/{canonical_id:path}")
def get_entity(
    canonical_id: str,
    days: int = Query(7, ge=1, le=90),
    articles_limit: int = Query(20, ge=1, le=100),
    quotes_limit: int = Query(15, ge=1, le=100),
    frames_limit: int = Query(8, ge=1, le=30),
    db: Session = Depends(get_db),
):
    """Profile + stats + recent articles + quotes + frames for one entity.

    `days` controls the trailing-window stats (default 7d this-week vs the
    previous 7d). Article/quote/frame lists are not date-windowed — they
    return the most recent N regardless of age.
    """
    seed = (
        db.query(Entity).filter(Entity.canonical_id == canonical_id).one_or_none()
    )
    if not seed:
        raise HTTPException(404, f"entity {canonical_id!r} not found")

    ent_ids = _entity_ids_for_canonical(db, canonical_id)
    if not ent_ids:
        ent_ids = [seed.id]

    now = datetime.utcnow()
    this_start = now - timedelta(days=days)
    prev_start = now - timedelta(days=2 * days)

    def _mention_count(start: datetime, end: datetime | None) -> int:
        q = (
            db.query(func.count(func.distinct(EntityMention.article_id)))
            .join(SourceItem, SourceItem.id == EntityMention.article_id)
            .filter(EntityMention.entity_id.in_(ent_ids))
            .filter(SourceItem.published_at >= start)
        )
        if end is not None:
            q = q.filter(SourceItem.published_at < end)
        return q.scalar() or 0

    mentions_this = _mention_count(this_start, None)
    mentions_prev = _mention_count(prev_start, this_start)

    # ── Recent articles mentioning this entity ───────────────────────
    # Article-level distinct, ordered by published_at desc, regardless of
    # age. The select-distinct-on-article shape is done via a subquery so
    # we get one SourceItem per article even if multiple alias mentions exist.
    article_id_rows = (
        db.query(EntityMention.article_id)
        .filter(EntityMention.entity_id.in_(ent_ids))
        .distinct()
        .all()
    )
    article_ids = [row[0] for row in article_id_rows]
    articles: list[SourceItem] = []
    if article_ids:
        articles = (
            db.query(SourceItem)
            .filter(SourceItem.id.in_(article_ids))
            .filter(SourceItem.published_at.isnot(None))
            .order_by(SourceItem.published_at.desc())
            .limit(articles_limit)
            .all()
        )
    outlet_map = preload_outlets(db, articles)
    articles_payload = [
        {
            "id": a.id,
            "title": a.title,
            "source_url": a.source_url,
            "source_name": display_source_name(a, outlet_map.get(a.outlet_id)),
            "published_at": a.published_at.isoformat() if a.published_at else None,
            "summary": a.summary,
            "sentiment": a.sentiment,
            "race_relevance_score": a.race_relevance_score or 0,
        }
        for a in articles
    ]

    # ── Supporting quotes — v15.0 claim_records mentioning this entity ──
    # Sort by article published_at desc so the newest quotes float to the
    # top, matching how the NarrativeDetail "Supporting quotes" panel reads.
    cr_id_rows = (
        db.query(ClaimRecordEntity.claim_record_id)
        .filter(ClaimRecordEntity.entity_id.in_(ent_ids))
        .all()
    )
    cr_ids = list({rid for (rid,) in cr_id_rows})
    quotes_payload: list[dict] = []
    if cr_ids:
        records = (
            db.query(ClaimRecord)
            .join(SourceItem, SourceItem.id == ClaimRecord.article_id)
            .filter(ClaimRecord.id.in_(cr_ids))
            .filter(SourceItem.published_at.isnot(None))
            .order_by(SourceItem.published_at.desc())
            .limit(quotes_limit)
            .all()
        )
        quote_article_ids = list({r.article_id for r in records})
        quote_articles_by_id = {
            a.id: a for a in
            db.query(SourceItem).filter(SourceItem.id.in_(quote_article_ids)).all()
        }
        quote_outlets = preload_outlets(db, quote_articles_by_id.values())
        for r in records:
            art = quote_articles_by_id.get(r.article_id)
            outlet = quote_outlets.get(art.outlet_id) if art else None
            quotes_payload.append(
                {
                    "id": r.id,
                    "evidence_span": r.evidence_span,
                    "label": r.label,
                    "confidence": r.confidence,
                    "article": {
                        "id": art.id,
                        "title": art.title,
                        "source_url": art.source_url,
                        "source_name": display_source_name(art, outlet),
                        "published_at": (
                            art.published_at.isoformat() if art.published_at else None
                        ),
                    } if art else None,
                }
            )

    # ── Narrative frames this entity's coverage matches ────────────
    # Count frame matches across all articles mentioning the entity, return
    # the top N by overlap volume. Joins the same article id set we already
    # have. Filter to active frames so the page doesn't surface dead frames
    # that haven't been pruned yet.
    frames_payload: list[dict] = []
    if article_ids:
        frame_rows = (
            db.query(
                NarrativeFrame.id,
                NarrativeFrame.name,
                NarrativeFrame.owner_type,
                NarrativeFrame.last_known_stage,
                func.count(func.distinct(NarrativeFrameMention.source_item_id)).label("n"),
            )
            .join(NarrativeFrameMention, NarrativeFrameMention.frame_id == NarrativeFrame.id)
            .filter(NarrativeFrameMention.source_item_id.in_(article_ids))
            .filter(NarrativeFrame.active.is_(True))
            .group_by(
                NarrativeFrame.id,
                NarrativeFrame.name,
                NarrativeFrame.owner_type,
                NarrativeFrame.last_known_stage,
            )
            .order_by(func.count(func.distinct(NarrativeFrameMention.source_item_id)).desc())
            .limit(frames_limit)
            .all()
        )
        frames_payload = [
            {
                "id": fid,
                "name": fname,
                "owner_type": fowner,
                "stage": fstage,
                "article_count": int(n or 0),
            }
            for (fid, fname, fowner, fstage, n) in frame_rows
        ]

    return {
        "entity": _entity_profile(seed),
        "stats": {
            "window_days": days,
            "mentions_this_week": mentions_this,
            "mentions_last_week": mentions_prev,
            "delta": mentions_this - mentions_prev,
            "total_articles": len(article_ids),
            "total_quotes": len(cr_ids),
        },
        "recent_articles": articles_payload,
        "supporting_quotes": quotes_payload,
        "narrative_frames": frames_payload,
    }
