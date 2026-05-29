"""CRUD + auto-suggest routes for campaign narrative frames."""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import (
    ClaimRecord, ClaimRecordEntity, Entity, EntityMention, EntityRelation,
    NarrativeFrame, NarrativeFrameMention, Outlet, SourceItem,
)
from app.services import narrative_frames as svc

router = APIRouter(prefix="/narrative-frames", tags=["narrative-frames"])


class FrameCreate(BaseModel):
    name: str
    description: Optional[str] = None
    owner_type: str = "candidate"
    # Optional — when set, the user has explicitly picked a 4-quadrant slot
    # (e.g. "Cognetti's Offense" = owner=candidate, subject=opponent).
    # When omitted, the heuristic in subject_classifier.py infers from name.
    subject_type: Optional[str] = None


class FrameUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    owner_type: Optional[str] = None
    subject_type: Optional[str] = None
    active: Optional[bool] = None


@router.get("")
def list_frames(db: Session = Depends(get_db)):
    return svc.get_frames_with_counts(db)


@router.get("/{frame_id}/detail")
def frame_detail(frame_id: int, db: Session = Depends(get_db)):
    """Full per-frame deep-dive: articles, daily activity, quotes, outlet mix."""
    detail = svc.get_frame_detail(db, frame_id)
    if not detail:
        raise HTTPException(status_code=404, detail="frame not found")
    return detail


@router.get("/{frame_id}/timeline")
def frame_timeline(
    frame_id: int, days: int = 90, db: Session = Depends(get_db),
):
    """Variant-level mention timeline for a frame.

    Returns per-day mention counts per variant + per-day frame totals over the
    requested window. Use this to render variant-evolution charts: stacked
    area showing how each variant's share has shifted over time.

    Query params:
      days — lookback window (default 90)
    """
    timeline = svc.get_frame_timeline(db, frame_id, days_back=days)
    if not timeline:
        raise HTTPException(status_code=404, detail="frame not found")
    return timeline


