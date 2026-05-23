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


def _run_auto_review() -> None:
    """Sync: triage review queue. Called by scheduler after each ingest."""
    from app.db import SessionLocal
    from app.services.auto_review import auto_review_queue
    with SessionLocal() as db:
        auto_review_queue(db)


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

    # Auto-triage the review queue so high-confidence articles flow through
    try:
        await asyncio.to_thread(_run_auto_review)
    except Exception:
        log.exception("Auto review triage failed after scheduled ingestion")

    # Merge any duplicate narrative frames that slipped through
    try:
        from app.services import narrative_frames as nf_svc
        def _run_dedup():
            from app.db import SessionLocal
            with SessionLocal() as db:
                result = nf_svc.audit_duplicates(db)
                if result.get("merged", 0):
                    log.info("dedup: merged %d duplicate frames after ingest", result["merged"])
        await asyncio.to_thread(_run_dedup)
    except Exception:
        log.exception("Dedup failed after scheduled ingestion")

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
        with SessionLocal() as db:
            result = nf_svc.audit_duplicates(db)
            log.info("rematch: dedup merged %d frames", result.get("merged", 0))
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
    from app.routes import ingest as ingest_routes

    log.info("Scheduled crawler starting")
    try:
        with SessionLocal() as db:
            results = crawl_all_webpage_monitors(db)
        ingest_routes._last_crawl_at = datetime.utcnow()
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
    from app.routes import ingest as ingest_routes

    log.info("Scheduled Reddit ingestion starting")
    try:
        with SessionLocal() as db:
            result = ingest_reddit(db)
        ingest_routes._last_reddit_at = datetime.utcnow()
        log.info(
            "Scheduled Reddit ingestion complete: added=%d skipped=%d errors=%d",
            result.added, result.skipped, result.errors,
        )
    except Exception:
        log.exception("Scheduled Reddit ingestion failed with an unhandled exception")


def _resume_pipeline_if_needed() -> None:
    """Startup check: if backfill is done but rescore hasn't run, resume the pipeline.

    This handles server restarts that kill in-memory state mid-pipeline. The
    backfill completion flag lives in the DB; rescore/rematch state lives in
    memory and resets on restart. We detect this mismatch and auto-resume so
    the user doesn't have to manually retrigger anything.
    """
    import time
    time.sleep(10)  # let the DB settle after startup

    from app.db import SessionLocal
    from app.models import CampaignConfig, SourceItem

    try:
        with SessionLocal() as db:
            campaign = db.query(CampaignConfig).first()
            if not campaign or not getattr(campaign, "extended_backfill_completed", False):
                return  # backfill hasn't run yet — nothing to resume

            # Check for unscored articles (rescore needed)
            unscored = db.query(SourceItem.id).filter(SourceItem.summary.is_(None)).count()
            if unscored > 0:
                log.info(
                    "pipeline_resume: backfill done, %d unscored articles — resuming rescore+rematch",
                    unscored,
                )
                from app.services import rescore as rescore_svc
                r = rescore_svc.start_rescore(
                    db,
                    delay_seconds=1.5,
                    only_unscored=True,
                    auto_rematch=True,
                )
                log.info("pipeline_resume: rescore queued — %s", r)

                # Also run feed discovery if it hasn't produced results yet
                from app.services.feed_discovery import feed_discovery_progress, discover_feeds_from_gdelt
                if not feed_discovery_progress.get("done"):
                    from app.models import RssFeed
                    existing_feed_count = db.query(RssFeed).filter(RssFeed.active == True).count()
                    if existing_feed_count > 0:
                        # Feeds already exist from a prior run — just mark done, don't re-probe
                        feed_discovery_progress.update({"done": True, "running": False, "created": 0, "probed": existing_feed_count})
                        log.info("pipeline_resume: feed_discovery skipped — %d feeds already in DB", existing_feed_count)
                    else:
                        try:
                            disc = discover_feeds_from_gdelt(db, min_articles=2)
                            log.info("pipeline_resume: feed_discovery done — created=%d probed=%d", disc["created"], disc["probed"])
                        except Exception as exc:
                            log.warning("pipeline_resume: feed_discovery failed: %s", exc)
            else:
                log.info("pipeline_resume: all articles already scored — triggering rematch only")
                enqueue_rematch(days_back=365)
    except Exception as exc:
        log.warning("pipeline_resume: startup check failed: %s", exc)


