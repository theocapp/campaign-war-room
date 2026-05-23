"""One-shot backfill: populate cluster-native tables from historical data.

Run:  python -m app.scripts.recluster_backfill

Idempotent and resumable — every write is UPSERT or
`INSERT … ON CONFLICT DO NOTHING`, and the script can be re-run safely after
a partial completion.

Passes (in order):
  1. Create StoryCluster rows for every distinct SourceItem.story_cluster_id.
  2. Re-cluster items with NULL story_cluster_id using assign_story_cluster_v2.
  3. Backfill frame_cluster_matches from narrative_frame_mentions
     (pre-aggregated SELECT → ON CONFLICT DO UPDATE).
  4. Backfill cluster_opponent_activities from opponent_activities with
     fingerprint computed at insert time.
  5. Sanity report.

No LLM calls. No deletions of legacy tables.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import SessionLocal, init_db
from app.models import (
    CampaignConfig,
    ClusterOpponentActivity,
    FrameClusterMatch,
    NarrativeFrameMention,
    Opponent,
    OpponentActivity,
    SourceItem,
    StoryCluster,
)
from app.services import story_clustering
from app.services.cluster_writes import _dt_str, _opponent_fingerprint

logger = logging.getLogger("recluster_backfill")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# ── Pass 1: create StoryCluster rows from existing story_cluster_id values ───

def _create_clusters_from_existing(db: Session) -> tuple[int, int]:
    """For every distinct non-null SourceItem.story_cluster_id, ensure a
    matching StoryCluster row exists. Returns (created, skipped_existing)."""
    distinct_ids = [
        r[0] for r in db.execute(
            text(
                "SELECT DISTINCT story_cluster_id FROM source_items "
                "WHERE story_cluster_id IS NOT NULL"
            )
        )
    ]
    logger.info("pass 1: %d distinct legacy cluster ids", len(distinct_ids))

    existing_ids = {r[0] for r in db.query(StoryCluster.id).all()}
    candidate_name = _campaign_candidate_name(db)

    created = 0
    skipped = 0
    for i, cid in enumerate(distinct_ids):
        if cid in existing_ids:
            skipped += 1
            continue
        members = (
            db.query(SourceItem)
            .filter(SourceItem.story_cluster_id == cid)
            .all()
        )
        if not members:
            continue
        _build_cluster_row(db, cid, members, candidate_name)
        created += 1
        if (i + 1) % 200 == 0:
            db.commit()
            logger.info("pass 1: %d/%d created=%d skipped=%d", i + 1, len(distinct_ids), created, skipped)
    db.commit()
    logger.info("pass 1 done: created=%d skipped_existing=%d", created, skipped)
    return created, skipped


def _build_cluster_row(
    db: Session,
    cluster_id: str,
    members: list[SourceItem],
    candidate_name: Optional[str],
) -> StoryCluster:
    """Construct one StoryCluster row from a list of member articles."""
    # seed = earliest by created_at (provenance)
    seed = min(members, key=lambda m: m.created_at or datetime.utcnow())
    # representative = winner of authority → length → earliest-pub
    rep = story_clustering._select_representative(db, [m.id for m in members])

    # aggregates
    times = []
    for m in members:
        t = m.published_at or m.ingested_at or m.created_at
        if t:
            times.append(t)
    first_seen = min(times) if times else datetime.utcnow()
    last_seen = max(times) if times else first_seen

    distinct_outlets = len({m.outlet_id for m in members if m.outlet_id})

    # SimHash from the representative's body (preferred) or title fallback
    rep_hash = story_clustering.simhash64(rep.raw_text or rep.title)
    simhash_hex = f"{rep_hash:016x}" if rep_hash else None

    # known_entities seed — light: opponent names whose attacks landed in any
    # member article + the campaign candidate name. Phase D's salience gate
    # will add to this organically as new articles arrive.
    opp_names = {
        r[0] for r in db.execute(
            text(
                "SELECT DISTINCT o.name FROM opponents o "
                "JOIN opponent_activities oa ON oa.opponent_id = o.id "
                "JOIN source_items s ON s.id = oa.source_item_id "
                "WHERE s.story_cluster_id = :cid"
            ),
            {"cid": cluster_id},
        )
        if r[0]
    }
    entities = sorted(opp_names | ({candidate_name} if candidate_name else set()))

    # last_llm_analysis_at — best-guess. Use the representative's ingested_at
    # since LLM analysis ran at ingest time historically.
    last_llm = rep.ingested_at or rep.created_at

    cluster = StoryCluster(
        id=cluster_id,
        seed_source_item_id=seed.id,
        representative_source_item_id=rep.id,
        analysis_anchor_source_item_id=rep.id,
        analysis_anchor_updated_at=last_llm,
        last_llm_analysis_at=last_llm,
        title_representative=rep.title,
        summary_representative=story_clustering._short_summary(rep),
        simhash_64=simhash_hex,
        first_seen_at=first_seen,
        last_seen_at=last_seen,
        article_count=len(members),
        outlet_count=distinct_outlets,
        source_diversity_score=0.0,
        known_entities=json.dumps(entities) if entities else None,
        dormant_since=None,
        structured_extraction=rep.structured_extraction,
    )
    db.add(cluster)
    return cluster


def _campaign_candidate_name(db: Session) -> Optional[str]:
    config = db.query(CampaignConfig).first()
    return (config.candidate_name or "").strip() or None if config else None


# ── Pass 2: re-cluster items with NULL story_cluster_id ──────────────────────

def _recluster_null_items(db: Session, batch_size: int = 500) -> int:
    """Run assign_story_cluster_v2 on every SourceItem whose story_cluster_id
    is NULL, in created_at order. Each call may either attach the item to an
    existing cluster or create a new one."""
    total = db.query(SourceItem).filter(SourceItem.story_cluster_id.is_(None)).count()
    if total == 0:
        logger.info("pass 2: no null story_cluster_id rows")
        return 0
    logger.info("pass 2: %d items need cluster assignment", total)

    processed = 0
    while True:
        items = (
            db.query(SourceItem)
            .filter(SourceItem.story_cluster_id.is_(None))
            .order_by(SourceItem.created_at.asc())
            .limit(batch_size)
            .all()
        )
        if not items:
            break
        for it in items:
            story_clustering.assign_story_cluster_v2(db, it)
        db.commit()
        processed += len(items)
        logger.info("pass 2: %d/%d", processed, total)
    logger.info("pass 2 done: %d items reclustered", processed)
    return processed


# ── Pass 3: backfill frame_cluster_matches ────────────────────────────────────

_BACKFILL_FCM_SQL = """
INSERT INTO frame_cluster_matches
  (frame_id, story_cluster_id, confidence, matched_by, source_type,
   representative_snapshot_ts, first_seen_at, last_seen_at)
