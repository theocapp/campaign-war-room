"""Cascade-aware delete helpers — the right way to remove rows from the
parent tables of this codebase without leaving orphans.

Why this module exists:

  On 2026-05-23 a cleanup script deleted ~163 SourceItem rows but left
  their associated StoryCluster rows in place. The cluster IDs are derived
  from the seed SourceItem id (format: ``"source-{N}"``). When new ingest
  cycles produced fresh SourceItems with the same auto-increment ids,
  every cluster INSERT hit a UNIQUE-constraint failure on the orphan
  cluster id. The entire ingest pipeline silently rolled back for two
  hours — only Reddit's ``last_run_at`` timestamp updated (it lives
  outside the transaction), giving a false signal that work was happening.

  See the post-mortem in tonight's audit. This helper exists so cleanup
  code doesn't reinvent the bug.

What this module provides:

  - ``safe_delete_source_items(db, ids)``: cascades through FCM, NFM,
    ClusterOpponentActivity, IssueMention, OpponentActivity, CandidateFrame,
    and StoryCluster (when the cluster is orphaned by the delete).
  - ``safe_delete_frame(db, frame_id)``: removes the frame plus its
    NarrativeFrameMention, FrameClusterMatch, FrameVariant, and
    FrameStageHistory rows, and nulls CandidateFrame.resolved_to_frame_id
    pointers (since the candidate-frame audit-trail outlives the promoted
    frame).
  - ``find_orphan_clusters(db)``: returns story_cluster ids that no
    SourceItem references. Used by the nightly GC job.
  - ``gc_orphans(db)``: defense-in-depth — sweeps and removes orphan
    rows across the schema. Idempotent. Safe to run periodically.

Never call ``db.query(SourceItem).delete()`` or
``db.query(NarrativeFrame).delete()`` directly in new code — always go
through these helpers. The bulk-delete path bypasses SQLAlchemy's
ORM-level cascades and will recreate the bug.
"""
from __future__ import annotations
import logging
from typing import Iterable

from sqlalchemy.orm import Session

from app.models import (
    CandidateFrame,
    ClusterOpponentActivity,
    FrameClusterMatch,
    FrameStageHistory,
    FrameVariant,
    IssueMention,
    NarrativeFrame,
    NarrativeFrameMention,
    OpponentActivity,
    SourceItem,
    StoryCluster,
)

logger = logging.getLogger(__name__)


