from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import (
    CampaignConfig,
    NarrativeFrame,
    NarrativeFrameMention,
    Opponent,
    SourceItem,
)
from app.services import briefing_summary as briefing_svc


def _compute_spikes(db: Session) -> list[dict]:
    """Delegates to the canonical cluster-based / reach-weighted spike detector.

    The previous implementation here used the legacy NarrativeFrameMention
    table and raw mention counts, which silently returned stale data once the
    pipeline moved to FrameClusterMatch + weighted reach. Now shares the same
    logic as /analytics/spikes.
    """
    from app.routes.analytics import detect_spike_alerts
    return detect_spike_alerts(db)

router = APIRouter()


def _item_dict(item: SourceItem) -> dict:
    return {
        "id": item.id,
        "title": item.title,
        "summary": item.summary,
        "source_name": item.source_name,
        "source_url": item.source_url,
        "published_at": item.published_at.isoformat() if item.published_at else None,
        "race_relevance_score": item.race_relevance_score,
        "actionability_label": item.actionability_label,
        "framing": getattr(item, "framing", None),
    }


def _is_llm_scored(item: SourceItem) -> bool:
    """Return False for articles whose summary looks like a raw RSS excerpt (not LLM-generated)."""
    s = item.summary or ""
    if "<" in s:
        return False
    if "[...]" in s:
        return False
    if " —" in s and ("(WB" in s or "(WN" in s or "(WY" in s or "(AP)" in s):
        return False
    return True


@router.get("/briefing/morning")
def get_morning_briefing(db: Session = Depends(get_db)):
    """
    Single-page briefing: new articles, narrative pulse, needs-response, LLM race-situation memo.
    """
    cutoff_24h = datetime.utcnow() - timedelta(hours=24)
    cutoff_48h = datetime.utcnow() - timedelta(hours=48)
    cutoff_7d = datetime.utcnow() - timedelta(days=7)
    cutoff_14d = datetime.utcnow() - timedelta(days=14)

    # Section 1 — Needs a response right now (published in last 48h)
    respond = (
        db.query(SourceItem)
        .filter(
            SourceItem.archived_as_irrelevant == False,  # noqa: E712
            SourceItem.published_at >= cutoff_48h,
            SourceItem.actionability_label == "respond",
        )
        .order_by(SourceItem.race_relevance_score.desc())
        .limit(5)
        .all()
    )

    # Section 2 — New since yesterday (published in last 48h, top relevant)
    respond_ids = {i.id for i in respond}
    new_articles_raw = (
        db.query(SourceItem)
        .filter(
            SourceItem.archived_as_irrelevant == False,  # noqa: E712
            SourceItem.published_at >= cutoff_48h,
            SourceItem.race_relevance_score >= 50,
        )
        .order_by(SourceItem.race_relevance_score.desc())
        .limit(50)
        .all()
    )

    new_articles = [
        a for a in new_articles_raw
        if a.id not in respond_ids and _is_llm_scored(a)
    ][:5]

    # Section 3 — Narrative pulse
    frames = db.query(NarrativeFrame).filter(NarrativeFrame.active == True).all()  # noqa: E712
    pulse = []
    for frame in frames:
        this_week = (
            db.query(func.count(NarrativeFrameMention.id))
            .join(SourceItem, SourceItem.id == NarrativeFrameMention.source_item_id)
            .filter(NarrativeFrameMention.frame_id == frame.id,
                    SourceItem.published_at >= cutoff_7d,
                    SourceItem.published_at.isnot(None))
            .scalar()
        )
        last_week = (
            db.query(func.count(NarrativeFrameMention.id))
            .join(SourceItem, SourceItem.id == NarrativeFrameMention.source_item_id)
            .filter(NarrativeFrameMention.frame_id == frame.id,
                    SourceItem.published_at >= cutoff_14d,
                    SourceItem.published_at < cutoff_7d,
                    SourceItem.published_at.isnot(None))
            .scalar()
        )
        pulse.append({
            "id": frame.id,
            "name": frame.name,
            "owner_type": frame.owner_type,
            "this_week": this_week,
            "last_week": last_week,
        })
    pulse.sort(key=lambda x: x["this_week"], reverse=True)

    # Meta — ingested uses created_at, relevant uses published_at + same quality filter as new_articles
    total_today = (
        db.query(func.count(SourceItem.id))
        .filter(SourceItem.created_at >= cutoff_24h)
        .scalar()
    )
    relevant_candidates = (
        db.query(SourceItem)
        .filter(SourceItem.archived_as_irrelevant == False,  # noqa: E712
                SourceItem.published_at >= cutoff_48h,
                SourceItem.race_relevance_score >= 50)
        .all()
    )
    relevant_today = sum(1 for i in relevant_candidates if _is_llm_scored(i))

    # LLM race-situation memo
    campaign = db.query(CampaignConfig).first()
    opponents = db.query(Opponent).limit(3).all()
    all_articles = [_item_dict(i) for i in respond] + [_item_dict(i) for i in new_articles]
    race_memo = briefing_svc.get_or_generate(db, all_articles, campaign, opponents)

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "meta": {
            "total_articles_today": total_today,
            "relevant_articles_today": relevant_today,
        },
        "race_memo": race_memo,
        "needs_response": [_item_dict(i) for i in respond],
        "new_articles": [_item_dict(i) for i in new_articles],
        "narrative_pulse": pulse,
        "spike_alerts": _compute_spikes(db),
    }


@router.get("/articles/recent")
def get_recent_relevant_articles(limit: int = 10, db: Session = Depends(get_db)):
    """Most recent race-relevant articles for the Dashboard right rail.

    Filters: not archived, LLM-scored (real summary, not raw RSS), score >= 50.
    Ordered by published_at desc so the user sees what's actually new — the
    review queue's priority sort buries fresh items behind older high-priority
    ones, which is the wrong signal for "what's happening now."
    """
    cutoff = datetime.utcnow() - timedelta(days=7)
    candidates = (
        db.query(SourceItem)
        .filter(
            SourceItem.archived_as_irrelevant == False,  # noqa: E712
            SourceItem.published_at >= cutoff,
            SourceItem.race_relevance_score >= 50,
        )
        .order_by(SourceItem.published_at.desc())
        .limit(limit * 3)  # over-fetch so the LLM-scored filter has headroom
        .all()
    )
    items = [a for a in candidates if _is_llm_scored(a)][:limit]
    return {"items": [_item_dict(a) for a in items]}
