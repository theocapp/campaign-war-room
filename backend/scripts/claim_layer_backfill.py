"""One-time backfill of the claim layer from existing entity_relations data.

The claim layer (Claim + ClaimSupport) sits between articles and the
entity_relations denormalization. Pre-existing data lives in entity_relations
with per-article evidence in evidence_json — this script reads that data and
populates the new tables.

After this runs, claims are the source of truth and entity_relations becomes
a derived denormalization. The dual-write code in persist_extraction keeps
both populated until consumers migrate.

USAGE:
    .venv/bin/python scripts/claim_layer_backfill.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.models import Claim, ClaimSupport, EntityRelation
from app.services.stance import stance_for


def backfill(db, dry_run: bool = False) -> dict:
    stats: Counter = Counter()
    relations = db.query(EntityRelation).all()

    for r in relations:
        # Skip if a claim already exists for this triple (idempotent).
        existing = (
            db.query(Claim)
            .filter(Claim.subject_id == r.subject_id,
                    Claim.predicate == r.predicate,
                    Claim.object_id == r.object_id)
            .one_or_none()
        )
        sv = stance_for(r.predicate)
        if existing:
            stats["already_existed"] += 1
            claim = existing
        else:
            claim = Claim(
                subject_id=r.subject_id,
                predicate=r.predicate,
                object_id=r.object_id,
                procedural=sv.procedural if sv else None,
                rhetorical=sv.rhetorical if sv else None,
                ideological=sv.ideological if sv else None,
                status="active",
                first_seen=r.first_seen,
                last_seen=r.last_seen,
                sample_quote=r.sample_quote,
                confidence=r.confidence or "medium",
                extractor_version="v14.1-backfilled",
            )
            db.add(claim)
            db.flush()  # need id for FK
            stats["claims_created"] += 1

        # Build claim_supports from evidence_json (or fall back to source_articles).
        try:
            evidence = json.loads(r.evidence_json or "[]")
        except Exception:
            evidence = []
        if not evidence:
            try:
                article_ids = json.loads(r.source_articles or "[]")
            except Exception:
                article_ids = []
            evidence = [
                {"article_id": aid, "sample_quote": None,
                 "confidence": r.confidence or "medium",
                 "extracted_at": None,
                 "extractor_version": "v14.1-backfilled"}
                for aid in article_ids
            ]

        for ev in evidence:
            aid = ev.get("article_id")
            if not aid:
                continue
            already = (
                db.query(ClaimSupport)
                .filter(ClaimSupport.claim_id == claim.id,
                        ClaimSupport.article_id == aid)
                .one_or_none()
            )
            if already:
                stats["support_rows_skipped_dup"] += 1
                continue
            from datetime import datetime as _dt
            try:
                extracted_at = _dt.fromisoformat(ev["extracted_at"]) if ev.get("extracted_at") else None
            except Exception:
                extracted_at = None
            db.add(ClaimSupport(
                claim_id=claim.id,
                article_id=aid,
                stance="supporting",  # historical data — no contesting markers yet
                sample_quote=ev.get("sample_quote"),
                confidence=ev.get("confidence") or "medium",
                extractor_version=ev.get("extractor_version") or "v14.1-backfilled",
                extracted_at=extracted_at,
            ))
            stats["support_rows_created"] += 1

    if dry_run:
        db.rollback()
        print("DRY RUN — no changes committed.")
    else:
        db.commit()
    return dict(stats)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        stats = backfill(db, dry_run=args.dry_run)
        for k, v in stats.items():
            print(f"  {k:30s} {v}")
        if not args.dry_run:
            total_claims = db.query(Claim).count()
            total_supports = db.query(ClaimSupport).count()
            print()
            print(f"  total claims in DB:           {total_claims}")
            print(f"  total claim_supports in DB:   {total_supports}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
