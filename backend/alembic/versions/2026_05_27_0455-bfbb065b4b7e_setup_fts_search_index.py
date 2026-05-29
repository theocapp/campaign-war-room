"""Set up full-text search index — dialect-aware.

SQLite branch: idempotent. The live war_room.db already has the FTS5
virtual table + 3 sync triggers (created by the legacy _migrate() block).
This migration only creates them if they don't exist, so it's a no-op
on the live DB but creates the index on fresh dev/test DBs.

Postgres branch: adds `search_tsv` tsvector column on source_items, a
GIN index on it, a BEFORE INSERT/UPDATE trigger that maintains the
column from title + raw_text, and a one-shot backfill of existing rows.

Downgrade: drops the search artifacts on both dialects.

Revision ID: bfbb065b4b7e
Revises: bb6913b5ae7e
Create Date: 2026-05-27 04:55:00
"""
from __future__ import annotations

import logging
from typing import Sequence, Union

from alembic import op

revision: str = "bfbb065b4b7e"
down_revision: Union[str, None] = "bb6913b5ae7e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

log = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "sqlite":
        _upgrade_sqlite(bind)
    elif dialect == "postgresql":
        _upgrade_postgres(bind)
    else:
        log.warning(
            "setup_fts_search_index: unsupported dialect %r — skipping FTS setup. "
            "Search will fall back to ILIKE at runtime.",
            dialect,
        )


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    if dialect == "sqlite":
        _downgrade_sqlite(bind)
    elif dialect == "postgresql":
        _downgrade_postgres(bind)


# ─── SQLite ────────────────────────────────────────────────────────────────

def _upgrade_sqlite(bind) -> None:
    exists = bind.exec_driver_sql(
        "SELECT count(*) FROM sqlite_master "
        "WHERE type='table' AND name='source_items_fts'"
    ).scalar()
    if exists:
        log.info("SQLite source_items_fts already exists — no-op.")
        return
    log.info("Creating SQLite FTS5 index source_items_fts.")
    bind.exec_driver_sql(
        "CREATE VIRTUAL TABLE source_items_fts "
        "USING fts5(title, raw_text, content='source_items', content_rowid='id')"
    )
    bind.exec_driver_sql(
        "INSERT INTO source_items_fts(rowid, title, raw_text) "
        "SELECT id, COALESCE(title,''), COALESCE(raw_text,'') FROM source_items"
    )
    bind.exec_driver_sql(
        "CREATE TRIGGER source_items_ai AFTER INSERT ON source_items BEGIN "
        "  INSERT INTO source_items_fts(rowid, title, raw_text) "
        "  VALUES (new.id, COALESCE(new.title,''), COALESCE(new.raw_text,'')); "
        "END"
    )
    bind.exec_driver_sql(
        "CREATE TRIGGER source_items_ad AFTER DELETE ON source_items BEGIN "
        "  INSERT INTO source_items_fts(source_items_fts, rowid, title, raw_text) "
        "  VALUES ('delete', old.id, COALESCE(old.title,''), COALESCE(old.raw_text,'')); "
        "END"
    )
    bind.exec_driver_sql(
        "CREATE TRIGGER source_items_au AFTER UPDATE ON source_items BEGIN "
        "  INSERT INTO source_items_fts(source_items_fts, rowid, title, raw_text) "
        "  VALUES ('delete', old.id, COALESCE(old.title,''), COALESCE(old.raw_text,'')); "
        "  INSERT INTO source_items_fts(rowid, title, raw_text) "
        "  VALUES (new.id, COALESCE(new.title,''), COALESCE(new.raw_text,'')); "
        "END"
    )


def _downgrade_sqlite(bind) -> None:
    log.info("Dropping SQLite FTS5 index source_items_fts.")
    for stmt in (
        "DROP TRIGGER IF EXISTS source_items_au",
        "DROP TRIGGER IF EXISTS source_items_ad",
        "DROP TRIGGER IF EXISTS source_items_ai",
        "DROP TABLE IF EXISTS source_items_fts",
    ):
        bind.exec_driver_sql(stmt)


# ─── Postgres ──────────────────────────────────────────────────────────────

def _upgrade_postgres(bind) -> None:
    log.info("Adding search_tsv column + GIN index + maintenance trigger.")
    bind.exec_driver_sql(
        "ALTER TABLE source_items ADD COLUMN IF NOT EXISTS search_tsv tsvector"
    )
    # Maintenance trigger function — concatenates title + raw_text and
    # casts to tsvector with the English dictionary.
    bind.exec_driver_sql(
        """
        CREATE OR REPLACE FUNCTION source_items_tsv_update()
        RETURNS trigger AS $$
        BEGIN
            NEW.search_tsv := to_tsvector(
                'english',
                COALESCE(NEW.title, '') || ' ' || COALESCE(NEW.raw_text, '')
            );
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    # Trigger runs BEFORE INSERT/UPDATE so search_tsv is always in sync
    # without needing a separate write path.
    bind.exec_driver_sql(
        "DROP TRIGGER IF EXISTS source_items_tsv_trigger ON source_items"
    )
    bind.exec_driver_sql(
        """
        CREATE TRIGGER source_items_tsv_trigger
        BEFORE INSERT OR UPDATE OF title, raw_text ON source_items
        FOR EACH ROW EXECUTE FUNCTION source_items_tsv_update()
        """
    )
    # Backfill existing rows BEFORE index creation so the index isn't
    # constantly invalidated during the UPDATE.
    bind.exec_driver_sql(
        """
        UPDATE source_items
        SET search_tsv = to_tsvector(
            'english',
            COALESCE(title, '') || ' ' || COALESCE(raw_text, '')
        )
        WHERE search_tsv IS NULL
        """
    )
    bind.exec_driver_sql(
        "CREATE INDEX IF NOT EXISTS ix_source_items_search_tsv "
        "ON source_items USING gin(search_tsv)"
    )


def _downgrade_postgres(bind) -> None:
    log.info("Dropping search_tsv column, index, and trigger.")
    bind.exec_driver_sql(
        "DROP TRIGGER IF EXISTS source_items_tsv_trigger ON source_items"
    )
    bind.exec_driver_sql("DROP FUNCTION IF EXISTS source_items_tsv_update()")
    bind.exec_driver_sql("DROP INDEX IF EXISTS ix_source_items_search_tsv")
    bind.exec_driver_sql(
        "ALTER TABLE source_items DROP COLUMN IF EXISTS search_tsv"
    )
