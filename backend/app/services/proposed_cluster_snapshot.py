"""Proposed-narrative cluster snapshots.

Persists the result of the live HDBSCAN compute (see narrative_landscape.py)
into the `proposed_cluster_snapshots` table so the Review Queue's Proposed
Narratives list stops mutating between user visits. Three operations:

    take_snapshot(db)        — compute HDBSCAN, persist new clusters, refresh
                               existing ones (matched by fingerprint).
    get_open_snapshots(db)   — return all snapshots with no dismissed_at and
                               no applied_at, in the same shape the frontend
                               already consumes (NarrativeLandscape).
    mark_dismissed(db, fp)   — stamp dismissed_at by fingerprint.
    mark_applied(db, fp, fid)— stamp applied_at + applied_to_frame_id by fp.

The snapshot row's lifecycle: create on first observation → refresh on
subsequent observations → mark dismissed or applied by user action.
Rows are never deleted (kept for audit). The open-list query filters by
applied_at + dismissed_at both being null.
"""
import hashlib
import json
import logging
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models import ProposedClusterSnapshot

logger = logging.getLogger(__name__)


def _fingerprint(member_ids: list[int]) -> str:
    """sha256 of sorted candidate_frame_ids — matches narrative_triage's
    fingerprint scheme exactly so the two systems can cross-reference."""
    payload = "|".join(str(i) for i in sorted(set(member_ids)))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def take_snapshot(db: Session, days_back: int = 21) -> dict:
    """Compute fresh clusters and persist them as snapshots.

    For each cluster from the live HDBSCAN compute:
      - If a row with the same fingerprint exists (open or closed), update
        its data fields and bump refreshed_at.
      - Otherwise insert a new row.

    Open snapshots whose fingerprint NO LONGER appears in the fresh compute
    are left as-is. They stay visible in the queue — that's the whole point
    of the snapshot. The user removes them by promoting, merging, or
    dismissing.

    Returns a count summary for the caller (UI / scheduler logging).
    """
    from app.services.narrative_landscape import _compute_landscape

    landscape = _compute_landscape(db, days_back=days_back)
    clusters = landscape.get("clusters") or []
    points = landscape.get("points") or []

    # Group points by cluster_id so we can attach the per-cluster member
    # detail when persisting.
    points_by_cluster: dict[int, list[dict]] = {}
    for p in points:
        cid = p.get("cluster_id", -1)
        if cid < 0:
            continue
        points_by_cluster.setdefault(cid, []).append(p)

    now = datetime.utcnow()
    inserted = 0
    refreshed = 0

    for c in clusters:
        cid = c.get("cluster_id")
        if cid is None or cid < 0:
            continue
        members = points_by_cluster.get(cid, [])
        member_ids = sorted({m["candidate_frame_id"] for m in members})
        if not member_ids:
            continue
        fp = _fingerprint(member_ids)

        existing = (
            db.query(ProposedClusterSnapshot)
            .filter(ProposedClusterSnapshot.cluster_fingerprint == fp)
            .first()
        )
        outlet_tier_counts = c.get("outlet_tier_counts") or {}
        # Coerce the tier-counts shape to plain ints so JSON serialization
        # is stable regardless of the source TypedDict.
        tier_dict = {
            k: int(outlet_tier_counts.get(k, 0))
            for k in ("national", "regional", "local", "blog", "social")
        }

        if existing:
            existing.cluster_id = cid
            existing.representative_name = c.get("representative_name") or existing.representative_name
            existing.size = int(c.get("size") or len(member_ids))
            existing.outlet_count = int(c.get("outlet_count") or 0)
            existing.outlet_names_json = json.dumps(c.get("outlet_names") or [])
            existing.outlet_tier_counts_json = json.dumps(tier_dict)
            existing.owner_type_hint = c.get("owner_type_hint") or existing.owner_type_hint
            existing.subject_type_hint = c.get("subject_type_hint")
            existing.member_candidate_frame_ids_json = json.dumps(member_ids)
            existing.points_json = json.dumps(members)
            existing.x = c.get("centroid_x")
            existing.y = c.get("centroid_y")
            existing.refreshed_at = now
            refreshed += 1
        else:
            row = ProposedClusterSnapshot(
                cluster_fingerprint=fp,
                cluster_id=cid,
                representative_name=c.get("representative_name") or "Unnamed cluster",
                size=int(c.get("size") or len(member_ids)),
                outlet_count=int(c.get("outlet_count") or 0),
                outlet_names_json=json.dumps(c.get("outlet_names") or []),
                outlet_tier_counts_json=json.dumps(tier_dict),
                owner_type_hint=c.get("owner_type_hint") or "media",
                subject_type_hint=c.get("subject_type_hint"),
                member_candidate_frame_ids_json=json.dumps(member_ids),
                points_json=json.dumps(members),
                x=c.get("centroid_x"),
                y=c.get("centroid_y"),
                created_at=now,
                refreshed_at=now,
            )
            db.add(row)
            inserted += 1

    db.commit()
    logger.info(
        "proposed_cluster_snapshot: take_snapshot done — inserted=%d refreshed=%d",
        inserted, refreshed,
    )
    return {
        "inserted": inserted,
        "refreshed": refreshed,
        "total_clusters_in_compute": len(clusters),
        "computed_at": now.isoformat(),
    }


