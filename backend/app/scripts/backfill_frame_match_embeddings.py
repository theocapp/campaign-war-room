"""Backfill SourceItem.frame_match_embedding for race-relevant articles.

After the migration adds the cache columns, the rematch gate would
populate them lazily on first use. This script pre-populates them so
the first rematch is fast (no embedding latency).

Idempotent: skips articles that already have an embedding under the
current model. Re-run safely after a model change to re-embed.

Usage:
    cd backend && .venv/bin/python -m app.scripts.backfill_frame_match_embeddings
"""
from __future__ import annotations

import json
import time

from app.db import SessionLocal
from app.models import SourceItem
from app.services.embeddings import current_primary_model_name, embed_texts

BATCH_SIZE = 100
MIN_RELEVANCE = 50
ARTICLE_BODY_CHARS = 1500


def article_text(item: SourceItem) -> str:
    title = (item.title or "").strip()
    body = (item.raw_text or item.summary or "").strip()
    return f"{title}\n\n{body[:ARTICLE_BODY_CHARS]}".strip()


def main() -> None:
    db = SessionLocal()
    model = current_primary_model_name()
    print(f"current embedding model: {model}")

    try:
        # Find articles that need backfill: race-relevant, have text,
        # and either no cached embedding OR cached under a different model.
        candidates = (
            db.query(SourceItem)
            .filter(
                SourceItem.race_relevance_score >= MIN_RELEVANCE,
                SourceItem.title.isnot(None),
            )
            .all()
        )
        need_embed = []
        for item in candidates:
            if not article_text(item):
                continue
            if item.frame_match_embedding and item.frame_match_embedding_model == model:
                continue  # already cached under current model
            need_embed.append(item)

        print(f"{len(need_embed)} articles need embedding (of {len(candidates)} race-relevant)")
        if not need_embed:
            print("nothing to do")
            return

        total_embedded = 0
        t_start = time.time()
        for i in range(0, len(need_embed), BATCH_SIZE):
            batch = need_embed[i:i + BATCH_SIZE]
            texts = [article_text(it) for it in batch]
            embs = embed_texts(texts, task_type="SEMANTIC_SIMILARITY")
            for item, emb in zip(batch, embs):
                if emb is None:
                    continue
                item.frame_match_embedding = json.dumps(emb)
                item.frame_match_embedding_model = model
                total_embedded += 1
            db.commit()
            done = min(i + BATCH_SIZE, len(need_embed))
            elapsed = time.time() - t_start
            rate = done / elapsed if elapsed > 0 else 0
            eta = (len(need_embed) - done) / rate if rate > 0 else 0
            print(f"  {done}/{len(need_embed)} embedded "
                  f"({elapsed:.0f}s elapsed, {rate:.1f}/s, ETA {eta:.0f}s)")

        print(f"\ndone. {total_embedded} articles embedded in {time.time()-t_start:.0f}s")
    finally:
        db.close()


if __name__ == "__main__":
    main()
