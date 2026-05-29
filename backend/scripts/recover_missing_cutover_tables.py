"""One-shot recovery for 3 tables that were missing from TABLE_ORDER at the
2026-05-29 Postgres cutover and therefore weren't copied:

  * proposed_cluster_snapshots — 4 rows (2 with applied_to_frame_id set)
  * tracked_third_party_accounts — 1 row (Scranton subreddit tracker)
  * search_result_cache — 0 rows (no loss but copy anyway for symmetry)

Reuses _copy_table + _reset_sequences from sqlite_to_postgres.py. Verifies
each table is empty in destination before writing — refuses to overwrite if
recovery has already run.

Run with: cd backend && .venv/bin/python scripts/recover_missing_cutover_tables.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from sqlalchemy import create_engine, text

# Make sibling script importable
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sqlite_to_postgres import _copy_table, _reset_sequences  # noqa: E402

log = logging.getLogger("recover")

SRC_URL = f"sqlite:///{Path(__file__).resolve().parent.parent}/war_room.db.cutover-20260529-050401"
DST_URL = "postgresql+psycopg://theo@localhost:5432/noctua"

TABLES_TO_RECOVER = [
    "proposed_cluster_snapshots",
    "tracked_third_party_accounts",
    "search_result_cache",
]


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    if not Path(SRC_URL.replace("sqlite:///", "")).exists():
        log.error("Source snapshot does not exist: %s", SRC_URL)
        return 1

    src_eng = create_engine(SRC_URL, connect_args={"check_same_thread": False})
    dst_eng = create_engine(DST_URL)

    # Refuse to run if any of the 3 target tables already has rows in dst.
    # This script is one-shot; running twice would either error on PK collisions
    # or (with --force semantics added) double-insert.
    with dst_eng.connect() as conn:
        for table in TABLES_TO_RECOVER:
            count = conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
            if count and count > 0:
                log.error(
                    "Destination %s already has %d rows. Recovery has already "
                    "run, or rows were inserted via the app since cutover. "
                    "Refusing to proceed.",
                    table, count,
                )
                return 1

    log.info("Destination tables are empty; proceeding with recovery.")

    total_copied = 0
    for table in TABLES_TO_RECOVER:
        skipped_pks: set = set()
        n = _copy_table(
            src_eng, dst_eng, table,
            dry_run=False,
            limit_rows=None,
            skip_orphans=False,
            skipped_pks_out=skipped_pks,
        )
        log.info("  %s → %d rows copied", table, n)
        total_copied += n

    log.info("Resetting sequences on recovered tables...")
    _reset_sequences(dst_eng)

    log.info("Recovery complete — %d rows total restored.", total_copied)
    return 0


if __name__ == "__main__":
    sys.exit(main())
