"""
Background scheduler for automated RSS ingestion.

Controlled by two environment variables (set in .env or shell):

    RSS_AUTO_INGEST_ENABLED          true | false   (default: true)
    RSS_AUTO_INGEST_INTERVAL_MINUTES integer        (default: 60)

The scheduler uses APScheduler's AsyncIOScheduler so it integrates cleanly
with FastAPI's asyncio event loop.  The actual ingestion work is offloaded to
a thread-pool via asyncio.to_thread so it never blocks the event loop.

Ingestion shares the same threading.Lock as the manual ingest-all endpoint,
so the two paths can never run concurrently.
"""
import asyncio
import logging
import os
import threading
from datetime import datetime

from apscheduler.schedulers.asyncio import AsyncIOScheduler

log = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


def _is_enabled() -> bool:
    val = os.getenv("RSS_AUTO_INGEST_ENABLED", "true").strip().lower()
    return val not in ("false", "0", "no")


def _interval_minutes() -> int:
    try:
        return max(1, int(os.getenv("RSS_AUTO_INGEST_INTERVAL_MINUTES", "30")))
    except ValueError:
        log.warning(
            "Invalid RSS_AUTO_INGEST_INTERVAL_MINUTES value — defaulting to 60"
        )
        return 60


def _run_narrative_refresh() -> None:
    """
    Sync: auto-suggest frames if none exist yet, then match recent unmatched articles.
    Safe to call after every ingestion run — all operations are idempotent.
    """
    from app.db import SessionLocal
    from app.models import NarrativeFrame, NarrativeFrameMention, SourceItem
    from app.services import narrative_frames as nf_svc
    from datetime import datetime, timedelta
    from sqlalchemy import not_, exists

    with SessionLocal() as db:
        # Count active frames
        frame_count = db.query(NarrativeFrame).filter(NarrativeFrame.active == True).count()

        # Count relevant articles from last 14 days
        cutoff = datetime.utcnow() - timedelta(days=14)
        relevant_count = (
            db.query(SourceItem)
            .filter(
                SourceItem.archived_as_irrelevant == False,
                SourceItem.created_at >= cutoff,
                SourceItem.summary.isnot(None),
            )
            .count()
        )

        # Auto-suggest if we have data but no frames yet
        if frame_count < 2 and relevant_count >= 10:
            log.info(
                "narrative_refresh: %d relevant articles, %d frames — auto-suggesting",
                relevant_count, frame_count,
            )
            suggested = nf_svc.suggest_frames(db, days_back=14)
            log.info("narrative_refresh: suggested %d frames", len(suggested))
            # Re-count after suggestion
            frame_count = db.query(NarrativeFrame).filter(NarrativeFrame.active == True).count()

        if frame_count == 0:
            log.info("narrative_refresh: no frames to match against, skipping")
            return

        # Find relevant articles from last 48h with no frame mentions yet
        recent_cutoff = datetime.utcnow() - timedelta(hours=48)
        unmatched = (
            db.query(SourceItem)
            .filter(
                SourceItem.archived_as_irrelevant == False,
                SourceItem.created_at >= recent_cutoff,
                not_(
                    exists().where(NarrativeFrameMention.source_item_id == SourceItem.id)
                ),
            )
            .all()
        )

        if not unmatched:
            log.info("narrative_refresh: no unmatched articles, done")
            return

        log.info("narrative_refresh: matching %d unmatched articles to frames", len(unmatched))
        total_mentions = 0
        for item in unmatched:
            matched = nf_svc.match_article_to_frames(db, item)
            total_mentions += len(matched)
        log.info("narrative_refresh: created %d frame mentions", total_mentions)


async def _scheduled_ingest_job() -> None:
    """Async job wrapper: runs sync ingestion in a thread-pool, then narrative refresh."""
    from app.services.rss_ingestion import try_ingest_all_rss

    log.info("Scheduled RSS ingestion starting")
    try:
        result = await asyncio.to_thread(try_ingest_all_rss, skip_if_locked=True)
        if result is None:
            log.warning(
                "Scheduled RSS ingestion skipped — previous run still active"
            )
        else:
            log.info(
                "Scheduled RSS ingestion complete: "
                "feeds=%d  added=%d  skipped=%d  errors=%d",
                result.feeds_processed,
                result.total_added,
                result.total_skipped,
                result.total_errors,
            )
    except Exception:
        log.exception("Scheduled RSS ingestion failed with an unhandled exception")
        return

    # Auto-suggest frames and match new articles after ingestion
    try:
        await asyncio.to_thread(_run_narrative_refresh)
    except Exception:
        log.exception("Narrative refresh failed after scheduled ingestion")

    # Invalidate briefing memo cache so next page load reflects new articles
    try:
        from app.services import briefing_summary
        briefing_summary.invalidate()
    except Exception:
        log.warning("Failed to invalidate briefing summary cache")


