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
    # Sized for the parallel rescore worker pool: 16 LLM keys × 2 sessions
    # (title read + scoring write) + headroom for the API and scheduler.
    pool_size=20,
    max_overflow=40,
    pool_timeout=60,
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
        "extended_backfill_completed": "INTEGER DEFAULT 0",
        "trends_keywords": "TEXT",
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
        "sentiment": "TEXT",
        "structured_extraction": "TEXT",
        "gdelt_themes": "TEXT",
    }.items():
        if col not in existing_si:
            conn.execute(text(f"ALTER TABLE source_items ADD COLUMN {col} {col_type}"))
    # Backfill ingested_at for any rows created before this column existed.
    conn.execute(text(
        "UPDATE source_items SET ingested_at = created_at WHERE ingested_at IS NULL"
    ))

    # FTS5 full-text search over source_items(title, raw_text).
    # Guarded by sqlite_master check — idempotent on subsequent startups.
    fts_exists = conn.execute(text(
        "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='source_items_fts'"
    )).scalar()
    if not fts_exists:
        conn.execute(text(
            "CREATE VIRTUAL TABLE source_items_fts "
            "USING fts5(title, raw_text, content='source_items', content_rowid='id')"
        ))
        # Populate FTS5 with all existing rows.
        conn.execute(text(
            "INSERT INTO source_items_fts(rowid, title, raw_text) "
            "SELECT id, COALESCE(title,''), COALESCE(raw_text,'') FROM source_items"
        ))
        # Triggers to keep FTS5 in sync with source_items.
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

    # opponents: FEC candidate ID for dedup against re-imports + name-format drift
    existing_opp = {row[1] for row in conn.execute(text("PRAGMA table_info(opponents)"))}
    if "fec_candidate_id" not in existing_opp:
        conn.execute(text("ALTER TABLE opponents ADD COLUMN fec_candidate_id TEXT"))
        conn.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_opponents_fec_candidate_id "
            "ON opponents(fec_candidate_id) WHERE fec_candidate_id IS NOT NULL"
        ))

    # outlets table is created by metadata.create_all; add outlet_id FK to source_items
    existing_si2 = {row[1] for row in conn.execute(text("PRAGMA table_info(source_items)"))}
    if "outlet_id" not in existing_si2:
        conn.execute(text("ALTER TABLE source_items ADD COLUMN outlet_id INTEGER REFERENCES outlets(id)"))

    # outlets: rss_url + districts columns (user-managed outlet catalog)
    existing_out = {row[1] for row in conn.execute(text("PRAGMA table_info(outlets)"))}
    if "rss_url" not in existing_out:
        conn.execute(text("ALTER TABLE outlets ADD COLUMN rss_url TEXT"))
    if "districts" not in existing_out:
        conn.execute(text("ALTER TABLE outlets ADD COLUMN districts TEXT"))
    if "monthly_visitors" not in existing_out:
        conn.execute(text("ALTER TABLE outlets ADD COLUMN monthly_visitors INTEGER"))

    # manual_source_reminders table is created by metadata.create_all; no ALTER needed
    # source_packs / source_pack_items are created by metadata.create_all
    existing_im = {row[1] for row in conn.execute(text("PRAGMA table_info(issue_mentions)"))}
    for col, col_type in {
        "link_strength": "INTEGER DEFAULT 0",
        "link_reasons": "TEXT",
    }.items():
        if col not in existing_im:
            conn.execute(text(f"ALTER TABLE issue_mentions ADD COLUMN {col} {col_type}"))

    # narrative_frame_mentions: extracted_text stores the specific claim extracted from an article
    existing_nfm = {row[1] for row in conn.execute(text("PRAGMA table_info(narrative_frame_mentions)"))}
    if "extracted_text" not in existing_nfm:
        conn.execute(text("ALTER TABLE narrative_frame_mentions ADD COLUMN extracted_text TEXT"))
    # claim_meta: JSON blob with claim_type, actor, intensity, temporal, attribution,
    # rebuttal_quote, etc. — full extracted claim metadata from v2 scoring.
    if "claim_meta" not in existing_nfm:
        conn.execute(text("ALTER TABLE narrative_frame_mentions ADD COLUMN claim_meta TEXT"))

    # source_items: source_credibility (high|medium|low) — assessed by LLM per article
    existing_si3 = {row[1] for row in conn.execute(text("PRAGMA table_info(source_items)"))}
    if "source_credibility" not in existing_si3:
        conn.execute(text(
            "ALTER TABLE source_items ADD COLUMN source_credibility TEXT DEFAULT 'medium'"
        ))
    # gdelt_tone: JSON blob of GDELT V2Tone fields (avg_tone, positive, negative,
    # polarity, etc.) when the article came from BigQuery backfill.
    if "gdelt_tone" not in existing_si3:
        conn.execute(text("ALTER TABLE source_items ADD COLUMN gdelt_tone TEXT"))

    # narrative_frames: last_known_stage + last_stage_check_at for
    # transition detection. See get_frames_with_counts for the logic.
    existing_nf = {row[1] for row in conn.execute(text("PRAGMA table_info(narrative_frames)"))}
    if "last_known_stage" not in existing_nf:
        conn.execute(text("ALTER TABLE narrative_frames ADD COLUMN last_known_stage TEXT"))
    if "last_stage_check_at" not in existing_nf:
        conn.execute(text("ALTER TABLE narrative_frames ADD COLUMN last_stage_check_at DATETIME"))

    # frame_stage_history is created by Base.metadata.create_all (model
    # defined in models.py) — just ensure the index exists.
    fsh_exists = conn.execute(text(
        "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='frame_stage_history'"
    )).scalar()
    if fsh_exists:
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_fsh_frame_transitioned "
            "ON frame_stage_history(frame_id, transitioned_at)"
        ))

    # frame_variants table is created by Base.metadata.create_all — index only.
    fv_exists = conn.execute(text(
        "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='frame_variants'"
    )).scalar()
    if fv_exists:
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_fv_frame_id ON frame_variants(frame_id)"
        ))

    # narrative_frame_mentions: variant_id + quote_embedding columns
    existing_nfm2 = {row[1] for row in conn.execute(text("PRAGMA table_info(narrative_frame_mentions)"))}
    if "variant_id" not in existing_nfm2:
        conn.execute(text(
            "ALTER TABLE narrative_frame_mentions ADD COLUMN variant_id INTEGER "
            "REFERENCES frame_variants(id)"
        ))
    if "quote_embedding" not in existing_nfm2:
        conn.execute(text("ALTER TABLE narrative_frame_mentions ADD COLUMN quote_embedding TEXT"))

    # ── Cluster-native tables (Phase A) ───────────────────────────────────────
    # The tables themselves are created by Base.metadata.create_all (called
    # before _migrate). This block adds the supporting indexes that aren't
    # expressed in the ORM definitions.
    cluster_tables_exist = conn.execute(text(
        "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='story_clusters'"
    )).scalar()
    if cluster_tables_exist:
        for stmt in (
            "CREATE INDEX IF NOT EXISTS ix_story_clusters_last_seen_at ON story_clusters(last_seen_at)",
            "CREATE INDEX IF NOT EXISTS ix_story_clusters_first_seen_at ON story_clusters(first_seen_at)",
            "CREATE INDEX IF NOT EXISTS ix_story_clusters_representative ON story_clusters(representative_source_item_id)",
            "CREATE INDEX IF NOT EXISTS ix_story_clusters_simhash_lastseen ON story_clusters(simhash_64, last_seen_at)",
            "CREATE INDEX IF NOT EXISTS ix_fcm_frame_id ON frame_cluster_matches(frame_id)",
            "CREATE INDEX IF NOT EXISTS ix_fcm_cluster_id ON frame_cluster_matches(story_cluster_id)",
            "CREATE INDEX IF NOT EXISTS ix_fcm_first_seen ON frame_cluster_matches(first_seen_at)",
            "CREATE INDEX IF NOT EXISTS ix_coa_opponent_id ON cluster_opponent_activities(opponent_id)",
            "CREATE INDEX IF NOT EXISTS ix_coa_cluster_id ON cluster_opponent_activities(story_cluster_id)",
        ):
            conn.execute(text(stmt))

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
    _repair_frame_data()
    _backfill_outlet_links()


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


def _repair_frame_data() -> None:
    """Clean hallucinated quotes and leaked prompt scaffolding from frame data."""
    from app.services.narrative_frames import repair_frame_data
    with SessionLocal() as db:
        repair_frame_data(db)


def _backfill_outlet_links() -> None:
    """Link existing source items to outlets by domain."""
    from app.services.outlet_linking import backfill_outlet_links
    with SessionLocal() as db:
        backfill_outlet_links(db)
