"""Clean rematch: rebuild FrameClusterMatch + NarrativeFrameMention from scratch.

NOTE: This script DELETES FCM/NFM rows but does NOT delete SourceItems or
StoryClusters. So the orphan-cluster failure mode does NOT apply here.
But future maintenance scripts that DO delete SourceItems should use
``app.services.safe_deletes.safe_delete_source_items`` to avoid the
2026-05-23 ingestion-blocking bug.



Why a separate script instead of using the existing rematch_all:

    rematch_all() skips clusters that already have any FrameClusterMatch row
    (see narrative_frames.py:911-915). After this session's matcher changes
    (verbatim quote required, real confidence, snippet validation, race-
    anchored MEDIA THEME), the existing 1,706 FCM rows are STALE — they
    were written by the old loose prompt and all hardcoded to confidence=75.
    A standard rematch wouldn't touch them. Plus upsert_frame_match uses
    MAX(excluded.confidence, existing.confidence), so even if we did try to
    overwrite, a new conf=60 match couldn't downgrade an old conf=75 row.

    This script does what the existing rematch path can't: drop all
    LLM-written FCM/NFM rows, then re-run match_article_to_frames against
    every eligible cluster from a clean slate.

Safety:

    - Dry-run by default. Prints what would change, estimates cost, exits.
    - --confirm-write required for any destructive operation.
    - Takes a DB snapshot before the destructive ops.
    - Preserves human-entered matches (matched_by != "llm").
    - Progress logged every 25 articles. Safe to ctrl-c — partial state is
      consistent (every commit is per-article) but obviously incomplete.

Usage:
    cd backend
    # Inspect first (no writes):
    python -m app.scripts.clean_rematch
    # Then actually run (after reviewing the dry-run output):
    python -m app.scripts.clean_rematch --confirm-write

Cost:
    judge_provider is gpt-4o-mini @ ~$0.0001 per call. For ~1,700 clusters
    that's roughly $0.17. Real LLM time is wall-clock dominated (~1-2s per
    call); total runtime estimate is ~30-60 min serial.
"""
from __future__ import annotations
import argparse
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[2]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.db import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    FrameClusterMatch,
    NarrativeFrame,
    NarrativeFrameMention,
    SourceItem,
    StoryCluster,
)


_STALE_CONF = 75  # the hardcoded default used by every old-prompt FCM write


