"""Post-backfill validation: sample LLM classifications and print for manual inspection.

USAGE:
    cd backend && .venv/bin/python scripts/perspective_validate.py [N]

N defaults to 50. Costs $0 (no LLM calls — just inspects existing DB rows).
"""
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.models import SourceItem


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    db = SessionLocal()

    # Overall distribution
    print("=" * 80)
    print("FINAL PERSPECTIVE DISTRIBUTION (race-relevant articles)")
    print("=" * 80)
    items = (
        db.query(SourceItem)
        .filter(SourceItem.archived_as_irrelevant == False)  # noqa: E712
        .filter(SourceItem.race_relevance_score >= 50)
        .all()
    )
    total = len(items)
    by_persp = Counter(it.perspective for it in items)
    by_method = Counter(it.perspective_method for it in items)
    by_conf = Counter(it.perspective_confidence for it in items)

    print(f"Total race-relevant: {total}")
    print()
    print("Perspective:")
    for k in ("pro_candidate", "pro_opponent", "neutral", None):
        c = by_persp.get(k, 0)
        print(f"  {k!r:18s} {c:5d} ({100*c/max(total,1):5.1f}%)")
    print()
    print("Method:")
    for k, c in by_method.most_common():
        print(f"  {k!r:18s} {c:5d} ({100*c/max(total,1):5.1f}%)")
    print()
    print("Confidence:")
    for k in ("high", "medium", "low", None):
        c = by_conf.get(k, 0)
        print(f"  {k!r:18s} {c:5d} ({100*c/max(total,1):5.1f}%)")

    # Sample LLM-classified items for manual inspection
    llm_items = [it for it in items if it.perspective_method == "llm"]
    print()
    print("=" * 80)
    print(f"RANDOM SAMPLE OF {min(n, len(llm_items))} LLM-CLASSIFIED ARTICLES")
    print("(for manual inspection)")
    print("=" * 80)
    random.seed(99)
    sample = random.sample(llm_items, min(n, len(llm_items)))
    for it in sample:
        title = (it.title or "")[:80]
        summary = (it.summary or "")[:140]
        print(f"\n  [{it.id:5d}] {it.perspective:14s} | {it.source_name!r:40s}")
        print(f"          title:   {title!r}")
        print(f"          summary: {summary!r}")
        print(f"          reason:  {(it.perspective_reason or '')[:160]}")

    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
