"""Race sentiment endpoints.

Phase 1: read + manual write of the six default sources (Polymarket, Kalshi,
Cook, Sabato, Inside Elections, DDHQ).
Phase 2 (this file): live-fetch endpoints (sync, backfill, history).
"""
import json
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import RaceSentiment, RaceSentimentSnapshot, SourceItem
from app.services.source_display import display_source_name, preload_outlets
from app.schemas import (
    RaceSentimentOut,
    RaceSentimentSnapshotOut,
    RaceSentimentUpdate,
)

router = APIRouter()


@router.get("/race-sentiment", response_model=list[RaceSentimentOut])
def list_race_sentiment(db: Session = Depends(get_db)):
    """All sources, ordered: markets first (by display_name), then ratings.

    Empty rows (never edited) are still returned so the UI can render the
    full source list with placeholder cells.
    """
    rows = db.query(RaceSentiment).all()
    rows.sort(key=lambda r: (0 if r.source_type == "market" else 1, r.display_name))
    return rows


@router.put("/race-sentiment/{source}", response_model=RaceSentimentOut)
def upsert_race_sentiment(
    source: str,
    payload: RaceSentimentUpdate,
    db: Session = Depends(get_db),
):
    """Update one source row in place. Source slug must already exist
    (seeded at startup) — this is partial-update, not create.
    """
    row = db.query(RaceSentiment).filter(RaceSentiment.source == source).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown race sentiment source: {source}")

    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        if k == "external_metadata" and v is not None:
            v = json.dumps(v)
        setattr(row, k, v)
    row.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(row)
    return row


@router.post("/race-sentiment/{source}/sync", response_model=RaceSentimentOut)
def manual_sync(source: str, db: Session = Depends(get_db)):
    """Run this source's connector right now. Phase 2 mostly for testing.

    The daily scheduler runs all configured sources automatically; this
    endpoint exists so the user can force a refresh after editing the
    connector config without waiting for the next cron tick.
    """
    from app.services.race_sentiment_sync import sync_one
    row = db.query(RaceSentiment).filter(RaceSentiment.source == source).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown source: {source}")
    if not row.external_id:
        raise HTTPException(
            status_code=400,
            detail=f"Source {source} has no external_id configured — nothing to sync",
        )
    ok = sync_one(db, source)
    db.refresh(row)
    if not ok and row.last_sync_error:
        # Surface the failure to the user so they can see why nothing changed.
        raise HTTPException(status_code=502, detail=row.last_sync_error)
    return row


@router.post("/race-sentiment/{source}/backfill")
def backfill_history(
    source: str,
    days_back: int = 90,
    db: Session = Depends(get_db),
):
    """One-shot history pull. Supports both Polymarket and Kalshi.

    Writes daily snapshots to `race_sentiment_snapshots`. Idempotent: a
    UNIQUE (source, captured_at) constraint blocks duplicate rows, so
    re-running just fills any gaps.
    """
    row = db.query(RaceSentiment).filter(RaceSentiment.source == source).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown source: {source}")
    if row.source_type != "market":
        raise HTTPException(
            status_code=400,
            detail="Backfill only supports market sources (Polymarket, Kalshi).",
        )
    try:
        metadata = json.loads(row.external_metadata) if row.external_metadata else {}
    except Exception:
        metadata = {}

    if source == "polymarket":
        if not metadata.get("event_slug"):
            raise HTTPException(
                status_code=400,
                detail="Source has no event_slug in external_metadata — configure it first.",
            )
        from app.services.prediction_market_monitor import polymarket_backfill_history
        result = polymarket_backfill_history(source, metadata, days_back=days_back)
    elif source == "kalshi":
        if not metadata.get("event_ticker"):
            raise HTTPException(
                status_code=400,
                detail="Source has no event_ticker in external_metadata — configure it first.",
            )
        from app.services.prediction_market_monitor import kalshi_backfill_history
        result = kalshi_backfill_history(source, metadata, days_back=days_back)
    else:
        raise HTTPException(
            status_code=400,
            detail=f"No history backfill implemented for source: {source}",
        )
    return {"source": source, **result}


