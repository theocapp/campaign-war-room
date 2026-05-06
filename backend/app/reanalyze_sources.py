"""CLI for reprocessing existing source items.

Usage:
    python -m app.reanalyze_sources --dry-run
"""
import argparse
import json

from app.db import SessionLocal, init_db
from app.services.reanalysis import ReanalysisOptions, reanalyze_sources


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reanalyze existing campaign source items.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--source-id", type=int, default=None)
    parser.add_argument("--include-reviewed", action="store_true", default=False)
    parser.add_argument("--include-dismissed", action="store_true", default=False)
    parser.add_argument("--include-archived", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--dry-run", action="store_true", default=False)
    return parser


def main() -> None:
    args = _parser().parse_args()
    init_db()
    options = ReanalysisOptions(
        limit=args.limit,
        source_id=args.source_id,
        include_reviewed=args.include_reviewed,
        include_dismissed=args.include_dismissed,
        include_archived=args.include_archived,
        dry_run=args.dry_run,
    )
    with SessionLocal() as db:
        result = reanalyze_sources(db, options)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
