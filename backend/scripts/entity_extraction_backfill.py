"""Entity-extraction backfill — Phase 3 of Feature A.

Loops over race-relevant articles, calls gpt-4o-mini with the LLM_SYSTEM_PROMPT
defined in app.services.entity_extraction, parses the JSON response into an
ExtractionResult, canonicalizes the entities (matching against the seeded
canonical entities and existing auto-discovered ones), and persists mentions
+ relations to the DB.

USAGE:
    cd backend && .venv/bin/python scripts/entity_extraction_backfill.py [--limit N]

  --limit N  : only process the N highest-relevance articles (default 50)
  --limit 0  : process all race-relevant articles (full backfill)

Cost: ~$0.0001/article via gpt-4o-mini. Sample 50 = ~$0.005. Full ~2400 = ~$0.30.
Runtime: ~0.3s/article rate-limited. Sample = ~2 min. Full = ~15 min.

Idempotent: re-running on the same article won't duplicate mentions; weights
on relations increment instead of creating duplicates. Safe to re-run.
"""
import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.models import Entity, EntityMention, EntityRelation, SourceItem
from app.services.entity_extraction import (
    EXCERPT_CHARS,
    EXTRACTOR_VERSION,
    LLM_SYSTEM_PROMPT,
    ExtractedClaim,
    ExtractedEntity,
    ExtractedRelation,
    ExtractionResult,
    SUMMARY_CHARS,
    TITLE_CHARS,
    persist_claims,
    persist_extraction,
)
from app.services.llm_provider import OpenAIProvider, _parse_json_response


def _parse_result_lenient(parsed: dict) -> tuple[ExtractionResult, dict]:
    """Build an ExtractionResult from the LLM's raw dict, dropping individual
    bad entities/claims/relations rather than failing the whole article.

    v15.0 — parses both `claims` (new quote-anchored shape) and `relations`
    (legacy triple shape). The v15.0 prompt produces claims; relations is
    preserved for replaying older extraction logs through the same parser.

    Returns (result, dropped_counts) with keys 'entities_dropped',
    'relations_dropped', and 'claims_dropped'.
    """
    from pydantic import ValidationError

    dropped = {"entities_dropped": 0, "relations_dropped": 0, "claims_dropped": 0}

    entities_in = parsed.get("entities") or []
    relations_in = parsed.get("relations") or []
    claims_in = parsed.get("claims") or []

    entities_out: list[ExtractedEntity] = []
    for e_dict in entities_in:
        try:
            entities_out.append(ExtractedEntity(**e_dict))
        except (ValidationError, TypeError):
            dropped["entities_dropped"] += 1
            continue

    relations_out: list[ExtractedRelation] = []
    for r_dict in relations_in:
        try:
            relations_out.append(ExtractedRelation(**r_dict))
        except (ValidationError, TypeError):
            dropped["relations_dropped"] += 1
            continue

    claims_out: list[ExtractedClaim] = []
    for c_dict in claims_in:
        try:
            claims_out.append(ExtractedClaim(**c_dict))
        except (ValidationError, TypeError):
            dropped["claims_dropped"] += 1
            continue

    return (
        ExtractionResult(
            entities=entities_out,
            claims=claims_out,
            relations=relations_out,
        ),
        dropped,
    )


