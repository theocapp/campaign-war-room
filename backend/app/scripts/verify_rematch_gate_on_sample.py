"""Verify the rematch gate on a small sample of articles with known NFMs.

For each sample article, computes the shortlist and checks whether each of
the article's known matching frames is still in the shortlist. This catches
calibration regressions before triggering a full rematch.

Read-only — does not write to the DB.

Usage:
    cd backend && .venv/bin/python -m app.scripts.verify_rematch_gate_on_sample
"""
from __future__ import annotations

import random
from collections import defaultdict

from app.db import SessionLocal
from app.models import NarrativeFrame, NarrativeFrameMention, SourceItem
from app.services.narrative_frames import _shortlist_frames_for_article


def main(sample_size: int = 30, seed: int = 7) -> None:
    random.seed(seed)
    db = SessionLocal()
    try:
        # Articles that have ≥1 NFM (we have ground-truth matches to verify)
        article_to_frame_ids: dict = defaultdict(set)
        for sid, fid in db.query(NarrativeFrameMention.source_item_id,
                                  NarrativeFrameMention.frame_id).all():
            article_to_frame_ids[sid].add(fid)

        candidate_ids = list(article_to_frame_ids.keys())
        sample_ids = random.sample(candidate_ids, min(sample_size, len(candidate_ids)))

        frames = (db.query(NarrativeFrame)
                  .filter(NarrativeFrame.active == True)  # noqa: E712
                  .all())
        n_total_frames = len(frames)

        kept_match_count = 0
        lost_match_count = 0
        articles_with_shortlist = 0
        articles_skipped = 0
        shortlist_sizes: list = []
        lost_examples: list = []

        for art_id in sample_ids:
            art = db.get(SourceItem, art_id)
            if not art:
                continue
            known_frame_ids = article_to_frame_ids[art_id]
            shortlist = _shortlist_frames_for_article(art, frames)
            shortlist_ids = {f.id for f in shortlist}
            if shortlist_ids:
                articles_with_shortlist += 1
                shortlist_sizes.append(len(shortlist_ids))
            else:
                articles_skipped += 1
            kept = known_frame_ids & shortlist_ids
            lost = known_frame_ids - shortlist_ids
            kept_match_count += len(kept)
            lost_match_count += len(lost)
            for lid in lost:
                lost_frame = next((f for f in frames if f.id == lid), None)
                lost_examples.append({
                    "article_id": art_id,
                    "article_title": (art.title or "")[:80],
                    "lost_frame_id": lid,
                    "lost_frame_name": lost_frame.name if lost_frame else "?",
                })

        n_articles = len(sample_ids)
        avg_short = (sum(shortlist_sizes) / len(shortlist_sizes)
                     if shortlist_sizes else 0)
        total_known = kept_match_count + lost_match_count

        print(f"sample: {n_articles} articles, {n_total_frames} total frames")
        print(f"\nshortlist sizes: mean={avg_short:.1f}, "
              f"articles fully skipped: {articles_skipped}/{n_articles}")
        print(f"\nknown NFMs retained in shortlist: {kept_match_count}/{total_known} "
              f"({100*kept_match_count/total_known:.1f}%)")
        print(f"known NFMs lost (gate too tight): {lost_match_count}")

        if lost_examples:
            print(f"\n=== Lost matches (first 10) ===")
            for r in lost_examples[:10]:
                print(f"  article {r['article_id']}: {r['article_title']!r}")
                print(f"    lost frame {r['lost_frame_id']}: {r['lost_frame_name']}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
