"""Dry-run the perspective classifier against all SourceItems.

USAGE:
    cd backend && .venv/bin/python scripts/perspective_dryrun.py

OUTPUTS:
  - stdout summary: method breakdown, confidence breakdown, % of articles
    classified at each phase
  - perspective_dryrun_results.csv: id, title, source_name, domain, method,
    confidence, perspective, reason — one row per article, sortable + filterable
    for manual inspection.

NO DB WRITES — purely diagnostic. Run this first, eyeball results, iterate
the classifier, then graduate to a backfill that persists results.
"""
import csv
import sys
from collections import Counter
from pathlib import Path

# Add backend root to path so we can import the app package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.models import SourceItem
from app.services.article_perspective import get_classifier, _extract_domain


def main() -> int:
    db = SessionLocal()
    classify = get_classifier(db)

    # Only score items that actually participate in narrative landscape:
    # archived items + irrelevant items don't render as dots, so they
    # don't need classification (would just be noise in our metrics).
    items = (
        db.query(SourceItem)
        .filter(SourceItem.archived_as_irrelevant == False)  # noqa: E712
        .filter(SourceItem.race_relevance_score >= 50)
        .all()
    )
    print(f"Classifying {len(items)} race-relevant articles…")

    method_counts: Counter = Counter()
    persp_counts: Counter = Counter()
    conf_counts: Counter = Counter()

    out_path = Path(__file__).parent / "perspective_dryrun_results.csv"
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "id", "title", "source_name", "domain",
            "source_owner_type",
            "method", "confidence", "perspective", "reason",
            "summary_excerpt",
        ])
        for item in items:
            r = classify(item)
            method_counts[r.method] += 1
            persp_counts[r.perspective] += 1
            conf_counts[r.confidence] += 1
            w.writerow([
                item.id,
                (item.title or "")[:140],
                item.source_name or "",
                _extract_domain(item.source_url) or "",
                item.source_owner_type or "",
                r.method, r.confidence, r.perspective,
                r.reason,
                (item.summary or "")[:200],
            ])

    print()
    print("=== Method breakdown ===")
    total = sum(method_counts.values())
    for m, c in method_counts.most_common():
        print(f"  {m:14s} {c:5d} ({100*c/total:5.1f}%)")

    print()
    print("=== Confidence breakdown ===")
    for cfd in ("high", "medium", "low"):
        c = conf_counts.get(cfd, 0)
        print(f"  {cfd:6s} {c:5d} ({100*c/max(total,1):5.1f}%)")

    print()
    print("=== Perspective breakdown ===")
    for p in ("pro_candidate", "pro_opponent", "neutral"):
        c = persp_counts.get(p, 0)
        print(f"  {p:14s} {c:5d} ({100*c/max(total,1):5.1f}%)")

    print()
    print(f"Wrote per-article rows → {out_path}")
    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