def run(
    limit: int = 50,
    rewrite: bool = False,
    worker_id: int = 0,
    num_workers: int = 1,
    skip_existing: bool = False,
) -> dict:
    """Extract entities + claim_records from race-relevant articles.

    Parallel-safe via worker partitioning. When `num_workers > 1`, this
    process only handles articles where `article.id % num_workers ==
    worker_id`, so multiple invocations with different worker_ids
    process disjoint sets and never write to the same article.

    The DB is SQLite WAL, which handles concurrent writes via brief
    busy-waits. Cross-worker entity/claim_record race conditions are
    handled at the persist layer (IntegrityError → re-query existing).
    """
    db = SessionLocal()

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        print("OPENAI_API_KEY not set — cannot run extraction.")
        return {}

    provider = OpenAIProvider(api_key=api_key, model="gpt-4o-mini")

    # Pick articles: race-relevant, not archived, ordered by relevance desc.
    # In sample mode we want the TOP-N most relevant — those will exercise
    # the most entities/relations and give the best quality check.
    q = (
        db.query(SourceItem)
        .filter(SourceItem.archived_as_irrelevant == False)  # noqa: E712
        .filter(SourceItem.race_relevance_score >= 50)
        .order_by(SourceItem.race_relevance_score.desc())
    )
    # Worker partitioning: each worker takes id % num_workers == worker_id.
    # This is deterministic, no coordination needed, and keeps each article
    # owned by exactly one worker. We use modulo on the SourceItem.id (an
    # integer primary key) rather than racing for un-claimed articles.
    if num_workers > 1:
        from sqlalchemy import func as _sa_func
        q = q.filter(_sa_func.mod(SourceItem.id, num_workers) == worker_id)
    if skip_existing:
        from app.models import ClaimRecord
        already = db.query(ClaimRecord.article_id).distinct()
        q = q.filter(~SourceItem.id.in_(already))
    if limit > 0:
        q = q.limit(limit)
    articles = q.all()
    worker_tag = f"w{worker_id}/{num_workers}" if num_workers > 1 else ""
    print(f"[{worker_tag}] Extracting from {len(articles)} articles (limit={limit if limit else 'ALL'})\n")

    # Counters
    stats = Counter()
    failures: list[tuple[int, str]] = []
    method_counts: Counter = Counter()  # how entities resolved

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
            # Lenient parsing: drop bad entities/relations individually instead
            # of failing the whole article when one item is malformed.
            result, dropped = _parse_result_lenient(parsed)
            stats["entities_dropped"] += dropped["entities_dropped"]
            stats["relations_dropped"] += dropped["relations_dropped"]
        except Exception as exc:
            failures.append((it.id, str(exc)[:200]))
            continue

        # Persist. v15.0+ writes quote-anchored claim records; older versions
        # write triple-shaped relations/claims. Detect by EXTRACTOR_VERSION
        # so this same script keeps working if we ever need to replay a
        # legacy extraction. `rewrite` is a no-op in v15.0 mode (claim_records
        # dedup on hash; no per-article "contribution" concept to subtract).
        try:
            if EXTRACTOR_VERSION.startswith("v15"):
                res = persist_claims(db, it.id, result)
                stats["mentions_created"] += res.get("mentions_created", 0)
                stats["claims_created"] = stats.get("claims_created", 0) + res.get("claims_created", 0)
                stats["claims_skipped_duplicate_hash"] = stats.get("claims_skipped_duplicate_hash", 0) + res.get("claims_skipped_duplicate_hash", 0)
                stats["claims_rejected_non_verbatim"] = stats.get("claims_rejected_non_verbatim", 0) + res.get("claims_rejected_non_verbatim", 0)
                stats["claims_rejected_no_entities_in_span"] = stats.get("claims_rejected_no_entities_in_span", 0) + res.get("claims_rejected_no_entities_in_span", 0)
                stats["claims_rejected_unresolved_entity"] = stats.get("claims_rejected_unresolved_entity", 0) + res.get("claims_rejected_unresolved_entity", 0)
                stats["entities_rejected_event_type_retired"] = stats.get("entities_rejected_event_type_retired", 0) + res.get("entities_rejected_event_type_retired", 0)
            else:
                res = persist_extraction(db, it.id, result, rewrite=rewrite)
                stats["mentions_created"] += res.get("mentions_created", 0)
                stats["relations_created"] += res.get("relations_created", 0)
                stats["relations_strengthened"] += res.get("relations_strengthened", 0)
            stats["articles_done"] += 1
        except Exception as exc:
            db.rollback()
            failures.append((it.id, f"persist: {str(exc)[:200]}"))
            continue

        if i % 10 == 0 or i == len(articles):
            elapsed = time.time() - start
            rate = i / max(elapsed, 0.001)
            eta = (len(articles) - i) / max(rate, 0.001)
            if EXTRACTOR_VERSION.startswith("v15"):
                print(
                    f"  [{i:4d}/{len(articles)}] {rate:.1f}/s ETA {eta/60:.1f}m "
                    f"| {stats['mentions_created']} mentions, "
                    f"{stats.get('claims_created', 0)} claims, "
                    f"{stats.get('claims_skipped_duplicate_hash', 0)} dup, "
                    f"{stats.get('claims_rejected_non_verbatim', 0)+stats.get('claims_rejected_no_entities_in_span', 0)+stats.get('claims_rejected_unresolved_entity', 0)} rejected, "
                    f"{len(failures)} failed"
                )
            else:
                print(
                    f"  [{i:4d}/{len(articles)}] {rate:.1f}/s ETA {eta/60:.1f}m "
                    f"| {stats['mentions_created']} mentions, "
                    f"{stats['relations_created']} new rels, "
                    f"{stats['relations_strengthened']} strengthened, "
                    f"{len(failures)} failed"
                )

    elapsed = time.time() - start
    print()
    print("=" * 80)
    print(f"Processed {stats['articles_done']} articles in {elapsed/60:.1f} min  (extractor {EXTRACTOR_VERSION})")
    print(f"  Mentions created:        {stats['mentions_created']}")
    if EXTRACTOR_VERSION.startswith("v15"):
        print(f"  Claim records created:   {stats.get('claims_created', 0)}")
        print(f"  Duplicate (skipped):     {stats.get('claims_skipped_duplicate_hash', 0)}")
        print(f"  Rejected non-verbatim:   {stats.get('claims_rejected_non_verbatim', 0)}")
        print(f"  Rejected entity-not-in-span: {stats.get('claims_rejected_no_entities_in_span', 0)}")
        print(f"  Rejected unresolved-entity:  {stats.get('claims_rejected_unresolved_entity', 0)}")
        print(f"  Event entities rejected: {stats.get('entities_rejected_event_type_retired', 0)}")
    else:
        print(f"  New relations:           {stats['relations_created']}")
        print(f"  Relations strengthened:  {stats['relations_strengthened']}")
    print(f"  Extraction failures:     {len(failures)}")

    # Recap of the entity landscape
    print()
    print("ENTITY LANDSCAPE (post-extraction):")
    type_counts = (
        db.query(Entity.type, Entity.seeded)
        .all()
    )
    by_type = Counter()
    by_type_seeded = Counter()
    for t, seeded in type_counts:
        by_type[t] += 1
        if seeded:
            by_type_seeded[t] += 1
    for t in ['person', 'organization', 'bill', 'location']:
        total = by_type[t]
        seeded_n = by_type_seeded[t]
        auto = total - seeded_n
        print(f"  {t:14s} {total:>4d} total   ({seeded_n} seeded + {auto} discovered)")
    print()

    # Top mentioned entities
    print("TOP 15 ENTITIES BY MENTION COUNT:")
    top = (
        db.query(Entity)
        .order_by(Entity.mention_count.desc())
        .limit(15)
        .all()
    )
    for e in top:
        flag = "[seed]" if e.seeded else "[auto]"
        print(f"  {flag} {e.type:12s} {e.name[:40]:40s} {e.mention_count} mentions")

    # Top relations
    print()
    print("TOP 15 RELATIONS BY WEIGHT:")
    top_rels = (
        db.query(EntityRelation)
        .order_by(EntityRelation.weight.desc())
        .limit(15)
        .all()
    )
    for r in top_rels:
        subj = db.query(Entity).get(r.subject_id)
        obj = db.query(Entity).get(r.object_id)
        if subj and obj:
            print(f"  {subj.name[:30]:30s} → {r.predicate:15s} → {obj.name[:30]:30s}  (weight={r.weight})")

    if failures:
        print()
        print(f"FAILURES (sample of {min(10, len(failures))}):")
        for aid, err in failures[:10]:
            print(f"  article {aid}: {err}")

    db.close()
    return dict(stats)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50,
                        help="N articles to process (default 50; 0 = all).")
    parser.add_argument("--rewrite", action="store_true",
                        help="Re-extract: clear each article's prior contribution "
                             "(decrement relation weights, drop mentions) before "
                             "writing the new extraction. Use when re-running at "
                             "a new EXTRACTOR_VERSION on articles already extracted.")
    parser.add_argument("--worker-id", type=int, default=0,
                        help="Worker ID (0-indexed). With --num-workers N, this "
                             "process handles articles where (id %% N == worker_id). "
                             "Use for parallel runs — kick off N processes with "
                             "--worker-id 0, 1, ..., N-1.")
    parser.add_argument("--num-workers", type=int, default=1,
                        help="Total worker count. Default 1 (no parallelism). "
                             "SQLite WAL handles concurrent writes; safe to set "
                             "up to ~8 workers without hitting lock contention.")
    parser.add_argument("--skip-existing", action="store_true",
                        help="Skip articles that already have at least one ClaimRecord. "
                             "Use this for incremental backfills — avoids re-paying "
                             "the LLM cost on already-extracted articles.")
    args = parser.parse_args()
    if not (0 <= args.worker_id < args.num_workers):
        parser.error(f"--worker-id must be in [0, --num-workers) — got {args.worker_id} of {args.num_workers}")
    run(limit=args.limit, rewrite=args.rewrite,
        worker_id=args.worker_id, num_workers=args.num_workers,
        skip_existing=args.skip_existing)
