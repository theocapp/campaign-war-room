"""Run commonsense rules against existing EntityRelation rows.

Catches role-level violations that the type-level domain/range layer doesn't:
POTUS represents a district, Senator represents a county, NY-17 rep represents
PA-08, etc.

Rules with action="reject" → relations are deleted.
Rules with action="flag_for_review" → relations stay but the (subject, object)
pair gets queued in entity_review_decisions (item_type='commonsense_flag') so
the user can confirm or dismiss in the review UI.

USAGE:
    .venv/bin/python scripts/entity_commonsense_cleanup.py            # dry-run report
    .venv/bin/python scripts/entity_commonsense_cleanup.py --apply    # apply
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.models import Entity, EntityRelation, EntityReviewDecision
from app.services.commonsense_rules import evaluate


REPORT_PATH = Path("/tmp/noctua_commonsense_report.md")


def find_violations(db) -> list[dict]:
    entities = {e.id: e for e in db.query(Entity).all()}
    out: list[dict] = []
    for r in db.query(EntityRelation).all():
        subj = entities.get(r.subject_id)
        obj = entities.get(r.object_id)
        if not subj or not obj:
            continue
        action, rule_name = evaluate(subj, r.predicate, obj)
        if not action:
            continue
        out.append({
            "relation_id": r.id,
            "rule": rule_name,
            "action": action,
            "subject": {"name": subj.name, "type": subj.type, "canonical_id": subj.canonical_id},
            "predicate": r.predicate,
            "object": {"name": obj.name, "type": obj.type, "canonical_id": obj.canonical_id},
            "weight": r.weight or 0,
            "sample_quote": r.sample_quote,
        })
    out.sort(key=lambda v: -v["weight"])
    return out


def write_report(violations: list[dict]) -> None:
    lines: list[str] = []
    lines.append(f"# Commonsense rule violations ({len(violations)} relations)\n")
    by_rule = Counter(v["rule"] for v in violations)
    by_action = Counter(v["action"] for v in violations)
    lines.append("## By rule")
    for r, n in by_rule.most_common():
        lines.append(f"- `{r}`: {n}")
    lines.append("")
    lines.append("## By action")
    for a, n in by_action.most_common():
        lines.append(f"- `{a}`: {n}")
    lines.append("")
    lines.append("## Top 30 by weight\n")
    for i, v in enumerate(violations[:30], 1):
        s = v["subject"]
        o = v["object"]
        q = (v.get("sample_quote") or "")[:120]
        lines.append(f"{i}. **{v['action']}** — {s['name']} ({s['type']}) → {v['predicate']} → {o['name']} ({o['type']})  weight={v['weight']}  rule=`{v['rule']}`")
        if q:
            lines.append(f"   sample: *\"{q}\"*")
    REPORT_PATH.write_text("\n".join(lines))
    print(f"Report: {REPORT_PATH}")


def apply_actions(db, violations: list[dict]) -> dict:
    stats: Counter = Counter()
    for v in violations:
        if v["action"] == "reject":
            rel = db.query(EntityRelation).filter(EntityRelation.id == v["relation_id"]).one_or_none()
            if rel:
                stats["weight_removed"] += rel.weight or 0
                db.delete(rel)
                stats["deleted"] += 1
        elif v["action"] == "flag_for_review":
            item_key = f"commonsense-{v['relation_id']}"
            existing = (
                db.query(EntityReviewDecision)
                .filter(EntityReviewDecision.item_type == "commonsense_flag",
                        EntityReviewDecision.item_key == item_key)
                .one_or_none()
            )
            if not existing:
                db.add(EntityReviewDecision(
                    item_type="commonsense_flag",
                    item_key=item_key,
                    decision="skip",  # placeholder — user will set proper decision
                    notes=f"rule={v['rule']}; weight={v['weight']}",
                ))
                stats["flagged"] += 1
        elif v["action"] == "downgrade_confidence":
            rel = db.query(EntityRelation).filter(EntityRelation.id == v["relation_id"]).one_or_none()
            if rel:
                rel.confidence = "low"
                stats["downgraded"] += 1
    db.commit()
    return dict(stats)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        violations = find_violations(db)
        print(f"Found {len(violations)} relations violating commonsense rules")
        write_report(violations)
        if violations[:8]:
            print()
            print("TOP 8 BY WEIGHT:")
            for v in violations[:8]:
                s = v["subject"]
                o = v["object"]
                print(f"  [{v['action']:8}] {s['name']:25} → {v['predicate']:14} → {o['name']:30}  w={v['weight']:3}  ({v['rule']})")
        if args.apply:
            print()
            print("Applying actions...")
            stats = apply_actions(db, violations)
            for k, v in stats.items():
                print(f"  {k:30s} {v}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
