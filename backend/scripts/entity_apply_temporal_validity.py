"""Apply known role-transition dates to EntityRelation rows.

Reads backend/data/role_transitions.<district>.json and sets valid_from /
valid_to on matching EntityRelation rows. If a transition is marked
create_if_missing=true and the relation doesn't exist in the graph yet,
the script creates an asserted relation with weight=0 (no article evidence
required — this is a curated fact, not extraction).

This is GKG principle #8 (facts decay): without temporal validity, Cartwright
forever "represents" PA-08 in our graph and Bresnahan looks ambiguous.

USAGE:
    .venv/bin/python scripts/entity_apply_temporal_validity.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.models import Entity, EntityRelation


DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "role_transitions.PA-08.json"


def parse_date(s: str | None) -> datetime | None:
    if s is None:
        return None
    return datetime.fromisoformat(s)


def apply_transitions(db, dry_run: bool = False) -> dict:
    data = json.loads(DATA_PATH.read_text())
    stats = {"updated": 0, "created": 0, "skipped_no_subject": 0, "skipped_no_object": 0,
             "skipped_no_relation": 0}

    for t in data["transitions"]:
        s_can = t["subject_canonical_id"]
        o_can = t["object_canonical_id"]
        pred = t["predicate"]

        subj = db.query(Entity).filter(Entity.canonical_id == s_can).one_or_none()
        obj = db.query(Entity).filter(Entity.canonical_id == o_can).one_or_none()
        if not subj:
            print(f"  SKIP: subject {s_can!r} not found in entities")
            stats["skipped_no_subject"] += 1
            continue
        if not obj:
            print(f"  SKIP: object {o_can!r} not found in entities")
            stats["skipped_no_object"] += 1
            continue

        rel = (
            db.query(EntityRelation)
            .filter(EntityRelation.subject_id == subj.id,
                    EntityRelation.predicate == pred,
                    EntityRelation.object_id == obj.id)
            .one_or_none()
        )

        vf = parse_date(t.get("valid_from"))
        vt = parse_date(t.get("valid_to"))

        if rel:
            rel.valid_from = vf
            rel.valid_to = vt
            stats["updated"] += 1
            print(f"  UPDATE: {s_can} → {pred} → {o_can} valid={t.get('valid_from')}..{t.get('valid_to')}")
        elif t.get("create_if_missing"):
            new_rel = EntityRelation(
                subject_id=subj.id,
                predicate=pred,
                object_id=obj.id,
                weight=0,
                valid_from=vf,
                valid_to=vt,
                confidence="high",  # this is a curated/asserted fact
                sample_quote=t.get("note", ""),
                source_articles=json.dumps([]),
            )
            db.add(new_rel)
            stats["created"] += 1
            print(f"  CREATE: {s_can} → {pred} → {o_can} valid={t.get('valid_from')}..{t.get('valid_to')} (asserted)")
        else:
            stats["skipped_no_relation"] += 1
            print(f"  SKIP: no existing relation {s_can} → {pred} → {o_can}, create_if_missing=false")

    if dry_run:
        db.rollback()
        print()
        print("DRY RUN — no changes committed.")
    else:
        db.commit()

    return stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Don't commit changes")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        print(f"Reading {DATA_PATH}\n")
        stats = apply_transitions(db, dry_run=args.dry_run)
        print()
        for k, v in stats.items():
            print(f"  {k:30s} {v}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
