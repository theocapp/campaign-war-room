from datetime import datetime
from pathlib import Path
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import declarative_base, sessionmaker

DB_PATH = Path(__file__).parent.parent / "war_room.db"
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine = create_engine(
    DATABASE_URL,
    connect_args={
        "check_same_thread": False,
        "timeout": 30,  # seconds to wait for a lock at the Python level
    },
)

@event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_conn, _record):
    cursor = dbapi_conn.cursor()
    # WAL lets readers proceed while a writer is active and queues concurrent
    # writers instead of immediately raising "database is locked".
    cursor.execute("PRAGMA journal_mode=WAL")
    # 30-second retry at the SQLite level — authoritative for lock contention.
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()

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
        "ingested_at": "DATETIME",
        "source_author": "TEXT",
    }.items():
        if col not in existing_si:
            conn.execute(text(f"ALTER TABLE source_items ADD COLUMN {col} {col_type}"))
    # Backfill ingested_at for any rows created before this column existed.
    conn.execute(text(
        "UPDATE source_items SET ingested_at = created_at WHERE ingested_at IS NULL"
    ))

    # opponents: FEC candidate ID for dedup against re-imports + name-format drift
    existing_opp = {row[1] for row in conn.execute(text("PRAGMA table_info(opponents)"))}
    if "fec_candidate_id" not in existing_opp:
        conn.execute(text("ALTER TABLE opponents ADD COLUMN fec_candidate_id TEXT"))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_opponents_fec_candidate_id "
            "ON opponents(fec_candidate_id) WHERE fec_candidate_id IS NOT NULL"
        ))

    # manual_source_reminders table is created by metadata.create_all; no ALTER needed
    # source_packs / source_pack_items are created by metadata.create_all
    existing_im = {row[1] for row in conn.execute(text("PRAGMA table_info(issue_mentions)"))}
    for col, col_type in {
        "link_strength": "INTEGER DEFAULT 0",
        "link_reasons": "TEXT",
    }.items():
        if col not in existing_im:
            conn.execute(text(f"ALTER TABLE issue_mentions ADD COLUMN {col} {col_type}"))

    # Note: `narratives`, `narrative_mentions`, `candidate_message_libraries`,
    # and `candidate_narratives` tables were created here by earlier migrations.
    # Their model classes were dropped during the narrative-frames pivot, so
    # they no longer get created on fresh DBs. Existing DBs still have the
    # tables sitting empty; intentional no-op rather than DROP — leave them.
    conn.commit()


def init_db():
    from app import models  # noqa: F401 — registers all models with Base
    Base.metadata.create_all(bind=engine)
    with engine.connect() as conn:
        _migrate(conn)
    _phase0_backfill()


def _phase0_backfill() -> None:
    """One-shot, idempotent cleanup of data written before the Phase 0 fixes.

    Two passes — both no-ops on a clean DB:
      1. Decode HTML entities + strip tags from OpponentActivity quote fields
         that still contain markup (left over before bug 2 fix).
      2. Merge duplicate Opponent rows (normalized-name collisions left over
         before bug 3 fix). Activities from the duplicate are re-pointed onto
         the canonical row with fingerprint-based dedup, then the duplicate
         is deleted.
    """
    from sqlalchemy import or_
    from app.models import Opponent, OpponentActivity
    from app.services.text_utils import strip_html_to_text
    from app.services.opponent_analysis import _activity_fingerprint
    from app.services.race_directory import _normalize_candidate_name

    with SessionLocal() as db:
        # Pass 1 — clean up encoded entities / residual tags in quote text.
        dirty = (
            db.query(OpponentActivity)
            .filter(or_(
                OpponentActivity.attack.like("%&#%"),
                OpponentActivity.attack.like("%<%"),
                OpponentActivity.claim.like("%&#%"),
                OpponentActivity.claim.like("%<%"),
                OpponentActivity.promise.like("%&#%"),
                OpponentActivity.promise.like("%<%"),
            ))
            .all()
        )
        for row in dirty:
            if row.attack:
                row.attack = strip_html_to_text(row.attack)[:500]
            if row.claim:
                row.claim = strip_html_to_text(row.claim)[:500]
            if row.promise:
                row.promise = strip_html_to_text(row.promise)[:300]
        if dirty:
            db.commit()

        # Pass 2 — merge duplicate opponents.
        opponents = db.query(Opponent).all()
        by_norm: dict[str, list[Opponent]] = {}
        for opp in opponents:
            key = _normalize_candidate_name(opp.name)
            if not key:
                continue
            by_norm.setdefault(key, []).append(opp)

        for group in by_norm.values():
            if len(group) < 2:
                continue
            # Canonical = the one whose name is human-readable
            # ("Rob Bresnahan" beats "BRESNAHAN, ROB"). If both / neither
            # have commas, prefer the one with a FEC ID stamped, then
            # whichever was created first.
            def _rank(o: Opponent) -> tuple[int, int, int]:
                has_comma = "," in (o.name or "")
                has_fec = bool(o.fec_candidate_id)
                created_ord = int((o.created_at or datetime.utcnow()).timestamp())
                return (1 if has_comma else 0, 0 if has_fec else 1, created_ord)

            group.sort(key=_rank)
            canonical = group[0]
            for dup in group[1:]:
                # Pull FEC ID from the duplicate if canonical doesn't have one.
                if dup.fec_candidate_id and not canonical.fec_candidate_id:
                    canonical.fec_candidate_id = dup.fec_candidate_id
                # Re-point activities, deduping against canonical's existing rows.
                canon_fps = {
                    _activity_fingerprint({"attack": r.attack, "claim": r.claim, "promise": r.promise})
                    for r in db.query(OpponentActivity).filter(OpponentActivity.opponent_id == canonical.id)
                }
                dup_acts = (
                    db.query(OpponentActivity)
                    .filter(OpponentActivity.opponent_id == dup.id)
                    .all()
                )
                for act in dup_acts:
                    fp = _activity_fingerprint({"attack": act.attack, "claim": act.claim, "promise": act.promise})
                    if fp in canon_fps:
                        db.delete(act)
                    else:
                        act.opponent_id = canonical.id
                        canon_fps.add(fp)
                db.flush()
                db.delete(dup)
            db.commit()
