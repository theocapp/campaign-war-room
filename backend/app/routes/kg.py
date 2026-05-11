import logging
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.knowledge_graph.narrative_engine import get_emerging_narratives, get_active_alerts
from app.knowledge_graph.orm import (
    KGAlert, KGClaim, KGEntity, KGNarrative, KGNarrativeClaim, KGSource,
    KGClaimEntity,
)
from app.models import SourceItem

log = logging.getLogger(__name__)

router = APIRouter(prefix="/kg")


# ── Response models ───────────────────────────────────────────────────────────

class KGSourceOut(BaseModel):
    id: int
    url: str
    title: str | None
    source_type: str | None
    source_name: str | None
    domain: str | None
    credibility_score: float
    verified_official: bool
    published_at: datetime | None

    class Config:
        from_attributes = True


class KGClaimOut(BaseModel):
    id: int
    text: str
    stance: str
    confidence: float
    source: KGSourceOut | None

    class Config:
        from_attributes = True


class KGEntityOut(BaseModel):
    id: int
    entity_type: str
    name: str
    canonical_name: str | None
    description: str | None

    class Config:
        from_attributes = True


class KGNarrativeSummary(BaseModel):
    id: int
    label: str
    description: str | None
    velocity_score: float
    status: str
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    claim_count: int
    source_count: int

    class Config:
        from_attributes = True


class KGNarrativeDetailOut(BaseModel):
    id: int
    label: str
    description: str | None
    velocity_score: float
    status: str
    first_seen_at: datetime | None
    last_seen_at: datetime | None
    claims: list[KGClaimOut]
    top_entities: list[KGEntityOut]

    class Config:
        from_attributes = True


class KGAlertOut(BaseModel):
    id: int
    narrative_id: int
    narrative_label: str
    alert_type: str
    severity_score: float
    message: str
    created_at: datetime
    resolved_at: datetime | None

    class Config:
        from_attributes = True


# ── Narratives ────────────────────────────────────────────────────────────────

class EmergingNarrativeOut(BaseModel):
    id: int
    label: str
    velocity: float
    score: float
    unique_sources: int
    unique_entities: int


@router.get("/narratives/emerging", response_model=list[EmergingNarrativeOut])
def list_emerging_narratives(limit: int = 20, db: Session = Depends(get_db)):
    narratives = get_emerging_narratives(db, limit=limit)
    return [
        EmergingNarrativeOut(
            id=n.narrative.id,
            label=n.narrative.label,
            velocity=n.narrative.velocity_score or 0.0,
            score=n.score,
            unique_sources=n.unique_sources,
            unique_entities=n.unique_entities,
        )
        for n in narratives
    ]


@router.get("/narratives/{narrative_id}", response_model=KGNarrativeDetailOut)
def get_narrative_detail(narrative_id: int, db: Session = Depends(get_db)):
    narrative = db.get(KGNarrative, narrative_id)
    if not narrative:
        raise HTTPException(status_code=404, detail="Narrative not found")

    # Fetch linked claims with sources
    claim_rows = (
        db.query(KGClaim)
        .join(KGNarrativeClaim, KGNarrativeClaim.claim_id == KGClaim.id)
        .filter(KGNarrativeClaim.narrative_id == narrative_id)
        .order_by(KGClaim.confidence.desc())
        .limit(50)
        .all()
    )

    claims_out = []
    for claim in claim_rows:
        src = db.get(KGSource, claim.source_id) if claim.source_id else None
        src_out = None
        if src:
            src_out = KGSourceOut(
                id=src.id,
                url=src.url,
                title=src.title,
                source_type=src.source_type,
                source_name=src.source_name,
                domain=src.domain,
                credibility_score=src.credibility_score or 0.5,
                verified_official=bool(src.verified_official),
                published_at=src.published_at,
            )
        claims_out.append(KGClaimOut(
            id=claim.id,
            text=claim.text,
            stance=claim.stance,
            confidence=claim.confidence,
            source=src_out,
        ))

    # Top entities via claim → entity links
    entity_ids: dict[int, int] = {}  # entity_id → mention count
    for claim in claim_rows:
        for link in claim.entity_links:
            entity_ids[link.entity_id] = entity_ids.get(link.entity_id, 0) + 1
    top_ids = sorted(entity_ids, key=lambda eid: entity_ids[eid], reverse=True)[:10]
    entities_out = []
    for eid in top_ids:
        ent = db.get(KGEntity, eid)
        if ent:
            entities_out.append(KGEntityOut(
                id=ent.id,
                entity_type=ent.entity_type,
                name=ent.name,
                canonical_name=ent.canonical_name,
                description=ent.description,
            ))

    return KGNarrativeDetailOut(
        id=narrative.id,
        label=narrative.label,
        description=narrative.description,
        velocity_score=narrative.velocity_score or 0.0,
        status=narrative.status,
        first_seen_at=narrative.first_seen_at,
        last_seen_at=narrative.last_seen_at,
        claims=claims_out,
        top_entities=entities_out,
    )


# ── Entities ──────────────────────────────────────────────────────────────────

@router.get("/entities/{entity_id}", response_model=KGEntityOut)
def get_entity(entity_id: int, db: Session = Depends(get_db)):
    ent = db.get(KGEntity, entity_id)
    if not ent:
        raise HTTPException(status_code=404, detail="Entity not found")
    return KGEntityOut(
        id=ent.id,
        entity_type=ent.entity_type,
        name=ent.name,
        canonical_name=ent.canonical_name,
        description=ent.description,
    )


