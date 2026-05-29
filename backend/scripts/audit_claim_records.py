"""Quality audit for v15.0 claim_records.

Reports:
  1. Overall counts (records, distinct articles, distinct entities involved)
  2. Label distribution (with %)
  3. Confidence distribution
  4. Entities per claim (mean, distribution)
  5. Top entities by claim volume
  6. Label distribution per top entity (which entities accumulate attacks vs
     endorsements vs etc.)
  7. Article source distribution (which outlets produced the most claims)
  8. New auto-discovered entities since last audit (anomalies / hallucinations?)
  9. 20 random claim spot-checks for manual quality eval — verbatim against
     source article text, entities-in-span, sensible label.

USAGE:
    python scripts/audit_claim_records.py [--version v15.0] [--sample N]
"""
from __future__ import annotations

import argparse
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func, text
from app.db import engine, SessionLocal
from app.models import (
    ClaimRecord, ClaimRecordEntity, Entity, SourceItem,
)


def audit(version: str = "v15.0", sample_size: int = 20) -> None:
    with SessionLocal() as db:
        # 1. Overall counts
        n_records = db.query(ClaimRecord).filter(ClaimRecord.extractor_version == version).count()
        n_articles = (
            db.query(ClaimRecord.article_id)
            .filter(ClaimRecord.extractor_version == version)
            .distinct().count()
        )
        n_links = (
            db.query(ClaimRecordEntity)
            .join(ClaimRecord, ClaimRecord.id == ClaimRecordEntity.claim_record_id)
            .filter(ClaimRecord.extractor_version == version)
            .count()
        )
        n_distinct_entities_in_claims = (
            db.query(ClaimRecordEntity.entity_id)
            .join(ClaimRecord, ClaimRecord.id == ClaimRecordEntity.claim_record_id)
            .filter(ClaimRecord.extractor_version == version)
            .distinct().count()
        )
        print(f"=" * 72)
        print(f"v15.0 CLAIM RECORD AUDIT")
        print(f"=" * 72)
        print(f"Claim records:             {n_records:,}")
        print(f"Distinct articles:         {n_articles:,}")
        print(f"Entity-link rows:          {n_links:,}")
        print(f"Distinct entities cited:   {n_distinct_entities_in_claims:,}")
        if n_records:
            print(f"Entities / claim (mean):   {n_links/n_records:.2f}")

        # 2. Label distribution
        print()
        print("LABEL DISTRIBUTION:")
        rows = (
            db.query(ClaimRecord.label, func.count(ClaimRecord.id))
            .filter(ClaimRecord.extractor_version == version)
            .group_by(ClaimRecord.label)
            .order_by(func.count(ClaimRecord.id).desc())
            .all()
        )
        for lbl, n in rows:
            pct = 100 * n / n_records if n_records else 0
            print(f"  {(lbl or '(null)'):20s} {n:>5}  ({pct:5.1f}%)")

        # 3. Confidence
        print()
        print("CONFIDENCE DISTRIBUTION:")
        rows = (
            db.query(ClaimRecord.confidence, func.count(ClaimRecord.id))
            .filter(ClaimRecord.extractor_version == version)
            .group_by(ClaimRecord.confidence)
            .all()
        )
        for c, n in rows:
            pct = 100 * n / n_records if n_records else 0
            print(f"  {c:8s} {n:>5}  ({pct:5.1f}%)")

        # 4. Top entities by claim volume
        print()
        print("TOP 15 ENTITIES BY CLAIM COUNT:")
        rows = db.execute(text(f"""
            SELECT e.canonical_id, e.name, e.type, e.seeded,
                   count(DISTINCT cr.id) as n_claims
            FROM claim_records cr
            JOIN claim_record_entities cre ON cre.claim_record_id = cr.id
            JOIN entities e ON e.id = cre.entity_id
            WHERE cr.extractor_version = '{version}'
            GROUP BY e.id ORDER BY n_claims DESC LIMIT 15
        """)).all()
        for r in rows:
            seed_tag = "[seed]" if r.seeded else "[auto]"
            print(f"  {seed_tag} {r.type:13s} {r.name[:30]:30s} {r.n_claims:>4}  ({r.canonical_id})")

        # 5. Label distribution per top entity
        print()
        print("TOP 6 ENTITIES — LABEL BREAKDOWN:")
        for r in rows[:6]:
            print(f"  {r.name}:")
            sub = db.execute(text(f"""
                SELECT cr.label, count(*) as n
                FROM claim_records cr
                JOIN claim_record_entities cre ON cre.claim_record_id = cr.id
                WHERE cr.extractor_version = '{version}' AND cre.entity_id = (
                    SELECT id FROM entities WHERE canonical_id = '{r.canonical_id}'
                )
                GROUP BY cr.label ORDER BY n DESC
            """)).all()
            for lbl, n in sub:
                print(f"    {(lbl or '(null)'):20s} {n}")

        # 6. Top outlets producing claims
        print()
        print("TOP 10 OUTLETS BY CLAIM COUNT:")
        rows = db.execute(text(f"""
            SELECT si.source_name, count(*) as n, count(DISTINCT cr.article_id) as articles
            FROM claim_records cr JOIN source_items si ON si.id = cr.article_id
            WHERE cr.extractor_version = '{version}' AND si.source_name IS NOT NULL
            GROUP BY si.source_name ORDER BY n DESC LIMIT 10
        """)).all()
        for r in rows:
            print(f"  {r.n:>4} claims / {r.articles:>3} articles  {r.source_name}")

        # 7. Verbatim accuracy check on a random sample
        print()
        print(f"VERBATIM ACCURACY — random sample of {sample_size}:")
        random.seed(42)
        all_ids = [
            r[0] for r in
            db.query(ClaimRecord.id)
            .filter(ClaimRecord.extractor_version == version).all()
        ]
        sample = random.sample(all_ids, min(sample_size, len(all_ids)))
        verbatim_ok = 0
        entities_ok_count = 0
        sample_records: list = []
        for cid in sample:
            cr = db.query(ClaimRecord).filter(ClaimRecord.id == cid).one()
            article = db.query(SourceItem).filter(SourceItem.id == cr.article_id).first()
            if not article or not article.raw_text:
                continue
            # Is the evidence span actually in raw_text?
            is_verbatim = cr.evidence_span in article.raw_text
            verbatim_ok += 1 if is_verbatim else 0
            # Are the linked entities actually findable in the span?
            entities = (
                db.query(Entity)
                .join(ClaimRecordEntity, ClaimRecordEntity.entity_id == Entity.id)
                .filter(ClaimRecordEntity.claim_record_id == cr.id)
                .all()
            )
            all_ents_in_span = True
            span_lc = cr.evidence_span.lower()
            for e in entities:
                forms = [e.name]
                if e.aliases:
                    try:
                        import json
                        forms.extend(json.loads(e.aliases))
                    except Exception:
                        pass
                if not any(f and f.lower() in span_lc for f in forms):
                    all_ents_in_span = False
                    break
            if all_ents_in_span:
                entities_ok_count += 1
            sample_records.append({
                "cr": cr, "article": article, "entities": entities,
                "verbatim": is_verbatim, "entities_in_span": all_ents_in_span,
            })

        print(f"  Verbatim (span in raw_text):     {verbatim_ok}/{len(sample_records)}")
        print(f"  All entities in span:            {entities_ok_count}/{len(sample_records)}")

        # Show all sampled records for manual eval
        print()
        print("SAMPLE RECORDS (for manual review):")
        for i, s in enumerate(sample_records, 1):
            cr = s["cr"]
            verb = "✓" if s["verbatim"] else "✗"
            ents_ok = "✓" if s["entities_in_span"] else "✗"
            print()
            print(f"  #{i}  Claim {cr.id}  v={verb} e={ents_ok}  label={cr.label or 'null'}  conf={cr.confidence}")
            print(f"     entities: {', '.join(e.name for e in s['entities'])}")
            print(f"     article: {s['article'].title[:80]!r}")
            print(f"     span:    {cr.evidence_span[:140]!r}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--version", default="v15.0")
    p.add_argument("--sample", type=int, default=20)
    args = p.parse_args()
    audit(version=args.version, sample_size=args.sample)