def _run_google_trends() -> None:
    """Sync: fetch Google Trends interest data for all configured terms."""
    from app.db import SessionLocal
    from app.services.google_trends import collect_trends

    log.info("Google Trends collection starting")
    try:
        with SessionLocal() as db:
            result = collect_trends(db)
        log.info("Google Trends: terms=%d rows_added=%d", result.get("terms", 0), result.get("rows_added", 0))
    except Exception:
        log.exception("Google Trends collection failed")


def _run_twitter_recheck() -> None:
    """Sync: re-probe Nitter for any twitter_profile monitors without a feed."""
    from app.db import SessionLocal
    from app.services.twitter_scraper import recheck_failed_twitter_monitors

    log.info("Twitter/Nitter recheck starting")
    try:
        with SessionLocal() as db:
            resolved = recheck_failed_twitter_monitors(db)
        log.info("Twitter/Nitter recheck complete: newly_resolved=%d", resolved)
    except Exception:
        log.exception("Twitter/Nitter recheck failed")


def _run_journalist_discovery() -> None:
    """Daily: discover journalists covering this race from recent article bylines
    and auto-create twitter_profile + bluesky_profile monitors for them."""
    from app.db import SessionLocal
    from app.services.monitors import auto_discover_journalists

    log.info("journalist_discovery: starting")
    try:
        with SessionLocal() as db:
            result = auto_discover_journalists(db)
        log.info("journalist_discovery: done — %s", result)
    except Exception:
        log.exception("journalist_discovery failed")


def _run_frame_momentum() -> None:
    """Daily: classify each frame's momentum signal by joining article velocity
    with Google Trends interest velocity. Writes results to
    NarrativeFrame.momentum_signal so the dashboard can surface viral / missing
    coverage / elite-only patterns."""
    from app.db import SessionLocal
    from app.services.frame_momentum import analyze_all_frames

    log.info("frame_momentum: starting")
    try:
        with SessionLocal() as db:
            result = analyze_all_frames(db)
        log.info("frame_momentum: done — %s", result)
    except Exception:
        log.exception("frame_momentum failed")


def _run_outlet_rediscovery() -> None:
    """Monthly: re-run LLM outlet discovery in `force` mode to catch newly
    emerged local outlets that weren't around at campaign setup. Idempotent —
    skips outlets already in the DB. Cheap (~$0.001 per run).
    """
    from app.db import SessionLocal
    from app.models import CampaignConfig
    from app.services.monitors import _auto_discover_outlets
    from app.services.source_discovery import _parse_state_code

    log.info("outlet_rediscovery: starting")
    try:
        with SessionLocal() as db:
            campaign = db.query(CampaignConfig).first()
            if not campaign or not campaign.district:
                log.info("outlet_rediscovery: no campaign or district — skipping")
                return
            state_code = _parse_state_code(campaign.district, campaign.location)
            created = _auto_discover_outlets(
                db,
                district=campaign.district,
                state_code=state_code,
                location=campaign.location,
                candidate=campaign.candidate_name,
                force=True,  # always augment, even for curated districts
            )
        log.info(
            "outlet_rediscovery: done — %d new outlets added for %s",
            created, campaign.district,
        )
    except Exception:
        log.exception("outlet_rediscovery failed")


