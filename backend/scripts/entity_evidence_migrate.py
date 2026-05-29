"""Backfill evidence_json on existing EntityRelation rows.

Existing pre-V14.2 relations stored evidence as a flat triple:
  - source_articles : list[int]        (article ids)
  - sample_quote    : str | None       (ONE representative quote)
  - confidence      : "high"|"medium"|"low"

The new evidence_json column stores a JSON array of evidence dicts:
  [{"article_id": X, "sample_quote": "...", "confidence": "...",
    "extracted_at": "...", "extractor_version": "..."}]

Migration is necessarily lossy — we have one quote across N articles, so the
first article in source_articles gets the quote and the rest get
sample_quote=null. confidence is the same across all entries. extractor_version
is tagged "v14.1-backfilled" so it's clear these weren't freshly extracted.

USAGE:
    .venv/bin/python scripts/entity_evidence_migrate.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.models import EntityRelation


def migrate(db, dry_run: bool = False) -> dict:
    stats = {"already_populated": 0, "backfilled": 0, "no_articles": 0, "total": 0}
    relations = db.query(EntityRelation).all()
    for r in relations:
        stats["total"] += 1
        if r.evidence_json:
            stats["already_populated"] += 1
            continue
        try:
            article_ids = json.loads(r.source_articles or "[]")
        except Exception:
            article_ids = []
        if not article_ids:
            stats["no_articles"] += 1
            continue

        extracted_at = r.created_at.isoformat() if r.created_at else None
        confidence = r.confidence or "medium"

        evidence = []
        for i, aid in enumerate(article_ids):
            evidence.append({
                "article_id": aid,
                # Only the first article gets the (single) sample_quote.
                # The rest can't be reconstructed from the flat schema.
                "sample_quote": r.sample_quote if i == 0 else None,
                "confidence": confidence,
                "extracted_at": extracted_at,
                "extractor_version": "v14.1-backfilled",
            })
        r.evidence_json = json.dumps(evidence)
        stats["backfilled"] += 1

    if dry_run:
        db.rollback()
        print("DRY RUN — no changes committed.")
    else:
        db.commit()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        stats = migrate(db, dry_run=args.dry_run)
        print()
        for k, v in stats.items():
            print(f"  {k:30s} {v}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
