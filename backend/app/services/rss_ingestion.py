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


def mark_rss_feed_fetched(db, feed_url: str, ts: datetime | None = None) -> datetime:
    """Stamp `RssFeed.last_fetched_at` AND any matching `SourceMonitor.last_checked_at`.

    The two tables hold overlapping data:
      - `rss_feeds` is the canonical, actively-updated table (111 rows,
        108/111 fresh as of 2026-05-24).
      - `source_monitors` (monitor_type='rss') was a partial unification
        migration that never completed (51 rows that exactly duplicate
        51 of the 111 rss_feeds URLs — all 51 had NULL last_checked_at
        because the scheduler only wrote to the rss_feeds column).

    Until we decide which table is canonical and migrate fully, this
    helper keeps both columns in sync so the UI's monitor-health view
    isn't lying. Caller is responsible for db.commit() / rollback.

    Returns the timestamp that was written (so the caller can reuse it
    for logging or other writes).
    """
    from app.models import RssFeed, SourceMonitor
    if ts is None:
        ts = datetime.utcnow()
    # Update the canonical table by URL (caller may have already done this
    # via the ORM object — repeating is idempotent).
    db.query(RssFeed).filter(RssFeed.url == feed_url).update(
        {RssFeed.last_fetched_at: ts}, synchronize_session=False,
    )
    # Mirror to source_monitors.last_checked_at for any rss-type monitor
    # with the same URL. Update returns the row count, which we log if
    # something is matched (mostly just so a missing-monitor case isn't
    # silent).
    n = db.query(SourceMonitor).filter(
        SourceMonitor.url == feed_url,
        SourceMonitor.monitor_type == "rss",
    ).update({SourceMonitor.last_checked_at: ts}, synchronize_session=False)
    if n > 0:
        log.debug("rss_ingestion: mirrored last_fetched_at to %d source_monitor row(s) for %s", n, feed_url)
    return ts


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
                # Stamp BOTH the canonical rss_feeds column AND any mirrored
                # source_monitors row (see mark_rss_feed_fetched docstring).
                feed.last_fetched_at = mark_rss_feed_fetched(db, feed.url)
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
