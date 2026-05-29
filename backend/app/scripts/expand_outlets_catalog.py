"""Add ~24 missing outlets to the catalog, then backfill outlet_id on
already-ingested SourceItems.

Why: as of 2026-05-24 only 34 outlets are catalogued, leaving 8,524 of
13,175 articles (65%) with NULL outlet_id. The matcher works correctly —
the catalog is just too small. After this script, ~3,500 unlinked rows
should get a real outlet, dropping the unlinked rate to ~38%.

Idempotent — uses INSERT OR IGNORE; safe to re-run.

Usage:  python -m app.scripts.expand_outlets_catalog [--dry-run]
"""
from __future__ import annotations
import sys
from datetime import datetime

from app.db import SessionLocal


# Each row: (name, domain, outlet_type, state, city, authority_score, notes)
# state/city use empty string if not applicable. Authority scores follow the
# existing pattern (0-9 scale, see outlets table for reference).
NEW_OUTLETS = [
    # ---- PA Local & Regional ----
    ("ABC27",                  "abc27.com",              "local_news",   "PA", "Harrisburg",    8, "WHTM Harrisburg PA"),
    ("NBC Philadelphia",       "nbcphiladelphia.com",    "broadcast",    "PA", "Philadelphia",  7, "WCAU NBC10"),
    ("WFMZ",                   "wfmz.com",               "broadcast",    "PA", "Allentown",     7, "Allentown-Bethlehem area"),
    ("Fox56 WOLF",             "fox56.com",              "broadcast",    "PA", "Scranton",      7, "WOLF Northeast PA"),
    ("PoliticsPA",             "politicspa.com",         "local_news",   "PA", "",              6, "Insider PA politics blog"),
    ("PAnow",                  "panow.com",              "local_news",   "PA", "",              5, "PA regional news"),
    ("Sunday Dispatch",        "psdispatch.com",         "local_news",   "PA", "Pittston",      5, "Pittston-area weekly"),
    ("iHeart Radio",           "iheart.com",             "broadcast",    "",   "",              5, "National radio aggregator"),

    # ---- National ----
    ("Fox News",               "foxnews.com",            "national",     "",   "",              7, ""),
    ("Newsweek",               "newsweek.com",           "national",     "",   "",              6, ""),
    ("Yahoo News",             "yahoo.com",              "national",     "",   "",              4, "Aggregator, low original reporting"),
    ("LA Times",               "latimes.com",            "national",     "",   "",              8, ""),
    ("The Independent UK",     "independent.co.uk",      "national",     "",   "",              5, "UK national paper"),
    ("The Epoch Times",        "theepochtimes.com",      "national",     "",   "",              3, ""),
    ("NBC Washington",         "nbcwashington.com",      "regional_news","",   "Washington DC", 6, ""),
    ("IBTimes",                "ibtimes.com",            "national",     "",   "",              4, "International Business Times"),

    # ---- Partisan / Opinion (low authority, useful for amplification analysis) ----
    ("Free Republic",          "freerepublic.com",       "social",       "",   "",              1, "Conservative forum, no editorial"),
    ("Breitbart",              "breitbart.com",          "blog",         "",   "",              3, ""),
    ("Raw Story",              "rawstory.com",           "blog",         "",   "",              4, ""),
    ("Townhall",               "townhall.com",           "blog",         "",   "",              3, ""),
    ("Washington Examiner",    "washingtonexaminer.com", "national",     "",   "",              5, ""),
    ("Daily Caller",           "dailycaller.com",        "blog",         "",   "",              3, ""),
    ("AlterNet",               "alternet.org",           "blog",         "",   "",              4, ""),
    ("Daily Signal",           "dailysignal.com",        "blog",         "",   "",              3, ""),
]


def main(dry_run: bool = False) -> None:
    db = SessionLocal()
    try:
        from app.models import Outlet, SourceItem
        from sqlalchemy import text as sql_text

        # ---- Pre-snapshot ----
        before_outlets = db.query(Outlet).count()
        before_unlinked = db.query(SourceItem).filter(SourceItem.outlet_id.is_(None)).count()
        before_total = db.query(SourceItem).count()
        print(f"=== Before ===")
        print(f"  outlets table:       {before_outlets} rows")
        print(f"  source_items total:  {before_total}")
        print(f"  unlinked:            {before_unlinked} ({100*before_unlinked/before_total:.1f}%)")
        print()

        # ---- Insert outlets (INSERT OR IGNORE on UNIQUE(domain)) ----
        now = datetime.utcnow().isoformat()
        added = 0
        skipped_existing = 0

        for name, domain, otype, state, city, score, notes in NEW_OUTLETS:
            existing = db.query(Outlet).filter_by(domain=domain).first()
            if existing:
                skipped_existing += 1
                continue
            if dry_run:
                print(f"  [DRY] would add: {name:30s}  {domain:30s}  tier={otype}  authority={score}")
                added += 1
                continue
            row = Outlet(
                name=name,
                domain=domain,
                outlet_type=otype,
                state=(state or None),
                city=(city or None),
                authority_score=score,
                active=True,
                notes=(notes or None),
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            db.add(row)
            added += 1

        if not dry_run:
            db.commit()
        print(f"=== Outlets added: {added}  (skipped {skipped_existing} that already exist) ===\n")

        if dry_run:
            print("Dry run — no backfill will run, no DB changes committed.")
            return

        # ---- Backfill existing source_items ----
        print("Running backfill_outlet_links (this links existing rows to the new outlets)...")
        from app.services.outlet_linking import backfill_outlet_links
        linked = backfill_outlet_links(db)
        print(f"=== Backfilled {linked} source_items ===\n")

        # ---- Post-snapshot ----
        after_outlets = db.query(Outlet).count()
        after_unlinked = db.query(SourceItem).filter(SourceItem.outlet_id.is_(None)).count()
        print(f"=== After ===")
        print(f"  outlets table:       {after_outlets} rows  (+{after_outlets - before_outlets})")
        print(f"  unlinked:            {after_unlinked} ({100*after_unlinked/before_total:.1f}%)  "
              f"(reduced by {before_unlinked - after_unlinked})")
        print()

        # ---- Top remaining unlinked (for next round) ----
        print("Top 10 remaining unlinked sources (could add to catalog in a future pass):")
        rows = db.execute(sql_text("""
            SELECT source_name, COUNT(*) c
            FROM source_items
            WHERE outlet_id IS NULL AND source_url IS NOT NULL
            GROUP BY source_name
            ORDER BY c DESC
            LIMIT 10
        """)).fetchall()
        for name, c in rows:
            print(f"  {c:5d}  {name}")
    finally:
        db.close()


if __name__ == "__main__":
    main(dry_run="--dry-run" in sys.argv)
