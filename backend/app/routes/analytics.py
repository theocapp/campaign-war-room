"""Analytics endpoints — cluster-native (Phase C).

All queries read from `frame_cluster_matches` and `story_clusters`. The legacy
`narrative_frame_mentions` table is no longer touched by analytics; one wire
story syndicated across N outlets contributes one cluster row, not N.

Reach is intentionally NOT cluster-deduped — it is summed across every member
article's outlet, so a wire story carried by 5 major papers has 5× the reach
of one blog post. The field names exposed on the API (`mention_count`,
`mentions_this_week`, etc.) are unchanged so the frontend keeps working; the
semantics have shifted from "article mention" to "story cluster" but each
cluster equals one story.
"""
from datetime import datetime, timedelta, date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import (
    CampaignConfig,
    FrameClusterMatch,
    GdeltToneSnapshot,
    GoogleTrendSnapshot,
    NarrativeFrame,
    Outlet,
    RssFeed,
    SourceItem,
    StoryCluster,
)

router = APIRouter()


# Weight used everywhere for "reach":
#   monthly_visitors × 0.003 (≈ 0.3% of monthly UVs see any single article)
#   fallback to authority_score / 10 when monthly_visitors is unknown.
def _reach_weight():
    return case(
        (Outlet.monthly_visitors.isnot(None), Outlet.monthly_visitors * 0.003),
        else_=func.coalesce(Outlet.authority_score, 5) / 10.0,
    )


@router.get("/frames/{frame_id}/timeseries")
def frame_timeseries(
    frame_id: int,
    bucket: str = Query("day", pattern="^(day|week)$"),
    days: int = Query(0, ge=0, le=1825),
    db: Session = Depends(get_db),
):
    """Day-by-day cluster counts + weighted reach for a frame.

    `count` is the number of clusters first attached to this frame on each day
    (no double-counting of wire syndication). `weighted_reach` is summed over
    every member article's outlet — wire syndication intentionally counts here.
    """
    # 1) Clusters per day, bucketed by FrameClusterMatch.first_seen_at — the
    #    day this frame first attached to the cluster.
    fcm_q = (
        db.query(
            func.date(FrameClusterMatch.first_seen_at).label("day"),
            func.count(FrameClusterMatch.id).label("count"),
        )
        .filter(FrameClusterMatch.frame_id == frame_id)
    )
    if days > 0:
        fcm_q = fcm_q.filter(
            FrameClusterMatch.first_seen_at >= datetime.utcnow() - timedelta(days=days)
        )
    cluster_rows = fcm_q.group_by("day").order_by("day").all()

    # 2) Reach per day — sum reach_weight over every SourceItem whose cluster
    #    matches this frame. Reach is NOT cluster-deduped.
    reach_q = (
        db.query(
            func.date(SourceItem.published_at).label("day"),
            func.round(func.sum(_reach_weight()), 1).label("weighted_reach"),
        )
        .select_from(FrameClusterMatch)
        .join(StoryCluster, StoryCluster.id == FrameClusterMatch.story_cluster_id)
        .join(SourceItem, SourceItem.story_cluster_id == StoryCluster.id)
        .outerjoin(Outlet, Outlet.id == SourceItem.outlet_id)
        .filter(
            FrameClusterMatch.frame_id == frame_id,
            SourceItem.published_at.isnot(None),
        )
    )
    if days > 0:
        reach_q = reach_q.filter(
            SourceItem.published_at >= datetime.utcnow() - timedelta(days=days)
        )
    reach_rows = reach_q.group_by("day").order_by("day").all()

    # Merge into a single series keyed by date.
    counts_by_date = {str(r.day): r.count for r in cluster_rows}
    reach_by_date = {str(r.day): float(r.weighted_reach or 0) for r in reach_rows}

    # Fill gaps so the chart has no holes.
    today = date.today()
    all_dates = set(counts_by_date) | set(reach_by_date)
    if all_dates:
        start = min(date.fromisoformat(d) for d in all_dates)
    elif days > 0:
        start = today - timedelta(days=days - 1)
    else:
        start = today
    series = []
    d = start
    while d <= today:
        day_str = str(d)
        series.append({
            "date": day_str,
            "count": counts_by_date.get(day_str, 0),
            "weighted_reach": reach_by_date.get(day_str, 0.0),
        })
        d += timedelta(days=1)

    return {"frame_id": frame_id, "bucket": bucket, "days": days, "series": series}


