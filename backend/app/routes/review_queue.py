from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.db import get_db
from app.models import SourceItem, OpponentActivity
from app.schemas import ReviewQueueItemOut, ReviewAction, PriorityUpdate, BulkReviewAction
from app.services.relevance_gate import build_keyword_pattern, passes_gate
from app.services.story_clustering import unique_by_cluster
from app.services.snapshots import source_out

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
    out = ReviewQueueItemOut.model_validate(source_out(item))
    out.related_issue_names = issue_names
    out.related_issue_ids = issue_ids
    out.opponent_attack_count = attack_count
    return out


# Single source of truth for "what belongs in the review queue?" — the list
# and count endpoints both call this, so the sidebar badge and the visible
# list can never drift apart again.
#
# Two stacked filters:
#   1. Category whitelist: only items the LLM tagged 'campaign' or
#      'priority_issue'. Excludes the long tail of sports / food / weather /
#      entertainment / generic_crime / explicit 'irrelevant'. The scorer
#      has been writing contradictory data (score=45 AND
#      content_category='irrelevant') — this filter resolves the
#      contradiction in favor of the category, which is the higher-signal
#      label in practice.
#   2. Score / actionability gate: keeps the existing >=40 OR review/respond
#      bar so we don't lose items the scorer flagged for human attention
#      even if the category was ambiguous.
def _review_queue_query(db: Session):
    # NOTE on the score gate: the per-article LLM scorer is currently flat —
    # it emits 45 for almost every 'campaign' item regardless of true
    # relevance. So the numeric threshold here is mostly redundant with the
    # category filter for now. We keep >=40 anyway, as a floor against any
    # future scorer that does discriminate within the band.
    return (
        db.query(SourceItem)
        .filter(SourceItem.reviewed == False)  # noqa: E712
        .filter(SourceItem.dismissed == False)  # noqa: E712
        .filter(SourceItem.archived_as_irrelevant == False)  # noqa: E712
        .filter(SourceItem.content_category.in_(["campaign", "priority_issue"]))
        .filter(
            or_(
                SourceItem.race_relevance_score >= 40,
                SourceItem.actionability_label.in_(["review", "respond"]),
            )
        )
    )


def _partition_by_relevance(
    items: list[SourceItem], db: Session,
) -> tuple[list[SourceItem], list[SourceItem]]:
    """Split items into (passes_gate, filtered_out) using the campaign's
    keyword relevance pattern. See `services/relevance_gate.py` for the
    full pass logic + safety bypasses.
    """
    pattern = build_keyword_pattern(db)
    passes: list[SourceItem] = []
    filtered: list[SourceItem] = []
    for it in items:
        (passes if passes_gate(it, pattern) else filtered).append(it)
    return passes, filtered


@router.get("/review-queue", response_model=list[ReviewQueueItemOut])
def get_review_queue(db: Session = Depends(get_db)):
    """Return every pending item the badge counts, deduped by story cluster
    and gated by the campaign-relevance keyword filter.

    Filter parity with /review-queue/count is load-bearing — the sidebar
    badge advertises a number, and clicking through must produce the same
    set. Both endpoints route through `_review_queue_query` for the base
    SQL filter and through `_partition_by_relevance` for the keyword gate.

    Items that fail the keyword gate are NOT deleted — they're available
    at /review-queue/filtered-out for the spot-check view. Limit is 200,
    large enough to cover realistic queue depth.
    """
    items = (
        _review_queue_query(db)
        .options(joinedload(SourceItem.issue_mentions))
        .order_by(SourceItem.race_relevance_score.desc(), SourceItem.created_at.desc())
        .limit(200)
        .all()
    )
    passes, _ = _partition_by_relevance(items, db)
    return [_enrich(db, item) for item in unique_by_cluster(passes)]


@router.get("/review-queue/filtered-out", response_model=list[ReviewQueueItemOut])
def get_review_queue_filtered_out(db: Session = Depends(get_db)):
    """Items the keyword gate kicked out of the main queue.

    Same base SQL filter as /review-queue, but returns the OPPOSITE side
    of the relevance partition. Used by the "Recently filtered" safety
    view so the user can spot-check what's being excluded and tune the
    keyword set if the gate is too aggressive.
    """
    items = (
        _review_queue_query(db)
        .options(joinedload(SourceItem.issue_mentions))
        .order_by(SourceItem.race_relevance_score.desc(), SourceItem.created_at.desc())
        .limit(200)
        .all()
    )
    _, filtered = _partition_by_relevance(items, db)
    return [_enrich(db, item) for item in unique_by_cluster(filtered)]


@router.get("/review-queue/count")
def get_queue_count(db: Session = Depends(get_db)):
    items = _review_queue_query(db).all()
    passes, _ = _partition_by_relevance(items, db)
    return {"count": len(unique_by_cluster(passes))}


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
def mark_reviewed(source_id: int, body: ReviewAction | None = None, db: Session = Depends(get_db)):
    """Mark an article reviewed. Body is optional — the UI's per-row Reviewed
    button posts without one, while a future "add note" affordance can pass
    `{review_note: "..."}`. Previously the body was required, so the no-body
    POST returned 422 and the click silently no-op'd on the frontend.
    """
    item = db.get(SourceItem, source_id)
    if not item:
        raise HTTPException(status_code=404, detail="Source not found")
    item.reviewed = True
    if body and body.review_note:
        item.review_note = body.review_note
    db.commit()
    db.refresh(item)
    return _enrich(db, item)


@router.post("/review-queue/{source_id}/dismiss", response_model=ReviewQueueItemOut)
def dismiss_item(source_id: int, body: ReviewAction | None = None, db: Session = Depends(get_db)):
    """Mark an article dismissed. Body is optional — see mark_reviewed."""
    item = db.get(SourceItem, source_id)
    if not item:
        raise HTTPException(status_code=404, detail="Source not found")
    item.dismissed = True
    if body and body.review_note:
        item.review_note = body.review_note
    db.commit()
    db.refresh(item)
    return _enrich(db, item)


@router.post("/review-queue/{source_id}/mark-relevant")
def mark_relevant(source_id: int, db: Session = Depends(get_db)):
    """Human override: confirm this article IS relevant to the race."""
    item = db.get(SourceItem, source_id)
    if not item:
        raise HTTPException(status_code=404, detail="Source not found")
    item.archived_as_irrelevant = False
    item.reviewed = True
    item.dismissed = False
    db.commit()
    return {"ok": True}


@router.post("/review-queue/{source_id}/mark-irrelevant")
def mark_irrelevant(source_id: int, db: Session = Depends(get_db)):
    """Human override: mark this article as NOT relevant to the race."""
    item = db.get(SourceItem, source_id)
    if not item:
        raise HTTPException(status_code=404, detail="Source not found")
    item.archived_as_irrelevant = True
    item.reviewed = True
    item.dismissed = True
    db.commit()
    return {"ok": True}


@router.post("/review-queue/{source_id}/priority", response_model=ReviewQueueItemOut)
def set_priority(source_id: int, body: PriorityUpdate, db: Session = Depends(get_db)):
    item = db.get(SourceItem, source_id)
    if not item:
        raise HTTPException(status_code=404, detail="Source not found")
    item.priority_score = body.priority_score
    db.commit()
    db.refresh(item)
    return _enrich(db, item)