def _scope_summary(db, stale_only: bool, empty_only: bool = False) -> dict:
    """Count what's about to change without doing anything.

    If `stale_only` is True, scope is limited to clusters that have at least
    one FCM row at the hardcoded-default confidence value (75). Those are
    the rows that pre-date this session's matcher changes.
    """
    fcm_total = db.query(FrameClusterMatch).count()
    fcm_llm = db.query(FrameClusterMatch).filter(
        FrameClusterMatch.matched_by == "llm").count()
    fcm_human = fcm_total - fcm_llm

    nfm_total = db.query(NarrativeFrameMention).count()
    nfm_llm = db.query(NarrativeFrameMention).filter(
        NarrativeFrameMention.matched_by == "llm").count()
    nfm_human = nfm_total - nfm_llm

    # Build the cluster set we'll process.
    base = (
        db.query(StoryCluster, SourceItem)
        .join(SourceItem, SourceItem.id == StoryCluster.representative_source_item_id)
        .filter(
            SourceItem.archived_as_irrelevant == False,  # noqa: E712
            SourceItem.race_relevance_score >= 55,
        )
    )
    if empty_only:
        # Clusters with ZERO FCM rows. Used as a recovery pass.
        with_any_fcm = {
            row[0] for row in (
                db.query(FrameClusterMatch.story_cluster_id).distinct().all()
            )
        }
        candidate_clusters = [
            (c, s) for (c, s) in base.all() if c.id not in with_any_fcm
        ]
        stale_cluster_ids = set()  # nothing to delete; clusters are already empty
        fcm_to_drop = 0
        nfm_to_drop = 0
        eligible = len(candidate_clusters)
        active_frames = db.query(NarrativeFrame).filter(
            NarrativeFrame.active == True).count()  # noqa: E712
        return {
            "fcm_total": fcm_total,
            "fcm_llm_to_drop": fcm_to_drop,
            "fcm_human_to_keep": fcm_human,
            "nfm_total": nfm_total,
            "nfm_llm_to_drop": nfm_to_drop,
            "nfm_human_to_keep": nfm_human,
            "eligible_clusters": eligible,
            "active_frames": active_frames,
            "candidate_items": [item for (_, item) in candidate_clusters],
            "stale_cluster_ids": stale_cluster_ids,
        }
    elif stale_only:
        # Only clusters that have at least one stale-conf FCM row.
        stale_cluster_ids = {
            row[0] for row in (
                db.query(FrameClusterMatch.story_cluster_id)
                .filter(FrameClusterMatch.confidence == _STALE_CONF,
                        FrameClusterMatch.matched_by == "llm")
                .distinct().all()
            )
        }
        candidate_clusters = [
            (c, s) for (c, s) in base.all() if c.id in stale_cluster_ids
        ]
        # Rows we'll actually delete: only FCM/NFM tied to stale clusters.
        # For mixed clusters we delete EVERY FCM row for that cluster so
        # the upsert's MAX-on-conflict doesn't preserve leftovers.
        if stale_cluster_ids:
            fcm_to_drop = (
                db.query(FrameClusterMatch)
                .filter(FrameClusterMatch.story_cluster_id.in_(stale_cluster_ids),
                        FrameClusterMatch.matched_by == "llm")
                .count()
            )
            # NFM rows are per-source_item; drop only those whose source
            # item belongs to a stale cluster.
            nfm_to_drop = (
                db.query(NarrativeFrameMention)
                .join(SourceItem, SourceItem.id == NarrativeFrameMention.source_item_id)
                .filter(SourceItem.story_cluster_id.in_(stale_cluster_ids),
                        NarrativeFrameMention.matched_by == "llm")
                .count()
            )
        else:
            fcm_to_drop = 0
            nfm_to_drop = 0
    else:
        candidate_clusters = base.all()
        fcm_to_drop = fcm_llm
        nfm_to_drop = nfm_llm
        stale_cluster_ids = None

    eligible = len(candidate_clusters)
    active_frames = db.query(NarrativeFrame).filter(
        NarrativeFrame.active == True).count()  # noqa: E712

    return {
        "fcm_total": fcm_total,
        "fcm_llm_to_drop": fcm_to_drop,
        "fcm_human_to_keep": fcm_human,
        "nfm_total": nfm_total,
        "nfm_llm_to_drop": nfm_to_drop,
        "nfm_human_to_keep": nfm_human,
        "eligible_clusters": eligible,
        "active_frames": active_frames,
        "candidate_items": [item for (_, item) in candidate_clusters],
        "stale_cluster_ids": stale_cluster_ids,
    }