@router.get("/{frame_id}/variant-articles")
def frame_variant_articles(
    frame_id: int,
    variant_id: int,
    date: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Articles supporting one variant of a frame, optionally on one date.

    Powers the click-to-filter behavior on the Variant Evolution chart. The
    user clicks a spike for variant V on day D, and we return the articles
    that produced that height.

    Query params:
      variant_id — required, the FrameVariant.id
      date       — optional YYYY-MM-DD; filters to articles published that day
    """
    return svc.get_variant_articles(db, frame_id, variant_id, date=date)


@router.get("/{frame_id}/quote-evidence")
def frame_quote_evidence(
    frame_id: int,
    limit: int = 200,
    db: Session = Depends(get_db),
):
    """Verbatim claim_record quotes drawn from articles that match this frame.

    v15.0 evidence layer: instead of paraphrasing what an article said about
    a frame, surface the actual quote spans the extractor identified. The
    join is article-level — every claim_record from an article matched to
    this frame is a candidate quote. Labels (attack / endorsement /
    policy_position / etc.) are presented as-is so the UI can group.

    Response shape:
      {
        "frame_id": int,
        "frame_name": str,
        "total": int,
        "by_label": { label_or_null: count, ... },
        "quotes": [ { evidence_span, label, confidence, article: { ... }, entities: [...] } ]
      }
    """
    frame = db.query(NarrativeFrame).filter(NarrativeFrame.id == frame_id).first()
    if not frame:
        raise HTTPException(status_code=404, detail="frame not found")

    # Article IDs the frame matches (deduped).
    article_ids = [
        sid for (sid,) in db.query(NarrativeFrameMention.source_item_id)
        .filter(NarrativeFrameMention.frame_id == frame_id)
        .distinct()
        .all()
    ]
    if not article_ids:
        return {"frame_id": frame_id, "frame_name": frame.name,
                "total": 0, "by_label": {}, "quotes": []}

    rows = (
        db.query(ClaimRecord, SourceItem, Outlet)
        .join(SourceItem, SourceItem.id == ClaimRecord.article_id)
        .outerjoin(Outlet, Outlet.id == SourceItem.outlet_id)
        .filter(ClaimRecord.article_id.in_(article_ids))
        .order_by(SourceItem.published_at.desc().nullslast())
        .limit(limit)
        .all()
    )

    # Bulk-fetch entity tags for the returned claim_records.
    cr_ids = [cr.id for cr, _, _ in rows]
    ent_map: dict[int, list[dict]] = {}
    if cr_ids:
        ent_rows = (
            db.query(ClaimRecordEntity, Entity)
            .join(Entity, Entity.id == ClaimRecordEntity.entity_id)
            .filter(ClaimRecordEntity.claim_record_id.in_(cr_ids))
            .all()
        )
        for cre, ent in ent_rows:
            ent_map.setdefault(cre.claim_record_id, []).append({
                "id": ent.canonical_id,
                "name": ent.name,
                "type": ent.type,
                "surface": cre.surface_text,
            })

    by_label: dict[str, int] = {}
    quotes = []
    for cr, si, outlet in rows:
        key = cr.label or "unlabeled"
        by_label[key] = by_label.get(key, 0) + 1
        quotes.append({
            "id": cr.id,
            "evidence_span": cr.evidence_span,
            "label": cr.label,
            "confidence": cr.confidence,
            "extractor_version": cr.extractor_version,
            "entities": ent_map.get(cr.id, []),
            "article": {
                "id": si.id,
                "title": si.title,
                "url": si.source_url,
                "published_at": si.published_at.isoformat() if si.published_at else None,
                "outlet_name": outlet.name if outlet else si.source_name,
                "bias_label": outlet.bias_label if outlet else None,
                "reliability_score": outlet.reliability_score if outlet else None,
            },
        })

    return {
        "frame_id": frame_id,
        "frame_name": frame.name,
        "total": len(quotes),
        "by_label": by_label,
        "quotes": quotes,
    }


def _invalidate_established_landscape() -> None:
    """Drop the established-landscape cache when frames change.

    Any frame create/update/delete shifts the topical map, so the cached
    UMAP projection becomes stale. Cheap to recompute (~1s for ~20 frames)
    so we just clear and let the next GET rebuild.
    """
    try:
        from app.services.narrative_landscape_established import invalidate_cache
        invalidate_cache()
    except Exception:
        pass  # best-effort; never block the mutation on cache-bust failure


def _invalidate_candidate_landscape() -> None:
    """Drop the candidate-frames-landscape cache after promote/merge.

    The Review Queue's "Proposed narratives" list reads from this cache.
    Without invalidation, promoting a cluster leaves it visible in the
    queue because the cached UMAP response still contains it — the
    candidate frames are marked resolved, but the cache predates that.
    """
    try:
        from app.services.narrative_landscape import invalidate_cache
        invalidate_cache()
    except Exception:
        pass  # best-effort; never block the mutation on cache-bust failure


def _schedule_rematch_after_edit() -> None:
    """Best-effort: enqueue a debounced background rematch. Never blocks
    the response if scheduling fails — the daily rematch_recent catches drift.
    """
    try:
        from app.services.scheduler import schedule_rematch_after_frame_edit
        schedule_rematch_after_frame_edit()
    except Exception:
        # Don't fail the CRUD response on scheduler issues.
        pass


@router.post("")
def create_frame(body: FrameCreate, db: Session = Depends(get_db)):
    owner = body.owner_type if body.owner_type in ("candidate", "opponent", "media") else "candidate"
    subject = body.subject_type if body.subject_type in ("candidate", "opponent", "media") else None
    frame = NarrativeFrame(
        name=body.name.strip(),
        description=(body.description or "").strip() or None,
        owner_type=owner,
        subject_type=subject,
        source="human",
        active=True,
    )
    db.add(frame)
    db.commit()
    db.refresh(frame)
    _invalidate_established_landscape()
    _schedule_rematch_after_edit()
    return {
        "id": frame.id, "name": frame.name, "description": frame.description,
        "owner_type": frame.owner_type, "subject_type": frame.subject_type,
    }


@router.put("/{frame_id}")
def update_frame(frame_id: int, body: FrameUpdate, db: Session = Depends(get_db)):
    frame = db.query(NarrativeFrame).get(frame_id)
    if not frame:
        raise HTTPException(status_code=404, detail="Frame not found")
    name_or_desc_changed = False
    if body.name is not None:
        if frame.name != body.name.strip():
            name_or_desc_changed = True
        frame.name = body.name.strip()
    if body.description is not None:
        new_desc = body.description.strip() or None
        if frame.description != new_desc:
            name_or_desc_changed = True
        frame.description = new_desc
    if body.owner_type is not None and body.owner_type in ("candidate", "opponent", "media"):
        frame.owner_type = body.owner_type
    if body.subject_type is not None:
        # Empty string clears back to NULL (= use heuristic). Any other valid
        # value persists the user's explicit choice.
        if body.subject_type == "":
            frame.subject_type = None
        elif body.subject_type in ("candidate", "opponent", "media"):
            frame.subject_type = body.subject_type
    if body.active is not None:
        frame.active = body.active
    frame.updated_at = datetime.utcnow()
    db.commit()
    _invalidate_established_landscape()
    # Only trigger rematch when the matching-relevant fields (name/description)
    # changed. Owner/subject/active toggles don't affect embeddings, so a
    # rematch isn't needed.
    if name_or_desc_changed or body.active is not None:
        _schedule_rematch_after_edit()
    return {"ok": True}


@router.delete("/{frame_id}")
def delete_frame(
    frame_id: int,
    confirm: str = "",
    dry_run: bool = False,
    db: Session = Depends(get_db),
):
    """Delete a NarrativeFrame and all its dependents.

    Routed through ``safe_delete_frame`` to ensure cascade to
    FrameClusterMatch / FrameVariant / FrameStageHistory and SET NULL
    on CandidateFrame.resolved_to_frame_id. The previous code path used
    ``db.delete(frame)`` which only cascaded to NarrativeFrameMention
    via the ORM relationship, leaving the rest as orphans (we found
    13 orphan frame_variants + 4 orphan candidate_frames in tonight's
    audit, all traceable to frames deleted via this route).

    Safety:
      - ``?dry_run=true`` returns the cascade counts that *would* be deleted,
        without touching anything. Use this to preview the blast radius
        before confirming a frame deletion.
      - The actual delete requires ``?confirm=DELETE+FRAME`` (URL-encoded).
        A frame can carry hundreds of FrameClusterMatch rows and tens of
        variants — a misclick here is meaningful data loss.
    """
    frame = db.query(NarrativeFrame).get(frame_id)
    if not frame:
        raise HTTPException(status_code=404, detail="Frame not found")

    if dry_run:
        # Preview cascade counts via a read-only sweep — same predicates
        # safe_delete_frame uses, but no deletes are issued.
        from app.models import (
            CandidateFrame as _CF,
            FrameClusterMatch as _FCM,
            FrameStageHistory as _FSH,
            FrameVariant as _FV,
            NarrativeFrameMention as _NFM,
        )
        return {
            "dry_run": True,
            "frame_id": frame_id,
            "frame_name": frame.name,
            "would_delete": {
                "frame_cluster_matches": db.query(_FCM).filter(
                    _FCM.frame_id == frame_id
                ).count(),
                "narrative_frame_mentions": db.query(_NFM).filter(
                    _NFM.frame_id == frame_id
                ).count(),
                "frame_variants": db.query(_FV).filter(
                    _FV.frame_id == frame_id
                ).count(),
                "frame_stage_history": db.query(_FSH).filter(
                    _FSH.frame_id == frame_id
                ).count(),
                "candidate_frame_refs_cleared": db.query(_CF).filter(
                    _CF.resolved_to_frame_id == frame_id
                ).count(),
                "narrative_frame": 1,
            },
        }

    if confirm != "DELETE FRAME":
        raise HTTPException(
            status_code=400,
            detail=(
                "Frame deletion cascades through FrameClusterMatch, "
                "FrameVariant, FrameStageHistory, and NarrativeFrameMention. "
                "Pass ?confirm=DELETE+FRAME (URL-encoded) to proceed, "
                "or ?dry_run=true to preview cascade counts first."
            ),
        )

    from app.services.safe_deletes import safe_delete_frame
    counts = safe_delete_frame(db, frame_id)
    db.commit()
    _invalidate_established_landscape()
    # Deleted frames orphan their matches via safe_delete_frame; no rematch
    # is needed for the deleted frame itself, but other articles may now match
    # remaining frames differently if the deletion affected shortlists (rare,
    # but cheap to re-verify).
    _schedule_rematch_after_edit()
    return {"ok": True, "deleted": counts}


@router.post("/suggest")
def suggest_frames(days_back: int = 14, db: Session = Depends(get_db)):
    """Ask the LLM to suggest narrative frames from recent article summaries."""
    frames = svc.suggest_frames(db, days_back=days_back)
    return {"suggested": len(frames), "frames": frames}


class PromoteCandidateRequest(BaseModel):
    suggested_name: str
    suggested_description: Optional[str] = ""
    owner_type: str = "media"
    # Optional — set when the user picked a specific quadrant in the promote
    # form. NULL falls back to heuristic at read time.
    subject_type: Optional[str] = None
    candidate_frame_ids: list[int]


@router.get("/candidate-frames/pending")
def list_pending_candidate_clusters(days_back: int = 21, db: Session = Depends(get_db)):
    """Return clusters of candidate_frames that meet promotion thresholds.

    Reads from the module-level cache populated by the daily scheduled job
    (candidate_frame_promoter_daily). On a fresh process or stale cache,
    falls through to live compute — which can take 5-30s due to Gemini
    embedding rate limits. The daily job keeps this hot in production.

    Response also surfaces `last_error` (from refresh_cache) and
    `embeddings_available` so the UI can show WHY there are 0 suggestions
    when there are zero — previously a Gemini-quota outage looked
    identical to "no narratives detected yet."

    Read-only. Returns at most a few dozen clusters; sorted strongest first.
    """
    from app.services.candidate_frame_promoter import (
        get_cached_suggestions, _CACHE, _EMBEDDINGS_AVAILABLE,
    )
    suggestions, computed_at, was_live = get_cached_suggestions(db, days_back=days_back)
    return {
        "count": len(suggestions),
        "suggestions": suggestions,
        "computed_at": computed_at.isoformat() if computed_at else None,
        "from_cache": not was_live,
        # Observability — empty-with-error is meaningfully different from
        # empty-because-no-narratives-found.
        "last_error": _CACHE.get("last_error"),
        "embeddings_available": _EMBEDDINGS_AVAILABLE,
    }


@router.get("/candidate-frames/snapshot")
def candidate_frames_snapshot(db: Session = Depends(get_db)):
    """Persistent snapshot of proposed-narrative clusters.

    Returns the same shape as /candidate-frames/landscape but reads from
    the `proposed_cluster_snapshots` table instead of recomputing HDBSCAN
    every request. The list stays stable between user visits — new
    clusters only appear when /candidate-frames/snapshot/refresh is hit
    (manual button or scheduler), and existing clusters only disappear
    when the user promotes / merges / dismisses them.

    First-load fallback: if the snapshot table is empty, automatically
    take an initial snapshot so the UI never shows "no proposals" when
    proposals actually exist. Subsequent loads return cached data.
    """
    from app.services.proposed_cluster_snapshot import (
        get_open_snapshots, take_snapshot,
    )
    from app.models import ProposedClusterSnapshot
    if db.query(ProposedClusterSnapshot).count() == 0:
        take_snapshot(db, days_back=21)
    return get_open_snapshots(db)


@router.post("/candidate-frames/snapshot/refresh")
def candidate_frames_snapshot_refresh(days_back: int = 21, db: Session = Depends(get_db)):
    """Re-run HDBSCAN and persist new clusters into the snapshot table.

    Existing snapshot rows are matched by fingerprint and refreshed in
    place (size / outlet counts may have changed). New clusters get new
    rows. Clusters that are no longer in the compute STAY in the open
    list — the user removes them by acting on them. This is the trade
    we make to keep the queue stable.
    """
    from app.services.proposed_cluster_snapshot import take_snapshot
    return take_snapshot(db, days_back=days_back)


class SnapshotDismissRequest(BaseModel):
    candidate_frame_ids: list[int]


@router.post("/candidate-frames/snapshot/dismiss")
def candidate_frames_snapshot_dismiss(
    req: SnapshotDismissRequest, db: Session = Depends(get_db),
):
    """Mark a snapshot row as dismissed by its member candidate_frame_ids.

    The frontend has the member ids in hand for any row it renders, so
    fingerprinting on the server side keeps the wire format simple.
    """
    from app.services.proposed_cluster_snapshot import mark_dismissed_by_member_ids
    ok = mark_dismissed_by_member_ids(db, req.candidate_frame_ids)
    return {"ok": ok}


@router.get("/candidate-frames/landscape")
def narrative_landscape(days_back: int = 21, db: Session = Depends(get_db)):
    """2D projection of pending candidate_frame embeddings + HDBSCAN clusters.

    Pure visualization data. Read-only. Cached for 25h (matches the cards
    endpoint so the two views stay aligned).

    Response shape: see narrative_landscape.NarrativeLandscape TypedDict.
    """
    from app.services.narrative_landscape import get_landscape
    return get_landscape(db, days_back=days_back)


@router.get("/landscape-established-dots")
def established_landscape_dots(db: Session = Depends(get_db)):
    """Dot-level landscape: every article extract as a 2D-projected dot.

    The V12 reframing of the Established view — atomic unit is an article
    extract, not a frame bubble. Frame and topic groupings still exist as
    metadata so the frontend can draw nested hulls + labels.

    Response shape: see services/landscape_dots.DotLandscape.
    """
    from app.services.landscape_dots import get_dot_landscape
    return get_dot_landscape(db)


@router.get("/landscape-established")
def established_landscape(db: Session = Depends(get_db)):
    """2D projection of ALREADY-PROMOTED narrative frames.

    Companion to the candidate-frames landscape — answers "where do my
    currently tracked narratives sit relative to each other?" rather than
    "what new themes are bubbling up?". One bubble per active frame,
    positioned by topical similarity (UMAP over name + description embedding).

    Response shape: see narrative_landscape_established.EstablishedLandscape.
    """
    from app.services.narrative_landscape_established import get_established_landscape
    return get_established_landscape(db)


@router.get("/{frame_id}/landscape-detail")
def frame_landscape_detail(frame_id: int, db: Session = Depends(get_db)):
    """Member articles for a single established frame — lazy-loaded when
    the user zooms into a bubble on the Landscape page.

    Kept separate from /landscape-established so the initial map fetch
    stays light (17 frames × ~30 articles each would be ~500 rows on
    every page load). Only the bubble the user opens pays the cost.
    """
    from app.services.narrative_landscape_established import get_frame_member_articles
    frame = db.query(NarrativeFrame).get(frame_id)
    if not frame:
        raise HTTPException(status_code=404, detail="frame not found")
    articles = get_frame_member_articles(db, frame_id, limit=40)
    return {"frame_id": frame_id, "articles": articles}


@router.post("/candidate-frames/promote")
def promote_candidate_cluster(req: PromoteCandidateRequest, db: Session = Depends(get_db)):
    """Promote a candidate_frame cluster into a real NarrativeFrame.

    The request body carries the user-edited name/description/owner_type
    (the API returned suggestions, but the human gets the final say) plus
    the list of candidate_frame ids to mark as resolved.
    """
    from app.services.candidate_frame_promoter import promote_cluster
    try:
        frame = promote_cluster(
            db,
            suggested_name=req.suggested_name,
            suggested_description=req.suggested_description or "",
            owner_type=req.owner_type,
            subject_type=req.subject_type,
            candidate_frame_ids=req.candidate_frame_ids,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    _invalidate_established_landscape()
    _invalidate_candidate_landscape()
    # Stamp the snapshot row (if any) so the proposal disappears from the
    # Review Queue's open list. Best-effort: failure here just means the
    # row sticks around until the next snapshot refresh.
    try:
        from app.services.proposed_cluster_snapshot import mark_applied_by_member_ids
        mark_applied_by_member_ids(db, req.candidate_frame_ids, frame_id=frame.id)
    except Exception:
        pass
    return {
        "id": frame.id,
        "name": frame.name,
        "description": frame.description,
        "owner_type": frame.owner_type,
        "subject_type": frame.subject_type,
    }


@router.post("/audit-duplicates")
def audit_duplicates(db: Session = Depends(get_db)):
    """Ask the LLM to find and merge semantic duplicate frames."""
    result = svc.audit_duplicates(db)
    return result


@router.post("/rematch")
def rematch_articles(days_back: int = 365, db: Session = Depends(get_db)):
    """Enqueue a rematch job and return immediately (<1 s)."""
    from app.services.scheduler import enqueue_rematch
    enqueue_rematch(days_back=days_back)
    return {"status": "queued", "days_back": days_back}


@router.get("/rematch-progress")
def rematch_progress():
    """Return the current rematch progress (polling-friendly)."""
    return svc.get_rematch_progress()


@router.delete("/{frame_id}/mentions/{source_item_id}")
def remove_mention(frame_id: int, source_item_id: int, db: Session = Depends(get_db)):
    """Remove a frame mention.

    Returns a response that EXPLICITLY surfaces the cascade: the user's
    intent ("this one article shouldn't be tagged with this frame")
    necessarily expands to "this story-cluster shouldn't be tagged"
    because the cluster-level FrameClusterMatch has no per-article
    granularity. If the cluster has N other articles, removing one
    mention drops the frame match for all N.

    Response includes `affected_other_articles` so the UI can warn the
    user before-or-after the click. Previously this was silent — the
    user removed one mention and the dashboard count silently dropped
    by 50.
    """
    from app.models import FrameClusterMatch, SourceItem
    mention = (
        db.query(NarrativeFrameMention)
        .filter_by(frame_id=frame_id, source_item_id=source_item_id)
        .first()
    )
    if mention:
        db.delete(mention)

    # Count how many OTHER articles in the same cluster will lose this
    # frame match as a side effect. Surface this to the caller.
    affected_others = 0
    item = db.query(SourceItem).filter_by(id=source_item_id).first()
    if item and item.story_cluster_id:
        affected_others = (
            db.query(SourceItem)
            .filter(SourceItem.story_cluster_id == item.story_cluster_id,
                    SourceItem.id != source_item_id)
            .count()
        )
        db.query(FrameClusterMatch).filter_by(
            frame_id=frame_id, story_cluster_id=item.story_cluster_id
        ).delete()

    db.commit()
    return {
        "ok": True,
        "affected_other_articles": affected_others,
        "cluster_match_removed": bool(item and item.story_cluster_id),
    }


@router.get("/{frame_id}/graph")
def frame_graph(frame_id: int, limit: int = 50, db: Session = Depends(get_db)):
    """Join the narrative-frames system to the entity-relations graph.

    Returns the entities and entity_relations that propagate this frame —
    i.e. those whose supporting articles overlap with the frame's mentions.

    Useful for answering "what's actually being SAID inside this narrative?" —
    not just which articles touch the frame, but which entity-level claims
    those articles produce. Closes the loop between narrative-tracking
    (what story is being told) and entity-graph (who-does-what-to-whom).
    """
    import json as _json

    frame = db.query(NarrativeFrame).filter(NarrativeFrame.id == frame_id).one_or_none()
    if not frame:
        raise HTTPException(404, f"frame {frame_id} not found")

    # Articles supporting this frame
    article_ids = {
        sid for (sid,) in
        db.query(NarrativeFrameMention.source_item_id)
        .filter(NarrativeFrameMention.frame_id == frame_id)
        .distinct()
        .all()
    }

    if not article_ids:
        return {
            "frame": {"id": frame.id, "name": frame.name, "description": frame.description,
                      "owner_type": frame.owner_type},
            "supporting_article_count": 0,
            "entities": [],
            "relations": [],
        }

    # Entities: count mentions IN this frame vs overall
    mention_rows = (
        db.query(EntityMention.entity_id)
        .filter(EntityMention.article_id.in_(article_ids))
        .all()
    )
    from collections import Counter
    entity_counts: Counter = Counter()
    for (eid,) in mention_rows:
        entity_counts[eid] += 1

    if entity_counts:
        entities = (
            db.query(Entity)
            .filter(Entity.id.in_(entity_counts.keys()))
            .all()
        )
    else:
        entities = []

    entities_out = []
    for e in sorted(entities, key=lambda x: -entity_counts.get(x.id, 0)):
        entities_out.append({
            "id": e.canonical_id,
            "name": e.name,
            "type": e.type,
            "affiliation": e.affiliation,
            "mention_count_in_frame": entity_counts.get(e.id, 0),
            "overall_mention_count": e.mention_count or 0,
            "seeded": bool(e.seeded),
        })
    entities_out = entities_out[:limit]

    # Relations: for each relation, compute overlap between its source_articles
    # and this frame's article_ids. Skip those with no overlap.
    all_relations = db.query(EntityRelation).all()
    entity_by_id = {e.id: e for e in db.query(Entity).all()}

    relations_out: list[dict] = []
    for r in all_relations:
        try:
            r_articles = set(_json.loads(r.source_articles or "[]"))
        except Exception:
            r_articles = set()
        overlap = r_articles & article_ids
        if not overlap:
            continue
        subj = entity_by_id.get(r.subject_id)
        obj = entity_by_id.get(r.object_id)
        if not subj or not obj:
            continue
        relations_out.append({
            "id": f"r-{r.id}",
            "source": subj.canonical_id,
            "source_name": subj.name,
            "target": obj.canonical_id,
            "target_name": obj.name,
            "type": r.predicate,
            "weight_in_frame": len(overlap),
            "overall_weight": r.weight or 0,
            "in_frame_share": round(len(overlap) / max(r.weight or 1, 1), 2),
            "sample_quote": r.sample_quote or "",
            "confidence": r.confidence or "medium",
        })

    # Sort by frame-specific weight (most-propagating relations first)
    relations_out.sort(key=lambda x: (-x["weight_in_frame"], -x["overall_weight"]))
    relations_out = relations_out[:limit]

    return {
        "frame": {"id": frame.id, "name": frame.name, "description": frame.description,
                  "owner_type": frame.owner_type},
        "supporting_article_count": len(article_ids),
        "entities": entities_out,
        "relations": relations_out,
    }


@router.get("/by-entity/{canonical_id}")
def frames_for_entity(canonical_id: str, db: Session = Depends(get_db)):
    """Reverse direction: which narrative frames feature this entity?

    For a given entity, find narrative frames whose supporting articles also
    mention this entity. Lets the EntityNetwork side panel show "this entity
    appears in N narratives."
    """
    entity = db.query(Entity).filter(Entity.canonical_id == canonical_id).one_or_none()
    if not entity:
        raise HTTPException(404, f"entity {canonical_id!r} not found")

    article_ids = {
        aid for (aid,) in
        db.query(EntityMention.article_id)
        .filter(EntityMention.entity_id == entity.id)
        .distinct()
        .all()
    }
    if not article_ids:
        return {"entity": {"id": entity.canonical_id, "name": entity.name}, "frames": []}

    # Find frames whose mentions overlap with these articles
    rows = (
        db.query(NarrativeFrameMention.frame_id, NarrativeFrame.name,
                 NarrativeFrame.owner_type, NarrativeFrame.description)
        .join(NarrativeFrame, NarrativeFrame.id == NarrativeFrameMention.frame_id)
        .filter(NarrativeFrameMention.source_item_id.in_(article_ids))
        .filter(NarrativeFrame.active == True)  # noqa: E712
        .all()
    )
    from collections import Counter
    frame_counts: Counter = Counter()
    frame_meta: dict[int, dict] = {}
    for fid, name, owner_type, desc in rows:
        frame_counts[fid] += 1
        frame_meta[fid] = {"id": fid, "name": name, "owner_type": owner_type,
                           "description": desc}

    frames_out = []
    for fid, count in frame_counts.most_common():
        meta = frame_meta[fid]
        meta["article_overlap_count"] = count
        frames_out.append(meta)

    return {
        "entity": {"id": entity.canonical_id, "name": entity.name, "type": entity.type},
        "frames": frames_out,
    }