SELECT
  m.frame_id,
  COALESCE(s.story_cluster_id, 'source-' || s.id) AS cluster_id,
  MAX(m.confidence) AS confidence,
  'llm' AS matched_by,
  'cluster_backfill' AS source_type,
  MIN(COALESCE(s.published_at, s.ingested_at)) AS rep_snapshot_ts,
  MIN(COALESCE(s.published_at, s.ingested_at)) AS first_seen,
  MAX(COALESCE(s.published_at, s.ingested_at)) AS last_seen
FROM narrative_frame_mentions m
JOIN source_items s ON s.id = m.source_item_id
JOIN story_clusters c
  ON c.id = COALESCE(s.story_cluster_id, 'source-' || s.id)
WHERE COALESCE(s.published_at, s.ingested_at) >= :cutoff
GROUP BY m.frame_id, cluster_id
ON CONFLICT (frame_id, story_cluster_id) DO UPDATE SET
  confidence = MAX(excluded.confidence, frame_cluster_matches.confidence),
  last_seen_at = MAX(excluded.last_seen_at, frame_cluster_matches.last_seen_at)
"""

# Pre-Phase 3.5 LLM runs lacked the keyword gate and JSON repair logic, so
# matches from articles published before the campaign window are unreliable.
# This cutoff keeps the backfill to campaign-era articles only.
_DEFAULT_CAMPAIGN_CUTOFF = "2024-01-01"


def _campaign_cutoff(db: Session) -> str:
    """Return ISO date string for the earliest article we'll backfill matches for.

    Uses CampaignConfig.monitoring_start_date if set; falls back to
    _DEFAULT_CAMPAIGN_CUTOFF so re-runs stay idempotent even without config.
    """
    config = db.query(CampaignConfig).first()
    if config:
        # monitoring_start_date may not exist on older configs — guard with getattr
        start = getattr(config, "monitoring_start_date", None)
        if start:
            if isinstance(start, datetime):
                return start.date().isoformat()
            return str(start)[:10]
    return _DEFAULT_CAMPAIGN_CUTOFF


def _backfill_frame_matches(db: Session) -> int:
    """Pre-aggregated SELECT + ON CONFLICT DO UPDATE. Idempotent.

    Only backfills matches from articles published on or after the campaign
    cutoff date — articles published before that were analyzed by older LLM
    runs that lacked quality guards and produced unreliable frame matches.
    """
    cutoff = _campaign_cutoff(db)
    logger.info("pass 3: backfilling frame matches with cutoff=%s", cutoff)
    before = db.query(FrameClusterMatch).count()
    db.execute(text(_BACKFILL_FCM_SQL), {"cutoff": cutoff})
    db.commit()
    after = db.query(FrameClusterMatch).count()
    logger.info("pass 3 done: frame_cluster_matches rows %d -> %d (cutoff=%s)", before, after, cutoff)
    return after - before


# ── Pass 4: backfill cluster_opponent_activities ─────────────────────────────

def _backfill_opponent_activities(db: Session) -> int:
    """Iterate legacy OpponentActivity rows, compute fingerprint, UPSERT into
    cluster_opponent_activities keyed by (opponent_id, cluster_id, fingerprint).

    Applies the same campaign cutoff as Pass 3 to avoid promoting bad matches
    from pre-campaign articles.
    """
    before = db.query(ClusterOpponentActivity).count()
    cutoff = _campaign_cutoff(db)
    logger.info("pass 4: backfilling opponent activities with cutoff=%s", cutoff)

    rows = db.execute(
        text(
            """
            SELECT oa.opponent_id,
                   COALESCE(s.story_cluster_id, 'source-' || s.id) AS cluster_id,
                   oa.claim, oa.attack, oa.promise,
                   COALESCE(s.published_at, s.ingested_at) AS event_ts
            FROM opponent_activities oa
            JOIN source_items s ON s.id = oa.source_item_id
            JOIN story_clusters c ON c.id = COALESCE(s.story_cluster_id, 'source-' || s.id)
            WHERE COALESCE(s.published_at, s.ingested_at) >= :cutoff
            """
        ),
        {"cutoff": cutoff},
    ).fetchall()
    logger.info("pass 4: %d legacy opponent_activity rows to backfill", len(rows))

    for i, (opponent_id, cluster_id, claim, attack, promise, event_ts) in enumerate(rows):
        fp = _opponent_fingerprint(claim, attack, promise)
        if not fp:
            continue
        ts = event_ts or datetime.utcnow()
        if isinstance(ts, datetime):
            ts = _dt_str(ts)  # space separator — see cluster_writes._dt_str
        db.execute(
            text(
                """
                INSERT INTO cluster_opponent_activities
                  (opponent_id, story_cluster_id, claim, attack, promise,
                   fingerprint, source_type, first_seen_at, last_seen_at)
                VALUES
                  (:opp, :cid, :claim, :attack, :promise, :fp, 'cluster_backfill', :ts, :ts)
                ON CONFLICT(opponent_id, story_cluster_id, fingerprint) DO UPDATE SET
                  last_seen_at = MAX(excluded.last_seen_at, cluster_opponent_activities.last_seen_at)
                """
            ),
            {
                "opp": opponent_id,
                "cid": cluster_id,
                "claim": claim,
                "attack": attack,
                "promise": promise,
                "fp": fp,
                "ts": ts,
            },
        )
        if (i + 1) % 200 == 0:
            db.commit()
    db.commit()
    after = db.query(ClusterOpponentActivity).count()
    logger.info("pass 4 done: cluster_opponent_activities rows %d -> %d", before, after)
    return after - before


# ── Pass 5: sanity report ─────────────────────────────────────────────────────

def _sanity_report(db: Session) -> dict:
    out = {}
    out["source_items"] = db.query(SourceItem).count()
    out["story_clusters"] = db.query(StoryCluster).count()
    out["narrative_frame_mentions"] = db.query(NarrativeFrameMention).count()
    out["frame_cluster_matches"] = db.query(FrameClusterMatch).count()
    out["opponent_activities"] = db.query(OpponentActivity).count()
    out["cluster_opponent_activities"] = db.query(ClusterOpponentActivity).count()

    # Cluster size distribution (in buckets)
    sizes = [r[0] for r in db.execute(text(
        "SELECT article_count FROM story_clusters"
    ))]
    buckets = {"1": 0, "2-3": 0, "4-9": 0, "10-39": 0, "40+": 0}
    for s in sizes:
        if s <= 1:
            buckets["1"] += 1
        elif s <= 3:
            buckets["2-3"] += 1
        elif s <= 9:
            buckets["4-9"] += 1
        elif s <= 39:
            buckets["10-39"] += 1
        else:
            buckets["40+"] += 1
    out["cluster_size_distribution"] = buckets

    # Orphans: source_items still missing a cluster id
    out["source_items_without_cluster"] = db.query(SourceItem).filter(
        SourceItem.story_cluster_id.is_(None)
    ).count()

    # Top 5 largest clusters (sanity check the representative quality)
    top = db.execute(text(
        "SELECT id, article_count, title_representative "
        "FROM story_clusters ORDER BY article_count DESC LIMIT 5"
    )).fetchall()
    out["top_clusters"] = [
        {"id": r[0], "size": r[1], "title": (r[2] or "")[:80]} for r in top
    ]

    # Unique-constraint sanity: rows where (frame_id, cluster_id) collisions
    # would have happened are merged by UPSERT, so the constraint check is
    # implicit — but assert there is at most one row per (frame,cluster).
    fcm_dupes = db.execute(text(
        "SELECT COUNT(*) FROM ("
        "  SELECT frame_id, story_cluster_id, COUNT(*) c"
        "  FROM frame_cluster_matches"
        "  GROUP BY frame_id, story_cluster_id HAVING c > 1"
        ")"
    )).scalar()
    out["frame_cluster_match_duplicates"] = fcm_dupes
    coa_dupes = db.execute(text(
        "SELECT COUNT(*) FROM ("
        "  SELECT opponent_id, story_cluster_id, fingerprint, COUNT(*) c"
        "  FROM cluster_opponent_activities"
        "  GROUP BY opponent_id, story_cluster_id, fingerprint HAVING c > 1"
        ")"
    )).scalar()
    out["cluster_opponent_duplicates"] = coa_dupes

    return out


def _print_report(report: dict) -> None:
    print("\n══════════════ BACKFILL SANITY REPORT ══════════════")
    print(f"  source_items:                  {report['source_items']:>8}")
    print(f"  story_clusters:                {report['story_clusters']:>8}")
    print(f"  source_items_without_cluster:  {report['source_items_without_cluster']:>8}")
    print(f"  narrative_frame_mentions:      {report['narrative_frame_mentions']:>8}  (legacy)")
    print(f"  frame_cluster_matches:         {report['frame_cluster_matches']:>8}  (cluster-native)")
    print(f"  opponent_activities:           {report['opponent_activities']:>8}  (legacy)")
    print(f"  cluster_opponent_activities:   {report['cluster_opponent_activities']:>8}  (cluster-native)")
    print(f"\n  Cluster size distribution:")
    for bucket, count in report["cluster_size_distribution"].items():
        print(f"    {bucket:>6}: {count}")
    print(f"\n  Constraint violations:")
    print(f"    frame_cluster_match duplicates:     {report['frame_cluster_match_duplicates']}")
    print(f"    cluster_opponent_activity dupes:    {report['cluster_opponent_duplicates']}")
    print(f"\n  Top 5 largest clusters:")
    for c in report["top_clusters"]:
        print(f"    [{c['size']:>4}] {c['id']:<14} {c['title']}")
    print("════════════════════════════════════════════════════\n")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-recluster", action="store_true",
        help="Skip pass 2 (re-cluster items with NULL story_cluster_id). "
             "Use when you want to backfill mentions only.",
    )
    parser.add_argument(
        "--report-only", action="store_true",
        help="Run only the sanity report; do not modify any data.",
    )
    args = parser.parse_args()

    # Ensure schema is up to date before we touch it.
    init_db()

    with SessionLocal() as db:
        if args.report_only:
            _print_report(_sanity_report(db))
            return 0

        logger.info("starting backfill")
        _create_clusters_from_existing(db)
        if not args.skip_recluster:
            _recluster_null_items(db)
        # After pass 2, any items that were null may have created new clusters
        # — re-run pass 1's idempotent path is unnecessary because v2 already
        # writes the StoryCluster row inline.
        _backfill_frame_matches(db)
        _backfill_opponent_activities(db)
        _print_report(_sanity_report(db))

        if db.execute(text(
            "SELECT COUNT(*) FROM ("
            "  SELECT frame_id, story_cluster_id, COUNT(*) c"
            "  FROM frame_cluster_matches"
            "  GROUP BY frame_id, story_cluster_id HAVING c > 1"
            ")"
        )).scalar() > 0:
            logger.error("FATAL: frame_cluster_matches has duplicate (frame,cluster) rows")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