def safe_delete_source_items(db: Session, ids: Iterable[int]) -> dict:
    """Delete source items + everything that depends on them.

    Cascade order (children → parent so each delete's FKs are already gone):

      1. NarrativeFrameMention (per source_item_id)
      2. IssueMention            (per source_item_id)
      3. OpponentActivity        (per source_item_id)
      4. CandidateFrame          (per source_item_id) — the staging-row audit trail
      5. For each StoryCluster whose ONLY remaining members are in the
         delete-set: delete its FrameClusterMatch, ClusterOpponentActivity,
         then the StoryCluster itself.
         (Clusters that still have other source_items survive.)
      6. SourceItem itself.

    Returns a dict of counts. Caller owns the transaction — the helper
    does not commit. Wrap in BEGIN/COMMIT in the caller.

    Why we don't cascade ALL clusters of deleted items: A cluster can hold
    many source items. If we deleted one article from a 50-article cluster,
    we don't want to lose the 49 others. So we only delete clusters that
    become EMPTY (no remaining source items) as a result of the delete.
    The empty-cluster check is the orphan-prevention guarantee.
    """
    ids = list(ids)
    if not ids:
        return {"source_items": 0}

    counts: dict[str, int] = {}

    # Per-item dependents.
    counts["narrative_frame_mentions"] = (
        db.query(NarrativeFrameMention)
        .filter(NarrativeFrameMention.source_item_id.in_(ids))
        .delete(synchronize_session=False)
    )
    counts["issue_mentions"] = (
        db.query(IssueMention)
        .filter(IssueMention.source_item_id.in_(ids))
        .delete(synchronize_session=False)
    )
    counts["opponent_activities"] = (
        db.query(OpponentActivity)
        .filter(OpponentActivity.source_item_id.in_(ids))
        .delete(synchronize_session=False)
    )
    counts["candidate_frames"] = (
        db.query(CandidateFrame)
        .filter(CandidateFrame.source_item_id.in_(ids))
        .delete(synchronize_session=False)
    )

    # Find clusters that would be orphaned by this delete. A cluster is
    # orphaned if every SourceItem with its cluster_id is in `ids`.
    # First, get the cluster_ids referenced by the deleting source items.
    cluster_ids = [
        r[0] for r in (
            db.query(SourceItem.story_cluster_id)
            .filter(SourceItem.id.in_(ids),
                    SourceItem.story_cluster_id.isnot(None))
            .distinct()
            .all()
        )
    ]
    orphan_cluster_ids: list[str] = []
    for cid in cluster_ids:
        # Count SourceItems in this cluster NOT being deleted.
        remaining = (
            db.query(SourceItem.id)
            .filter(
                SourceItem.story_cluster_id == cid,
                ~SourceItem.id.in_(ids),
            )
            .first()
        )
        if remaining is None:
            orphan_cluster_ids.append(cid)

    counts["frame_cluster_matches"] = 0
    counts["cluster_opponent_activities"] = 0
    counts["story_clusters"] = 0
    if orphan_cluster_ids:
        counts["frame_cluster_matches"] = (
            db.query(FrameClusterMatch)
            .filter(FrameClusterMatch.story_cluster_id.in_(orphan_cluster_ids))
            .delete(synchronize_session=False)
        )
        counts["cluster_opponent_activities"] = (
            db.query(ClusterOpponentActivity)
            .filter(ClusterOpponentActivity.story_cluster_id.in_(orphan_cluster_ids))
            .delete(synchronize_session=False)
        )
        counts["story_clusters"] = (
            db.query(StoryCluster)
            .filter(StoryCluster.id.in_(orphan_cluster_ids))
            .delete(synchronize_session=False)
        )

    # Finally, the source items themselves.
    counts["source_items"] = (
        db.query(SourceItem)
        .filter(SourceItem.id.in_(ids))
        .delete(synchronize_session=False)
    )

    logger.info("safe_delete_source_items: %s", counts)
    return counts


def safe_delete_frame(db: Session, frame_id: int) -> dict:
    """Delete a NarrativeFrame and all its dependents.

    Specifically:
      - NarrativeFrameMention rows (ORM cascade also catches these but
        being explicit is safer when bulk-delete bypasses cascade)
      - FrameClusterMatch rows
      - FrameVariant rows (and NULL out any NFM.variant_id pointing at them)
      - FrameStageHistory rows
      - For each CandidateFrame with resolved_to_frame_id = frame_id:
        SET NULL on resolved_to_frame_id (preserve the audit row, drop the
        dead reference). The promoted-frame audit trail outlives the frame.

    Caller owns the transaction.
    """
    counts: dict[str, int] = {}

    # NFM rows — null out variant_id BEFORE deleting variants
    variant_ids = [r[0] for r in (
        db.query(FrameVariant.id)
        .filter(FrameVariant.frame_id == frame_id)
        .all()
    )]
    if variant_ids:
        (
            db.query(NarrativeFrameMention)
            .filter(NarrativeFrameMention.variant_id.in_(variant_ids))
            .update({NarrativeFrameMention.variant_id: None},
                    synchronize_session=False)
        )

    counts["frame_cluster_matches"] = (
        db.query(FrameClusterMatch)
        .filter(FrameClusterMatch.frame_id == frame_id)
        .delete(synchronize_session=False)
    )
    counts["narrative_frame_mentions"] = (
        db.query(NarrativeFrameMention)
        .filter(NarrativeFrameMention.frame_id == frame_id)
        .delete(synchronize_session=False)
    )
    counts["frame_variants"] = (
        db.query(FrameVariant)
        .filter(FrameVariant.frame_id == frame_id)
        .delete(synchronize_session=False)
    )
    counts["frame_stage_history"] = (
        db.query(FrameStageHistory)
        .filter(FrameStageHistory.frame_id == frame_id)
        .delete(synchronize_session=False)
    )
    counts["candidate_frame_refs_cleared"] = (
        db.query(CandidateFrame)
        .filter(CandidateFrame.resolved_to_frame_id == frame_id)
        .update({CandidateFrame.resolved_to_frame_id: None,
                 CandidateFrame.resolved_at: None},
                synchronize_session=False)
    )
    counts["narrative_frame"] = (
        db.query(NarrativeFrame)
        .filter(NarrativeFrame.id == frame_id)
        .delete(synchronize_session=False)
    )

    logger.info("safe_delete_frame(id=%d): %s", frame_id, counts)
    return counts


