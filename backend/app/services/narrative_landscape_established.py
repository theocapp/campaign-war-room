"""
2D landscape over ESTABLISHED narrative frames (already-promoted ones).

Differs from `narrative_landscape.py` in two important ways:

  1. No clustering. Established frames are already discrete tracked narratives;
     each frame is its own point on the map (not a member of some HDBSCAN
     cluster).
  2. Sizing comes from real engagement metrics (mentions_total, outlet count)
     instead of the candidate-frame "size = how many AI suggestions clumped
     here" heuristic.

The UMAP projection embeds `name + description` for each active frame, so
positions reflect topical similarity. Two frames sitting close together =
the AI sees them as semantically related (e.g. "Bresnahan's healthcare
record" and "Cognetti's affordability message" might end up adjacent if
both discuss healthcare cost).

Why this view is useful
-----------------------
The proposed-narratives landscape answers "what new themes are emerging?"
This view answers "where do my CURRENTLY TRACKED narratives sit relative to
each other, and which ones own which topical neighborhoods?"

If two of your established frames land on top of each other, that's a
hint they may be the same narrative under two names (consolidation
candidate). If a region is empty, that's an opening — a topic with no
tracked narrative in it.

Caching
-------
Same TTL convention as the candidate landscape (25h). Established frames
don't change often, so even daily refresh is overkill — but we keep the
window short enough that a freshly promoted frame shows up within a day.
"""
from __future__ import annotations
import logging
import threading
from datetime import datetime
from typing import Optional, TypedDict

from sqlalchemy.orm import Session

from app.models import NarrativeFrame

logger = logging.getLogger(__name__)


class EstablishedFramePoint(TypedDict):
    """One established NarrativeFrame as a bubble on the map."""
    frame_id: int
    name: str
    description: Optional[str]
    owner_type: str          # who BENEFITS: candidate | opponent | media
    subject_type: str        # who it's ABOUT: candidate | opponent | media (V13.19)
    x: float                 # UMAP coord (raw, frontend rescales)
    y: float                 # UMAP coord (raw, frontend rescales)
    mentions_total: int      # drives bubble SIZE
    mentions_this_week: int
    outlet_count: int        # distinct outlets across all mentions
    outlet_tier_counts: dict[str, int]
    stage: Optional[str]
    momentum_signal: Optional[str]


class TopicRegionEntry(TypedDict):
    """One named region returned alongside the frames list. Mirrors
    services/topic_regions.TopicRegion — kept here as a TypedDict so the
    OpenAPI / frontend type hints line up."""
    region_id: int
    persisted_id: Optional[int]   # DB row id for inline-edit endpoint
    label: str
    member_frame_ids: list[int]
    edited_by_user: bool
    owner_mix: dict[str, float]
    # V13.19 — 4-quadrant breakdown:
    # {"our_defense", "our_offense", "their_defense", "their_offense", "media"}
    # Each value is the authority-weighted contribution of frames in that
    # quadrant (same weighting as owner_mix). Lets the frontend render
    # topic colors using the (owner × subject) two-axis encoding.
    quadrant_mix: dict[str, float]


class EstablishedLandscape(TypedDict):
    frames: list[EstablishedFramePoint]
    regions: list[TopicRegionEntry]            # labeled HDBSCAN groupings
    ungrouped_frame_ids: list[int]             # noise — frames that didn't join a region
    computed_at: str
    n_total: int
    error: Optional[str]


_CACHE: dict = {
    "data": None,
    "computed_at": None,
}
_lock = threading.Lock()


def get_established_landscape(db: Session, max_age_hours: int = 25) -> EstablishedLandscape:
    """Return cached projection or compute fresh."""
    cached = _CACHE.get("data")
    computed_at = _CACHE.get("computed_at")

    if (
        cached is None
        or computed_at is None
        or (datetime.utcnow() - computed_at).total_seconds() > max_age_hours * 3600
    ):
        return _compute(db)
    return cached


def invalidate_cache() -> None:
    """Drop the cached projection. Called after promote/delete to force a refresh.

    Also clears the topic-regions and dot-landscape caches since both are
    computed against / derived from this landscape — stale child caches
    would point at the wrong frames after a frame add/remove.
    """
    with _lock:
        _CACHE["data"] = None
        _CACHE["computed_at"] = None
    try:
        from app.services.topic_regions import invalidate_cache as _inv_regions
        _inv_regions()
    except Exception:
        pass
    try:
        from app.services.landscape_dots import invalidate_cache as _inv_dots
        _inv_dots()
    except Exception:
        pass