def _backup_db() -> str:
    """Snapshot war_room.db to a timestamped .bak file. Returns the path."""
    src = _BACKEND / "war_room.db"
    if not src.exists():
        raise SystemExit(f"DB not found at {src}")
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = src.with_suffix(f".db.bak-clean-rematch-{ts}")
    shutil.copy2(src, dst)
    return str(dst)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--confirm-write", action="store_true",
                    help="Required to perform destructive ops. Without this, dry-run only.")
    ap.add_argument("--delay-seconds", type=float, default=0.1,
                    help="Seconds between LLM calls (rate-limit cushion).")
    ap.add_argument("--limit", type=int, default=0,
                    help="If > 0, process at most this many clusters (test runs).")
    ap.add_argument(
        "--stale-only", action="store_true", default=True,
        help="(default) Only process clusters with at least one conf=75 FCM "
             "row. These are the rows from the old prompt that need rebuilding. "
             "Clusters where every row was already written by the new prompt "
             "(conf != 75) are left alone.",
    )
    ap.add_argument(
        "--all-clusters", action="store_true",
        help="Override --stale-only: process every eligible cluster, including "
             "ones that already have only new-pipeline (conf != 75) data.",
    )
    ap.add_argument(
        "--empty-only", action="store_true",
        help="Override others: process only eligible clusters with ZERO FCM "
             "rows. Useful as a recovery pass — e.g. if a previous run "
             "partially emptied clusters but didn't rebuild them.",
    )
    args = ap.parse_args()
    stale_only = not args.all_clusters and not args.empty_only
    empty_only = args.empty_only

    db = SessionLocal()
    try:
        # ---- Phase 1: report scope
        scope = _scope_summary(db, stale_only=stale_only, empty_only=empty_only)
        print("=" * 60)
        mode = "empty-only" if empty_only else ("stale-only" if stale_only else "all-clusters")
        print(f"CLEAN REMATCH — SCOPE  (mode: {mode})")
        print("=" * 60)
        print(f"  Active frames:                   {scope['active_frames']}")
        label = (
            "Empty eligible clusters" if empty_only else
            "Clusters w/ stale conf=75 rows" if stale_only else
            "All eligible clusters (relevance≥55)"
        )
        print(f"  {label:<33} {scope['eligible_clusters']}")
        print()
        print(f"  Existing FCM rows:               {scope['fcm_total']}")
        print(f"    matched_by=llm  (to drop):     {scope['fcm_llm_to_drop']}")
        print(f"    matched_by=human (to keep):    {scope['fcm_human_to_keep']}")
        print()
        print(f"  Existing NFM rows:               {scope['nfm_total']}")
        print(f"    matched_by=llm  (to drop):     {scope['nfm_llm_to_drop']}")
        print(f"    matched_by=human (to keep):    {scope['nfm_human_to_keep']}")
        print()
        process_n = args.limit or scope["eligible_clusters"]
        est_cost = process_n * 0.0001
        est_time_min = process_n * 1.5 / 60
        print(f"  Will run match_article_to_frames on {process_n} items")
        print(f"  Estimated LLM cost: ~${est_cost:.2f} (~$0.0001/call)")
        print(f"  Estimated wall time: ~{est_time_min:.0f} min "
              f"(serial @ {args.delay_seconds}s between calls)")
        print()

        if not args.confirm_write:
            print("DRY RUN — no changes made. Re-run with --confirm-write to proceed.")
            return 0

        # ---- Phase 2: backup
        backup_path = _backup_db()
        print(f"DB snapshotted → {backup_path}")
        print()

        # ---- Phase 3: drop LLM rows (scoped to clusters we'll actually
        # rematch in Phase 4). The delete and the rematch must operate on
        # the SAME cluster set, otherwise --limit leaves the rest in a
        # half-deleted state.
        print("Dropping LLM-written FCM/NFM rows…")
        # Compute the cluster IDs we'll process in Phase 4.
        items_to_process = scope["candidate_items"]
        if args.limit:
            items_to_process = items_to_process[: args.limit]
        process_cluster_ids = [it.story_cluster_id for it in items_to_process
                                if it.story_cluster_id]
        if stale_only and scope["stale_cluster_ids"] is not None:
            # Intersect: only delete from clusters that BOTH have stale rows
            # AND are in the (possibly --limit-capped) set we'll rematch.
            target_ids = [cid for cid in process_cluster_ids
                          if cid in scope["stale_cluster_ids"]]
            n_fcm = 0
            n_nfm = 0
            if target_ids:
                n_fcm = (
                    db.query(FrameClusterMatch)
                    .filter(FrameClusterMatch.story_cluster_id.in_(target_ids),
                            FrameClusterMatch.matched_by == "llm")
                    .delete(synchronize_session=False)
                )
                target_item_ids = [
                    row[0] for row in (
                        db.query(SourceItem.id)
                        .filter(SourceItem.story_cluster_id.in_(target_ids))
                        .all()
                    )
                ]
                if target_item_ids:
                    n_nfm = (
                        db.query(NarrativeFrameMention)
                        .filter(NarrativeFrameMention.source_item_id.in_(target_item_ids),
                                NarrativeFrameMention.matched_by == "llm")
                        .delete(synchronize_session=False)
                    )
        else:
            # --all-clusters mode: still respect --limit so a small test run
            # doesn't wipe rows we won't rebuild.
            if args.limit and process_cluster_ids:
                n_fcm = (
                    db.query(FrameClusterMatch)
                    .filter(FrameClusterMatch.story_cluster_id.in_(process_cluster_ids),
                            FrameClusterMatch.matched_by == "llm")
                    .delete(synchronize_session=False)
                )
                target_item_ids = [
                    row[0] for row in (
                        db.query(SourceItem.id)
                        .filter(SourceItem.story_cluster_id.in_(process_cluster_ids))
                        .all()
                    )
                ]
                n_nfm = 0
                if target_item_ids:
                    n_nfm = (
                        db.query(NarrativeFrameMention)
                        .filter(NarrativeFrameMention.source_item_id.in_(target_item_ids),
                                NarrativeFrameMention.matched_by == "llm")
                        .delete(synchronize_session=False)
                    )
            else:
                n_fcm = db.query(FrameClusterMatch).filter(
                    FrameClusterMatch.matched_by == "llm").delete(synchronize_session=False)
                n_nfm = db.query(NarrativeFrameMention).filter(
                    NarrativeFrameMention.matched_by == "llm").delete(synchronize_session=False)
        # Reset cached stage ONLY for frames whose FCM rows were touched.
        # Previously we reset every frame, causing the next dashboard load
        # to write up to 200 redundant FrameStageHistory transitions even
        # when --limit only processed 50 clusters. Scope to actually-
        # changed frames so the history table stays meaningful.
        if process_cluster_ids:
            touched_frame_ids = [
                r[0] for r in db.query(FrameClusterMatch.frame_id).filter(
                    FrameClusterMatch.story_cluster_id.in_(process_cluster_ids)
                ).distinct().all()
            ]
            if touched_frame_ids:
                db.query(NarrativeFrame).filter(
                    NarrativeFrame.id.in_(touched_frame_ids)
                ).update(
                    {NarrativeFrame.last_known_stage: None,
                     NarrativeFrame.last_stage_check_at: None},
                    synchronize_session=False,
                )
        db.commit()
        print(f"  Deleted {n_fcm} FCM and {n_nfm} NFM rows. Cleared cached stages.")
        print()

        # ---- Phase 4: rematch
        from app.services.narrative_frames import match_article_to_frames
        items = scope["candidate_items"]
        if args.limit:
            items = items[:args.limit]
        print(f"Rematching {len(items)} cluster representatives…")
        t0 = time.time()
        total_matches = 0
        no_match = 0
        errors = 0
        for i, item in enumerate(items, 1):
            try:
                matched = match_article_to_frames(db, item)
                if matched:
                    total_matches += len(matched)
                else:
                    no_match += 1
            except Exception as exc:
                errors += 1
                print(f"  [{i}/{len(items)}] item={item.id} ERROR: {exc}",
                      file=sys.stderr)
            if i % 25 == 0 or i == len(items):
                elapsed = time.time() - t0
                rate = i / elapsed
                eta = (len(items) - i) / rate if rate > 0 else 0
                print(f"  [{i}/{len(items)}] matches={total_matches} "
                      f"no_match={no_match} errors={errors} "
                      f"({rate:.1f}/s, ETA {eta/60:.1f}m)")
            if i < len(items):
                time.sleep(args.delay_seconds)
        print()
        print(f"DONE. {total_matches} new FCM matches written across {len(items)} clusters.")
        print(f"     no_match={no_match}, errors={errors}, "
              f"elapsed={(time.time() - t0)/60:.1f}m")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
