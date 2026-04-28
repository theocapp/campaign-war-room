from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.db import get_db
from app.models import SourceItem, OpponentActivity
from app.schemas import ReviewQueueItemOut, ReviewAction, PriorityUpdate, BulkReviewAction

router = APIRouter()


def _enrich(db: Session, item: SourceItem) -> ReviewQueueItemOut:
    issue_names = [m.issue.name for m in item.issue_mentions if m.issue]
    issue_ids = [m.issue.id for m in item.issue_mentions if m.issue]
    attack_count = (
        db.query(OpponentActivity)
        .filter(
            OpponentActivity.source_item_id == item.id,
            OpponentActivity.attack.isnot(None),
        )
        .count()
    )
    out = ReviewQueueItemOut.model_validate(item)
    out.related_issue_names = issue_names
    out.related_issue_ids = issue_ids
    out.opponent_attack_count = attack_count
    return out


@router.get("/review-queue", response_model=list[ReviewQueueItemOut])
def get_review_queue(db: Session = Depends(get_db)):
    items = (
        db.query(SourceItem)
        .options(joinedload(SourceItem.issue_mentions))
        .filter(SourceItem.reviewed == False)  # noqa: E712
        .filter(SourceItem.dismissed == False)  # noqa: E712
        .order_by(SourceItem.priority_score.desc(), SourceItem.created_at.desc())
        .limit(50)
        .all()
    )
    return [_enrich(db, item) for item in items]


@router.get("/review-queue/count")
def get_queue_count(db: Session = Depends(get_db)):
    count = (
        db.query(SourceItem)
        .filter(SourceItem.reviewed == False)  # noqa: E712
        .filter(SourceItem.dismissed == False)  # noqa: E712
        .count()
    )
    return {"count": count}


# Bulk endpoints MUST be registered before /{source_id}/... to avoid route conflict
@router.post("/review-queue/bulk/review")
def bulk_review(body: BulkReviewAction, db: Session = Depends(get_db)):
    updated = 0
    for sid in body.source_ids:
        item = db.get(SourceItem, sid)
        if item:
            item.reviewed = True
            if body.review_note:
                item.review_note = body.review_note
            updated += 1
    db.commit()
    return {"updated": updated}


@router.post("/review-queue/bulk/dismiss")
def bulk_dismiss(body: BulkReviewAction, db: Session = Depends(get_db)):
    updated = 0
    for sid in body.source_ids:
        item = db.get(SourceItem, sid)
        if item:
            item.dismissed = True
            if body.review_note:
                item.review_note = body.review_note
            updated += 1
    db.commit()
    return {"updated": updated}


@router.post("/review-queue/{source_id}/review", response_model=ReviewQueueItemOut)
def mark_reviewed(source_id: int, body: ReviewAction, db: Session = Depends(get_db)):
    item = db.get(SourceItem, source_id)
    if not item:
        raise HTTPException(status_code=404, detail="Source not found")
    item.reviewed = True
    if body.review_note:
        item.review_note = body.review_note
    db.commit()
    db.refresh(item)
    return _enrich(db, item)


@router.post("/review-queue/{source_id}/dismiss", response_model=ReviewQueueItemOut)
def dismiss_item(source_id: int, body: ReviewAction, db: Session = Depends(get_db)):
    item = db.get(SourceItem, source_id)
    if not item:
        raise HTTPException(status_code=404, detail="Source not found")
    item.dismissed = True
    if body.review_note:
        item.review_note = body.review_note
    db.commit()
    db.refresh(item)
    return _enrich(db, item)


@router.post("/review-queue/{source_id}/priority", response_model=ReviewQueueItemOut)
def set_priority(source_id: int, body: PriorityUpdate, db: Session = Depends(get_db)):
    item = db.get(SourceItem, source_id)
    if not item:
        raise HTTPException(status_code=404, detail="Source not found")
    item.priority_score = body.priority_score
    db.commit()
    db.refresh(item)
    return _enrich(db, item)