# ── Alerts ────────────────────────────────────────────────────────────────────

@router.get("/alerts", response_model=list[KGAlertOut])
def list_active_alerts(limit: int = 50, db: Session = Depends(get_db)):
    alerts = get_active_alerts(db, limit=limit)
    return [
        KGAlertOut(
            id=a.id,
            narrative_id=a.narrative_id,
            narrative_label=a.narrative.label if a.narrative else "",
            alert_type=a.alert_type,
            severity_score=a.severity_score,
            message=a.message,
            created_at=a.created_at,
            resolved_at=a.resolved_at,
        )
        for a in alerts
    ]


class BackfillResult(BaseModel):
    queued: int
    skipped_archived: int
    skipped_empty: int
    message: str


def _backfill_worker(source_item_ids: list[int]) -> None:
    """Run KG pipeline on a batch of SourceItem IDs in a background thread."""
    import os
    os.environ.setdefault("ENABLE_KG_PIPELINE", "1")

    from app.db import SessionLocal
    from app.services.ingestion import _run_kg_pipeline

    db = SessionLocal()
    try:
        processed = 0
        for sid in source_item_ids:
            item = db.get(SourceItem, sid)
            if item is None:
                continue
            try:
                _run_kg_pipeline(db, item)
                processed += 1
            except Exception as exc:
                log.error("KG backfill: failed for source_item_id=%d: %s", sid, exc)
        log.info("KG backfill: processed %d/%d items", processed, len(source_item_ids))
    finally:
        db.close()


@router.post("/backfill", response_model=BackfillResult)
def backfill_kg_extraction(
    background_tasks: BackgroundTasks,
    relevance_min: float = 0.0,
    limit: int = 500,
    db: Session = Depends(get_db),
):
    """
    Re-run KG extraction on existing SourceItems that have not yet produced
    KGClaims (or produced very few).  Runs asynchronously in a background task.

    Query params:
      relevance_min  — only process items with race_relevance_score >= this value
                       (default 0.0 = all non-archived items)
      limit          — max items to queue per call (default 500)
    """
    import os
    if os.environ.get("ENABLE_KG_PIPELINE", "").lower() not in ("1", "true", "yes"):
        raise HTTPException(
            status_code=400,
            detail="ENABLE_KG_PIPELINE is not set. Set it to '1' to enable the KG pipeline.",
        )

    # Find SourceItems not yet in kg_sources (or with 0 claims) for high/med relevance
    existing_source_ids: set[int] = {
        row.source_item_id
        for row in db.query(KGSource.source_item_id).filter(KGSource.source_item_id.isnot(None)).all()
    }

    query = (
        db.query(SourceItem)
        .filter(
            SourceItem.archived_as_irrelevant.is_(False),
        )
    )
    if relevance_min > 0:
        query = query.filter(SourceItem.race_relevance_score >= relevance_min)

    all_items = query.limit(limit * 3).all()  # fetch extra so we can filter

    to_process: list[int] = []
    skipped_archived = 0
    skipped_empty = 0

    for item in all_items:
        if len(to_process) >= limit:
            break
        if item.archived_as_irrelevant:
            skipped_archived += 1
            continue
        text = (item.raw_text or item.title or "").strip()
        if not text:
            skipped_empty += 1
            continue
        to_process.append(item.id)

    if to_process:
        background_tasks.add_task(_backfill_worker, to_process)

    return BackfillResult(
        queued=len(to_process),
        skipped_archived=skipped_archived,
        skipped_empty=skipped_empty,
        message=(
            f"Queued {len(to_process)} items for KG re-extraction in background. "
            f"Skipped {skipped_archived} archived and {skipped_empty} empty items."
        ),
    )


@router.get("/stats", response_model=dict)
def kg_stats(db: Session = Depends(get_db)):
    """Return high-level KG claim/entity/source counts for debugging."""
    claim_count = db.query(KGClaim).count()
    entity_count = db.query(KGEntity).count()
    source_count = db.query(KGSource).count()
    narrative_count = db.query(KGNarrative).count()

    # Claims per source (top 10)
    from sqlalchemy import func
    top_sources = (
        db.query(KGSource.title, KGSource.source_name, func.count(KGClaim.id).label("claims"))
        .outerjoin(KGClaim, KGClaim.source_id == KGSource.id)
        .group_by(KGSource.id)
        .order_by(func.count(KGClaim.id).desc())
        .limit(10)
        .all()
    )

    return {
        "total_claims": claim_count,
        "total_entities": entity_count,
        "total_kg_sources": source_count,
        "total_narratives": narrative_count,
        "top_sources_by_claims": [
            {"title": r.title, "source_name": r.source_name, "claims": r.claims}
            for r in top_sources
        ],
    }


@router.post("/alerts/{alert_id}/resolve", response_model=KGAlertOut)
def resolve_alert(alert_id: int, db: Session = Depends(get_db)):
    alert = db.get(KGAlert, alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    if alert.resolved_at:
        raise HTTPException(status_code=400, detail="Alert already resolved")
    alert.resolved_at = datetime.utcnow()
    db.commit()
    db.refresh(alert)
    return KGAlertOut(
        id=alert.id,
        narrative_id=alert.narrative_id,
        narrative_label=alert.narrative.label if alert.narrative else "",
        alert_type=alert.alert_type,
        severity_score=alert.severity_score,
        message=alert.message,
        created_at=alert.created_at,
        resolved_at=alert.resolved_at,
    )
