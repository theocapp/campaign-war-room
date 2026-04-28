from pathlib import Path
from sqlalchemy import create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

DB_PATH = Path(__file__).parent.parent / "war_room.db"
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _migrate(conn) -> None:
    """Add any missing columns to existing tables (SQLite-safe ALTER TABLE)."""
    # campaign_config new columns
    existing = {row[1] for row in conn.execute(text("PRAGMA table_info(campaign_config)"))}
    for col, col_type in {
        "office": "TEXT", "location": "TEXT", "election_date": "DATETIME",
        "campaign_message": "TEXT", "key_priorities": "TEXT", "created_at": "DATETIME",
    }.items():
        if col not in existing:
            conn.execute(text(f"ALTER TABLE campaign_config ADD COLUMN {col} {col_type}"))

    # source_items review/priority columns
    existing_si = {row[1] for row in conn.execute(text("PRAGMA table_info(source_items)"))}
    for col, col_type in {
        "reviewed": "INTEGER DEFAULT 0",
        "dismissed": "INTEGER DEFAULT 0",
        "priority_score": "INTEGER DEFAULT 0",
        "review_note": "TEXT",
        "evidence_score": "INTEGER DEFAULT 50",
        "credibility_score": "INTEGER DEFAULT 50",
    }.items():
        if col not in existing_si:
            conn.execute(text(f"ALTER TABLE source_items ADD COLUMN {col} {col_type}"))

    # manual_source_reminders table is created by metadata.create_all; no ALTER needed
    # source_packs / source_pack_items are created by metadata.create_all
    conn.commit()


def init_db():
    from app import models  # noqa: F401 — registers all models with Base
    Base.metadata.create_all(bind=engine)
    with engine.connect() as conn:
        _migrate(conn)
