"""
Dot-level landscape: every article extract as one 2D-projected dot.

This is the V12 reframing of the Landscape page. Instead of bubbles
(narratives) with dots inside, the atomic unit is the article extract:

  TOPIC: Corruption Allegations           ← topic-level hull/label
    NARRATIVE: Bresnahan's Stock Trades   ← narrative-level hull/label
      dot — extract from Times-Tribune    ← the data
      dot — extract from Citizens Voice
      dot — ... 30 more

The grouping hierarchy is given by the data (not discovered by clustering):
  - each dot has a known frame_id (parent narrative)
  - each frame has a known topic_region (set by topic_regions service)

UMAP only decides LAYOUT — semantically related extracts end up near
each other, so narrative groups and topic groups appear as visual
clusters naturally. Frame name stripping (see text_anonymize.py) keeps
the clustering topic-driven instead of subject-driven.

Cost
----
~1000 extracts × ~100 tokens each = ~100K tokens for embedding.
At text-embedding-3-large pricing ($0.13/1M), about $0.013 cold compute.
Cached in-process (24h TTL) and invalidated on any frame mutation.

Performance
-----------
1053 dots in our current data. UMAP over ~1000 points is ~2-3s. Whole
endpoint cold-compute is ~5-10s; warm responses are instant.
"""
from __future__ import annotations
import logging
import threading
from datetime import datetime
from typing import Optional, TypedDict

from sqlalchemy.orm import Session

from app.models import NarrativeFrame, NarrativeFrameMention, Outlet, SourceItem

logger = logging.getLogger(__name__)


# ── Types ──────────────────────────────────────────────────────────────────

class ExtractDot(TypedDict):
    """One article extract as a dot on the map."""
    id: int                  # NarrativeFrameMention.id
    x: float                 # UMAP coord
    y: float
    frame_id: int            # parent NarrativeFrame
    owner_type: str          # who BENEFITS — candidate | opponent | media
    subject_type: str        # V13.19 — who it's ABOUT — candidate | opponent | media
    extracted_text: str
    source_item_id: int
    source_title: Optional[str]
    source_name: Optional[str]
    outlet_name: Optional[str]
    outlet_type: Optional[str]
    published_at: Optional[str]


class NarrativeGroupInfo(TypedDict):
    """Metadata for a narrative-level grouping (one per frame).
    Frontend uses this to draw narrative hulls + labels."""
    frame_id: int
    name: str
    description: Optional[str]
    owner_type: str
    subject_type: str        # V13.19 — see ExtractDot
    mentions_total: int
    dot_count: int           # how many dots in this group


class TopicGroupInfo(TypedDict):
    """Metadata for a topic-level grouping (one per HDBSCAN region).
    Frontend uses this to draw topic hulls + labels + handle edits."""
    region_id: int
    persisted_id: Optional[int]
    label: str
    edited_by_user: bool
    member_frame_ids: list[int]
    owner_mix: dict[str, float]
    quadrant_mix: dict[str, float]   # V13.19 — see topic_regions.TopicRegion


class DotLandscape(TypedDict):
    dots: list[ExtractDot]
    narratives: list[NarrativeGroupInfo]
    topics: list[TopicGroupInfo]
    ungrouped_frame_ids: list[int]   # frames whose dots have no topic-region overlay
    computed_at: str
    n_total: int
    error: Optional[str]


# ── Cache ──────────────────────────────────────────────────────────────────

_CACHE: dict = {"data": None, "computed_at": None}
_lock = threading.Lock()


def invalidate_cache() -> None:
    """Drop the cached dot landscape. Called on any frame mutation."""
    with _lock:
        _CACHE["data"] = None
        _CACHE["computed_at"] = None


def get_dot_landscape(db: Session, max_age_hours: int = 24) -> DotLandscape:
    """Return cached projection or compute fresh."""
    cached = _CACHE.get("data")
    computed_at = _CACHE.get("computed_at")
    if (
        cached is not None
        and computed_at is not None
        and (datetime.utcnow() - computed_at).total_seconds() <= max_age_hours * 3600
    ):
        return cached
    return _compute(db)


