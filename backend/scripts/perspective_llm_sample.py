"""Run the LLM Phase 3 classifier on a SMALL random sample of fallback
articles so we can manually inspect quality before any full backfill.

USAGE:
    cd backend && .venv/bin/python scripts/perspective_llm_sample.py [N]

N defaults to 30. Estimated cost at gpt-4o-mini pricing:
    30 articles × ~$0.0001 = $0.003
"""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.models import CampaignConfig, Opponent, SourceItem
from app.services.article_perspective import (
    classify_with_llm,
    get_classifier,
)


def main() -> int:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    db = SessionLocal()

    cfg = db.query(CampaignConfig).first()
    opp = db.query(Opponent).first()
    cand_name = cfg.candidate_name if cfg else ""
    cand_party = cfg.party if cfg else ""
    opp_name = opp.name if opp else ""
    opp_party = opp.party if opp else ""

    print(f"Race: {cand_name} ({cand_party}) vs {opp_name} ({opp_party})")

    classify = get_classifier(db)

    # Find items that fall through to LLM
    items = (
        db.query(SourceItem)
        .filter(SourceItem.archived_as_irrelevant == False)  # noqa: E712
        .filter(SourceItem.race_relevance_score >= 50)
        .all()
    )
    fallback = [item for item in items if classify(item).method == "fallback"]
    print(f"Fallback pool: {len(fallback)} items. Sampling {n}…")

    random.seed(42)
    sample = random.sample(fallback, min(n, len(fallback)))

    results: list[tuple[SourceItem, object]] = []
    for i, item in enumerate(sample, 1):
        r = classify_with_llm(item, cand_name, cand_party, opp_name, opp_party)
        results.append((item, r))
        print(f"[{i:3d}/{len(sample)}] id={item.id} → {r.perspective:14s} ({r.reason[:80]})")

    print()
    print("=== Distribution ===")
    from collections import Counter
    c = Counter(r.perspective for _, r in results)
    for p, cnt in c.most_common():
        print(f"  {p:14s} {cnt}")

    print()
    print("=== Manual inspection output (for you to review) ===")
    for item, r in results:
        print(f"\n  [{item.id}] {item.title[:80]!r}")
        print(f"        source: {item.source_name!r}, url: {item.source_url!r}")
        print(f"        summary: {(item.summary or '')[:200]!r}")
        print(f"        → {r.perspective}  ({r.reason})")

    db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
