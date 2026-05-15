"""
Shared RSS ingestion service.

Used by both the manual /api/rss-feeds/ingest-all endpoint and the
background scheduler so that ingestion logic is never duplicated and
both paths share the same concurrency lock.
"""
import logging
import threading
from dataclasses import dataclass
from datetime import datetime

log = logging.getLogger(__name__)

# Shared threading lock — both the scheduled job (running in a thread-pool
# via asyncio.to_thread) and the sync route handler (running in FastAPI's
# thread-pool) acquire this before touching the DB.  Non-blocking acquire
# lets callers decide whether to skip or wait.
ingest_lock = threading.Lock()


@dataclass
class IngestAllResult:
    feeds_processed: int
    total_added: int
    total_skipped: int
    total_errors: int


def ingest_all_active_rss_feeds() -> IngestAllResult:
    """
    Ingest every active RSS feed.  Creates and closes its own DB session so
    it can be called from a background thread without FastAPI's DI context.
    Callers are responsible for holding ``ingest_lock`` before calling this.
    """
    from app.db import SessionLocal
    from app.models import RssFeed
    from app.services import ingestion

    db = SessionLocal()
    try:
        feeds = db.query(RssFeed).filter_by(active=True).all()
        total_added = total_skipped = total_errors = 0

        for feed in feeds:
            try:
                r = ingestion.ingest_rss(db, feed.url, feed.name)
                feed.last_fetched_at = datetime.utcnow()
                total_added += r.added
                total_skipped += r.skipped
                log.info(
                    "RSS ingested: %r  added=%d  skipped=%d",
                    feed.name, r.added, r.skipped,
                )
            except Exception:
                log.exception("Failed to ingest RSS feed %r (%s)", feed.name, feed.url)
                total_errors += 1

        db.commit()
        return IngestAllResult(
            feeds_processed=len(feeds),
            total_added=total_added,
            total_skipped=total_skipped,
            total_errors=total_errors,
        )
    finally:
        db.close()


def try_ingest_all_rss(*, skip_if_locked: bool = True) -> IngestAllResult | None:
    """
    Acquire ``ingest_lock`` and run :func:`ingest_all_active_rss_feeds`.

    Returns ``None`` without doing anything when ``skip_if_locked=True`` and
    another ingestion run is already in progress.  When ``skip_if_locked=False``
    it blocks until the lock is free (manual-endpoint behaviour).
    """
    acquired = ingest_lock.acquire(blocking=not skip_if_locked)
    if not acquired:
        log.warning("RSS ingestion already running — skipping this trigger")
        return None
    try:
        return ingest_all_active_rss_feeds()
    finally:
        ingest_lock.release()
