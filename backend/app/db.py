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
        "relevance_keywords": "TEXT", "excluded_keywords": "TEXT", "geography_keywords": "TEXT",
        "race_level": "TEXT", "election_type": "TEXT", "district_number": "TEXT",
        "neighborhood_keywords": "TEXT", "sparse_race_mode": "INTEGER DEFAULT 0",
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
        "race_relevance_score": "INTEGER DEFAULT 0",
        "race_relevance_label": "TEXT DEFAULT 'irrelevant'",
        "source_owner_type": "TEXT DEFAULT 'unclear'",
        "source_owner_confidence": "TEXT DEFAULT 'low'",
        "relevance_reasons": "TEXT",
        "actionability_score": "INTEGER DEFAULT 0",
        "actionability_label": "TEXT DEFAULT 'ignore'",
        "content_category": "TEXT DEFAULT 'irrelevant'",
        "geo_relevance": "TEXT DEFAULT 'none'",
        "candidate_mentioned": "INTEGER DEFAULT 0",
        "opponent_mentioned": "INTEGER DEFAULT 0",
        "district_mentioned": "INTEGER DEFAULT 0",
        "priority_issue_mentioned": "INTEGER DEFAULT 0",
        "archived_as_irrelevant": "INTEGER DEFAULT 0",
        "story_cluster_id": "TEXT",
        "duplicate_of_source_id": "INTEGER",
        "extraction_quality_score": "INTEGER DEFAULT 100",
        "extraction_quality_label": "TEXT DEFAULT 'good'",
        "extraction_quality_reasons": "TEXT",
    }.items():
        if col not in existing_si:
            conn.execute(text(f"ALTER TABLE source_items ADD COLUMN {col} {col_type}"))

    # manual_source_reminders table is created by metadata.create_all; no ALTER needed
    # source_packs / source_pack_items are created by metadata.create_all
    existing_im = {row[1] for row in conn.execute(text("PRAGMA table_info(issue_mentions)"))}
    for col, col_type in {
        "link_strength": "INTEGER DEFAULT 0",
        "link_reasons": "TEXT",
    }.items():
        if col not in existing_im:
            conn.execute(text(f"ALTER TABLE issue_mentions ADD COLUMN {col} {col_type}"))

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS narratives (
            id INTEGER PRIMARY KEY,
            canonical_text TEXT NOT NULL,
            short_label VARCHAR NOT NULL,
            narrative_type VARCHAR NOT NULL,
            owner_type VARCHAR DEFAULT 'unknown',
            direction VARCHAR DEFAULT 'neutral',
            status VARCHAR DEFAULT 'emerging',
            first_seen_at DATETIME,
            last_seen_at DATETIME,
            source_cluster_count INTEGER DEFAULT 0,
            source_count INTEGER DEFAULT 0,
            messenger_diversity_count INTEGER DEFAULT 0,
            geography_count INTEGER DEFAULT 0,
            traction_score INTEGER DEFAULT 0,
            evidence_strength VARCHAR DEFAULT 'weak',
            response_status VARCHAR DEFAULT 'no_response',
            notes TEXT,
            created_at DATETIME,
            updated_at DATETIME
        )
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS narrative_mentions (
            id INTEGER PRIMARY KEY,
            narrative_id INTEGER NOT NULL,
            source_item_id INTEGER,
            opponent_activity_id INTEGER,
            source_cluster_id VARCHAR,
            matched_text TEXT,
            mention_role VARCHAR DEFAULT 'repeat',
            confidence_score INTEGER DEFAULT 50,
            created_at DATETIME,
            FOREIGN KEY(narrative_id) REFERENCES narratives(id),
            FOREIGN KEY(source_item_id) REFERENCES source_items(id),
            FOREIGN KEY(opponent_activity_id) REFERENCES opponent_activities(id)
        )
    """))
    existing_narratives = {row[1] for row in conn.execute(text("PRAGMA table_info(narratives)"))}
    for col, col_type in {
        "owner_confidence": "VARCHAR DEFAULT 'low'",
        "attribution_type": "VARCHAR DEFAULT 'unclear'",
        "target_confidence": "VARCHAR DEFAULT 'low'",
        "candidate_narrative_id": "INTEGER",
    }.items():
        if col not in existing_narratives:
            conn.execute(text(f"ALTER TABLE narratives ADD COLUMN {col} {col_type}"))
    existing_nm = {row[1] for row in conn.execute(text("PRAGMA table_info(narrative_mentions)"))}
    for col, col_type in {
        "owner_confidence": "VARCHAR DEFAULT 'low'",
        "attribution_type": "VARCHAR DEFAULT 'unclear'",
        "target_confidence": "VARCHAR DEFAULT 'low'",
        "candidate_narrative_id": "INTEGER",
    }.items():
        if col not in existing_nm:
            conn.execute(text(f"ALTER TABLE narrative_mentions ADD COLUMN {col} {col_type}"))

    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS candidate_message_libraries (
            id INTEGER PRIMARY KEY,
            campaign_config_id INTEGER,
            core_message TEXT,
            short_bio_frame TEXT,
            tone_guidance TEXT,
            created_at DATETIME,
            updated_at DATETIME,
            FOREIGN KEY(campaign_config_id) REFERENCES campaign_config(id)
        )
    """))
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS candidate_narratives (
            id INTEGER PRIMARY KEY,
            library_id INTEGER NOT NULL,
            short_label VARCHAR NOT NULL,
            canonical_text TEXT NOT NULL,
            narrative_kind VARCHAR NOT NULL,
            issue_name VARCHAR,
            preferred_phrases TEXT,
            avoid_phrases TEXT,
            must_mention_points TEXT,
            red_lines TEXT,
            priority INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1,
            created_at DATETIME,
            updated_at DATETIME,
            FOREIGN KEY(library_id) REFERENCES candidate_message_libraries(id)
        )
    """))
    conn.commit()


def init_db():
    from app import models  # noqa: F401 — registers all models with Base
    Base.metadata.create_all(bind=engine)
    with engine.connect() as conn:
        _migrate(conn)