def _run_monitor_prune() -> None:
    """Weekly: deactivate auto-discovered social monitors that produced no
    race-relevant content. Soft delete — reactivatable if the same handle
    is later re-discovered. Conservative defaults applied; the candidate /
    opponent accounts are protected by name-match in prune_unproductive_monitors."""
    from app.db import SessionLocal
    from app.services.monitors import prune_unproductive_monitors

    log.info("monitor_prune: starting (dry_run=False — applying)")
    try:
        with SessionLocal() as db:
            result = prune_unproductive_monitors(db, dry_run=False)
        if result.get("pruned"):
            log.info("monitor_prune: deactivated %d monitor(s) — %s",
                     result["pruned"],
                     [a["name"] for a in result.get("actions", [])])
        else:
            log.info("monitor_prune: nothing to prune (%s)",
                     {k: v for k, v in result.items() if k.startswith("skipped")})
    except Exception:
        log.exception("monitor_prune failed")


def _run_bluesky_poll() -> None:
    """Every 15 minutes: fetch new posts from all active bluesky_profile monitors."""
    from app.db import SessionLocal
    from app.services.bluesky_scraper import poll_all_bluesky_monitors

    try:
        with SessionLocal() as db:
            result = poll_all_bluesky_monitors(db)
        # Quiet log unless we actually did something.
        if result.get("monitors", 0) > 0 and (result.get("added", 0) or result.get("failed", 0)):
            log.info("bluesky_poll: %s", result)
    except Exception:
        log.exception("bluesky_poll failed")


def _run_gdelt_realtime() -> None:
    """Sync: poll GDELT for articles from the last 30 minutes."""
    from app.db import SessionLocal
    from app.services.gdelt_monitor import poll_gdelt_realtime

    try:
        with SessionLocal() as db:
            result = poll_gdelt_realtime(db)
        if not result.get("skipped"):
            log.info(
                "GDELT realtime: added=%d skipped=%d errors=%d",
                result.get("added", 0), result.get("skipped", 0), result.get("errors", 0),
            )
    except Exception:
        log.exception("GDELT realtime poll failed")


def _run_gdelt_tone_snapshots() -> None:
    """Sync: collect daily GDELT tone data for candidate + opponents."""
    from app.db import SessionLocal
    from app.services.gdelt_monitor import collect_tone_snapshots

    try:
        with SessionLocal() as db:
            result = collect_tone_snapshots(db, days_back=90)
        if not result.get("skipped"):
            log.info(
                "GDELT tone snapshots: upserted=%d entities=%d",
                result.get("upserted", 0), result.get("entities", 0),
            )
    except Exception:
        log.exception("GDELT tone snapshot collection failed")


