"""Backfill Outlet.monthly_visitors from a calibrated Tranco-rank model.

Reads backend/data/outlet_monthly_visitors.json (106 editorial outlets that
lacked a monthly_visitors value). For each outlet matched by domain, fills
monthly_visitors ONLY IF it is currently NULL -- existing values are never
overwritten. Domains not already in the outlets table are reported as
unmatched and skipped (we do not invent outlet rows here).

Why: reach weighting (analytics.py / narrative_frames.py) does
monthly_visitors * 0.003 when a value exists, else authority_score / 10 --
a ~1000x scale gap. Before this backfill only ~30 outlets had a value, so
reach rankings were dominated by whichever outlets happened to get a number
at setup. This puts editorial outlets on the same honest scale.

The numbers are band-accurate, NOT count-accurate (see _meta in the JSON).
Surface them as reach TIERS, never as precise per-article viewer counts.

USAGE:
    .venv/bin/python scripts/backfill_outlet_monthly_visitors.py            # dry-run (default)
    .venv/bin/python scripts/backfill_outlet_monthly_visitors.py --apply    # write to DB
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.models import Outlet


DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "outlet_monthly_visitors.json"


def apply(db, do_write: bool) -> dict:
    doc = json.loads(DATA_PATH.read_text())
    entries = doc["outlets"]
    stats = {
        "in_file": len(entries),
        "would_fill": 0,      # matched, currently NULL -> will set
        "skipped_has_value": 0,  # matched but already has a value -> untouched
        "unmatched": 0,       # domain not in outlets table -> skipped
    }
    to_fill = []  # (domain, new_value, basis)
    for entry in entries:
        domain = entry["domain"]
        new_mv = entry["monthly_visitors"]
        outlet = db.query(Outlet).filter(Outlet.domain == domain).one_or_none()
        if outlet is None:
            stats["unmatched"] += 1
            print(f"  UNMATCHED  {domain:34s} (not in outlets table — skipped)")
            continue
        if outlet.monthly_visitors is not None:
            stats["skipped_has_value"] += 1
            continue
        to_fill.append((domain, new_mv, entry["basis"]))
        outlet.monthly_visitors = new_mv
        stats["would_fill"] += 1

    print()
    print(f"  {'domain':34s} {'-> monthly_visitors':>20s}  basis")
    for domain, new_mv, basis in to_fill:
        print(f"  {domain:34s} {new_mv:>20,d}  {basis}")

    if do_write:
        db.commit()
        print("\n  APPLIED — changes committed.")
    else:
        db.rollback()
        print("\n  DRY RUN — no changes committed. Re-run with --apply to write.")
    return stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Write to the DB. Without this flag the script is a dry-run.")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        stats = apply(db, do_write=args.apply)
        print()
        for k, v in stats.items():
            print(f"  {k:22s} {v}")
        total = db.query(Outlet).count()
        have_mv = db.query(Outlet).filter(Outlet.monthly_visitors.isnot(None)).count()
        print()
        print(f"  Outlets in DB:                 {total}")
        print(f"  Outlets with monthly_visitors: {have_mv} ({have_mv * 100 / max(total, 1):.0f}%)")
    finally:
        db.close()


if __name__ == "__main__":
    main()
