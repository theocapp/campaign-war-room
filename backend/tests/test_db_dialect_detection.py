"""Regression tests for `app.db` dialect detection.

The original implementation used `DATABASE_URL.startswith("postgresql:")`,
which returns False on `postgresql+psycopg://...` because the next character
after "postgresql" is "+", not ":". That silently disabled the
`_set_postgres_session_defaults` connect listener — so `statement_timeout`,
`lock_timeout`, and `idle_in_transaction_session_timeout` were never applied
to the production database despite the listener being defined.

These tests use `make_url(...).get_backend_name()` directly so they don't
depend on importing `app.db` (which builds a real engine against DATABASE_URL
at import time).
"""
from __future__ import annotations

from sqlalchemy.engine.url import make_url


def _is_postgres(url: str) -> bool:
    return make_url(url).get_backend_name() in ("postgresql", "postgres")


def _is_sqlite(url: str) -> bool:
    return make_url(url).get_backend_name() == "sqlite"


def test_postgres_with_psycopg_driver_qualifier_detected():
    # The actual production URL — the failure mode that motivated this test.
    assert _is_postgres("postgresql+psycopg://theo@localhost:5432/noctua")


def test_postgres_with_psycopg2_driver_qualifier_detected():
    assert _is_postgres("postgresql+psycopg2://u:p@host/db")


def test_postgres_bare_scheme_detected():
    assert _is_postgres("postgresql://theo@localhost:5432/noctua")


def test_postgres_legacy_scheme_alias_detected():
    # `postgres://` (without the "ql") is a common legacy alias; SQLAlchemy
    # normalizes it to postgresql.
    assert _is_postgres("postgres://u:p@host/db")


def test_sqlite_with_relative_path_detected():
    assert _is_sqlite("sqlite:///war_room.db")


def test_sqlite_with_absolute_path_detected():
    assert _is_sqlite("sqlite:////var/lib/war_room.db")


def test_sqlite_with_pysqlite_driver_qualifier_detected():
    assert _is_sqlite("sqlite+pysqlite:///war_room.db")


def test_app_db_module_flags_match_url():
    """End-to-end: confirm the module-level flags in `app.db` reflect
    whatever DATABASE_URL the env resolved to. Catches future regressions
    where the parser is swapped back to a naive prefix match."""
    from app import db

    expected = make_url(db.DATABASE_URL).get_backend_name()
    if expected in ("postgresql", "postgres"):
        assert db._IS_POSTGRES is True
        assert db._IS_SQLITE is False
    elif expected == "sqlite":
        assert db._IS_SQLITE is True
        assert db._IS_POSTGRES is False
