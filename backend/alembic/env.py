"""Alembic environment.

Database URL resolution mirrors backend/app/db.py:
  1. `--url` / `-x url=...` from the alembic command line
  2. `DATABASE_URL` from env (loads .env from project root + backend/)
  3. Falls back to the SQLite path computed from DB_PATH

So `alembic upgrade head` with no extra config always points at the
same database the running app uses. To run a migration against a
different target (e.g. a scratch Postgres) without changing .env:

    DATABASE_URL=postgresql+psycopg://... alembic upgrade head
"""
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

_BACKEND = Path(__file__).resolve().parent.parent
_PROJECT_ROOT = _BACKEND.parent

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
_env_url = os.environ.get("DATABASE_URL")
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
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