def _run_analytics_startup_catchup() -> None:
    """Startup: collect tone + trends data if it is missing or stale.

    The tone and Google Trends jobs run on a 24h interval, so their first run
    is 24h after the scheduler starts. A backend that restarts often would keep
    resetting that timer and never accumulate data. This runs once at startup,
    but skips collection when data is already fresh (<20h old) so frequent
    restarts don't hammer the GDELT / Google APIs.
    """
    from datetime import timedelta

    from sqlalchemy import func

    from app.db import SessionLocal
    from app.models import GdeltToneSnapshot, GoogleTrendSnapshot

    fresh_cutoff = datetime.utcnow() - timedelta(hours=20)
    try:
        with SessionLocal() as db:
            tone_latest = db.query(func.max(GdeltToneSnapshot.snapshot_date)).scalar()
            trends_latest = db.query(func.max(GoogleTrendSnapshot.snapshot_date)).scalar()
    except Exception:
        log.exception("analytics startup catch-up: staleness check failed")
        return

    if tone_latest is None or tone_latest < fresh_cutoff:
        log.info("analytics startup catch-up: tone data stale/missing — collecting")
        _run_gdelt_tone_snapshots()
    if trends_latest is None or trends_latest < fresh_cutoff:
        log.info("analytics startup catch-up: trends data stale/missing — collecting")
        _run_google_trends()


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
        _run_crawler,
        trigger="interval",
        hours=6,
        id="crawler_auto",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    # Reddit: every 2 hours
    _scheduler.add_job(
        _run_reddit,
        trigger="interval",
        hours=2,
        id="reddit_auto",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    # FEC filings: daily (IE notices must be filed within 24-48 hours of expenditure)
    _scheduler.add_job(
        _run_fec,
        trigger="interval",
        hours=24,
        id="fec_daily",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    # Twitter/Nitter recheck: daily — retries monitors that couldn't find a
    # working Nitter instance last time (instances come and go)
    _scheduler.add_job(
        _run_twitter_recheck,
        trigger="interval",
        hours=24,
        id="twitter_nitter_recheck",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    # Journalist auto-discovery: daily — extracts bylines from recent
    # race-relevant articles and creates twitter_profile + bluesky_profile
    # monitors for the journalists who keep showing up. Self-updating
    # for any campaign.
    _scheduler.add_job(
        _run_journalist_discovery,
        trigger="interval",
        hours=24,
        id="journalist_discovery_daily",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    # Bluesky polling: every 15 minutes — fetches new posts from all
    # bluesky_profile monitors via the public AT Protocol API.
    _scheduler.add_job(
        _run_bluesky_poll,
        trigger="interval",
        minutes=15,
        id="bluesky_poll",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    # Frame momentum: daily — classifies each frame's momentum signal by
    # joining article volume with Google Trends interest. Surfaces viral /
    # missing-coverage / elite-only narratives.
    _scheduler.add_job(
        _run_frame_momentum,
        trigger="interval",
        hours=24,
        id="frame_momentum_daily",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    # Outlet re-discovery: monthly — re-runs LLM outlet discovery in force
    # mode to catch newly-emerged local outlets the campaign should monitor.
    # Idempotent (skips existing domains), cheap (~$0.001/run).
    _scheduler.add_job(
        _run_outlet_rediscovery,
        trigger="interval",
        days=30,
        id="outlet_rediscovery_monthly",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    # Monitor pruning: weekly — soft-deactivate auto-discovered social
    # monitors that never produced race-relevant content. Counterpart to
    # journalist_discovery_daily; together they form a self-tuning loop.
    _scheduler.add_job(
        _run_monitor_prune,
        trigger="interval",
        days=7,
        id="monitor_prune_weekly",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    # GDELT real-time: every 15 minutes — catches articles from outlets we
    # don't have RSS feeds for; feeds into the normal ingest pipeline.
    _scheduler.add_job(
        _run_gdelt_realtime,
        trigger="interval",
        minutes=15,
        id="gdelt_realtime",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    # GDELT tone snapshots: daily — captures how positive/negative media
    # coverage is trending for the candidate and opponents.
    _scheduler.add_job(
        _run_gdelt_tone_snapshots,
        trigger="interval",
        hours=24,
        id="gdelt_tone_daily",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    # Google Trends: daily — search interest for candidate/opponent/issue terms
    _scheduler.add_job(
        _run_google_trends,
        trigger="interval",
        hours=24,
        id="google_trends_daily",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    # Run narrative refresh once at startup to catch up on any unmatched articles
    _scheduler.add_job(
        _run_narrative_refresh,
        trigger="date",
        id="narrative_refresh_startup",
        replace_existing=True,
    )
    # Analytics catch-up: the tone/trends interval jobs don't fire until 24h
    # after startup, so collect once now if that data is missing or stale.
    _scheduler.add_job(
        _run_analytics_startup_catchup,
        trigger="date",
        id="analytics_startup_catchup",
        replace_existing=True,
    )
    _scheduler.start()
    # Resume pipeline on startup: if backfill is done but rescore/rematch haven't
    # run yet (in-memory state was lost on restart), auto-resume from where we left off.
    # Uses a daemon thread directly — avoid asyncio.ensure_future which fails in a
    # ThreadPoolExecutor thread (no event loop there).
    threading.Thread(
        target=_resume_pipeline_if_needed, daemon=True, name="pipeline_resume_startup"
    ).start()
    log.info("RSS ingestion scheduler started")


def stop_scheduler() -> None:
    """Stop the background scheduler gracefully.  Called during app shutdown."""
    global _scheduler

    if _scheduler is not None:
        log.info("Stopping RSS ingestion scheduler")
        _scheduler.shutdown(wait=False)
        _scheduler = None
        log.info("RSS ingestion scheduler stopped")
