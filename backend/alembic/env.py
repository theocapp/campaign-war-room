"""Alembic environment.

Database URL resolution mirrors backend/app/db.py:
  1. `--url` / `-x url=...` from the alembic command line
  2. `DATABASE_URL` from env (loads .env from project root + backend/)
  3. Falls back to the SQLite path computed from DB_PATH

So `alembic upgrade head` with no extra config always points at the
same database the running app uses. To run a migration against a
different target (e.g. a scratch Postgres) without changing .env, use
EITHER form:

    alembic -x url=postgresql+psycopg://... upgrade head     # most explicit
    DATABASE_URL=postgresql+psycopg://... alembic upgrade head

NOTE: the root .env is loaded with override=True (so it wins over
backend/.env), which would otherwise clobber an exported DATABASE_URL.
We capture the exported value FIRST so the second form above keeps
working — without that, `DATABASE_URL=...scratch alembic` silently runs
against whatever .env points at (i.e. the LIVE db). The `-x url=` form is
immune regardless.
"""
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

_BACKEND = Path(__file__).resolve().parent.parent
_PROJECT_ROOT = _BACKEND.parent

# Capture an explicitly-exported DATABASE_URL before load_dotenv(override=True)
# overwrites it, so `DATABASE_URL=...scratch alembic ...` targets the scratch
# DB instead of silently falling through to the live .env value.
_explicit_db_url = os.environ.get("DATABASE_URL")

# Load .env files. Root wins over backend/.env if both define a key —
# matches main.py's load_dotenv order.
try:
    from dotenv import load_dotenv

    load_dotenv(_BACKEND / ".env")
    load_dotenv(_PROJECT_ROOT / ".env", override=True)
except ImportError:
    pass

sys.path.insert(0, str(_BACKEND))

from app.db import Base, DB_PATH  # noqa: E402
import app.models  # noqa: E402,F401  — register models with Base

config = context.config

_cli_url = context.get_x_argument(as_dictionary=True).get("url")
_env_url = _explicit_db_url or os.environ.get("DATABASE_URL")
_resolved_url = _cli_url or _env_url or f"sqlite:///{DB_PATH}"
config.set_main_option("sqlalchemy.url", _resolved_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _is_sqlite() -> bool:
    return _resolved_url.startswith("sqlite:")


def run_migrations_offline() -> None:
    context.configure(
        url=_resolved_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=_is_sqlite(),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=_is_sqlite(),
        )
        with context.begin_transaction():
            # `app/db.py` attaches a connect listener that sets a 60s
            # `statement_timeout` and a 30min `idle_in_transaction_session_timeout`
            # on every new Postgres session. Today Alembic builds its own
            # engine so that listener doesn't fire here — but if init_db()
            # ever shares the app engine, or the listener is moved to a
            # class-level hook, those defaults would kill any migration
            # that rewrites or backfills a large table.
            #
            # SET LOCAL applies for the duration of this transaction only,
            # which is exactly the scope we want for migration safety. It
            # also has to live INSIDE context.begin_transaction() — putting
            # the SETs before it triggers SQLAlchemy 2.x's autobegin on a
            # bare statement, which then conflicts with Alembic's
            # transaction wrapper and causes the entire migration tx to
            # ROLLBACK silently on connection exit (the migration appears
            # to succeed in the log but no DDL persists). Diagnosed
            # 2026-05-29 after the featured_appearances migration kept
            # rolling back; the alembic_version UPDATE was visible in the
            # SQL trace immediately before the ROLLBACK.
            if connection.dialect.name == "postgresql":
                connection.exec_driver_sql("SET LOCAL statement_timeout = 0")
                connection.exec_driver_sql(
                    "SET LOCAL idle_in_transaction_session_timeout = 0"
                )
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
