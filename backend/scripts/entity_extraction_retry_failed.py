"""Retry race-relevant articles that have no entity_mentions yet.

After the initial backfill, 425 race-relevant articles were left without any
mentions: 275 hit Pydantic ValidationError on a single bad field (now handled
by the lenient parser in entity_extraction_backfill.py), 117 produced empty
extractions (re-running won't change those — same prompt, deterministic seed),
and 33 are new ingests since the backfill started.

Uses the same provider/seed/prompt as the original backfill so it's idempotent:
articles that already have mentions are skipped via the entity_mentions join.

USAGE:
    cd backend && .venv/bin/python scripts/entity_extraction_retry_failed.py [--limit N]
"""
import argparse
import os
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.models import EntityMention, SourceItem
from app.services.entity_extraction import (
    EXCERPT_CHARS, LLM_SYSTEM_PROMPT, SUMMARY_CHARS, TITLE_CHARS, persist_extraction,
)
from app.services.llm_provider import OpenAIProvider, _parse_json_response
from scripts.entity_extraction_backfill import _parse_result_lenient


def find_unprocessed(db, limit: int = 0):
    """Race-relevant articles with no entity_mentions row."""
    subq = db.query(EntityMention.article_id).distinct().subquery()
    q = (
        db.query(SourceItem)
        .filter(SourceItem.archived_as_irrelevant == False)  # noqa: E712
        .filter(SourceItem.race_relevance_score >= 50)
        .filter(SourceItem.id.notin_(subq))
        .order_by(SourceItem.race_relevance_score.desc())
    )
    if limit > 0:
        q = q.limit(limit)
    return q.all()


def run(limit: int = 0) -> dict:
    db = SessionLocal()
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        print("OPENAI_API_KEY not set")
        return {}

    provider = OpenAIProvider(api_key=api_key, model="gpt-4o-mini")
    articles = find_unprocessed(db, limit=limit)
    print(f"Retrying {len(articles)} unprocessed articles\n", flush=True)

    stats: Counter = Counter()
    failures: list[tuple[int, str]] = []
    start = time.time()

    for i, it in enumerate(articles, 1):
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
            failures.append((it.id, str(exc)[:200]))
            continue

        try:
            res = persist_extraction(db, it.id, result)
            stats["mentions_created"] += res["mentions_created"]
            stats["relations_created"] += res["relations_created"]
            stats["relations_strengthened"] += res["relations_strengthened"]
            stats["articles_done"] += 1
            if res["mentions_created"] == 0:
                stats["articles_zero_entities"] += 1
        except Exception as exc:
            db.rollback()
            failures.append((it.id, f"persist: {str(exc)[:200]}"))
            continue

        if i % 10 == 0 or i == len(articles):
            elapsed = time.time() - start
            rate = i / max(elapsed, 0.001)
            eta = (len(articles) - i) / max(rate, 0.001)
            print(
                f"  [{i:4d}/{len(articles)}] {rate:.1f}/s ETA {eta/60:.1f}m "
                f"| {stats['articles_done']} done, "
                f"{stats['mentions_created']} new mentions, "
                f"{stats['articles_zero_entities']} empty, "
                f"{len(failures)} failed",
                flush=True,
            )

    elapsed = time.time() - start
    print()
    print("=" * 70)
    print(f"Processed {stats['articles_done']}/{len(articles)} articles in {elapsed/60:.1f} min")
    print(f"  New mentions:               {stats['mentions_created']}")
    print(f"  New relations:              {stats['relations_created']}")
    print(f"  Relations strengthened:     {stats['relations_strengthened']}")
    print(f"  Articles with 0 entities:   {stats['articles_zero_entities']} (re-runs of empty extractions)")
    print(f"  Entities dropped (lenient): {stats['entities_dropped']}")
    print(f"  Relations dropped (lenient):{stats['relations_dropped']}")
    print(f"  Persistent failures:        {len(failures)}")

    if failures:
        print()
        print(f"SAMPLE FAILURES (first 10):")
        for aid, err in failures[:10]:
            print(f"  article {aid}: {err}")

    db.close()
    return dict(stats)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0,
                        help="N articles to retry (default 0 = all unprocessed).")
    args = parser.parse_args()
    run(limit=args.limit)
