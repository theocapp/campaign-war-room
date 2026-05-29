"""
2D narrative landscape for the candidate_frame UI.

Projects the high-dimensional embeddings of pending candidate_frames down
to 2D via UMAP, then runs HDBSCAN over the SAME high-dim space (not the
2D projection) so cluster assignments stay semantically meaningful. The
2D coords are PURELY for visualization — they don't drive any decisions.

Why this exists
---------------
The narrative cards UI is great for "tell me details about this narrative."
The 2D landscape is the complementary view: "show me the SHAPE of what's
out there."

  - Empty regions = topical gaps (issues nobody's writing about)
  - Dense clusters = recurring narratives
  - Two clusters sitting close together = related but distinct narratives
    (combined-attack opportunity)
  - Lone dots = one-offs the campaign can usually ignore

UMAP vs t-SNE vs PCA
--------------------
UMAP picked because it preserves cluster STRUCTURE best for embedding
data + is built on the same density assumptions as HDBSCAN — so the
visual landscape matches the actual clustering decisions.

random_state is fixed so the layout is stable across refreshes. Without
it, every recomputation rotates/flips the map and the user gets disoriented.

Caching
-------
A daily refresh is plenty — narrative landscapes don't shift hour-by-hour.
Cached via a process-level dict keyed on (days_back). Cache TTL 25h
(matches candidate_frame_promoter for consistency). Cold compute is ~1
second on ~200 points, so even un-cached calls are fast.
"""
from __future__ import annotations
import logging
import threading
from datetime import datetime, timedelta
from typing import Optional, TypedDict

from sqlalchemy.orm import Session

from app.models import CandidateFrame, SourceItem, Outlet

logger = logging.getLogger(__name__)


class LandscapePoint(TypedDict):
    """One candidate_frame as a dot on the 2D map."""
    candidate_frame_id: int
    x: float
    y: float
    cluster_id: int           # -1 = HDBSCAN noise (singleton)
    suggested_name: str
    evidence_quote: str       # truncated, for hover tooltip
    owner_type_hint: str
    source_item_id: Optional[int]
    source_name: Optional[str]   # raw source_name from source_items
    source_title: Optional[str]  # article title for modal "member articles" list
    outlet_id: Optional[int]
    outlet_name: Optional[str]   # canonical outlet name (e.g. "Times-Tribune")
    outlet_type: Optional[str]   # tier — national / regional_news / local_news / blog / social


class LandscapeCluster(TypedDict):
    """Cluster-level metadata (one per HDBSCAN cluster, excluding noise)."""
    cluster_id: int
    size: int                       # number of points
    representative_name: str        # most-frequent suggested_name in the cluster
    owner_type_hint: str            # modal owner_type across members
    # Inferred subject (who the narrative is ABOUT) from the cluster's
    # representative name. Combines with owner_type_hint to produce a
    # 4-quadrant label on the frontend. Falls back to "media" when the
    # heuristic can't pin a side.
    subject_type_hint: str          # candidate | opponent | media
    outlet_count: int               # distinct outlets across the cluster's articles
    outlet_tier_counts: dict[str, int]  # {"national": 2, "regional": 1, ...}
    outlet_names: list[str]         # distinct outlet names, sorted


class NarrativeLandscape(TypedDict):
    points: list[LandscapePoint]
    clusters: list[LandscapeCluster]
    computed_at: str
    n_total: int              # total candidate_frames examined
    n_clustered: int          # those that landed in any cluster
    n_noise: int              # candidates that became singletons
    error: Optional[str]      # if anything went wrong during compute


_CACHE: dict = {
    "data": None,             # NarrativeLandscape | None
    "computed_at": None,      # datetime
    "days_back": None,
}
_lock = threading.Lock()


def get_landscape(db: Session, days_back: int = 21, max_age_hours: int = 25) -> NarrativeLandscape:
    """Return cached landscape if fresh, else compute + cache.

    UMAP + HDBSCAN on ~200 points takes ~1-2s. Cache TTL of 25 hours
    matches candidate_frame_promoter so the two views (cards + map)
    stay aligned.
    """
    cached = _CACHE.get("data")
    computed_at = _CACHE.get("computed_at")
    cached_days = _CACHE.get("days_back")

    if (
        cached is None
        or computed_at is None
        or cached_days != days_back
        or (datetime.utcnow() - computed_at).total_seconds() > max_age_hours * 3600
    ):
        return _compute_landscape(db, days_back=days_back)
    return cached


def refresh_landscape(db: Session, days_back: int = 21) -> NarrativeLandscape:
    """Force a fresh HDBSCAN/UMAP compute. Bypasses the 25h cache.

    Used by the daily scheduler job so the landscape cache is always
    warm + reflects the latest candidate frames, regardless of whether
    anyone loaded the /landscape page in the last 25 hours. Writes the
    result into the module cache so subsequent reads are fast.
    """
    return _compute_landscape(db, days_back=days_back)