def _compute(db: Session) -> EstablishedLandscape:
    """Embed + project active frames. Writes to cache on success."""
    import numpy as np

    try:
        from umap import UMAP
        from app.services._numba_serialize import numba_lock
    except Exception as exc:
        return _empty(f"dependency missing: {exc}")

    try:
        from app.services.embeddings import embed_texts
    except Exception as exc:
        return _empty(f"embeddings module unavailable: {exc}")

    # Pull the real engagement metrics via the existing aggregator so
    # mentions/outlet counts match what the Narratives list shows.
    from app.services.narrative_frames import get_frames_with_counts

    try:
        frames_with_counts = get_frames_with_counts(db)
    except Exception as exc:
        logger.exception("established landscape: get_frames_with_counts failed")
        return _empty(f"could not load frame metrics: {exc}")

    if not frames_with_counts:
        return _empty(None)

    # Embed name + description. Strip candidate/opponent names first so
    # positions cluster by TOPIC rather than SUBJECT — e.g. healthcare
    # narratives (both Cognetti's and Bresnahan's) land near each other.
    # Color still tells the user which side benefits, so the SUBJECT
    # channel isn't lost — see text_anonymize.py docstring.
    from app.services.text_anonymize import get_anonymizer
    strip = get_anonymizer(db)
    texts = [
        strip(f"{f['name']}. {(f.get('description') or '')[:400]}").strip()
        for f in frames_with_counts
    ]
    embeddings = embed_texts(texts, task_type="SEMANTIC_SIMILARITY")

    keep_idx = [i for i, e in enumerate(embeddings) if e is not None]
    if len(keep_idx) < 2:
        # UMAP needs at least 2 points. With 0-1 frames the "map" doesn't
        # make sense anyway — show a friendly empty state.
        return _empty("too few frames with successful embeddings (need ≥ 2)")

    embs = np.array([embeddings[i] for i in keep_idx], dtype=np.float32)

    # L2-normalize so cosine distance behaves like Euclidean — matches the
    # candidate landscape's convention.
    norms = np.linalg.norm(embs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    embs_norm = embs / norms

    # UMAP. With ~17 frames, n_neighbors needs to stay small (default 15
    # would cause it to use all points as "neighbors" → flat result).
    n_neighbors = max(2, min(8, len(keep_idx) - 1))
    reducer = UMAP(
        n_components=2,
        n_neighbors=n_neighbors,
        min_dist=0.25,        # spread points out a bit more than candidate landscape
        metric="cosine",
        random_state=42,
        n_jobs=1,
    )
    # Serialize against any other UMAP/HDBSCAN call in the process.
    # See _numba_serialize.py for the rationale.
    with numba_lock:
        coords = reducer.fit_transform(embs_norm)

    # Per-frame outlet-tier counts derived from outlet_tiers in get_frames_with_counts.
    # The frame dict from that function already includes `outlet_tiers` so we
    # can reuse it directly — keeps the "outlet diversity" signal consistent
    # with the Narratives list and Dashboard.
    #
    # V13.19 — subject_type computed once per frame via the shared subject-
    # classifier. Lives alongside owner_type so the frontend can render the
    # 4-quadrant color scheme (owner × subject) without any per-frame
    # lookups of its own.
    from app.services.subject_classifier import get_subject_classifier
    classify = get_subject_classifier(db)
    points: list[EstablishedFramePoint] = []
    for j, original_idx in enumerate(keep_idx):
        f = frames_with_counts[original_idx]
        tiers = f.get("outlet_tiers") or {}
        outlet_count = sum(int(v or 0) for v in tiers.values())
        points.append({
            "frame_id": int(f["id"]),
            "name": f["name"],
            "description": f.get("description"),
            "owner_type": f.get("owner_type") or "media",
            "subject_type": classify(f["name"]),
            "x": float(coords[j, 0]),
            "y": float(coords[j, 1]),
            "mentions_total": int(f.get("mentions_total") or 0),
            "mentions_this_week": int(f.get("mentions_this_week") or 0),
            "outlet_count": outlet_count,
            "outlet_tier_counts": {
                "national": int(tiers.get("national") or 0),
                "regional": int(tiers.get("regional") or 0),
                "local": int(tiers.get("local") or 0),
                "blog": int(tiers.get("blog") or 0),
                "social": int(tiers.get("social") or 0),
            },
            "stage": f.get("stage"),
            "momentum_signal": f.get("momentum_signal"),
        })

    # ── Topic regions (HDBSCAN + LLM labels) ──────────────────────────────
    # Runs against the same frame positions we just computed so the
    # regions match the visible bubbles 1:1. Falls back to empty regions
    # if HDBSCAN/LLM fails — landscape still works, just without overlays.
    regions: list[TopicRegionEntry] = []
    ungrouped: list[int] = []
    try:
        from app.services.topic_regions import get_topic_regions
        from app.models import TopicRegionLabel
        tr = get_topic_regions(db, points)
        # Look up the persisted DB row id for each region so the frontend
        # can call PUT /api/topic-regions/{persisted_id}/label for edits.
        # Matched by the same sorted member_frame_ids JSON the service
        # writes during _compute.
        import json as _json
        for region in tr["regions"]:
            key_json = _json.dumps(sorted(region["member_frame_ids"]))
            row = (
                db.query(TopicRegionLabel)
                .filter(TopicRegionLabel.member_frame_ids_json == key_json)
                .first()
            )
            regions.append({
                "region_id": region["region_id"],
                "persisted_id": int(row.id) if row else None,
                "label": region["label"],
                "member_frame_ids": region["member_frame_ids"],
                "edited_by_user": region["edited_by_user"],
                "owner_mix": region["owner_mix"],
                "quadrant_mix": region.get("quadrant_mix") or {},
            })
        ungrouped = tr["ungrouped_frame_ids"]
    except Exception as exc:
        logger.warning("established landscape: topic regions failed: %s", exc)

    result: EstablishedLandscape = {
        "frames": points,
        "regions": regions,
        "ungrouped_frame_ids": ungrouped,
        "computed_at": datetime.utcnow().isoformat(),
        "n_total": len(points),
        "error": None,
    }

    with _lock:
        _CACHE["data"] = result
        _CACHE["computed_at"] = datetime.utcnow()

    logger.info(
        "established landscape: projected %d frames, %d regions, %d ungrouped",
        len(points), len(regions), len(ungrouped),
    )
    return result


def _empty(error: Optional[str]) -> EstablishedLandscape:
    return {
        "frames": [],
        "regions": [],
        "ungrouped_frame_ids": [],
        "computed_at": datetime.utcnow().isoformat(),
        "n_total": 0,
        "error": error,
    }


class FrameMemberArticle(TypedDict):
    """One article extract used as a dot inside the focused established bubble."""
    source_item_id: int
    title: Optional[str]
    extracted_text: Optional[str]  # the actual quote / claim — what makes the dot meaningful
    source_name: Optional[str]
    outlet_name: Optional[str]
    outlet_type: Optional[str]
    published_at: Optional[str]


def get_frame_member_articles(
    db: Session, frame_id: int, limit: int = 40,
) -> list[FrameMemberArticle]:
    """Article extracts that mention this frame. Used as dots when zooming
    into an established bubble in the Landscape view.

    Lazy-loaded (not part of the main landscape payload) so the initial
    GET stays cheap — we only fetch the ~30-40 extracts for the bubble
    the user actually opens.

    Pulled from NarrativeFrameMention (not FrameClusterMatch) because the
    LLM-extracted quote on the mention row is what makes each dot useful —
    that's the specific claim from the article that triggered the match.
    Without it, dots are just opaque markers.

    Newest-first. Limit defaults to 40 so even a frame with 240 mentions
    doesn't dump a wall of dots — the user can always click through to the
    frame detail page for the full list.
    """
    from app.models import NarrativeFrameMention, SourceItem, Outlet

    rows = (
        db.query(
            SourceItem.id,
            SourceItem.title,
            SourceItem.source_name,
            SourceItem.published_at,
            NarrativeFrameMention.extracted_text,
            Outlet.name.label("outlet_name"),
            Outlet.outlet_type,
        )
        .select_from(NarrativeFrameMention)
        .join(SourceItem, SourceItem.id == NarrativeFrameMention.source_item_id)
        .outerjoin(Outlet, Outlet.id == SourceItem.outlet_id)
        .filter(NarrativeFrameMention.frame_id == frame_id)
        .order_by(SourceItem.published_at.desc().nulls_last())
        .limit(limit)
        .all()
    )

    out: list[FrameMemberArticle] = []
    for sid, title, source_name, published_at, extracted, oname, otype in rows:
        out.append({
            "source_item_id": int(sid),
            "title": title,
            "extracted_text": extracted,
            "source_name": source_name,
            "outlet_name": oname,
            "outlet_type": otype,
            "published_at": published_at.isoformat() if published_at else None,
        })
    return out
