from datetime import datetime, timedelta, date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import String, case, func
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import CampaignConfig, NarrativeFrame, NarrativeFrameMention, Outlet, RssFeed, SourceItem

router = APIRouter()


@router.get("/frames/{frame_id}/timeseries")
def frame_timeseries(
    frame_id: int,
    bucket: str = Query("day", pattern="^(day|week)$"),
    days: int = Query(0, ge=0, le=1825),
    db: Session = Depends(get_db),
):
    """
    Day-by-day mention counts bucketed by article publish date.
    days=0 means all time.
    """
    # Count distinct story clusters per day (unique stories) AND sum outlet
    # authority weights (reach). Unique stories dedup wire coverage; reach does
    # NOT dedup — a wire story in 5 major papers has 5× the reach of one blog.
    cluster_key = func.coalesce(SourceItem.story_cluster_id, func.cast(SourceItem.id, String))
    # Weight: monthly_visitors * 0.003 when available, else authority_score / 10.
    reach_weight = case(
        (Outlet.monthly_visitors.isnot(None), Outlet.monthly_visitors * 0.003),
        else_=func.coalesce(Outlet.authority_score, 5) / 10.0,
    )
    q = (
        db.query(
            func.date(SourceItem.published_at).label("day"),
            func.count(func.distinct(cluster_key)).label("count"),
            func.round(func.sum(reach_weight), 1).label("weighted_reach"),
        )
        .select_from(NarrativeFrameMention)
        .join(SourceItem, SourceItem.id == NarrativeFrameMention.source_item_id)
        .outerjoin(Outlet, Outlet.id == SourceItem.outlet_id)
        .filter(
            NarrativeFrameMention.frame_id == frame_id,
            SourceItem.published_at.isnot(None),
        )
    )
    if days > 0:
        q = q.filter(SourceItem.published_at >= datetime.utcnow() - timedelta(days=days))
    rows = q.group_by("day").order_by("day").all()

    # Fill in zeros for every day in the range so the chart has no gaps.
    by_date: dict[str, dict] = {str(r.day): {"count": r.count, "weighted_reach": float(r.weighted_reach or 0)} for r in rows}
    today = date.today()
    if rows:
        start = date.fromisoformat(str(rows[0].day))
    elif days > 0:
        start = today - timedelta(days=days - 1)
    else:
        start = today
    series = []
    d = start
    while d <= today:
        day_str = str(d)
        entry = by_date.get(day_str, {"count": 0, "weighted_reach": 0.0})
        series.append({"date": day_str, "count": entry["count"], "weighted_reach": entry["weighted_reach"]})
        d += timedelta(days=1)

    return {"frame_id": frame_id, "bucket": bucket, "days": days, "series": series}


@router.get("/frames/{frame_id}/share-of-voice")
def frame_share_of_voice(
    frame_id: int,
    days: int = Query(7, ge=1, le=90),
    db: Session = Depends(get_db),
):
    """Candidate vs opponent vs neutral share of articles mentioning this frame."""
    cutoff = datetime.utcnow() - timedelta(days=days)

    mentions = (
        db.query(NarrativeFrameMention)
        .join(SourceItem, SourceItem.id == NarrativeFrameMention.source_item_id)
        .filter(
            NarrativeFrameMention.frame_id == frame_id,
            SourceItem.published_at >= cutoff,
            SourceItem.published_at.isnot(None),
        )
        .all()
    )

    if not mentions:
        return {"frame_id": frame_id, "days": days, "total": 0, "candidate": 0, "opponent": 0, "neutral": 0}

    source_ids = [m.source_item_id for m in mentions]
    items = (
        db.query(SourceItem.id, SourceItem.source_owner_type, SourceItem.story_cluster_id)
        .filter(SourceItem.id.in_(source_ids))
        .all()
    )

    # Deduplicate by story cluster — a single wire story across 5 outlets should
    # contribute one vote, not five. Items without a cluster id fall back to the
    # source id so each still counts once.
    counts = {"candidate": 0, "opponent": 0, "neutral": 0}
    seen_clusters: set[str] = set()
    for sid, owner, cluster in items:
        key = cluster or f"source-{sid}"
        if key in seen_clusters:
            continue
        seen_clusters.add(key)
        owner = owner or "unclear"
        if owner in ("candidate", "campaign"):
            counts["candidate"] += 1
        elif owner == "opponent":
            counts["opponent"] += 1
        else:
            counts["neutral"] += 1

    total = len(seen_clusters)
    if total == 0:
        return {"frame_id": frame_id, "days": days, "total": 0, "candidate": 0, "opponent": 0, "neutral": 0}
    return {
        "frame_id": frame_id,
        "days": days,
        "total": total,
        "candidate": round(counts["candidate"] / total * 100),
        "opponent": round(counts["opponent"] / total * 100),
        "neutral": round(counts["neutral"] / total * 100),
    }