def invalidate_cache() -> None:
    """Drop the cached projection. Called after promote/merge so the next
    landscape GET recomputes against the now-resolved candidate frames.

    Without this, the Review Queue's "Proposed narratives" list keeps
    showing a cluster the user just promoted (or merged) because the
    cached UMAP response still contains it — even though the underlying
    candidate frames are now marked resolved_to_frame_id.
    """
    with _lock:
        _CACHE["data"] = None
        _CACHE["computed_at"] = None
        _CACHE["days_back"] = None


def _compute_landscape(db: Session, days_back: int = 21) -> NarrativeLandscape:
    """Fresh compute. Writes to module cache on success."""
    import numpy as np

    try:
        # Lazy import — heavy dependencies, avoid loading them at module import time
        from umap import UMAP
        from hdbscan import HDBSCAN
        from app.services._numba_serialize import numba_lock
    except Exception as exc:
        logger.warning("narrative_landscape: missing dependency (%s)", exc)
        return _empty_landscape(f"dependency missing: {exc}")

    try:
        from app.services.embeddings import embed_texts
    except Exception as exc:
        return _empty_landscape(f"embeddings module unavailable: {exc}")

    # Pull every pending candidate_frame in the window.
    cutoff = datetime.utcnow() - timedelta(days=days_back)
    rows = (
        db.query(CandidateFrame)
        .filter(
            CandidateFrame.resolved_to_frame_id.is_(None),
            CandidateFrame.created_at >= cutoff,
        )
        .all()
    )
    if not rows:
        return _empty_landscape(None)

    # Hydrate outlet/source info for tooltips + per-cluster aggregation.
    # Two queries: source_items (for outlet_id + source_name + title) and
    # outlets (for canonical name + tier).
    sids = [r.source_item_id for r in rows if r.source_item_id]
    si_meta: dict[int, tuple[Optional[int], Optional[str], Optional[str]]] = {}
    if sids:
        for sid, oid, sname, stitle in db.query(
            SourceItem.id, SourceItem.outlet_id,
            SourceItem.source_name, SourceItem.title,
        ).filter(SourceItem.id.in_(sids)).all():
            si_meta[sid] = (oid, sname, stitle)

    outlet_ids = {oid for oid, _, _ in si_meta.values() if oid}
    outlet_meta: dict[int, tuple[Optional[str], Optional[str]]] = {}
    if outlet_ids:
        for oid, oname, otype in db.query(
            Outlet.id, Outlet.name, Outlet.outlet_type,
        ).filter(Outlet.id.in_(outlet_ids)).all():
            outlet_meta[oid] = (oname, otype)

    # Embed via the standard pipeline (OpenAI primary, provider-coherent).
    # Strip candidate names FIRST so UMAP groups by topic (healthcare, taxes,
    # corruption…) instead of subject (Cognetti vs. Bresnahan). Color
    # already encodes the subject channel — see text_anonymize.py docstring.
    from app.services.text_anonymize import get_anonymizer
    strip = get_anonymizer(db)
    texts = [
        strip(f"{r.suggested_name}. {(r.evidence_quote or '')[:300]}")
        for r in rows
    ]
    embeddings = embed_texts(texts, task_type="SEMANTIC_SIMILARITY")

    # Filter to successful embeddings + remember original indices.
    keep_idx = [i for i, e in enumerate(embeddings) if e is not None]
    if len(keep_idx) < 3:
        return _empty_landscape("too few embeddings succeeded to project")

    embs = np.array([embeddings[i] for i in keep_idx], dtype=np.float32)

    # L2-normalize so cosine ~ Euclidean — matches candidate_frame_promoter +
    # frame_variants conventions.
    norms = np.linalg.norm(embs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    embs_norm = embs / norms

    # HDBSCAN clusters on the FULL-dim embeddings (not the 2D projection).
    # The 2D coords are visualization-only; cluster ASSIGNMENTS use the
    # same high-dim density model as candidate_frame_promoter so the two
    # views never disagree about what's in what cluster.
    clusterer = HDBSCAN(
        min_cluster_size=3,
        min_samples=1,
        metric="euclidean",
        cluster_selection_method="leaf",
    )
    # Serialize HDBSCAN + UMAP under one lock so Numba's workqueue layer
    # doesn't get hit by two threads simultaneously. See _numba_serialize.py.
    with numba_lock:
        cluster_labels = clusterer.fit_predict(embs_norm)

        # UMAP for the 2D projection. random_state fixed so the layout is
        # stable across recomputations — without it, every refresh rotates
        # or mirrors the map and the user gets disoriented.
        n_neighbors = min(15, max(2, len(keep_idx) - 1))
        reducer = UMAP(
            n_components=2,
            n_neighbors=n_neighbors,
            min_dist=0.1,
            metric="cosine",
            random_state=42,  # stability across refreshes
            n_jobs=1,         # numba parallel + fixed random_state is undefined
        )
        coords = reducer.fit_transform(embs_norm)

    # Build the point list.
    points: list[LandscapePoint] = []
    for j, original_idx in enumerate(keep_idx):
        r = rows[original_idx]
        outlet_id, source_name, source_title = si_meta.get(
            r.source_item_id, (None, None, None),
        )
        outlet_name, outlet_type = outlet_meta.get(
            outlet_id, (None, None),
        ) if outlet_id else (None, None)
        points.append({
            "candidate_frame_id": r.id,
            "x": float(coords[j, 0]),
            "y": float(coords[j, 1]),
            "cluster_id": int(cluster_labels[j]),  # -1 = noise
            "suggested_name": r.suggested_name,
            "evidence_quote": (r.evidence_quote or "")[:240],
            "owner_type_hint": r.owner_type_hint or "media",
            "source_item_id": r.source_item_id,
            "source_name": source_name,
            "source_title": source_title,
            "outlet_id": outlet_id,
            "outlet_name": outlet_name,
            "outlet_type": outlet_type,
        })

    # Build cluster-level summary (skip noise = cluster_id -1).
    # Per-cluster outlet aggregation collapses the broad outlet_type categories
    # into the same 5 tier buckets used elsewhere (national/regional/local/blog/social)
    # so the modal can render outlet-mix bars consistently with the rest of the app.
    from collections import Counter
    by_cluster: dict[int, list[LandscapePoint]] = {}
    for p in points:
        by_cluster.setdefault(p["cluster_id"], []).append(p)

    def _to_tier(otype: Optional[str]) -> str:
        """Collapse Outlet.outlet_type values into the 5-tier display vocab.
        Matches the convention in narrative_frames.get_frames_with_counts."""
        if otype in ("national", "broadcast"):
            return "national"
        if otype == "regional_news":
            return "regional"
        if otype == "local_news":
            return "local"
        if otype == "blog":
            return "blog"
        if otype == "social":
            return "social"
        return "other"

    # Subject-type heuristic — bound to the current campaign's
    # candidate/opponent name tokens. Computed once per landscape build so the
    # inner loop is just a substring count.
    from app.services.subject_classifier import get_subject_classifier
    _classify_subject = get_subject_classifier(db)

    clusters: list[LandscapeCluster] = []
    for cluster_id, members in by_cluster.items():
        if cluster_id == -1:
            continue  # noise points stay in the per-point list but don't get a cluster summary
        name_counter = Counter(m["suggested_name"] for m in members)
        owner_counter = Counter(m["owner_type_hint"] for m in members)
        # Distinct outlets (skip None outlet_ids — articles without outlet linkage).
        distinct_outlet_ids = {m["outlet_id"] for m in members if m["outlet_id"]}
        distinct_outlet_names = sorted({
            m["outlet_name"] for m in members
            if m["outlet_name"]
        })
        tier_counts: dict[str, int] = {
            "national": 0, "regional": 0, "local": 0,
            "blog": 0, "social": 0, "other": 0,
        }
        # Tally by DISTINCT outlet, not by article — wire syndication shouldn't
        # inflate the "national outlet" count.
        seen_outlet_ids: set[int] = set()
        for m in members:
            oid = m["outlet_id"]
            if oid is None or oid in seen_outlet_ids:
                continue
            seen_outlet_ids.add(oid)
            tier_counts[_to_tier(m["outlet_type"])] += 1

        rep_name = name_counter.most_common(1)[0][0]
        clusters.append({
            "cluster_id": cluster_id,
            "size": len(members),
            "representative_name": rep_name,
            "owner_type_hint": owner_counter.most_common(1)[0][0],
            "subject_type_hint": _classify_subject(rep_name),
            "outlet_count": len(distinct_outlet_ids),
            "outlet_tier_counts": tier_counts,
            "outlet_names": distinct_outlet_names,
        })

    clusters.sort(key=lambda c: -c["size"])

    n_noise = sum(1 for p in points if p["cluster_id"] == -1)
    result: NarrativeLandscape = {
        "points": points,
        "clusters": clusters,
        "computed_at": datetime.utcnow().isoformat(),
        "n_total": len(points),
        "n_clustered": len(points) - n_noise,
        "n_noise": n_noise,
        "error": None,
    }

    # Stash in cache.
    with _lock:
        _CACHE["data"] = result
        _CACHE["computed_at"] = datetime.utcnow()
        _CACHE["days_back"] = days_back

    logger.info(
        "narrative_landscape: computed %d points → %d clusters, %d noise",
        len(points), len(clusters), n_noise,
    )
    return result


def _empty_landscape(error: Optional[str]) -> NarrativeLandscape:
    return {
        "points": [],
        "clusters": [],
        "computed_at": datetime.utcnow().isoformat(),
        "n_total": 0,
        "n_clustered": 0,
        "n_noise": 0,
        "error": error,
    }