def get_open_snapshots(db: Session) -> dict:
    """Return open (unactioned) snapshots in NarrativeLandscape shape.

    The frontend ReviewQueue already knows how to render this shape, so we
    repackage the persisted rows into the same dict the live landscape
    endpoint returns. Closed snapshots (dismissed_at or applied_at set)
    are excluded — they've already been triaged.
    """
    rows = (
        db.query(ProposedClusterSnapshot)
        .filter(
            ProposedClusterSnapshot.dismissed_at.is_(None),
            ProposedClusterSnapshot.applied_at.is_(None),
        )
        .order_by(ProposedClusterSnapshot.created_at.desc())
        .all()
    )

    clusters = []
    all_points: list[dict] = []
    last_refreshed: Optional[datetime] = None

    for r in rows:
        try:
            outlet_names = json.loads(r.outlet_names_json)
        except Exception:
            outlet_names = []
        try:
            tier_counts = json.loads(r.outlet_tier_counts_json)
        except Exception:
            tier_counts = {"national": 0, "regional": 0, "local": 0, "blog": 0, "social": 0}
        try:
            points = json.loads(r.points_json)
        except Exception:
            points = []

        clusters.append({
            "cluster_id": r.cluster_id,
            "size": r.size,
            "outlet_count": r.outlet_count,
            "outlet_names": outlet_names,
            "outlet_tier_counts": tier_counts,
            "owner_type_hint": r.owner_type_hint,
            "subject_type_hint": r.subject_type_hint,
            "representative_name": r.representative_name,
            "centroid_x": r.x,
            "centroid_y": r.y,
        })
        all_points.extend(points)
        if r.refreshed_at and (last_refreshed is None or r.refreshed_at > last_refreshed):
            last_refreshed = r.refreshed_at

    return {
        "points": all_points,
        "clusters": clusters,
        "computed_at": (last_refreshed or datetime.utcnow()).isoformat(),
        "n_total": sum(c["size"] for c in clusters),
        "n_clustered": sum(c["size"] for c in clusters),
        "n_noise": 0,
        "error": None,
    }


def mark_dismissed_by_fingerprint(db: Session, fp: str) -> bool:
    """User dismissed this proposal. Return True if a row was stamped."""
    row = (
        db.query(ProposedClusterSnapshot)
        .filter(ProposedClusterSnapshot.cluster_fingerprint == fp)
        .first()
    )
    if not row:
        return False
    if row.dismissed_at is None:
        row.dismissed_at = datetime.utcnow()
        db.commit()
    return True


def mark_dismissed_by_member_ids(db: Session, member_ids: list[int]) -> bool:
    """Convenience wrapper: caller has the candidate_frame_ids, not the fp."""
    return mark_dismissed_by_fingerprint(db, _fingerprint(member_ids))


def mark_applied_by_member_ids(
    db: Session, member_ids: list[int], frame_id: Optional[int] = None,
) -> bool:
    """User promoted or merged this proposal. Stamp the row."""
    fp = _fingerprint(member_ids)
    row = (
        db.query(ProposedClusterSnapshot)
        .filter(ProposedClusterSnapshot.cluster_fingerprint == fp)
        .first()
    )
    if not row:
        return False
    if row.applied_at is None:
        row.applied_at = datetime.utcnow()
        row.applied_to_frame_id = frame_id
        db.commit()
    return True


def member_ids_for_fingerprint(db: Session, fp: str) -> list[int]:
    """Look up a snapshot's member candidate_frame_ids by fingerprint."""
    row = (
        db.query(ProposedClusterSnapshot)
        .filter(ProposedClusterSnapshot.cluster_fingerprint == fp)
        .first()
    )
    if not row:
        return []
    try:
        return list(json.loads(row.member_candidate_frame_ids_json))
    except Exception:
        return []
