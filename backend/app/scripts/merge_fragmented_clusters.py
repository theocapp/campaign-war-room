"""Merge fragmented wire-pickup clusters using the new wider clustering window.

Background
----------
Story clustering (v2) was originally tuned with a 14-day candidate window and
a 7-day published-close tolerance. Wire stories republished more than ~7 days
apart fell into separate clusters even when they were obviously the same
story — e.g. a press release picked up by a local outlet on day 1 and a
regional aggregator on day 9 would land in two clusters instead of one.

The window has since been widened (CLUSTER_WINDOW_DAYS=30,
CLUSTER_PUBLISHED_CLOSE_DAYS=14), but that only affects NEW ingestion. This
script re-evaluates EXISTING clusters under the new rules and merges
fragments.

Strategy
--------
Walk clusters in chronological order (earliest first_seen first). For each
cluster, check whether its representative article would attach to any
already-processed (earlier) cluster under the v2 matching rules. If yes,
merge this cluster INTO the earlier one.

Walking chronologically means a "chain" of fragments (A → B → C, all the
same story) collapses correctly: B merges into A, then C will see A's
(now-larger) cluster as a candidate and merge into it.

Run modes
---------
  --dry-run (default):  Report what would happen, no writes.
  --apply:              Perform the merges.

Idempotent: re-running after --apply should report 0 merges.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import StoryCluster
from app.services import story_clustering

logger = logging.getLogger("merge_fragmented_clusters")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# Re-export the canonical helpers from story_clustering for back-compat with
# imports under `_numbers_in_title` / `_numbers_mismatch` names. The actual
# implementation lives in story_clustering.py so both live ingestion and
# this backfill share one definition of the number-mismatch rule.
_numbers_in_title = story_clustering.numbers_in_title
_numbers_mismatch = story_clustering.numbers_mismatch


def _parse_dt(v):
    """SQLite returns DATETIME columns as strings when queried via raw SQL.
    Parse to datetime; pass through if already a datetime; return None for
    null/unparseable."""
    if v is None or isinstance(v, datetime):
        return v
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace(" ", "T"))
        except ValueError:
            return None
    return None


def find_merges(db: Session) -> list[dict]:
    """Walk clusters chronologically and identify which would merge into earlier ones.

    Returns a list of merge actions: {old_id, new_id, reason, old_count, new_count_before}.
    """
    window_days = int(os.environ.get("CLUSTER_WINDOW_DAYS", "30"))
    hamming_max = int(os.environ.get("CLUSTER_SIMHASH_HAMMING_MAX", "6"))
    published_close_days = int(os.environ.get("CLUSTER_PUBLISHED_CLOSE_DAYS", "14"))
    # Backfill-specific knobs (tighter than live v2). All env-controlled so
    # tuning iterations don't require code changes.
    rule2_jaccard_min = float(os.environ.get("MERGE_RULE2_JACCARD_MIN", "0.92"))
    rule2_hamming_max = int(os.environ.get("MERGE_RULE2_HAMMING_MAX", "8"))
    rule3_jaccard_min = float(os.environ.get("MERGE_RULE3_JACCARD_MIN", "0.65"))
    # Reject a Rule-3 merge when both titles contain digit tokens and any of
    # those digit tokens are mismatched — catches "District 3" vs "District 8",
    # year mismatches, etc., where normalize_title would otherwise collapse
    # short tokens. Domain-agnostic.
    require_number_match = os.environ.get("MERGE_REQUIRE_NUMBER_MATCH", "1") == "1"
    # Reject when body lengths differ by more than this ratio. Wire pickup
    # republishes the same body, so length variance is small (typically <2x
    # due to outlet formatting). 10x is a generous bound — only the most
    # extreme template-vs-detailed-article mismatches get blocked.
    max_length_ratio = float(os.environ.get("MERGE_MAX_LENGTH_RATIO", "10"))

    logger.info(
        "merge rules: window_days=%d hamming_max=%d published_close_days=%d "
        "rule2(jaccard>=%.2f,hamming<=%d) rule3(jaccard>=%.2f,hamming<=%d,temporal) "
        "number_match=%s",
        window_days, hamming_max, published_close_days,
        rule2_jaccard_min, rule2_hamming_max, rule3_jaccard_min, hamming_max,
        require_number_match,
    )

    # Pull cluster + representative as a plain row (no ORM tracking) so this
    # function has no session side-effects regardless of mode.
    rows = db.execute(text(
        """
        SELECT
          c.id,
          c.first_seen_at, c.last_seen_at,
          c.article_count, c.title_representative, c.simhash_64,
          c.representative_source_item_id,
          s.title, s.raw_text, s.source_url, s.published_at, s.ingested_at
        FROM story_clusters c
        JOIN source_items s ON s.id = c.representative_source_item_id
        ORDER BY c.first_seen_at ASC, c.id ASC
        """
    )).fetchall()
    logger.info("scanning %d clusters", len(rows))

    # In-memory representation of a candidate cluster — only the fields the
    # matcher needs. Mutating these is safe (not ORM-backed).
    # Keyed by cluster id.
    live: dict[str, dict] = {}
    merges: list[dict] = []

    for r in rows:
        (cid, first_seen, last_seen, article_count, title_rep, simhash_hex,
         rep_id, rep_title, rep_text, rep_url, rep_pub, rep_ing) = r
        first_seen = _parse_dt(first_seen)
        last_seen = _parse_dt(last_seen)
        rep_pub = _parse_dt(rep_pub)
        rep_ing = _parse_dt(rep_ing)

        item_canonical = story_clustering.canonical_url(rep_url)
        item_hash = story_clustering.simhash64(rep_text or rep_title)
        item_body_len = len((rep_text or "").split())
        short_text = item_body_len < 50

        # Bad-data guard: a few rows have first_seen_at = year 0001 (sentinel
        # value from earlier ingestion bugs). Treat them as "now" so the
        # cutoff math doesn't underflow.
        base = first_seen or rep_ing or datetime.utcnow()
        if base.year < 1900:
            base = datetime.utcnow()
        cutoff = base - timedelta(days=window_days)
        for k in list(live.keys()):
            if (live[k]["last_seen"] or datetime.min) < cutoff:
                del live[k]

        matched_id: Optional[str] = None
        match_reason: Optional[str] = None
        # Most-recent first — wire pickup typically clusters with the latest fragment.
        for cand_id in sorted(
            live, key=lambda k: live[k]["last_seen"] or datetime.min, reverse=True,
        ):
            cand = live[cand_id]
            # Rule 1: URL canonical match (safe — same article was ingested twice)
            if item_canonical and story_clustering.canonical_url(cand["rep_url"]) == item_canonical:
                matched_id, match_reason = cand_id, "url"
                break
            if short_text:
                # Short-text items skip body-simhash rules. Only rule 1 above
                # applies. Title-only matching would over-merge for these.
                continue
            sim = story_clustering.title_similarity(rep_title, cand["rep_title"])
            cand_hash = story_clustering._hex_to_int(cand["simhash_hex"])
            if cand_hash is None:
                continue
            ham = story_clustering.hamming(item_hash, cand_hash)
            # Number-mismatch guard (applies to rules 2 and 3): if both
            # titles have digits and they don't share any, skip. Catches
            # District 8 vs District 3, May 18 vs May 4, etc. that
            # normalize_title would otherwise collapse.
            if require_number_match and _numbers_mismatch(rep_title, cand["rep_title"]):
                continue
            # Length-ratio guard: same wire pickup has similar body length.
            # Wildly-different lengths with matching simhash means one fully
            # contains the other (e.g. press release embedded in a long
            # article) — treat as different objects.
            cand_len = cand["body_len"]
            if item_body_len and cand_len:
                ratio = max(item_body_len, cand_len) / max(min(item_body_len, cand_len), 1)
                if ratio > max_length_ratio:
                    continue
            # Rule 2 (TIGHTENED for backfill): high title similarity AND
            # simhash agreement. The v2 rule 2 takes title-only at 0.92 — that
            # over-merges on prefix-similar items.
            if sim >= rule2_jaccard_min and ham <= rule2_hamming_max:
                matched_id = cand_id
                match_reason = f"title={sim:.2f}+hamming={ham}"
                break
            # Rule 3: mid title + body simhash + temporal proximity
            if sim >= rule3_jaccard_min and ham <= hamming_max and story_clustering._published_close(
                rep_pub, cand["rep_pub"], days=published_close_days,
            ):
                matched_id = cand_id
                match_reason = f"title={sim:.2f}+hamming={ham}+temporal"
                break

        if matched_id:
            tgt = live[matched_id]
            merges.append({
                "old_id": cid,
                "new_id": matched_id,
                "reason": match_reason,
                "old_count": article_count or 1,
                "old_title": (title_rep or "")[:80],
                "new_title": (tgt["title_rep"] or "")[:80],
                "new_count_before": tgt["article_count"],
            })
            tgt["article_count"] += article_count or 1
            if last_seen and (tgt["last_seen"] is None or last_seen > tgt["last_seen"]):
                tgt["last_seen"] = last_seen
        else:
            live[cid] = {
                "last_seen": last_seen,
                "article_count": article_count or 1,
                "title_rep": title_rep,
                "simhash_hex": simhash_hex,
                "rep_title": rep_title,
                "rep_url": rep_url,
                "rep_pub": rep_pub,
                "body_len": item_body_len,
            }

    logger.info("merge analysis complete: %d merges identified", len(merges))
    return merges


def apply_merges(db: Session, merges: list[dict]) -> dict:
    """Apply merges to the database. Returns a stats dict.

    For each merge (old_id → new_id):
      1. Reassign source_items.story_cluster_id from old → new
      2. Reassign frame_cluster_matches, handling (frame_id, new_id) collisions
         by deleting the old row when both exist.
      3. Reassign cluster_opponent_activities similarly.
      4. Recompute the target cluster's aggregates
         (article_count, outlet_count, first_seen_at, last_seen_at).
      5. Delete the old StoryCluster row.

    Done in batches with periodic commits to bound transaction size.
    """
    moved_items = 0
    moved_fcm = 0
    deleted_fcm = 0
    moved_coa = 0
    deleted_coa = 0
    deleted_clusters = 0

    for i, m in enumerate(merges):
        old_id = m["old_id"]
        new_id = m["new_id"]

        # 1. source_items
        moved_items += db.execute(
            text("UPDATE source_items SET story_cluster_id = :new WHERE story_cluster_id = :old"),
            {"new": new_id, "old": old_id},
        ).rowcount

        # 2. frame_cluster_matches — handle UNIQUE(frame_id, story_cluster_id) collisions
        # If target already has a row for this frame_id, merge into it (keep max confidence + widest window).
        db.execute(
            text(
                """
                UPDATE frame_cluster_matches AS tgt
                SET confidence = MAX(tgt.confidence, src.confidence),
                    first_seen_at = MIN(tgt.first_seen_at, src.first_seen_at),
                    last_seen_at  = MAX(tgt.last_seen_at,  src.last_seen_at)
                FROM frame_cluster_matches AS src
                WHERE src.story_cluster_id = :old
                  AND tgt.story_cluster_id = :new
                  AND tgt.frame_id = src.frame_id
                """
            ),
            {"old": old_id, "new": new_id},
        )
        deleted_fcm += db.execute(
            text(
                """
                DELETE FROM frame_cluster_matches
                WHERE story_cluster_id = :old
                  AND frame_id IN (
                    SELECT frame_id FROM frame_cluster_matches WHERE story_cluster_id = :new
                  )
                """
            ),
            {"old": old_id, "new": new_id},
        ).rowcount
        moved_fcm += db.execute(
            text("UPDATE frame_cluster_matches SET story_cluster_id = :new WHERE story_cluster_id = :old"),
            {"new": new_id, "old": old_id},
        ).rowcount

        # 3. cluster_opponent_activities — UNIQUE(opponent_id, story_cluster_id, fingerprint)
        db.execute(
            text(
                """
                UPDATE cluster_opponent_activities AS tgt
                SET first_seen_at = MIN(tgt.first_seen_at, src.first_seen_at),
                    last_seen_at  = MAX(tgt.last_seen_at,  src.last_seen_at)
                FROM cluster_opponent_activities AS src
                WHERE src.story_cluster_id = :old
                  AND tgt.story_cluster_id = :new
                  AND tgt.opponent_id = src.opponent_id
                  AND tgt.fingerprint = src.fingerprint
                """
            ),
            {"old": old_id, "new": new_id},
        )
        deleted_coa += db.execute(
            text(
                """
                DELETE FROM cluster_opponent_activities
                WHERE story_cluster_id = :old
                  AND EXISTS (
                    SELECT 1 FROM cluster_opponent_activities tgt
                    WHERE tgt.story_cluster_id = :new
                      AND tgt.opponent_id = cluster_opponent_activities.opponent_id
                      AND tgt.fingerprint = cluster_opponent_activities.fingerprint
                  )
                """
            ),
            {"old": old_id, "new": new_id},
        ).rowcount
        moved_coa += db.execute(
            text("UPDATE cluster_opponent_activities SET story_cluster_id = :new WHERE story_cluster_id = :old"),
            {"new": new_id, "old": old_id},
        ).rowcount

        # 4. Recompute target cluster aggregates from its (now expanded) members.
        db.execute(
            text(
                """
                UPDATE story_clusters
                SET article_count = (
                        SELECT COUNT(*) FROM source_items WHERE story_cluster_id = :new
                    ),
                    outlet_count = (
                        SELECT COUNT(DISTINCT outlet_id) FROM source_items
                        WHERE story_cluster_id = :new AND outlet_id IS NOT NULL
                    ),
                    first_seen_at = (
                        SELECT MIN(COALESCE(published_at, ingested_at, created_at))
                        FROM source_items WHERE story_cluster_id = :new
                    ),
                    last_seen_at = (
                        SELECT MAX(COALESCE(published_at, ingested_at, created_at))
                        FROM source_items WHERE story_cluster_id = :new
                    )
                WHERE id = :new
                """
            ),
            {"new": new_id},
        )

        # 5. Delete the old (now-empty) cluster row.
        deleted_clusters += db.execute(
            text("DELETE FROM story_clusters WHERE id = :old"),
            {"old": old_id},
        ).rowcount

        if (i + 1) % 200 == 0:
            db.commit()
            logger.info("applied %d/%d merges", i + 1, len(merges))

    db.commit()
    return {
        "merges": len(merges),
        "moved_items": moved_items,
        "moved_fcm": moved_fcm,
        "deleted_fcm_collisions": deleted_fcm,
        "moved_coa": moved_coa,
        "deleted_coa_collisions": deleted_coa,
        "deleted_clusters": deleted_clusters,
    }


def _report(merges: list[dict], db: Session) -> None:
    print("\n══════════════ CLUSTER MERGE REPORT ══════════════")
    print(f"  Total merges identified:  {len(merges)}")

    if not merges:
        print("  Nothing to merge. Done.")
        print("══════════════════════════════════════════════════\n")
        return

    # Breakdown by rule (helps interpret the false-positive risk)
    by_rule: dict[str, int] = {}
    for m in merges:
        key = (
            "url" if m["reason"] == "url"
            else "title+hamming" if m["reason"].startswith("title=") and "temporal" not in m["reason"]
            else "title+hamming+temporal" if "temporal" in m["reason"]
            else m["reason"]
        )
        by_rule[key] = by_rule.get(key, 0) + 1
    print("  By rule:")
    for k, v in sorted(by_rule.items(), key=lambda x: -x[1]):
        print(f"    {k:<28} {v}")

    # Size of resulting clusters (count_before + this old's count, may stack)
    sizes: dict[int, int] = {}
    for m in merges:
        # crude estimate — doesn't account for further merges into same target
        size = (m["new_count_before"] or 1) + (m["old_count"] or 1)
        bucket = "1" if size <= 1 else "2-3" if size <= 3 else "4-9" if size <= 9 else "10-39" if size <= 39 else "40+"
        sizes[bucket] = sizes.get(bucket, 0) + 1
    print("  Resulting cluster size buckets (incoming merges):")
    for k in ("2-3", "4-9", "10-39", "40+"):
        if k in sizes:
            print(f"    {k}: {sizes[k]}")

    # Top 15 biggest merges by size of incoming fragment
    print("\n  Top 15 incoming merges (by old_count):")
    for m in sorted(merges, key=lambda x: x["old_count"], reverse=True)[:15]:
        print(
            f"    [{m['old_count']:>3}] {m['old_id']:<14} → {m['new_id']:<14} "
            f"({m['reason']})\n         old: {m['old_title']}\n         new: {m['new_title']}"
        )

    # Current cluster-count baseline
    total_clusters = db.query(StoryCluster).count()
    print(f"\n  Current story_clusters total:  {total_clusters}")
    print(f"  After merges (estimate):       {total_clusters - len(merges)}")
    print("══════════════════════════════════════════════════\n")


def main() -> int:
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="Perform the merges. Without this flag, runs dry-run only.",
    )
    parser.add_argument(
        "--dump", metavar="PATH",
        help="Dump all proposed merges to a JSON file for inspection.",
    )
    args = parser.parse_args()

    with SessionLocal() as db:
        merges = find_merges(db)
        _report(merges, db)

        if args.dump:
            with open(args.dump, "w") as f:
                json.dump(merges, f, indent=2, default=str)
            print(f"Dumped {len(merges)} merges to {args.dump}")

        if not args.apply:
            print("Dry-run only — no writes. Pass --apply to perform merges.")
            return 0
        if not merges:
            return 0

        logger.info("applying %d merges …", len(merges))
        stats = apply_merges(db, merges)
        print("\n  Apply complete:")
        for k, v in stats.items():
            print(f"    {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
