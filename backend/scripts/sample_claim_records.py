"""sample_claim_records.py — spot-check tool for v15.0 claim extraction.

Prints random ClaimRecords from the DB so a human can eyeball-validate
extraction quality before trusting the data for downstream features
like the grounded briefing memo.

For each record shows:
  - article title + outlet + published date + URL
  - the verbatim quote span
  - the label (or "(none)" — NULL labels are valid per the v15.0 prompt)
  - the entities linked to the quote, with the surface text they appeared as

USAGE:
    cd backend && .venv/bin/python scripts/sample_claim_records.py
    cd backend && .venv/bin/python scripts/sample_claim_records.py --n 30
    cd backend && .venv/bin/python scripts/sample_claim_records.py --label attack
    cd backend && .venv/bin/python scripts/sample_claim_records.py --label any
    cd backend && .venv/bin/python scripts/sample_claim_records.py --entity person:bresnahan

Labels: statement, attack, defense, endorsement, policy_position, vote,
        announcement, commitment, OR 'any' (any non-null), OR 'none' (only unlabeled).
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import func as sql_func

from app.db import SessionLocal
from app.models import ClaimRecord, ClaimRecordEntity, Entity, SourceItem


def fetch_sample(db, n, label_filter=None, entity_canon_id=None):
    q = db.query(ClaimRecord)

    if label_filter:
        if label_filter == "any":
            q = q.filter(ClaimRecord.label.isnot(None))
        elif label_filter == "none":
            q = q.filter(ClaimRecord.label.is_(None))
        else:
            q = q.filter(ClaimRecord.label == label_filter)

    if entity_canon_id:
        entity = db.query(Entity).filter(Entity.canonical_id == entity_canon_id).one_or_none()
        if not entity:
            print(f"Entity not found: {entity_canon_id}", file=sys.stderr)
            sys.exit(1)
        q = q.join(
            ClaimRecordEntity,
            ClaimRecordEntity.claim_record_id == ClaimRecord.id,
        ).filter(ClaimRecordEntity.entity_id == entity.id)

    q = q.order_by(sql_func.random()).limit(n)
    return q.all()


def print_record(db, claim):
    article = db.query(SourceItem).filter(SourceItem.id == claim.article_id).one_or_none()
    entities = (
        db.query(Entity, ClaimRecordEntity.surface_text)
        .join(ClaimRecordEntity, ClaimRecordEntity.entity_id == Entity.id)
        .filter(ClaimRecordEntity.claim_record_id == claim.id)
        .all()
    )

    print("─" * 78)
    label = claim.label or "(none)"
    print(f"  CLAIM #{claim.id}   label={label}   confidence={claim.confidence}")
    if article:
        title = article.title or "(no title)"
        outlet = article.source_name or "?"
        pub = article.published_at.strftime("%Y-%m-%d") if article.published_at else "?"
        print(f"  Article: {title}")
        print(f"  Source:  {outlet} · {pub}")
        if article.source_url:
            print(f"  URL:     {article.source_url}")
    else:
        print(f"  Article: (article #{claim.article_id} not found)")
    print()
    print(f'  Quote:   "{claim.evidence_span}"')
    print()
    if entities:
        print("  Entities:")
        for ent, surface in entities:
            aff = f":{ent.affiliation}" if ent.affiliation else ""
            tag = f"[{ent.type}{aff}]"
            line = f"    - {tag} {ent.name} ({ent.canonical_id})"
            if surface and surface.lower() != ent.name.lower():
                line += f'  appeared as: "{surface}"'
            print(line)
    else:
        print("  Entities: (none linked)")
    print()


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--n", type=int, default=20, help="Number to sample (default 20)")
    parser.add_argument(
        "--label",
        type=str,
        default=None,
        help=(
            "Filter by label: attack, endorsement, vote, commitment, "
            "policy_position, defense, statement, announcement, "
            "OR 'any' (any non-null), OR 'none' (only unlabeled)"
        ),
    )
    parser.add_argument(
        "--entity",
        type=str,
        default=None,
        help="Filter by entity canonical_id, e.g. person:bresnahan",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        sample = fetch_sample(db, args.n, args.label, args.entity)
        if not sample:
            print("No matching claim records found.")
            return

        filter_desc = []
        if args.label:
            filter_desc.append(f"label={args.label}")
        if args.entity:
            filter_desc.append(f"entity={args.entity}")
        suffix = f"  ({', '.join(filter_desc)})" if filter_desc else ""

        print()
        print(f"  Showing {len(sample)} random claim records{suffix}")
        print()

        for claim in sample:
            print_record(db, claim)

        print("─" * 78)
        print(f"  {len(sample)} records printed.")
        print()
    finally:
        db.close()


if __name__ == "__main__":
    main()