def _run_rematch(days_back: int = 30) -> None:
    """Sync: run rematch_all in-thread.  Called by enqueue_rematch."""
    from app.db import SessionLocal
    from app.services import narrative_frames as nf_svc

    log.info("rematch: starting (days_back=%d)", days_back)
    try:
        with SessionLocal() as db:
            count = nf_svc.rematch_all(db, days_back=days_back)
        log.info("rematch: done — %d mentions created/updated", count)
    except Exception:
        log.exception("rematch: failed")


def enqueue_rematch(days_back: int = 30) -> None:
    """Enqueue a rematch_all run. Returns immediately; work runs in background.

    If the APScheduler is running, adds a date-triggered (run-now) job.
    If the scheduler is disabled, falls back to a daemon thread.
    """
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        job_id = f"rematch_manual_{int(datetime.utcnow().timestamp())}"
        _scheduler.add_job(
            _run_rematch,
            args=[days_back],
            trigger="date",
            id=job_id,
            replace_existing=False,
            max_instances=1,
        )
        log.info("enqueue_rematch: job %s queued (days_back=%d)", job_id, days_back)
    else:
        # Scheduler not running — run in a daemon thread so the HTTP response
        # still returns immediately.
        threading.Thread(
            target=_run_rematch, args=[days_back], daemon=True, name="rematch_manual"
        ).start()
        log.info("enqueue_rematch: scheduler not running, started thread (days_back=%d)", days_back)


def _run_crawler() -> None:
    """Sync: crawl all active webpage monitors. Called by scheduler."""
    from app.db import SessionLocal
    from app.services.ingestion_crawler import crawl_all_webpage_monitors

    log.info("Scheduled crawler starting")
    try:
        with SessionLocal() as db:
            results = crawl_all_webpage_monitors(db)
        total_added = sum(r.added for r in results)
        total_errors = sum(r.errors for r in results)
        log.info(
            "Scheduled crawler complete: monitors=%d added=%d errors=%d",
            len(results), total_added, total_errors,
        )
    except Exception:
        log.exception("Scheduled crawler failed with an unhandled exception")


def _run_reddit() -> None:
    """Sync: ingest Reddit. Called by scheduler."""
    from app.db import SessionLocal
    from app.services.ingestion_reddit import ingest_reddit

    log.info("Scheduled Reddit ingestion starting")
    try:
        with SessionLocal() as db:
            result = ingest_reddit(db)
        log.info(
            "Scheduled Reddit ingestion complete: added=%d skipped=%d errors=%d",
            result.added, result.skipped, result.errors,
        )
    except Exception:
        log.exception("Scheduled Reddit ingestion failed with an unhandled exception")


def _run_fec() -> None:
    """Sync: poll all active FEC monitors. Called by scheduler (daily)."""
    from app.db import SessionLocal
    from app.services.monitors import run_fec_monitors

    log.info("Scheduled FEC polling starting")
    try:
        with SessionLocal() as db:
            results = run_fec_monitors(db)
        log.info(
            "Scheduled FEC polling complete: monitors_run=%d fec_filings=%d fec_ie_district=%d",
            results["monitors_run"], results["fec_filings"], results["fec_ie_district"],
        )
    except Exception:
        log.exception("Scheduled FEC polling failed with an unhandled exception")


def start_scheduler() -> None:
    """Start the background scheduler.  Called once during app startup."""
    global _scheduler

    if not _is_enabled():
        log.info(
            "RSS auto-ingestion disabled (RSS_AUTO_INGEST_ENABLED=false) — "
            "scheduler not started"
        )
        return

    interval = _interval_minutes()
    log.info(
        "Starting RSS auto-ingestion scheduler (interval=%d min)", interval
    )

    _scheduler = AsyncIOScheduler()
    _scheduler.add_job(
        _scheduled_ingest_job,
        trigger="interval",
        minutes=interval,
        id="rss_auto_ingest",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    # Crawler: every 6 hours
    _scheduler.add_job(
        lambda: asyncio.ensure_future(asyncio.to_thread(_run_crawler)),
        trigger="interval",
        hours=6,
        id="crawler_auto",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    # Reddit: every 2 hours
    _scheduler.add_job(
        lambda: asyncio.ensure_future(asyncio.to_thread(_run_reddit)),
        trigger="interval",
        hours=2,
        id="reddit_auto",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    # FEC filings: daily (IE notices must be filed within 24-48 hours of expenditure)
    _scheduler.add_job(
        lambda: asyncio.ensure_future(asyncio.to_thread(_run_fec)),
        trigger="interval",
        hours=24,
        id="fec_daily",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    # Run narrative refresh once at startup to catch up on any unmatched articles
    _scheduler.add_job(
        lambda: asyncio.ensure_future(asyncio.to_thread(_run_narrative_refresh)),
        trigger="date",
        id="narrative_refresh_startup",
        replace_existing=True,
    )
    _scheduler.start()
    log.info("RSS ingestion scheduler started")


def stop_scheduler() -> None:
    """Stop the background scheduler gracefully.  Called during app shutdown."""
    global _scheduler

    if _scheduler is not None:
        log.info("Stopping RSS ingestion scheduler")
        _scheduler.shutdown(wait=False)
        _scheduler = None
        log.info("RSS ingestion scheduler stopped")
