"""Targeted re-extraction under the v14.3 tightened prompt.

After the partisan guard ran in v14.2, 4 cross-party `endorses` relations
got reclassified to `co_sponsored`. The underlying articles still exist
with extractions produced under the old loose `endorses` definition. This
script re-extracts those articles (and any others with relations now
flagged for review) using the new tighter prompt, in `rewrite` mode so the
prior article-level contribution is dropped before the new one is written.

Cost: ~$0.0001 per article. Usually under 200 articles total — under $0.02.

USAGE:
    .venv/bin/python scripts/entity_targeted_reextract.py [--dry-run] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.models import Entity, EntityRelation, SourceItem
from app.services.entity_extraction import (
    EXCERPT_CHARS, LLM_SYSTEM_PROMPT, SUMMARY_CHARS, TITLE_CHARS, persist_extraction,
)
from app.services.llm_provider import OpenAIProvider, _parse_json_response
from scripts.entity_extraction_backfill import _parse_result_lenient


# Hard-coded list of (subject, object) target pairs whose articles we re-extract.
# These are the cross-party relations the partisan guard reclassified — the
# articles that produced them were extracted under the v14.1 loose prompt and
# should produce cleaner output under v14.3.
TARGET_PAIRS = [
    ("person:bresnahan", "bill:aca-subsidies"),
    ("person:auto:brian-fitzpatrick", "bill:aca-subsidies"),
    ("person:auto:mike-lawler", "bill:aca-subsidies"),
    ("person:auto:ryan-mackenzie", "bill:aca-subsidies"),
]


def find_target_articles(db) -> list[int]:
    """Article IDs supporting the target relations, deduplicated."""
    article_ids: set[int] = set()
    for s_can, o_can in TARGET_PAIRS:
        subj = db.query(Entity).filter(Entity.canonical_id == s_can).one_or_none()
        obj = db.query(Entity).filter(Entity.canonical_id == o_can).one_or_none()
        if not subj or not obj:
            continue
        rels = (
            db.query(EntityRelation)
            .filter(EntityRelation.subject_id == subj.id, EntityRelation.object_id == obj.id)
            .all()
        )
        for r in rels:
            try:
                ids = json.loads(r.source_articles or "[]")
            except Exception:
                ids = []
            article_ids.update(ids)
    return sorted(article_ids)


def run(dry_run: bool = False, limit: int = 0) -> dict:
    db = SessionLocal()
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        print("OPENAI_API_KEY not set")
        return {}

    provider = OpenAIProvider(api_key=api_key, model="gpt-4o-mini")
    article_ids = find_target_articles(db)
    if limit > 0:
        article_ids = article_ids[:limit]
    print(f"Re-extracting {len(article_ids)} articles under EXTRACTOR_VERSION=v14.3")

    if dry_run:
        for aid in article_ids[:20]:
            it = db.query(SourceItem).filter(SourceItem.id == aid).first()
            if it:
                print(f"  article {aid}: {(it.title or '')[:90]}")
        if len(article_ids) > 20:
            print(f"  ... and {len(article_ids) - 20} more")
        return {"dry_run": len(article_ids)}

    stats: Counter = Counter()
    start = time.time()
    failures: list[tuple[int, str]] = []

    for i, aid in enumerate(article_ids, 1):
        it = db.query(SourceItem).filter(SourceItem.id == aid).first()
        if not it:
            continue
        title = (it.title or "")[:TITLE_CHARS]
        summary = (it.summary or "")[:SUMMARY_CHARS]
        excerpt = (it.raw_text or "")[:EXCERPT_CHARS]
        user_prompt = (
            f"Article title: {title}\n"
            f"Summary: {summary}\n"
            f"Excerpt: {excerpt}\n\n"
            "Extract entities and relations. Output JSON only."
        )

        try:
            raw = provider._chat(
                user_prompt=user_prompt,
                system_prompt=LLM_SYSTEM_PROMPT,
                json_mode=True,
                temperature=0,
                seed=42,
            )
            parsed = _parse_json_response(raw) or {}
            result, dropped = _parse_result_lenient(parsed)
            stats["entities_dropped"] += dropped["entities_dropped"]
            stats["relations_dropped"] += dropped["relations_dropped"]
        except Exception as exc:
            failures.append((aid, str(exc)[:200]))
            continue

        try:
            res = persist_extraction(db, aid, result, rewrite=True)
            for k, v in res.items():
                stats[k] += v
            db.commit()
            stats["articles_done"] += 1
        except Exception as exc:
            db.rollback()
            failures.append((aid, f"persist: {str(exc)[:200]}"))
            continue

        if i % 10 == 0 or i == len(article_ids):
            elapsed = time.time() - start
            rate = i / max(elapsed, 0.001)
            print(f"  [{i:3d}/{len(article_ids)}] rate={rate:.1f}/s | done={stats['articles_done']} failed={len(failures)}")

    elapsed = time.time() - start
    print()
    print(f"Processed {stats['articles_done']}/{len(article_ids)} in {elapsed/60:.1f} min")
    for k, v in sorted(stats.items()):
        print(f"  {k:30s} {v}")
    if failures[:5]:
        print("\nSAMPLE FAILURES:")
        for aid, err in failures[:5]:
            print(f"  article {aid}: {err}")

    db.close()
    return dict(stats)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    run(dry_run=args.dry_run, limit=args.limit)


if __name__ == "__main__":
    main()
