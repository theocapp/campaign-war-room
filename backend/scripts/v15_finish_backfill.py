"""Finish the v15.0 extraction backfill on articles not yet covered.

The main `entity_extraction_backfill.py` re-extracts everything by default; this
one-off filters to race-relevant articles whose id is NOT yet present in
claim_records and runs the same extraction pipeline on just those.

Usage:
    cd backend && .venv/bin/python scripts/v15_finish_backfill.py
"""
from __future__ import annotations

import os
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from app.db import SessionLocal
from app.models import SourceItem
from app.services.entity_extraction import (
    EXCERPT_CHARS,
    EXTRACTOR_VERSION,
    LLM_SYSTEM_PROMPT,
    SUMMARY_CHARS,
    TITLE_CHARS,
    persist_claims,
)
from app.services.llm_provider import OpenAIProvider, _parse_json_response
from scripts.entity_extraction_backfill import _parse_result_lenient


def main():
    assert EXTRACTOR_VERSION.startswith("v15"), \
        f"Expected v15.x extractor; got {EXTRACTOR_VERSION}"

    db = SessionLocal()
    covered = {aid for (aid,) in db.execute(
        text("SELECT DISTINCT article_id FROM claim_records")
    ).fetchall()}
    articles = (
        db.query(SourceItem)
        .filter(SourceItem.archived_as_irrelevant == False)  # noqa: E712
        .filter(SourceItem.race_relevance_score >= 50)
        .filter(~SourceItem.id.in_(covered) if covered else True)
        .order_by(SourceItem.race_relevance_score.desc())
        .all()
    )
    print(f"Extractor: {EXTRACTOR_VERSION}")
    print(f"Already covered: {len(covered)} articles")
    print(f"Remaining to extract: {len(articles)} articles\n")
    if not articles:
        print("Nothing to do.")
        return

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        print("OPENAI_API_KEY not set — cannot run extraction.")
        sys.exit(1)
    provider = OpenAIProvider(api_key=api_key, model="gpt-4o-mini")
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
        except Exception as exc:
            failures.append((it.id, str(exc)[:200]))
            continue

        try:
            res = persist_claims(db, it.id, result)
            stats["mentions_created"] += res.get("mentions_created", 0)
            stats["claims_created"] += res.get("claims_created", 0)
            stats["claims_skipped_duplicate_hash"] += res.get("claims_skipped_duplicate_hash", 0)
            stats["claims_rejected_non_verbatim"] += res.get("claims_rejected_non_verbatim", 0)
            stats["claims_rejected_no_entities_in_span"] += res.get("claims_rejected_no_entities_in_span", 0)
            stats["claims_rejected_unresolved_entity"] += res.get("claims_rejected_unresolved_entity", 0)
            stats["articles_done"] += 1
        except Exception as exc:
            db.rollback()
            failures.append((it.id, f"persist: {str(exc)[:200]}"))
            continue

        if i % 25 == 0 or i == len(articles):
            elapsed = time.time() - start
            rate = i / max(elapsed, 0.001)
            eta = (len(articles) - i) / max(rate, 0.001)
            print(
                f"  [{i:4d}/{len(articles)}] {rate:.2f}/s ETA {eta/60:.1f}m "
                f"| {stats['mentions_created']} mentions, "
                f"{stats['claims_created']} claims, "
                f"{stats['claims_skipped_duplicate_hash']} dup, "
                f"{stats['claims_rejected_non_verbatim']+stats['claims_rejected_no_entities_in_span']+stats['claims_rejected_unresolved_entity']} rejected, "
                f"{len(failures)} failed",
                flush=True,
            )

    elapsed = time.time() - start
    print()
    print("=" * 80)
    print(f"Processed {stats['articles_done']} articles in {elapsed/60:.1f} min ({EXTRACTOR_VERSION})")
    print(f"  Mentions created:           {stats['mentions_created']}")
    print(f"  Claim records created:      {stats['claims_created']}")
    print(f"  Duplicate (skipped):        {stats['claims_skipped_duplicate_hash']}")
    print(f"  Rejected non-verbatim:      {stats['claims_rejected_non_verbatim']}")
    print(f"  Rejected entity-not-in-span:{stats['claims_rejected_no_entities_in_span']}")
    print(f"  Rejected unresolved-entity: {stats['claims_rejected_unresolved_entity']}")
    print(f"  Extraction failures:        {len(failures)}")
    if failures[:5]:
        print("\nFirst 5 failures:")
        for aid, err in failures[:5]:
            print(f"  article {aid}: {err}")


if __name__ == "__main__":
    main()
