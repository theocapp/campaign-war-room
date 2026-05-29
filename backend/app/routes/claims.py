"""Claim-layer API — inspect, retract, and audit individual claims.

The claim is the unit between extraction and the entity-relation graph.
This endpoint exposes claims directly so the UI / external consumers can:
  - inspect the full provenance of a single (subject, predicate, object) fact
  - see all articles supporting AND contesting the claim
  - mark a claim as retracted (e.g., after human review found it wrong)

Why this exists: when a relation looks wrong (e.g., "Bresnahan endorses ACA"),
the user needs to see EVERY article that produced it, choose which articles
were misreads, and either retract the claim entirely or contest specific
supporting articles. Doing that on the flat entity_relations row required
diff-checking JSON arrays; with claims + claim_supports it's normal SQL.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Claim, ClaimSupport, Entity, SourceItem


router = APIRouter(prefix="/claims", tags=["claims"])


class RetractRequest(BaseModel):
    reason: Optional[str] = None
    by: Optional[str] = None


def _entity_summary(e: Entity) -> dict:
    return {
        "id": e.canonical_id,
        "name": e.name,
        "type": e.type,
        "affiliation": e.affiliation,
    }


@router.get("/{claim_id}")
def get_claim(claim_id: int, db: Session = Depends(get_db)):
    """Full claim view: triple + dimensions + lifecycle + all supporting
    and contesting articles with their per-article evidence."""
    claim = db.query(Claim).filter(Claim.id == claim_id).one_or_none()
    if not claim:
        raise HTTPException(404, f"claim {claim_id} not found")
    subj = db.query(Entity).filter(Entity.id == claim.subject_id).one_or_none()
    obj = db.query(Entity).filter(Entity.id == claim.object_id).one_or_none()
    if not subj or not obj:
        raise HTTPException(500, "claim references missing entities")

    supports = db.query(ClaimSupport).filter(ClaimSupport.claim_id == claim.id).all()
    article_ids = [s.article_id for s in supports]
    articles = (
        {a.id: a for a in db.query(SourceItem).filter(SourceItem.id.in_(article_ids)).all()}
        if article_ids else {}
    )

    from app.services.source_display import display_source_name, preload_outlets
    outlets_map = preload_outlets(db, articles.values())

    supporting = []
    contesting = []
    for s in supports:
        art = articles.get(s.article_id)
        row = {
            "article_id": s.article_id,
            "article_title": art.title if art else None,
            "article_url": art.source_url if art else None,
            "outlet": (display_source_name(art, outlets_map.get(art.outlet_id)) if art else None),
            "published_at": art.published_at.isoformat() if art and art.published_at else None,
            "sample_quote": s.sample_quote,
            "confidence": s.confidence,
            "extractor_version": s.extractor_version,
            "extracted_at": s.extracted_at.isoformat() if s.extracted_at else None,
            "stance": s.stance,
        }
        if s.stance == "contesting":
            contesting.append(row)
        else:
            supporting.append(row)

    supporting.sort(key=lambda r: r.get("published_at") or "", reverse=True)
    contesting.sort(key=lambda r: r.get("published_at") or "", reverse=True)

    return {
        "claim": {
            "id": claim.id,
            "subject": _entity_summary(subj),
            "predicate": claim.predicate,
            "object": _entity_summary(obj),
            "stance": {
                "procedural": claim.procedural,
                "rhetorical": claim.rhetorical,
                "ideological": claim.ideological,
            },
            "status": claim.status,
            "retracted_at": claim.retracted_at.isoformat() if claim.retracted_at else None,
            "retracted_by": claim.retracted_by,
            "retracted_reason": claim.retracted_reason,
            "first_seen": claim.first_seen.isoformat() if claim.first_seen else None,
            "last_seen": claim.last_seen.isoformat() if claim.last_seen else None,
            "sample_quote": claim.sample_quote,
            "confidence": claim.confidence,
            "extractor_version": claim.extractor_version,
            "supporting_count": len(supporting),
            "contesting_count": len(contesting),
        },
        "supporting_articles": supporting,
        "contesting_articles": contesting,
    }


@router.post("/{claim_id}/retract")
def retract_claim(claim_id: int, body: RetractRequest, db: Session = Depends(get_db)):
    """Mark a claim as retracted. Doesn't delete the claim or its supporting
    article rows (preserves the audit trail) — just flips status so derived
    views can filter it out."""
    from datetime import datetime
    claim = db.query(Claim).filter(Claim.id == claim_id).one_or_none()
    if not claim:
        raise HTTPException(404, f"claim {claim_id} not found")
    claim.status = "retracted"
    claim.retracted_at = datetime.utcnow()
    claim.retracted_by = body.by or "user"
    claim.retracted_reason = body.reason
    db.commit()
    return {"ok": True, "claim_id": claim.id, "status": claim.status}


@router.post("/{claim_id}/reactivate")
def reactivate_claim(claim_id: int, db: Session = Depends(get_db)):
    """Reverse a retraction — set status back to 'active'."""
    claim = db.query(Claim).filter(Claim.id == claim_id).one_or_none()
    if not claim:
        raise HTTPException(404, f"claim {claim_id} not found")
    claim.status = "active"
    claim.retracted_at = None
    claim.retracted_by = None
    claim.retracted_reason = None
    db.commit()
    return {"ok": True, "claim_id": claim.id, "status": claim.status}
