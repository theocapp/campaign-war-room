"""Database engine + session factory + boot-time DB setup.

Dialect-aware: reads `DATABASE_URL` from env. SQLite-specific pragmas and
`check_same_thread` are only attached when the URL points at SQLite. The
same code paths work against either dialect — see
POSTGRES_MIGRATION_PLAN.md for the migration phases.

Schema management lives in Alembic (`backend/alembic/`). `init_db()` calls
`alembic upgrade head` programmatically so dev/test runs don't need a
separate shell step. The legacy `_migrate()` block (raw ALTER TABLE + FTS
creation) has been retired; pre-existing live DBs are stamped at the
baseline revision and pick up future changes via Alembic versions.
"""
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import declarative_base, sessionmaker

# Load .env before reading DATABASE_URL — main.py imports this module before
# any service module triggers dotenv loading, so without this the .env-defined
# URL is invisible and we silently fall back to the SQLite default.
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent.parent / ".env")
except ImportError:
    pass

log = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "war_room.db"
DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DB_PATH}")
# Parse via SQLAlchemy so the `+driver` qualifier ("postgresql+psycopg://")
# normalizes correctly — a naive `startswith("postgresql:")` check returns
# False on the real URL and silently disables the dialect-conditional code
# (connect listener, connect_args). Bug fixed 2026-05-29.
#
# `get_backend_name()` returns the literal scheme prefix ("postgres" for the
# legacy `postgres://` alias, "postgresql" for the canonical form). Accept
# both — matches the original intent of `startswith(("postgresql:", "postgres:"))`.
_backend = make_url(DATABASE_URL).get_backend_name()
_IS_SQLITE = _backend == "sqlite"
_IS_POSTGRES = _backend in ("postgresql", "postgres")

# Pool sized for the parallel rescore worker pool: 16 LLM keys × 2 sessions
# (title read + scoring write) + headroom for the API and scheduler.
_engine_kwargs: dict = {
    "pool_size": 20,
    "max_overflow": 40,
    "pool_timeout": 60,
}
if _IS_SQLITE:
    _engine_kwargs["connect_args"] = {
        # We use the engine from multiple threads (scheduler, request
        # handlers, background workers). SQLite's default thread guard
        # would refuse that.
        "check_same_thread": False,
        # Seconds to wait for a write lock at the Python level.
        "timeout": 30,
    }

engine = create_engine(DATABASE_URL, **_engine_kwargs)

if _IS_SQLITE:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        # WAL lets readers proceed while a writer is active and queues
        # concurrent writers instead of immediately raising "database is
        # locked".
        cursor.execute("PRAGMA journal_mode=WAL")
        # 30-second retry at the SQLite level — authoritative for lock
        # contention.
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

if _IS_POSTGRES:
    @event.listens_for(engine, "connect")
    def _set_postgres_session_defaults(dbapi_conn, _record):
        """Per-connection safety knobs.

        statement_timeout — kill any single SQL statement that runs longer
            than 60s. Normal API queries should be under a second; long
            analytics or background jobs can override per-session via
            `SET LOCAL statement_timeout = '...'`.
        lock_timeout — fail fast on contention instead of stalling the
            request. Lock pile-ups become visible quickly.
        idle_in_transaction_session_timeout — kill sessions that hold a
            transaction open and idle for 30 minutes. Prevents the
            "long-running transaction blocks autovacuum" footgun.

            Sized to the rescore worker pattern: `_process_item` opens a
            session (which begins a tx on first SELECT) and then makes
            LLM calls inside it. Provider rate-limit retries can sleep up
            to 5 min × 3 attempts = 15 min, plus 2-3 min for the LLM call
            itself. 30 min leaves headroom while still catching truly
            leaked transactions.

            Alembic migrations clear this (and statement_timeout) to 0
            in `backend/alembic/env.py` — a long backfill migration must
            never be killed by these defaults.
        """
        with dbapi_conn.cursor() as cur:
            cur.execute("SET statement_timeout = '60s'")
            cur.execute("SET lock_timeout = '10s'")
            cur.execute("SET idle_in_transaction_session_timeout = '30min'")


# ── Pool observability ────────────────────────────────────────────────────
# Track basic counters and the most recent pool-stress events so the
# /api/admin/dbstats endpoint can surface what's happening without us
# having to tail logs.

