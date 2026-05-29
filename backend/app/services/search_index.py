"""Dialect-aware full-text search index for source_items.

Bridges SQLite FTS5 and Postgres tsvector behind one interface so the route
layer stays dialect-agnostic. Two callers:

  ensure_search_index(engine)
      Idempotent setup. Called from init_db() on app boot. On SQLite,
      creates the source_items_fts virtual table + 3 sync triggers (the
      historical _migrate() block; pre-existing live DBs already have it).
      On Postgres, no-op — schema for search_tsv + GIN + trigger lives in
      an Alembic migration.

  search_articles(db, query, limit) -> list[int]
      Returns source_item ids ordered best-match first.

NOTE — this is a COMPATIBILITY layer, NOT the long-term search architecture.
SQLite FTS5 and Postgres tsvector have different stemming, tokenization,
and ranking. Search results will not be identical pre/post-cutover. See
POSTGRES_MIGRATION_PLAN.md — when retrieval quality starts mattering more
than operational simplicity, swap to Tantivy / OpenSearch / Meilisearch as
a separate project.
"""
from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)


def ensure_search_index(engine: Engine) -> None:
    """Bring the search index to existence if missing. Safe on every boot."""
    dialect = engine.dialect.name
    if dialect == "sqlite":
        _ensure_sqlite_fts(engine)
    elif dialect == "postgresql":
        # Postgres setup is owned by the Alembic migration so the
        # search_tsv column + GIN index + trigger are created in a single
        # transactional unit, not lazily at boot.
        return
    else:
        log.warning(
            "search_index: unsupported dialect %r — search will fall back to ILIKE",
            dialect,
        )


def search_articles(db: Session, query: str, limit: int) -> list[int]:
    """Return ordered source_item ids matching `query`, best match first.

    Multi-word queries try phrase match first, then fall back to OR of
    individual tokens so users always see results for at least one word.
    """
    if not query or not query.strip():
        return []
    dialect = db.bind.dialect.name
    if dialect == "sqlite":
        return _search_sqlite(db, query, limit)
    if dialect == "postgresql":
        return _search_postgres(db, query, limit)
    return _search_ilike_fallback(db, query, limit)


# ─── SQLite (FTS5) ────────────────────────────────────────────────────────

def _ensure_sqlite_fts(engine: Engine) -> None:
    with engine.connect() as conn:
        exists = conn.execute(text(
            "SELECT count(*) FROM sqlite_master "
            "WHERE type='table' AND name='source_items_fts'"
        )).scalar()
        if exists:
            return
        log.info("Creating SQLite FTS5 index for source_items.")
        conn.execute(text(
            "CREATE VIRTUAL TABLE source_items_fts "
            "USING fts5(title, raw_text, content='source_items', content_rowid='id')"
        ))
        conn.execute(text(
            "INSERT INTO source_items_fts(rowid, title, raw_text) "
            "SELECT id, COALESCE(title,''), COALESCE(raw_text,'') FROM source_items"
        ))
        conn.execute(text(
            "CREATE TRIGGER source_items_ai AFTER INSERT ON source_items BEGIN "
            "  INSERT INTO source_items_fts(rowid, title, raw_text) "
            "  VALUES (new.id, COALESCE(new.title,''), COALESCE(new.raw_text,'')); "
            "END"
        ))
        conn.execute(text(
            "CREATE TRIGGER source_items_ad AFTER DELETE ON source_items BEGIN "
            "  INSERT INTO source_items_fts(source_items_fts, rowid, title, raw_text) "
            "  VALUES ('delete', old.id, COALESCE(old.title,''), COALESCE(old.raw_text,'')); "
            "END"
        ))
        conn.execute(text(
            "CREATE TRIGGER source_items_au AFTER UPDATE ON source_items BEGIN "
            "  INSERT INTO source_items_fts(source_items_fts, rowid, title, raw_text) "
            "  VALUES ('delete', old.id, COALESCE(old.title,''), COALESCE(old.raw_text,'')); "
            "  INSERT INTO source_items_fts(rowid, title, raw_text) "
            "  VALUES (new.id, COALESCE(new.title,''), COALESCE(new.raw_text,'')); "
            "END"
        ))
        conn.commit()


def _search_sqlite(db: Session, query: str, limit: int) -> list[int]:
    safe = query.strip().replace('"', '""')

    def _run(fts_q: str) -> list[int]:
        rows = db.execute(
            text(
                "SELECT rowid FROM source_items_fts "
                "WHERE source_items_fts MATCH :q "
                "ORDER BY rank LIMIT :lim"
            ),
            {"q": fts_q, "lim": limit},
        ).fetchall()
        return [r[0] for r in rows]

    ids = _run(f'"{safe}"')
    if ids:
        return ids
    tokens = [t.replace('"', "") for t in safe.split() if t]
    if not tokens:
        return []
    return _run(" OR ".join(f'"{t}"' for t in tokens))


# ─── Postgres (tsvector + GIN) ────────────────────────────────────────────

def _search_postgres(db: Session, query: str, limit: int) -> list[int]:
    """`websearch_to_tsquery` is the user-friendly query parser: it handles
    quoted phrases, OR, and negation with `-` automatically, so we don't have
    to mirror the SQLite phrase→token fallback by hand.
    """
    rows = db.execute(
        text(
            "SELECT id FROM source_items "
            "WHERE search_tsv @@ websearch_to_tsquery('english', :q) "
            "ORDER BY ts_rank(search_tsv, websearch_to_tsquery('english', :q)) DESC "
            "LIMIT :lim"
        ),
        {"q": query.strip(), "lim": limit},
    ).fetchall()
    return [r[0] for r in rows]


# ─── ILIKE fallback (unsupported dialects) ────────────────────────────────

def _search_ilike_fallback(db: Session, query: str, limit: int) -> list[int]:
    rows = db.execute(
        text(
            "SELECT id FROM source_items "
            "WHERE LOWER(title) LIKE :pat OR LOWER(raw_text) LIKE :pat "
            "LIMIT :lim"
        ),
        {"pat": f"%{query.strip().lower()}%", "lim": limit},
    ).fetchall()
    return [r[0] for r in rows]