def _compute(db: Session) -> DotLandscape:
    """Fresh embed + UMAP + projection. Writes cache on success."""
    import numpy as np

    try:
        from umap import UMAP
        from app.services._numba_serialize import numba_lock
    except Exception as exc:
        return _empty(f"umap unavailable: {exc}")

    try:
        from app.services.embeddings import embed_texts
    except Exception as exc:
        return _empty(f"embeddings unavailable: {exc}")

    # ── 1. Pull all extracts with their frame + source + outlet metadata ──
    # One join query so the dot list comes back with everything the
    # frontend needs in a single hop.
    rows = (
        db.query(
            NarrativeFrameMention.id,
            NarrativeFrameMention.frame_id,
            NarrativeFrameMention.extracted_text,
            NarrativeFrameMention.source_item_id,
            NarrativeFrame.name.label("frame_name"),
            NarrativeFrame.description.label("frame_description"),
            NarrativeFrame.owner_type.label("frame_owner_type"),
            SourceItem.title.label("source_title"),
            SourceItem.source_name,
            SourceItem.published_at,
            # V13.21 — per-article perspective (pro_candidate / pro_opponent /
            # neutral / NULL). Falls through to narrative owner_type when NULL.
            SourceItem.perspective.label("article_perspective"),
            Outlet.name.label("outlet_name"),
            Outlet.outlet_type,
        )
        .select_from(NarrativeFrameMention)
        .join(NarrativeFrame, NarrativeFrame.id == NarrativeFrameMention.frame_id)
        .outerjoin(SourceItem, SourceItem.id == NarrativeFrameMention.source_item_id)
        .outerjoin(Outlet, Outlet.id == SourceItem.outlet_id)
        .filter(NarrativeFrame.active == True)
        .filter(NarrativeFrameMention.extracted_text.isnot(None))
        .filter(NarrativeFrameMention.extracted_text != "")
        .all()
    )
    if not rows:
        return _empty(None)

    # ── 2. Embed extracted_text (with name stripping) ─────────────────────
    # Same anonymizer as the other landscape services — strip candidate +
    # opponent names so positions cluster by TOPIC, not by who's mentioned.
    from app.services.text_anonymize import get_anonymizer
    strip = get_anonymizer(db)
    texts = [strip((r.extracted_text or "")[:500]) for r in rows]
    embeddings = embed_texts(texts, task_type="SEMANTIC_SIMILARITY")

    keep_idx = [i for i, e in enumerate(embeddings) if e is not None]
    if len(keep_idx) < 3:
        return _empty("too few embeddings succeeded to project")

    embs = np.array([embeddings[i] for i in keep_idx], dtype=np.float32)

    # L2-normalize so cosine ≈ Euclidean — matches other landscape services.
    norms = np.linalg.norm(embs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    embs_norm = embs / norms

    # ── 3. UMAP project to 2D ─────────────────────────────────────────────
    # 1000+ points → n_neighbors=15 is fine (default-ish). Slightly larger
    # min_dist than the small-dataset landscape to give breathing room
    # between dots.
    n_neighbors = min(15, max(2, len(keep_idx) - 1))
    reducer = UMAP(
        n_components=2,
        n_neighbors=n_neighbors,
        min_dist=0.15,
        metric="cosine",
        random_state=42,
        n_jobs=1,
    )
    # Serialize against any other UMAP/HDBSCAN call. See _numba_serialize.py.
    with numba_lock:
        coords = reducer.fit_transform(embs_norm)

    # V13.19 — subject classifier shared across dots + narratives.
    # V13.20 — DOTS classify their subject from their OWN extracted_text
    # (the article quote), not just from the parent narrative's name.
    # An article extract is a specific quote with a specific subject;
    # within a single narrative, different extracts can be about
    # different actors. Falls back to the FRAME-level subject only
    # when the extract mentions no actor (generic policy discussion,
    # commentary without attribution) — preserves the narrative-level
    # intent when the text itself is uninformative.
    from app.services.subject_classifier import get_subject_classifier
    classify = get_subject_classifier(db)
    # Pre-compute per-frame so we don't re-classify the same frame name
    # multiple times when many dots share a parent.
    frame_subject: dict[int, str] = {}
    for r in rows:
        if r.frame_id not in frame_subject:
            frame_subject[r.frame_id] = classify(r.frame_name)

    def _classify_extract(text: str, frame_id: int) -> str:
        """Per-dot subject classification with frame-level fallback."""
        if text:
            t_subj = classify(text)
            if t_subj != "media":
                return t_subj
        # Extract didn't mention any actor — inherit the frame's
        # subject. This keeps generic-commentary dots colored
        # consistent with the narrative they're part of.
        return frame_subject.get(frame_id, "media")

    # V13.21 — map article-level perspective to owner_type for dot color.
    # When SourceItem.perspective is set, it overrides the narrative's
    # owner_type (more granular signal). Falls back to narrative owner_type
    # when perspective is NULL.
    def _resolve_owner(article_perspective, frame_owner):
        if article_perspective == "pro_candidate":
            return "candidate"
        if article_perspective == "pro_opponent":
            return "opponent"
        if article_perspective == "neutral":
            return "media"
        return frame_owner or "media"

    # ── 4. Build dot list ─────────────────────────────────────────────────
    dots: list[ExtractDot] = []
    for j, original_idx in enumerate(keep_idx):
        r = rows[original_idx]
        dots.append({
            "id": int(r.id),
            "x": float(coords[j, 0]),
            "y": float(coords[j, 1]),
            "frame_id": int(r.frame_id),
            "owner_type": _resolve_owner(getattr(r, "article_perspective", None), r.frame_owner_type),
            "subject_type": _classify_extract(r.extracted_text or "", r.frame_id),
            "extracted_text": r.extracted_text or "",
            "source_item_id": int(r.source_item_id) if r.source_item_id else 0,
            "source_title": r.source_title,
            "source_name": r.source_name,
            "outlet_name": r.outlet_name,
            "outlet_type": r.outlet_type,
            "published_at": r.published_at.isoformat() if r.published_at else None,
        })

    # ── 5. Narrative-level groupings ──────────────────────────────────────
    # One entry per frame that has at least one dot. Used for narrative
    # hulls + labels in the UI.
    from collections import Counter
    dot_counts = Counter(d["frame_id"] for d in dots)

    frame_rows_seen: set[int] = set()
    narrative_meta: dict[int, dict] = {}
    for r in rows:
        if r.frame_id in frame_rows_seen:
            continue
        frame_rows_seen.add(r.frame_id)
        narrative_meta[r.frame_id] = {
            "name": r.frame_name,
            "description": r.frame_description,
            "owner_type": r.frame_owner_type,
        }

    narratives: list[NarrativeGroupInfo] = []
    for frame_id, n in dot_counts.most_common():
        meta = narrative_meta.get(frame_id, {})
        narratives.append({
            "frame_id": frame_id,
            "name": meta.get("name") or f"Frame {frame_id}",
            "description": meta.get("description"),
            "owner_type": meta.get("owner_type") or "media",
            "subject_type": frame_subject.get(frame_id, "media"),
            "mentions_total": int(n),  # dot count IS mentions total here
            "dot_count": int(n),
        })

    # ── 6. Topic-level groupings — reuse existing service ─────────────────
    # Topic regions are computed against the established-landscape FRAME
    # positions, not these dot positions. Map the regions through to
    # frame_id sets so the frontend can draw topic hulls over the dots.
    topics: list[TopicGroupInfo] = []
    ungrouped_frame_ids: list[int] = []
    try:
        from app.services.narrative_landscape_established import get_established_landscape
        est = get_established_landscape(db)
        for region in est.get("regions", []):
            topics.append({
                "region_id": region["region_id"],
                "persisted_id": region.get("persisted_id"),
                "label": region["label"],
                "edited_by_user": region["edited_by_user"],
                "member_frame_ids": list(region["member_frame_ids"]),
                "owner_mix": region["owner_mix"],
                "quadrant_mix": region.get("quadrant_mix") or {},
            })
        ungrouped_frame_ids = list(est.get("ungrouped_frame_ids", []))
    except Exception as exc:
        logger.warning("dot landscape: topic regions unavailable: %s", exc)

    result: DotLandscape = {
        "dots": dots,
        "narratives": narratives,
        "topics": topics,
        "ungrouped_frame_ids": ungrouped_frame_ids,
        "computed_at": datetime.utcnow().isoformat(),
        "n_total": len(dots),
        "error": None,
    }

    with _lock:
        _CACHE["data"] = result
        _CACHE["computed_at"] = datetime.utcnow()

    logger.info(
        "dot landscape: %d dots, %d narratives, %d topics",
        len(dots), len(narratives), len(topics),
    )
    return result


def _empty(error: Optional[str]) -> DotLandscape:
    return {
        "dots": [],
        "narratives": [],
        "topics": [],
        "ungrouped_frame_ids": [],
        "computed_at": datetime.utcnow().isoformat(),
        "n_total": 0,
        "error": error,
    }
