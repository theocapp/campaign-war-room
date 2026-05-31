"""Validate (and optionally apply) the hallucination-cap fix in
campaign_analysis._compute_relevance_score.

The new cap-bypass logic accepts three signals (any one keeps the article
above the floor):

  1. candidate/opponent NAME in title + raw_text (uncontaminated — ignores
     summary, which is LLM-generated and known to paste stretch boilerplate
     onto unrelated articles)
  2. district code (e.g. "PA-08") in title + raw_text
  3. source-attribution: source_name structurally references a candidate
     (their account, YouTube channel, Google News query) — requires both
     a surname and a recognized feed marker

geography_keywords (Scranton, NEPA, Luzerne, …) are deliberately NOT used
here — too broad for a post-LLM hallucination check.

This script:
  1. Pulls every article with race_relevance_score == 40 and
     archived_as_irrelevant=False — the precise cap-capped set.
  2. Computes the "uncap" score in pure Python using the new logic.
     Assumes verdict="relevant" (base 50) — a conservative floor (cap-capped
     "critical"-verdict articles will under-score; far fewer than "relevant").
  3. Prints a before/after table.
  4. With --apply, UPDATEs the rows that move ≥50 with the new score.

Usage:
    cd backend && .venv/bin/python -m scripts.validate_cap_fix          # dry run
    cd backend && .venv/bin/python -m scripts.validate_cap_fix --apply  # writes
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func

from app.db import SessionLocal
from app.models import ClaimRecord, SourceItem
from app.services.campaign_analysis import (
    _build_context,
    _count_name_mentions_uncontaminated,
    _has_district_mention,
    _source_attributed_to_candidate,
)


def _compute_new_score(
    item: SourceItem,
    ctx: dict,
    claim_counts: dict[str, int],
) -> tuple[int, bool, list[str]]:
    """Recompute the score under the new cap logic.

    Returns (new_score, cap_would_fire, signals).

    Assumes verdict="relevant" — base 50. For cap-capped articles the
    assumption is almost always correct because "relevant" is far more
    common than "critical"; under-scoring a few "critical" articles is
    safer than over-scoring.
    """
    title_hits, body_hits = _count_name_mentions_uncontaminated(item, ctx)

    title_bonus = min(20, title_hits * 12)
    body_bonus = min(8, body_hits * 4)

    high = claim_counts.get("high", 0)
    med = claim_counts.get("medium", 0)
    claim_bonus = min(10, high * 3 + med * 1)

    cred_bonus = 3 if (item.source_credibility or "") == "high" else 0

    base = 50  # conservative floor
    score = base + title_bonus + body_bonus + claim_bonus + cred_bonus

    has_name = title_hits > 0 or body_hits > 0
    has_district = _has_district_mention(item, ctx)
    has_src_attr = _source_attributed_to_candidate(item, ctx)

    signals: list[str] = []
    if has_name:
        signals.append("name")
    if has_district:
        signals.append("district")
    if has_src_attr:
        signals.append("source-attribution")

    cap_fires = not (has_name or has_district or has_src_attr)
    if cap_fires:
        score = min(score, 40)

    return max(0, min(100, score)), cap_fires, signals


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the new scores for the uncapped articles to the DB. "
             "Default: dry-run, print only.",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        ctx = _build_context(db)

        items = (
            db.query(SourceItem)
            .filter(
                SourceItem.archived_as_irrelevant.is_(False),
                SourceItem.race_relevance_score == 40,
            )
            .order_by(SourceItem.id.desc())
            .all()
        )

        print(f"\nCampaign context: candidate={ctx.get('candidate')!r}, "
              f"opponents={ctx.get('opponents')}, "
              f"district={ctx.get('district')!r}")
        print(f"\nFound {len(items)} articles at score==40 "
              "(precise cap-target set).\n")

        claim_data: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        if items:
            rows = (
                db.query(
                    ClaimRecord.article_id,
                    ClaimRecord.confidence,
                    func.count().label("cnt"),
                )
                .filter(ClaimRecord.article_id.in_([i.id for i in items]))
                .group_by(ClaimRecord.article_id, ClaimRecord.confidence)
                .all()
            )
            for r in rows:
                claim_data[r.article_id][r.confidence] = r.cnt

        print(
            f"{'ID':>6}  {'OLD':>4}  {'NEW':>4}  {'DELTA':>5}  {'CAP?':>5}  "
            f"{'SIGNALS':<28}  {'SOURCE':<30}  TITLE"
        )
        print("-" * 200)

        n_uncapped = 0
        n_still_capped = 0
        to_update: list[tuple[int, int]] = []

        for item in items:
            new_score, cap_fires, signals = _compute_new_score(
                item, ctx, claim_data.get(item.id, {})
            )
            old = item.race_relevance_score
            delta = new_score - old

            if new_score >= 50 and old < 50:
                n_uncapped += 1
                to_update.append((item.id, new_score))
            elif cap_fires:
                n_still_capped += 1

            cap_str = "yes" if cap_fires else ""
            signals_str = ",".join(signals) if signals else "—"
            src_str = (item.source_name or "")[:30]
            title_str = (item.title or "")[:110]
            print(
                f"{item.id:>6}  {old:>4}  {new_score:>4}  {delta:>+5d}  "
                f"{cap_str:>5}  {signals_str:<28}  {src_str:<30}  {title_str}"
            )

        print(f"\n--- SUMMARY ---")
        print(f"Articles uncapped (now ≥50): {n_uncapped}")
        print(f"Articles still capped at 40:  {n_still_capped}")

        if args.apply:
            if not to_update:
                print("\nNothing to write — no articles would move ≥50.")
                return
            print(f"\n--- APPLYING {len(to_update)} updates ---")
            for item_id, new_score in to_update:
                db.query(SourceItem).filter(SourceItem.id == item_id).update(
                    {SourceItem.race_relevance_score: new_score}
                )
            db.commit()
            print(f"Committed. {len(to_update)} rows updated.")
        else:
            print("\n(dry run — pass --apply to write changes)")

    finally:
        db.close()


if __name__ == "__main__":
    main()