@router.get("/frames/{frame_id}/share-of-voice")
def frame_share_of_voice(
    frame_id: int,
    days: int = Query(7, ge=1, le=90),
    db: Session = Depends(get_db),
):
    """Candidate vs opponent vs neutral share of clusters mentioning this frame.

    Each matched cluster contributes one vote. The cluster's "voice" is the
    source_owner_type of its representative article. Future work could
    aggregate across all member articles; for now, representative is the
    single source of truth and matches the UI's "this cluster is from X" model.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)

    rows = (
        db.query(SourceItem.source_owner_type)
        .select_from(FrameClusterMatch)
        .join(StoryCluster, StoryCluster.id == FrameClusterMatch.story_cluster_id)
        .join(SourceItem, SourceItem.id == StoryCluster.representative_source_item_id)
        .filter(
            FrameClusterMatch.frame_id == frame_id,
            FrameClusterMatch.first_seen_at >= cutoff,
        )
        .all()
    )

    counts = {"candidate": 0, "opponent": 0, "neutral": 0}
    for (owner,) in rows:
        owner = owner or "unclear"
        if owner in ("candidate", "campaign"):
            counts["candidate"] += 1
        elif owner == "opponent":
            counts["opponent"] += 1
        else:
            counts["neutral"] += 1

    total = sum(counts.values())
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


def detect_spike_alerts(db: Session) -> list[dict]:
    """Frames whose last-24h weighted reach is ≥ 2× the 7-day daily average.

    Shared between the /analytics/spikes endpoint and the morning briefing.
    Reach is summed across all member articles of every matched cluster, so
    a wire story carried by major papers is correctly weighted against a
    burst of low-authority blog posts.
    """
    now = datetime.utcnow()
    cutoff_24h = now - timedelta(hours=24)
    cutoff_7d = now - timedelta(days=7)

    frames = db.query(NarrativeFrame).filter(NarrativeFrame.active == True).all()  # noqa: E712

    spikes = []
    for frame in frames:
        reach_24h = _frame_reach_in_window(db, frame.id, cutoff_24h)
        reach_7d = _frame_reach_in_window(db, frame.id, cutoff_7d)

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
    return spikes


@router.get("/analytics/spikes")
def spike_report(db: Session = Depends(get_db)):
    return {"spikes": detect_spike_alerts(db)}


def _frame_reach_in_window(db: Session, frame_id: int, cutoff: datetime) -> float:
    """Sum reach_weight over every member article (any cluster matched to this
    frame) that arrived after `cutoff`. Bucket by FrameClusterMatch.first_seen_at
    so a cluster that's been attached to the frame for months doesn't keep
    re-triggering spikes on new article evidence."""
    val = (
        db.query(func.round(func.sum(_reach_weight()), 2))
        .select_from(FrameClusterMatch)
        .join(StoryCluster, StoryCluster.id == FrameClusterMatch.story_cluster_id)
        .join(SourceItem, SourceItem.story_cluster_id == StoryCluster.id)
        .outerjoin(Outlet, Outlet.id == SourceItem.outlet_id)
        .filter(
            FrameClusterMatch.frame_id == frame_id,
            FrameClusterMatch.first_seen_at >= cutoff,
        )
        .scalar()
    )
    return float(val or 0)


@router.get("/analytics/tone")
def get_tone_history(days: int = 30, db: Session = Depends(get_db)):
    """Return daily GDELT tone snapshots for candidate + opponents.

    Used to render a media-tone trend chart on the Narratives page.
    Response shape:
      { "entities": [{ "label": str, "entity_type": str,
                        "series": [{ "date": "YYYY-MM-DD", "tone": float }] }] }
    """
    cutoff = datetime.utcnow() - timedelta(days=days)
    rows = (
        db.query(GdeltToneSnapshot)
        .filter(GdeltToneSnapshot.snapshot_date >= cutoff)
        .order_by(GdeltToneSnapshot.query_label, GdeltToneSnapshot.snapshot_date)
        .all()
    )
    by_entity: dict[str, dict] = {}
    for row in rows:
        # GDELT returns 0.0 when the day had few/no indexed articles for the
        # query — that's "no signal," not a real neutral-tone reading. A
        # genuine average tone landing on exactly 0.00 is vanishingly rare.
        if row.avg_tone == 0.0:
            continue
        if row.query_label not in by_entity:
            by_entity[row.query_label] = {
                "label": row.query_label,
                "entity_type": row.entity_type,
                "series": [],
            }
        by_entity[row.query_label]["series"].append({
            "date": row.snapshot_date.strftime("%Y-%m-%d"),
            "tone": row.avg_tone,
        })
    return {"entities": list(by_entity.values())}


@router.get("/analytics/search-trends")
def get_search_trends(days: int = 90, geo: str = "US-PA", db: Session = Depends(get_db)):
    """Return Google Trends interest-over-time for all tracked terms.

    `geo` selects the geography: "US-PA" (statewide) or "US-PA-577"
    (Wilkes Barre-Scranton DMA).

    Response: { "terms": [{ "term": str, "series": [{ "date": "YYYY-MM-DD", "interest": int }] }] }
    """
    from app.services.google_trends import get_trends_series
    return {"terms": get_trends_series(db, days=days, geo=geo)}
