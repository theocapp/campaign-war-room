"""Generalized re-extraction targeting articles with stale evidence.

Uses the same machinery as entity_targeted_reextract.py but instead of a
hardcoded list of target pairs, asks the drift API for articles whose
supporting relations have evidence from any extractor_version other than
the current one. Re-extracts them in rewrite mode so the prior article-level
contribution is replaced by fresh extraction under the current prompt.

USAGE:
    .venv/bin/python scripts/entity_drift_reextract.py                  # dry-run, lists target articles
    .venv/bin/python scripts/entity_drift_reextract.py --apply           # re-extract, up to default limit
    .venv/bin/python scripts/entity_drift_reextract.py --apply --limit 100
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
from app.models import EntityRelation, SourceItem
from app.services.entity_extraction import (
    EXCERPT_CHARS, EXTRACTOR_VERSION, LLM_SYSTEM_PROMPT,
    SUMMARY_CHARS, TITLE_CHARS, persist_extraction,
)
from app.services.extractor_versions import current as current_version
from app.services.llm_provider import OpenAIProvider, _parse_json_response
from scripts.entity_extraction_backfill import _parse_result_lenient


def find_stale_articles(db, limit: int) -> list[int]:
    """Article IDs supporting relations with stale evidence, ordered by
    aggregate stale-relation weight (highest impact first)."""
    cur = current_version().version
    rel_articles: dict[int, set[int]] = {}
    stale_rel_ids: set[int] = set()
    rel_weights: dict[int, int] = {}

    for r in db.query(EntityRelation).all():
        rel_weights[r.id] = r.weight or 0
        try:
            evidence = json.loads(r.evidence_json or "[]")
        except Exception:
            evidence = []
        versions = {ev.get("extractor_version") for ev in evidence}
        if cur in versions:
            continue  # at least one piece of evidence is fresh — skip
        stale_rel_ids.add(r.id)
        article_ids: set[int] = set()
        for ev in evidence:
            aid = ev.get("article_id")
            if aid is not None:
                article_ids.add(aid)
        if not article_ids:
            # Fall back to source_articles
            try:
                aids = json.loads(r.source_articles or "[]")
            except Exception:
                aids = []
            article_ids.update(aids)
        rel_articles[r.id] = article_ids

    # Aggregate per-article: sum stale relation weight per article
    article_weight: Counter = Counter()
    for rel_id in stale_rel_ids:
        w = rel_weights.get(rel_id, 0)
        for aid in rel_articles.get(rel_id, ()):
            article_weight[aid] += w

    return [aid for aid, _ in article_weight.most_common(limit)]


def run(dry_run: bool, limit: int) -> dict:
    db = SessionLocal()
    cur = current_version()
    article_ids = find_stale_articles(db, limit if limit > 0 else 10_000)
    print(f"Current extractor: {cur.version}  ({cur.summary})")
    print(f"Targeting {len(article_ids)} articles with stale evidence "
          f"(limit={limit if limit > 0 else 'all'})\n")

    if not article_ids:
        print("No stale articles found — graph is fully fresh under the current version.")
        return {}

    if dry_run:
        for aid in article_ids[:25]:
            it = db.query(SourceItem).filter(SourceItem.id == aid).first()
            if it:
                print(f"  {aid}: {(it.title or '')[:90]}")
        if len(article_ids) > 25:
            print(f"  ... and {len(article_ids) - 25} more")
        return {"dry_run": len(article_ids)}

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        print("OPENAI_API_KEY not set — cannot re-extract.")
        return {}

    provider = OpenAIProvider(api_key=api_key, model="gpt-4o-mini")
    stats: Counter = Counter()
    failures: list[tuple[int, str]] = []
    start = time.time()

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
            eta = (len(article_ids) - i) / max(rate, 0.001) / 60
            print(f"  [{i:4}/{len(article_ids)}] rate={rate:.1f}/s ETA {eta:.0f}m "
                  f"| done={stats['articles_done']} failed={len(failures)}")

    elapsed = (time.time() - start) / 60
    print(f"\nProcessed {stats['articles_done']}/{len(article_ids)} in {elapsed:.1f} min")
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
    parser.add_argument("--apply", action="store_true", help="Actually re-extract")
    parser.add_argument("--limit", type=int, default=200,
                        help="Cap on number of articles to re-extract per run (default 200)")
    args = parser.parse_args()
    run(dry_run=not args.apply, limit=args.limit)


if __name__ == "__main__":
    main()
