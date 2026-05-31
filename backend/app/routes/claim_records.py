"""v15.0 claim-record API — quote-anchored claims per entity.

This is the new shape (v15.0+) — distinct from the legacy /claims endpoint
which serves triple-shaped Claim rows. Claim records are verbatim quote
spans tagged with the entities that appear in them. No predicates, no
subject/object directionality — the quote text itself is the source of
truth.

Endpoints:
  GET /api/claim-records?entity=person:cognetti
    → all claim_records involving that canonical entity, with article info
  GET /api/claim-records/{id}
    → single record full view (for inspector modal)
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ClaimRecord, ClaimRecordEntity, Entity, Outlet, SourceItem
from app.services.source_display import display_source_name, preload_outlets


router = APIRouter(prefix="/claim-records", tags=["claim-records"])


def _entity_summary(e: Entity) -> dict:
    return {
        "id": e.canonical_id,
        "name": e.name,
        "type": e.type,
        "affiliation": e.affiliation,
    }


def _record_payload(
    cr: ClaimRecord,
    entities: list[Entity],
    article: Optional[SourceItem],
    outlet: Optional[Outlet] = None,
) -> dict:
    return {
        "id": cr.id,
        "article_id": cr.article_id,
        "evidence_span": cr.evidence_span,
        "evidence_start_char": cr.evidence_start_char,
        "evidence_end_char": cr.evidence_end_char,
        "evidence_hash": cr.evidence_hash,
        "label": cr.label,
        "confidence": cr.confidence,
        "extractor_version": cr.extractor_version,
        "created_at": cr.created_at.isoformat() if cr.created_at else None,
        "entities": [_entity_summary(e) for e in entities],
        "article": {
            "id": article.id,
            "title": article.title,
            "source_url": article.source_url,
            "source_name": display_source_name(article, outlet),
            "published_at": (
                article.published_at.isoformat() if article.published_at else None
            ),
        } if article else None,
    }


@router.get("")
def list_claim_records(
    entity: Optional[str] = Query(
        None, description="Canonical entity ID (e.g. 'person:cognetti'). "
                          "Returns all claim_records involving this entity."
    ),
    limit: int = Query(200, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    """List claim_records, optionally filtered by entity.

    For entity-anchored UI views: pass `entity=person:cognetti` to get
    every quote-anchored claim that mentions Cognetti, with article + other
    entities included. Sorted by article publish date desc, newest first.
    """
    if not entity:
        # Without an entity filter, return the most recent records globally
        records = (
            db.query(ClaimRecord)
            .order_by(ClaimRecord.id.desc())
            .limit(limit)
            .all()
        )
    else:
        ent = db.query(Entity).filter(Entity.canonical_id == entity).one_or_none()
        if not ent:
            raise HTTPException(404, f"entity {entity!r} not found")
        # All claim_records that include this entity
        record_ids = (
            db.query(ClaimRecordEntity.claim_record_id)
            .filter(ClaimRecordEntity.entity_id == ent.id)
            .all()
        )
        rid_set = [rid for (rid,) in record_ids]
        if not rid_set:
            return {"entity": _entity_summary(ent), "count": 0, "records": []}
        records = (
            db.query(ClaimRecord)
            .filter(ClaimRecord.id.in_(rid_set))
            .order_by(ClaimRecord.id.desc())
            .limit(limit)
            .all()
        )

    # Batch-load entities + articles for the returned records
    rec_ids = [r.id for r in records]
    article_ids = list({r.article_id for r in records})
    articles_by_id = {
        a.id: a for a in
        db.query(SourceItem).filter(SourceItem.id.in_(article_ids)).all()
    }
    outlets_map = preload_outlets(db, articles_by_id.values())
    # entity-junction
    junction_rows = (
        db.query(ClaimRecordEntity)
        .filter(ClaimRecordEntity.claim_record_id.in_(rec_ids))
        .all()
    )
    entity_ids_used = list({j.entity_id for j in junction_rows})
    entities_by_id = {
        e.id: e for e in
        db.query(Entity).filter(Entity.id.in_(entity_ids_used)).all()
    }
    per_record_entities: dict[int, list[Entity]] = {}
    for j in junction_rows:
        ent = entities_by_id.get(j.entity_id)
        if ent:
            per_record_entities.setdefault(j.claim_record_id, []).append(ent)

    payload = []
    for r in records:
        art = articles_by_id.get(r.article_id)
        outlet = outlets_map.get(art.outlet_id) if art else None
        payload.append(
            _record_payload(r, per_record_entities.get(r.id, []), art, outlet)
        )

    if entity:
        ent = db.query(Entity).filter(Entity.canonical_id == entity).one()
        return {
            "entity": _entity_summary(ent),
            "count": len(payload),
            "records": payload,
        }
    return {"count": len(payload), "records": payload}


@router.get("/{record_id}")
def get_claim_record(record_id: int, db: Session = Depends(get_db)):
    """Single claim_record full view."""
    cr = db.query(ClaimRecord).filter(ClaimRecord.id == record_id).one_or_none()
    if not cr:
        raise HTTPException(404, f"claim_record {record_id} not found")
    article = db.query(SourceItem).filter(SourceItem.id == cr.article_id).first()
    junction = (
        db.query(ClaimRecordEntity)
        .filter(ClaimRecordEntity.claim_record_id == cr.id)
        .all()
    )
    entity_ids = [j.entity_id for j in junction]
    entities = (
        db.query(Entity).filter(Entity.id.in_(entity_ids)).all()
        if entity_ids else []
    )
    outlet = db.get(Outlet, article.outlet_id) if article and article.outlet_id else None
    return _record_payload(cr, entities, article, outlet)