@dataclass
class _PoolStats:
    connects: int = 0
    checkouts: int = 0
    checkins: int = 0
    invalidations: int = 0
    last_invalidate_at: datetime | None = None
    last_invalidate_reason: str | None = None
    recent_slow_checkouts: list[dict] = field(default_factory=list)


pool_stats = _PoolStats()


@event.listens_for(engine, "connect")
def _track_connect(*_):
    pool_stats.connects += 1


@event.listens_for(engine, "checkout")
def _track_checkout(*_):
    pool_stats.checkouts += 1


@event.listens_for(engine, "checkin")
def _track_checkin(*_):
    pool_stats.checkins += 1


@event.listens_for(engine, "invalidate")
def _track_invalidate(_dbapi_conn, _record, exception):
    pool_stats.invalidations += 1
    pool_stats.last_invalidate_at = datetime.utcnow()
    pool_stats.last_invalidate_reason = (
        f"{type(exception).__name__}: {exception}" if exception else "unknown"
    )
    log.warning(
        "DB pool connection invalidated (total=%d, reason=%s)",
        pool_stats.invalidations, pool_stats.last_invalidate_reason,
    )


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Bring the database to current state and run idempotent seed/repair.

    Order matters:
      1. Apply pending Alembic migrations (schema + dialect-conditional
         data fixes). Equivalent to `alembic upgrade head`.
      2. Make sure the dialect-appropriate search index exists. SQLite
         creates the FTS5 virtual table here if missing; Postgres no-ops
         (its setup runs inside the Alembic migration).
      3. Run data-level seed and repair passes. Each is idempotent.
    """
    from app import models  # noqa: F401 — registers all models with Base

    _alembic_upgrade_head()

    # Search index — dialect-dispatch.
    from app.services.search_index import ensure_search_index
    ensure_search_index(engine)

    _phase0_backfill()
    _repair_frame_data()
    _backfill_outlet_links()
    _seed_canonical_entities()
    _seed_race_sentiment_sources()


def _alembic_upgrade_head() -> None:
    """Apply any pending Alembic migrations programmatically.

    Used at app boot so devs running `uvicorn` don't have to remember a
    separate `alembic upgrade head` step. Alembic acquires an internal
    lock during the migration run, so it's safe even if the app boots
    while a manual `alembic` invocation is in flight.

    If migrations fail, this raises and `init_db()` bubbles the failure
    up — the app refuses to start against a stale schema rather than
    serving bad data.
    """
    from alembic import command
    from alembic.config import Config

    backend_root = Path(__file__).resolve().parent.parent
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    # alembic/env.py resolves the URL from `DATABASE_URL` env var, falling
    # back to the same SQLite path this module uses. Stays in sync.
    log.info("Running alembic upgrade head (target=%s)", DATABASE_URL)
    command.upgrade(cfg, "head")


def _seed_race_sentiment_sources() -> None:
    """Seed the six default race-sentiment sources if they don't yet exist.

    Idempotent — only inserts rows whose `source` slug is missing. Existing
    rows (with user-entered values) are never touched.

    Rating connectors (Cook, Sabato, Inside Elections, DDHQ) are auto-configured
    at startup from CampaignConfig.district — no network calls required, since
    their external_id is a fixed URL and only the district label in metadata varies.

    Market connectors (Polymarket, Kalshi) require a network call to discover
    the right market ticker/slug, so they are configured lazily on first sync
    via sync_one() in race_sentiment_sync.py.
    """
    import json

    defaults = [
        ("polymarket",       "market", "Polymarket"),
        ("kalshi",           "market", "Kalshi"),
        ("cook",             "rating", "Cook Political Report"),
        ("sabato",           "rating", "Sabato's Crystal Ball"),
        ("inside_elections", "rating", "Inside Elections"),
    ]

    # Fixed URLs per rating source. external_id is the *fetcher target*
    # (what we GET when syncing); source_url is the *click-through link*
    # shown to the user in the dashboard, so it always points to the
    # authoritative source — even when we're sourcing the data via a
    # mirror. Cook and Sabato are sourced via 270toWin because their
    # own sites are Cloudflare-blocked to direct scraping.
    #
    # NOTE: the 270toWin URLs are election-year-specific. Refresh when
    # the cycle rolls over to 2028, or template the year from
    # CampaignConfig.election_date if multi-cycle deployments matter.
    _RATING_FETCH_URLS = {
        "cook":             "https://www.270towin.com/2026-house-election/cook-political-report-2026-house-ratings",
        "sabato":           "https://www.270towin.com/2026-house-election/crystal-ball-2026-house-forecast",
        "inside_elections": "https://insideelections.com/ratings/house",
    }
    _RATING_DISPLAY_URLS = {
        "cook":             "https://www.cookpolitical.com/ratings/house-race-ratings",
        "sabato":           "https://centerforpolitics.org/crystalball/",
        "inside_elections": "https://insideelections.com/ratings/house",
    }

    with SessionLocal() as db:
        from app.models import RaceSentiment, CampaignConfig
        config = db.query(CampaignConfig).first()
        district = ((config.district if config else None) or "").strip().upper()
        existing = {row.source: row for row in db.query(RaceSentiment).all()}
        added = 0
        configured = 0
        for slug, kind, display in defaults:
            if slug not in existing:
                row = RaceSentiment(source=slug, source_type=kind, display_name=display)
                db.add(row)
                db.flush()
                existing[slug] = row
                added += 1
            row = existing[slug]

            # Auto-configure rating sources from district (no network needed).
            if row.external_id is None and slug in _RATING_FETCH_URLS and district and "-" in district:
                state, _, district_num = district.partition("-")
                row.external_id = _RATING_FETCH_URLS[slug]
                row.source_url = _RATING_DISPLAY_URLS[slug]
                row.external_metadata = json.dumps({
                    "district_label": district,
                    "state": state,
                    "district_number": district_num.lstrip("0") or "0",
                })
                configured += 1

        if added or configured:
            db.commit()


def _seed_canonical_entities() -> None:
    """Load the per-race canonical entity seed file into the `entities` table.

    Idempotent: only inserts entities whose canonical_id is not already
    present. Safe to run on every startup.

    Reads the seed file matching the current campaign's district:
      backend/data/canonical_entities.<DISTRICT>.json

    No-op if no campaign is configured or no seed file exists for that
    district. The extraction pipeline still works without seed (every
    entity gets auto-discovered) — seed just reduces dedup work.
    """
    import json
    from pathlib import Path

    with SessionLocal() as db:
        from app.models import CampaignConfig, Entity
        config = db.query(CampaignConfig).first()
        if not config or not config.district:
            return
        seed_path = (Path(__file__).resolve().parent.parent /
                     "data" / f"canonical_entities.{config.district}.json")
        if not seed_path.exists():
            return
        try:
            with seed_path.open() as f:
                seed = json.load(f)
        except Exception:
            return

        # Use a dict of existing seeded entities so we can refresh aliases.
        existing_seeded = {
            row.canonical_id: row
            for row in db.query(Entity).filter(Entity.seeded == True).all()  # noqa: E712
        }
        added = 0
        updated = 0
        for ent in seed.get("entities", []):
            cid = ent.get("canonical_id")
            if not cid:
                continue
            # Type-specific fields are nested under metadata_json so we don't
            # need a per-type table. Extract the known core fields and stash
            # the rest as JSON metadata.
            core_keys = {"canonical_id", "type", "name", "aliases",
                         "description", "affiliation"}
            metadata = {k: v for k, v in ent.items() if k not in core_keys}
            aliases_json = json.dumps(ent.get("aliases", []))
            md_json = json.dumps(metadata) if metadata else None

            if cid in existing_seeded:
                # Refresh aliases / description / metadata from seed file
                # (in case the user added new aliases). Don't touch
                # mention_count / first_seen / last_seen (those are
                # extraction-managed).
                row = existing_seeded[cid]
                changed = False
                if row.aliases != aliases_json:
                    row.aliases = aliases_json
                    changed = True
                if row.description != ent.get("description"):
                    row.description = ent.get("description")
                    changed = True
                if row.affiliation != ent.get("affiliation"):
                    row.affiliation = ent.get("affiliation")
                    changed = True
                if (row.metadata_json or None) != md_json:
                    row.metadata_json = md_json
                    changed = True
                if changed:
                    updated += 1
            else:
                db.add(Entity(
                    canonical_id=cid,
                    type=ent.get("type", ""),
                    name=ent.get("name", ""),
                    aliases=aliases_json,
                    description=ent.get("description"),
                    affiliation=ent.get("affiliation"),
                    metadata_json=md_json,
                    seeded=True,
                ))
                added += 1
        if added or updated:
            db.commit()


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
