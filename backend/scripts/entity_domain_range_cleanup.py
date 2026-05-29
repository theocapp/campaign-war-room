"""Delete EntityRelation rows that violate the domain/range constraints.

Pre-V14.3 extractions had no domain/range check, so the LLM produced
nonsense like:
  - location → represents → person ("scranton represents cognetti")
  - person → represents → bill ("Jeffries represents ACA")
  - location → represents → location ("Duryea represents Luzerne County")
  - location → member_of → bill

This script finds rows where (subject.type, predicate, object.type) violates
the PREDICATE_DOMAIN_RANGE table in entity_extraction.py and deletes them.
Dry-run by default; --apply to commit.

USAGE:
    .venv/bin/python scripts/entity_domain_range_cleanup.py            # dry-run report
    .venv/bin/python scripts/entity_domain_range_cleanup.py --apply    # delete violations
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.models import Entity, EntityRelation
from app.services.entity_extraction import PREDICATE_DOMAIN_RANGE, relation_type_allowed


REPORT_PATH = Path("/tmp/noctua_domain_range_report.md")


def find_violations(db) -> list[dict]:
    entities = {e.id: e for e in db.query(Entity).all()}
    out: list[dict] = []
    for r in db.query(EntityRelation).all():
        subj = entities.get(r.subject_id)
        obj = entities.get(r.object_id)
        if not subj or not obj:
            continue
        if relation_type_allowed(subj.type, r.predicate, obj.type):
            continue
        try:
            articles = json.loads(r.source_articles or "[]")
        except Exception:
            articles = []
        out.append({
            "relation_id": r.id,
            "subject": {"name": subj.name, "type": subj.type, "canonical_id": subj.canonical_id},
            "predicate": r.predicate,
            "object": {"name": obj.name, "type": obj.type, "canonical_id": obj.canonical_id},
            "weight": r.weight or 0,
            "evidence_articles": len(articles),
            "sample_quote": r.sample_quote,
        })
    out.sort(key=lambda v: -v["weight"])
    return out


def write_report(violations: list[dict]) -> None:
    lines: list[str] = []
    lines.append(f"# Domain/range violations ({len(violations)} relations)\n")
    by_kind: Counter = Counter()
    for v in violations:
        by_kind[(v["subject"]["type"], v["predicate"], v["object"]["type"])] += 1
    lines.append("## Violation patterns (most common first)\n")
    for (s, p, o), n in by_kind.most_common(15):
        lines.append(f"- {s} → {p} → {o} — {n} rows")
    lines.append("")
    lines.append("## Top 30 by weight\n")
    for i, v in enumerate(violations[:30], 1):
        s = v["subject"]
        o = v["object"]
        q = (v.get("sample_quote") or "")[:120]
        lines.append(f"{i}. {s['name']} ({s['type']}) → {v['predicate']} → {o['name']} ({o['type']}) "
                     f"— weight {v['weight']}, {v['evidence_articles']} articles")
        if q:
            lines.append(f"   sample: *\"{q}\"*")
    REPORT_PATH.write_text("\n".join(lines))
    print(f"Report: {REPORT_PATH}")


def apply_deletions(db, violations: list[dict]) -> dict:
    stats = {"deleted": 0, "weight_removed": 0}
    for v in violations:
        rel = db.query(EntityRelation).filter(EntityRelation.id == v["relation_id"]).one_or_none()
        if not rel:
            continue
        stats["weight_removed"] += rel.weight or 0
        db.delete(rel)
        stats["deleted"] += 1
    db.commit()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Delete the violating relations")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        violations = find_violations(db)
        print(f"Found {len(violations)} relations violating domain/range constraints")
        write_report(violations)
        if violations[:8]:
            print()
            print("TOP 8 BY WEIGHT:")
            for v in violations[:8]:
                s = v["subject"]
                o = v["object"]
                print(f"  {s['name']:25s} ({s['type']:5}) → {v['predicate']:15s} → {o['name']:30s} ({o['type']:5}) — weight {v['weight']}")
        if args.apply:
            print()
            print("Applying deletions...")
            stats = apply_deletions(db, violations)
            print(f"  Deleted relations:        {stats['deleted']}")
            print(f"  Combined weight removed:  {stats['weight_removed']}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