@router.get(
    "/race-sentiment/{source}/history",
    response_model=list[RaceSentimentSnapshotOut],
)
def get_history(
    source: str,
    days: int = 30,
    include_suspect: bool = False,
    db: Session = Depends(get_db),
):
    """Daily snapshots for the last N days, oldest-first.

    Powers the forecast chart in Phase 3 and the Timeline market-reaction
    overlay. Returned as a flat list — the frontend buckets by source
    and renders a line per series.

    Suspect rows (flagged by the coherence + temporal-isolation checks
    in race_sentiment_sync) are filtered out by default. They stay in
    the DB for audit; pass `include_suspect=true` to see them. See
    `/api/race-sentiment/suspect-snapshots` for the standalone audit
    view (the user wants to know what was filtered out and why).
    """
    cutoff = datetime.utcnow() - timedelta(days=days)
    q = (
        db.query(RaceSentimentSnapshot)
        .filter(
            RaceSentimentSnapshot.source == source,
            RaceSentimentSnapshot.captured_at >= cutoff,
        )
    )
    if not include_suspect:
        q = q.filter(RaceSentimentSnapshot.suspect.is_(False))
    rows = q.order_by(RaceSentimentSnapshot.captured_at.asc()).all()
    return rows


@router.get("/race-sentiment/suspect-snapshots")
def list_suspect_snapshots(days: int = 90, db: Session = Depends(get_db)):
    """All snapshots flagged as suspect in the last N days, with reasons.

    Audit endpoint — lets the user see what got filtered out of charts
    and why. Returns plain dicts (not the typed schema) so the reason
    string is always present.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)
    rows = (
        db.query(RaceSentimentSnapshot)
        .filter(
            RaceSentimentSnapshot.suspect.is_(True),
            RaceSentimentSnapshot.captured_at >= cutoff,
        )
        .order_by(RaceSentimentSnapshot.captured_at.desc())
        .all()
    )
    return [
        {
            "id": r.id,
            "source": r.source,
            "source_type": r.source_type,
            "candidate_pct": r.candidate_pct,
            "opponent_pct": r.opponent_pct,
            "captured_at": r.captured_at.isoformat() + "Z" if r.captured_at else None,
            "suspect_reason": r.suspect_reason,
        }
        for r in rows
    ]


@router.post("/race-sentiment/sync-all")
def sync_all_endpoint(db: Session = Depends(get_db)):
    """Manually trigger the daily sync. Same code path the scheduler runs."""
    from app.services.race_sentiment_sync import sync_all
    return sync_all(db)


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 — timeline events for the /forecast chart
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/race-sentiment/events")
def list_timeline_events(days: int = 30, db: Session = Depends(get_db)):
    """Unified timeline of frame events + top articles in the last N days.

    The forecast page overlays these on the Polymarket history chart.
    Returned in chronological order (oldest first). Each entry has a `type`
    discriminator so the frontend can color/icon them differently.

    Event types:
      - frame_created      → frame was promoted from a candidate cluster.
                             Most useful "the campaign started tracking this"
                             signal. Filtered to active=True frames.
      - frame_stage_change → narrative moved between lifecycle stages.
                             Filtered to important destinations only
                             (mainstream / spreading) to avoid clutter.
      - top_article        → highest-scored relevant article of the day.
                             One per day max. Use race_relevance_score >= 75
                             as the filter — that matches the "high" tier
                             in race_relevance_label.

    FEC events and viral-signal-change events are deferred to a follow-up:
    FEC needs a deeper look at the monitor schema, and viral signals don't
    have a transition log yet (we only see the current state, not history).
    """
    from datetime import datetime, timedelta
    from app.models import NarrativeFrame, FrameStageHistory, SourceItem

    cutoff = datetime.utcnow() - timedelta(days=days)
    events: list[dict] = []

    # ── Frame promotions (creations of active frames)
    frames = (
        db.query(NarrativeFrame)
        .filter(NarrativeFrame.created_at >= cutoff)
        .filter(NarrativeFrame.active.is_(True))
        .order_by(NarrativeFrame.created_at.asc())
        .all()
    )
    for f in frames:
        events.append({
            "type": "frame_created",
            "timestamp": f.created_at.isoformat() + "Z" if f.created_at else None,
            "label": f.name,
            "frame_id": f.id,
            "owner_type": f.owner_type,
            "subject_type": f.subject_type,
        })

    # ── Stage transitions to interesting destinations
    transitions = (
        db.query(FrameStageHistory)
        .filter(FrameStageHistory.transitioned_at >= cutoff)
        .filter(FrameStageHistory.to_stage.in_(["mainstream", "spreading"]))
        .order_by(FrameStageHistory.transitioned_at.asc())
        .all()
    )
    # Cache frame names so we can label each transition cheaply.
    frame_names = {f.id: f.name for f in frames}
    missing_ids = {t.frame_id for t in transitions if t.frame_id not in frame_names}
    if missing_ids:
        for f in db.query(NarrativeFrame).filter(NarrativeFrame.id.in_(missing_ids)).all():
            frame_names[f.id] = f.name
    for t in transitions:
        events.append({
            "type": "frame_stage_change",
            "timestamp": t.transitioned_at.isoformat() + "Z" if t.transitioned_at else None,
            "label": f"{frame_names.get(t.frame_id, f'Frame {t.frame_id}')} → {t.to_stage}",
            "frame_id": t.frame_id,
            "from_stage": t.from_stage,
            "to_stage": t.to_stage,
        })

    # ── Top article per day (highest race_relevance_score per calendar day)
    # We bucket by date string to dedupe. Limit to high-relevance only to
    # avoid every random local article landing on the chart.
    articles = (
        db.query(SourceItem)
        .filter(SourceItem.published_at >= cutoff)
        .filter(SourceItem.race_relevance_score >= 75)
        .filter(SourceItem.archived_as_irrelevant.is_(False))
        .order_by(SourceItem.race_relevance_score.desc(), SourceItem.published_at.desc())
        .limit(500)
        .all()
    )
    outlets_map = preload_outlets(db, articles)
    seen_dates: set[str] = set()
    for a in articles:
        if not a.published_at:
            continue
        d = a.published_at.strftime("%Y-%m-%d")
        if d in seen_dates:
            continue
        seen_dates.add(d)
        events.append({
            "type": "top_article",
            "timestamp": a.published_at.isoformat() + "Z",
            "label": a.title[:100] if a.title else "(no title)",
            "article_id": a.id,
            "score": a.race_relevance_score,
            "source_name": display_source_name(a, outlets_map.get(a.outlet_id)),
        })

    # Final sort: oldest first, ties broken by type (so a stage_change on
    # the same minute as a frame_created comes after — the creation logically
    # happens first).
    type_order = {"frame_created": 0, "frame_stage_change": 1, "top_article": 2}
    events.sort(key=lambda e: (e.get("timestamp") or "", type_order.get(e["type"], 9)))
    return events


# ─────────────────────────────────────────────────────────────────────────────
# Narrative lifecycle — derived from article-match data (NOT system-event data)
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/race-sentiment/narrative-lifecycle")
def narrative_lifecycle(stale_days: int = 30, db: Session = Depends(get_db)):
    """Per-frame lifecycle events derived from when articles actually
    matched the frame — not when the frame was promoted in the database.

    For each active frame with at least one article match, emits up to three
    timeline events:

      - emerged → published_at of the earliest matched article.
        This is when the narrative first appeared in the press, often
        years before we were tracking it.
      - peaked  → the calendar day with the most matched articles, plus
        the count. Captures the moment a narrative was loudest.
      - faded   → published_at of the most recent match, BUT only when
        the frame is stale (no matches in the last `stale_days` days).
        Active frames don't get a fade event.

    Returns a flat list of events sorted oldest first. Same shape as
    `/race-sentiment/events` so the frontend can union them.

    This replaces the misleading frame_created event from `/events`, which
    fired at promotion time and made the timeline look like nothing
    happened before the system started tracking.
    """
    from collections import defaultdict
    from datetime import datetime, timedelta
    from app.models import NarrativeFrame, NarrativeFrameMention, SourceItem
    from app.services.subject_classifier import get_subject_classifier

    stale_cutoff = datetime.utcnow() - timedelta(days=stale_days)

    # Fallback classifier — used when NarrativeFrame.subject_type is NULL.
    # The frontend quadrant palette expects 'candidate' | 'opponent' | 'media';
    # the heuristic returns one of these from the frame name.
    classify_subject = get_subject_classifier(db)

    # Pull frame_id, owner_type, subject_type, name, and the matched article's
    # published_at for every NFM tied to an active frame. owner_type +
    # subject_type drive the 4-quadrant color scheme on the frontend (see
    # frontend-v2/src/lib/quadrantColor.ts).
    rows = (
        db.query(
            NarrativeFrame.id,
            NarrativeFrame.name,
            NarrativeFrame.owner_type,
            NarrativeFrame.subject_type,
            SourceItem.published_at,
        )
        .join(NarrativeFrameMention, NarrativeFrameMention.frame_id == NarrativeFrame.id)
        .join(SourceItem, SourceItem.id == NarrativeFrameMention.source_item_id)
        .filter(NarrativeFrame.active.is_(True))
        .filter(SourceItem.published_at.isnot(None))
        .all()
    )

    # Group: frame_id → { meta, list of published_at }
    per_frame: dict[int, dict] = {}
    for fid, name, owner, subject, ts in rows:
        f = per_frame.setdefault(
            fid,
            {"name": name, "owner_type": owner, "subject_type": subject, "ts": []},
        )
        f["ts"].append(ts)
    # total_mentions is just len(ts) for each frame — counted while we group.
    # Sized this way (frame's full historical reach) so it represents the
    # narrative's magnitude over time, not just the slice in the chart's
    # current time window.

    events: list[dict] = []
    for fid, info in per_frame.items():
        ts_list = info["ts"]
        if not ts_list:
            continue
        first = min(ts_list)
        last = max(ts_list)

        # Day with highest match count (calendar day in UTC). Tiebreak: most
        # recent day wins — recent peaks are more interesting than ancient ones.
        per_day: dict[str, int] = defaultdict(int)
        for t in ts_list:
            per_day[t.strftime("%Y-%m-%d")] += 1
        peak_day_str, peak_count = max(per_day.items(), key=lambda kv: (kv[1], kv[0]))
        peak_dt = datetime.strptime(peak_day_str, "%Y-%m-%d")

        # Resolved subject_type: explicit field if set, otherwise fall back
        # to the name-based heuristic so every event has a quadrant.
        resolved_subject = info["subject_type"]
        if not resolved_subject:
            try:
                resolved_subject = classify_subject(info["name"] or "")
            except Exception:
                resolved_subject = "media"
        base_meta = {
            "label": info["name"],
            "frame_id": fid,
            "owner_type": info["owner_type"],
            "subject_type": resolved_subject,
            # Lifetime article-match count for this frame — drives pin size
            # on the Timeline as a stable measure of the narrative's reach.
            "total_mentions": len(ts_list),
        }
        events.append({
            "type": "narrative_emerged",
            "timestamp": first.isoformat() + "Z",
            **base_meta,
        })
        events.append({
            "type": "narrative_peaked",
            "timestamp": peak_dt.isoformat() + "Z",
            "peak_count": peak_count,
            **base_meta,
        })
        # Only emit fade if the frame is stale
        if last < stale_cutoff:
            events.append({
                "type": "narrative_faded",
                "timestamp": last.isoformat() + "Z",
                **base_meta,
            })

    type_order = {"narrative_emerged": 0, "narrative_peaked": 1, "narrative_faded": 2}
    events.sort(key=lambda e: (e.get("timestamp") or "", type_order.get(e["type"], 9)))
    return events