@router.get("/monitoring/start-date")
def get_monitoring_start_date(db: Session = Depends(get_db)):
    """Return the date continuous RSS monitoring began (earliest RSS feed last_fetched_at)."""
    earliest = db.query(func.min(RssFeed.last_fetched_at)).scalar()
    campaign = db.query(CampaignConfig).first()
    return {
        "monitoring_start": earliest.date().isoformat() if earliest else None,
        "has_backfill": bool(campaign and campaign.historical_backfill_completed),
    }


@router.get("/analytics/spikes")
def spike_report(db: Session = Depends(get_db)):
    """
    Returns frames where the last 24h mention count is at least 2× the 7d daily average
    and the absolute count is >= 3. Used to surface urgency on the Briefing page.
    """
    now = datetime.utcnow()
    cutoff_24h = now - timedelta(hours=24)
    cutoff_7d = now - timedelta(days=7)

    frames = db.query(NarrativeFrame).filter(NarrativeFrame.active == True).all()  # noqa: E712

    # Spike detection uses weighted reach: a burst of low-authority blog posts
    # shouldn't trigger the same alert as equivalent coverage in major papers.
    reach_weight = func.case(
        (Outlet.monthly_visitors.isnot(None), Outlet.monthly_visitors * 0.003),
        else_=func.coalesce(Outlet.authority_score, 5) / 10.0,
    )

    spikes = []
    for frame in frames:
        reach_24h = (
            db.query(func.round(func.sum(reach_weight), 2))
            .select_from(NarrativeFrameMention)
            .join(SourceItem, SourceItem.id == NarrativeFrameMention.source_item_id)
            .outerjoin(Outlet, Outlet.id == SourceItem.outlet_id)
            .filter(
                NarrativeFrameMention.frame_id == frame.id,
                NarrativeFrameMention.created_at >= cutoff_24h,
            )
            .scalar() or 0
        )
        reach_7d = (
            db.query(func.round(func.sum(reach_weight), 2))
            .select_from(NarrativeFrameMention)
            .join(SourceItem, SourceItem.id == NarrativeFrameMention.source_item_id)
            .outerjoin(Outlet, Outlet.id == SourceItem.outlet_id)
            .filter(
                NarrativeFrameMention.frame_id == frame.id,
                NarrativeFrameMention.created_at >= cutoff_7d,
            )
            .scalar() or 0
        )

        daily_avg_7d = reach_7d / 7.0
        if reach_24h >= 1.5 and daily_avg_7d > 0 and reach_24h >= daily_avg_7d * 2:
            spikes.append({
                "frame_id": frame.id,
                "frame_name": frame.name,
                "owner_type": frame.owner_type,
                "count_24h": round(float(reach_24h), 1),
                "daily_avg_7d": round(float(daily_avg_7d), 1),
                "ratio": round(float(reach_24h) / float(daily_avg_7d), 1),
            })

    spikes.sort(key=lambda x: x["ratio"], reverse=True)
    return {"spikes": spikes}
