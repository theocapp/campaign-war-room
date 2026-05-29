"""Apply curated bias_label + reliability_score to the outlets table.

Reads backend/data/outlet_reliability.json (curated from AllSides + Ad Fontes
+ Wikipedia per-outlet pages). For each outlet matched by domain, sets the
two new columns. Outlets not in the JSON are left null.

USAGE:
    .venv/bin/python scripts/outlet_reliability_apply.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.models import Outlet


DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "outlet_reliability.json"


def apply(db, dry_run: bool = False) -> dict:
    data = json.loads(DATA_PATH.read_text())
    stats = {"updated": 0, "created": 0, "unmatched": 0, "total_in_seed": 0}
    for entry in data["outlets"]:
        stats["total_in_seed"] += 1
        domain = entry["domain"]
        outlet = db.query(Outlet).filter(Outlet.domain == domain).one_or_none()
        if outlet:
            outlet.bias_label = entry.get("bias_label")
            outlet.reliability_score = entry.get("reliability_score")
            stats["updated"] += 1
        else:
            # Create the outlet entry so the rating is preserved even if
            # we haven't seen articles from this domain yet. Authority_score
            # defaults to a middle value.
            outlet = Outlet(
                name=entry.get("name", domain),
                domain=domain,
                outlet_type="national",
                bias_label=entry.get("bias_label"),
                reliability_score=entry.get("reliability_score"),
            )
            db.add(outlet)
            stats["created"] += 1

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
        stats = apply(db, dry_run=args.dry_run)
        for k, v in stats.items():
            print(f"  {k:30s} {v}")
        # Coverage summary
        total = db.query(Outlet).count()
        rated = db.query(Outlet).filter(Outlet.reliability_score.isnot(None)).count()
        print()
        print(f"  Outlets in DB:                {total}")
        print(f"  Outlets with reliability tag: {rated} ({rated*100/max(total,1):.0f}%)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