def find_orphan_clusters(db: Session) -> list[str]:
    """Return story_cluster ids where the seed source_item no longer exists."""
    return [
        r[0] for r in (
            db.query(StoryCluster.id)
            .filter(~StoryCluster.seed_source_item_id.in_(db.query(SourceItem.id)))
            .all()
        )
    ]


def gc_orphans(db: Session) -> dict:
    """Defense-in-depth sweep: remove orphan rows that slipped through.

    Idempotent and safe to call periodically. Returns counts of what was
    removed. If everything is clean, returns all-zeros.

    Specifically cleans:
      - story_clusters whose seed source_item is gone (the bug class
        that bit us — orphan clusters block new cluster INSERTs via the
        ``"source-{N}"`` id collision)
      - frame_cluster_matches whose cluster is gone (FK orphan)
      - cluster_opponent_activities whose cluster is gone (FK orphan)
      - frame_variants whose frame is gone (FK orphan)
      - candidate_frames.resolved_to_frame_id pointing nowhere (SET NULL)
    """
    counts: dict[str, int] = {}

    # 1. Find and delete orphan story_clusters (the dangerous class).
    orphan_clusters = find_orphan_clusters(db)
    if orphan_clusters:
        counts["frame_cluster_matches_dropped"] = (
            db.query(FrameClusterMatch)
            .filter(FrameClusterMatch.story_cluster_id.in_(orphan_clusters))
            .delete(synchronize_session=False)
        )
        counts["cluster_opponent_activities_dropped"] = (
            db.query(ClusterOpponentActivity)
            .filter(ClusterOpponentActivity.story_cluster_id.in_(orphan_clusters))
            .delete(synchronize_session=False)
        )
        counts["story_clusters_dropped"] = (
            db.query(StoryCluster)
            .filter(StoryCluster.id.in_(orphan_clusters))
            .delete(synchronize_session=False)
        )
    else:
        counts["story_clusters_dropped"] = 0

    # 2. Orphan FCM rows whose frame is gone. SQLAlchemy doesn't allow
    # outerjoin+delete in one query; use a subquery instead.
    orphan_fcm_frame = (
        db.query(FrameClusterMatch)
        .filter(~FrameClusterMatch.frame_id.in_(db.query(NarrativeFrame.id)))
        .delete(synchronize_session=False)
    )
    if orphan_fcm_frame:
        counts["fcm_dead_frame_dropped"] = orphan_fcm_frame

    # 3. Orphan frame_variants whose frame is gone.
    orphan_variants = (
        db.query(FrameVariant)
        .filter(~FrameVariant.frame_id.in_(db.query(NarrativeFrame.id)))
        .delete(synchronize_session=False)
    )
    if orphan_variants:
        counts["frame_variants_dropped"] = orphan_variants

    # 4. candidate_frames.resolved_to_frame_id → SET NULL when dead.
    orphan_cf_refs = (
        db.query(CandidateFrame)
        .filter(
            CandidateFrame.resolved_to_frame_id.isnot(None),
            ~CandidateFrame.resolved_to_frame_id.in_(
                db.query(NarrativeFrame.id)
            ),
        )
        .update(
            {CandidateFrame.resolved_to_frame_id: None,
             CandidateFrame.resolved_at: None},
            synchronize_session=False,
        )
    )
    if orphan_cf_refs:
        counts["candidate_frame_refs_cleared"] = orphan_cf_refs

    return counts
