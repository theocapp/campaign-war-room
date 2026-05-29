"""Backfill: create outlet rows + link articles by publisher_domain.

When the RSS/Google News ingestion sets `SourceItem.publisher_domain`,
it tries to link the article to an existing outlet. But:
  - the outlet may not have existed yet at ingestion time
  - the outlet was created later but ingestion's cache went stale
  - the article was ingested before the linking logic existed

This one-shot fixes the gap. For each SourceItem with
publisher_domain set but no outlet_id:

  1. If an outlet with that domain already exists → set outlet_id.
  2. Otherwise create a new outlet (name via app.services.source_display
     overrides + prettifier), then set outlet_id on the article.

Default mode is DRY-RUN (no DB writes). Pass --commit to apply.

USAGE:
    cd backend && .venv/bin/python scripts/backfill_outlets_from_publisher_domain.py
    cd backend && .venv/bin/python scripts/backfill_outlets_from_publisher_domain.py --commit
"""
import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import SessionLocal
from app.models import Outlet, SourceItem
from app.services.source_display import _DOMAIN_NAME_OVERRIDES, _prettify_domain


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true",
                        help="Actually write to DB. Default is dry-run.")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        # 1. Find SourceItems with publisher_domain set but no outlet_id
        items = (
            db.query(SourceItem)
            .filter(SourceItem.publisher_domain.isnot(None))
            .filter(SourceItem.outlet_id.is_(None))
            .all()
        )
        print(f"Found {len(items)} articles with publisher_domain but no outlet_id.")
        if not items:
            print("Nothing to do.")
            return

        # 2. Group by domain
        domain_to_items: dict[str, list[SourceItem]] = {}
        for it in items:
            d = (it.publisher_domain or "").lower().strip()
            if not d:
                continue
            domain_to_items.setdefault(d, []).append(it)
        print(f"Distinct domains: {len(domain_to_items)}")
        print()

        # 3. Pre-fetch all existing outlets keyed by domain
        existing_outlets = {
            o.domain.lower(): o
            for o in db.query(Outlet).filter(Outlet.domain.isnot(None)).all()
        }

        # 4. For each domain: either link to existing or create new
        linked_to_existing = Counter()
        created_outlets: dict[str, str] = {}  # domain -> name
        articles_linked = 0

        for domain, group in sorted(domain_to_items.items(), key=lambda kv: -len(kv[1])):
            existing = existing_outlets.get(domain)
            if existing:
                # Existing outlet — just link articles
                for it in group:
                    if args.commit:
                        it.outlet_id = existing.id
                    articles_linked += 1
                linked_to_existing[domain] = len(group)
            else:
                # New outlet — derive name, then create
                name = _DOMAIN_NAME_OVERRIDES.get(domain) or _prettify_domain(domain)
                if not name:
                    print(f"  SKIP {domain}: cannot derive a name")
                    continue
                if args.commit:
                    new_outlet = Outlet(
                        name=name,
                        domain=domain,
                        outlet_type="local_news",   # safe default; user can refine later
                        authority_score=5,           # neutral default
                        reliability_score=None,      # explicitly unscored — needs manual review
                        active=True,
                        notes="Auto-created by backfill_outlets_from_publisher_domain.py",
                    )
                    db.add(new_outlet)
                    db.flush()  # get id
                    for it in group:
                        it.outlet_id = new_outlet.id
                    existing_outlets[domain] = new_outlet  # subsequent passes can see it
                articles_linked += len(group)
                created_outlets[domain] = name

        # 5. Commit / abort
        if args.commit:
            db.commit()
            print(f"\nCOMMITTED.")
        else:
            db.rollback()
            print(f"\nDRY RUN — no changes written. Re-run with --commit to apply.")

        # 6. Report
        print()
        print(f"Articles linked to outlets: {articles_linked}")
        print(f"  ↳ to existing outlets:    {sum(linked_to_existing.values())}")
        print(f"  ↳ to NEW outlets:         {articles_linked - sum(linked_to_existing.values())}")
        print(f"New outlets {'created' if args.commit else 'would be created'}: {len(created_outlets)}")
        print()

        if linked_to_existing:
            print("Top domains linked to existing outlets:")
            for domain, n in linked_to_existing.most_common(15):
                outlet = existing_outlets[domain]
                score = outlet.reliability_score if outlet.reliability_score is not None else "—"
                print(f"  {n:4d}  {domain:30s}  → {outlet.name}  (score={score})")
            print()

        if created_outlets:
            print(f"New outlets ({'created' if args.commit else 'to create'}):")
            # Sort by article count desc
            ranked = sorted(
                created_outlets.items(),
                key=lambda kv: -len(domain_to_items[kv[0]]),
            )
            for domain, name in ranked[:25]:
                n = len(domain_to_items[domain])
                print(f"  {n:4d}  {domain:30s}  → {name!r}")
            if len(ranked) > 25:
                print(f"  ... and {len(ranked) - 25} more.")

    finally:
        db.close()


if __name__ == "__main__":
    main()
