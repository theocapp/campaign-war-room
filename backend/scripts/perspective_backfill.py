"""Backfill SourceItem.perspective for every race-relevant article.

Pipeline (cheapest → most expensive):
  1. Phase 0-2 (free):  existing labels, outlet bias, attribution.
                         Updates the row directly.
  2. Phase 3 (LLM):     gpt-4o-mini classifies whatever falls through.
                         Commits in batches of 25 so progress is durable.

USAGE:
    cd backend && .venv/bin/python scripts/perspective_backfill.py [--no-llm]

  --no-llm: only run Phase 0-2 (free, fast).  Useful to see what's
            classifiable for free before paying for the LLM phase.

Cost (with LLM): ~$0.0001 / article × ~1900 unclassified = ~$0.20.
Runtime: ~5-10 minutes (limited by OpenAI request rate).
"""
import argparse
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.models import CampaignConfig, Opponent, SourceItem
from app.services.article_perspective import (
    PerspectiveResult,
    classify_with_llm,
    get_classifier,
)


def _persist(item: SourceItem, r: PerspectiveResult) -> None:
    item.perspective = r.perspective
    item.perspective_method = r.method
    item.perspective_confidence = r.confidence
    item.perspective_reason = r.reason[:240]  # safety cap


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-llm", action="store_true", help="Skip Phase 3 LLM fallback")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N items (for testing)")
    args = parser.parse_args()

    db = SessionLocal()
    cfg = db.query(CampaignConfig).first()
    opp = db.query(Opponent).first()
    cand_name = cfg.candidate_name if cfg else ""
    cand_party = cfg.party if cfg else ""
    opp_name = opp.name if opp else ""
    opp_party = opp.party if opp else ""
    print(f"Race: {cand_name} ({cand_party}) vs {opp_name} ({opp_party})")
    if not cand_party or not opp_party:
        print("WARNING: candidate or opponent party missing — perspective mapping may be wrong")

    classify = get_classifier(db)

    q = (
        db.query(SourceItem)
        .filter(SourceItem.archived_as_irrelevant == False)  # noqa: E712
        .filter(SourceItem.race_relevance_score >= 50)
    )
    if args.limit:
        q = q.limit(args.limit)
    items = q.all()
    print(f"Total race-relevant items: {len(items)}")

    # ── Phase 1: cheap classifier (Phase 0-2) ─────────────────────────────
    cheap_results: list[tuple[SourceItem, PerspectiveResult]] = []
    fallback_items: list[SourceItem] = []
    for item in items:
        r = classify(item)
        if r.method == "fallback":
            fallback_items.append(item)
        else:
            cheap_results.append((item, r))

    print(f"\n=== Cheap phase done ===")
    print(f"  Classified by cheap phases: {len(cheap_results)}")
    print(f"  Falling through to LLM:     {len(fallback_items)}")

    # Persist cheap results immediately so we don't redo them if LLM phase fails.
    for item, r in cheap_results:
        _persist(item, r)
    db.commit()
    print(f"  Cheap results committed.")

    # ── Phase 2: LLM ──────────────────────────────────────────────────────
    if args.no_llm:
        print("\n--no-llm passed; skipping LLM phase.")
    else:
        print(f"\n=== LLM phase: {len(fallback_items)} items ===")
        BATCH = 25
        method_counts: Counter = Counter()
        persp_counts: Counter = Counter()
        # Construct provider once and reuse across all calls.
        import os
        try:
            from app.services.llm_provider import OpenAIProvider
            api_key = os.environ.get("OPENAI_API_KEY", "").strip()
            if not api_key:
                print("OPENAI_API_KEY not set — aborting LLM phase.")
                return 1
            provider = OpenAIProvider(api_key=api_key, model="gpt-4o-mini")
        except Exception as exc:
            print(f"Failed to init OpenAIProvider: {exc}")
            return 1

        start = time.time()
        for i, item in enumerate(fallback_items, 1):
            r = classify_with_llm(
                item, cand_name, cand_party, opp_name, opp_party,
                provider=provider,
            )
            method_counts[r.method] += 1
            persp_counts[r.perspective] += 1
            _persist(item, r)
            if i % BATCH == 0:
                db.commit()
                elapsed = time.time() - start
                rate = i / max(elapsed, 0.001)
                eta = (len(fallback_items) - i) / max(rate, 0.001)
                print(
                    f"  [{i:5d}/{len(fallback_items)}] "
                    f"{rate:.1f} items/sec, ETA {eta/60:.1f} min"
                )
        db.commit()

        print(f"\n  LLM done. Methods: {dict(method_counts)}")
        print(f"  Perspectives: {dict(persp_counts)}")

    # ── Final stats ───────────────────────────────────────────────────────
    total = (
        db.query(SourceItem)
        .filter(SourceItem.archived_as_irrelevant == False)  # noqa: E712
        .filter(SourceItem.race_relevance_score >= 50)
        .filter(SourceItem.perspective.isnot(None))
        .count()
    )
    print(f"\n=== Final: {total} items now have a perspective label ===")
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
