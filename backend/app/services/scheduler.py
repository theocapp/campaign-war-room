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

# Module-level health state. Exposed via /api/system/scheduler-health so
# the user can see what's failing/skipping without tailing uvicorn logs.
# Specifically catches the 2026-05-23 pattern: scheduled job throws,
# APScheduler updates last_run_at anyway, no visible failure surface.
_scheduler_health: dict = {
    "last_rss_success": None,    # iso datetime when RSS last completed cleanly
    "last_rss_skip": None,       # iso datetime when RSS was lock-skipped
    "last_rss_error": None,      # string of last exception class+msg
    "last_rss_error_at": None,   # iso datetime of last error
}


def get_scheduler_health() -> dict:
    """Snapshot for the observability endpoint."""
    return dict(_scheduler_health)


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
            # Lock contention: another ingest is still running. Track this
            # so we can detect the "lock stuck forever" pattern (which was
            # the root cause of the 2026-05-23 4-hour outage). If we see
            # this WARN repeatedly for >2 hours, something is wedged.
            _scheduler_health["last_rss_skip"] = datetime.utcnow().isoformat()
            log.warning(
                "Scheduled RSS ingestion skipped — previous run still active"
            )
        else:
            _scheduler_health["last_rss_success"] = datetime.utcnow().isoformat()
            _scheduler_health["last_rss_error"] = None
            log.info(
                "Scheduled RSS ingestion complete: "
                "feeds=%d  added=%d  skipped=%d  errors=%d",
                result.feeds_processed,
                result.total_added,
                result.total_skipped,
                result.total_errors,
            )
    except Exception as exc:
        # Surface to the health endpoint instead of pure-log-only. The
        # prior behavior left exceptions in the logs (which nobody tails)
        # while APScheduler's last_run_at updated successfully, creating
        # a false signal that work was happening.
        _scheduler_health["last_rss_error"] = f"{type(exc).__name__}: {exc}"
        _scheduler_health["last_rss_error_at"] = datetime.utcnow().isoformat()
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

    # NOTE: audit_duplicates used to run here, after every 30-min RSS ingest
    # (~48 LLM calls/day for a job that needs to happen at most once daily —
    # frames don't change every 30 min). It's now scheduled separately on a
    # 24h interval below as `frame_dedup_daily`.

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
    """Sync: ingest Reddit via two paths.

    1. Direct Reddit JSON API (`ingest_reddit`) — fast-fails on 403 when
       unauthed (which is the post-2024 default; needs OAuth wired up).
    2. Tavily-backed Reddit search (`ingest_tavily_reddit`) — works when
       SEARCH_PROVIDER=tavily + TAVILY_API_KEY are set; no Reddit auth
       needed at all.

    Google News Reddit feeds (added in source_discovery.py) come in via
    the normal RSS path — not handled here, they're regular monitors.
    """
    from app.db import SessionLocal
    from app.services.ingestion_reddit import ingest_reddit
    from app.services.tavily_reddit import ingest_tavily_reddit
    from app.routes import ingest as ingest_routes

    log.info("Scheduled Reddit ingestion starting")
    try:
        with SessionLocal() as db:
            direct = ingest_reddit(db)
        ingest_routes._last_reddit_at = datetime.utcnow()
        log.info(
            "Reddit direct: added=%d skipped=%d errors=%d",
            direct.added, direct.skipped, direct.errors,
        )
    except Exception:
        log.exception("Reddit direct ingestion failed")
    # Tavily path — runs alongside, no-op if not configured.
    try:
        with SessionLocal() as db:
            tav = ingest_tavily_reddit(db)
        log.info(
            "Reddit via Tavily: queries=%d added=%d skipped=%d errors=%d",
            tav.queries_run, tav.added, tav.skipped, tav.errors,
        )
    except Exception:
        log.exception("Reddit via Tavily failed")


def _run_mastodon() -> None:
    """Sync: fetch Mastodon hashtag timelines from configured instances."""
    from app.db import SessionLocal
    from app.services.mastodon_ingest import ingest_mastodon

    log.info("Mastodon ingestion starting")
    try:
        with SessionLocal() as db:
            result = ingest_mastodon(db)
        log.info(
            "Mastodon ingestion complete: instances=%d tags=%d added=%d skipped=%d errors=%d",
            result.instances_polled, result.tags_polled,
            result.added, result.skipped, result.errors,
        )
    except Exception:
        log.exception("Mastodon ingestion failed")


def _run_orphan_gc() -> None:
    """Daily defense-in-depth: sweep orphan rows across the schema.

    Cleanup code SHOULD use safe_deletes helpers, but this is the
    backstop for any path that doesn't (third-party scripts, ad-hoc
    SQL fixes, future code that forgets). Idempotent and cheap — if
    nothing is orphaned, returns immediately.

    The orphan class we most fear: StoryCluster rows whose seed
    SourceItem has been deleted. The cluster id is derived from the
    seed (``source-{N}``); leaving the cluster around blocks new
    cluster inserts via UNIQUE-constraint collision. See the 2026-05-23
    post-mortem.
    """
    from app.db import SessionLocal
    from app.services.safe_deletes import gc_orphans

    log.info("orphan_gc: starting")
    try:
        with SessionLocal() as db:
            counts = gc_orphans(db)
            db.commit()
        if any(v > 0 for v in counts.values()):
            log.warning("orphan_gc: cleaned up orphans: %s", counts)
        else:
            log.info("orphan_gc: clean — no orphans found")
    except Exception:
        log.exception("orphan_gc: failed")


def _run_search_monitors() -> None:
    """Periodic re-run of all active search_query monitors.

    Search-query monitors were previously executed ONLY when first created
    (monitors.py:822-826) — they then sat forever with stale results.
    In one PA-08 instance, 34 monitors were 10 days stale at audit time.
    This job re-runs them on a 30-min cadence so the search-backed signal
    (currently Tavily) stays current.

    Cost: with Tavily as the provider, each monitor = 1 search call.
    34 monitors × 48 runs/day = 1632 Tavily calls/day. With the 4-key
    rotation (1000 calls/key/day = 4000/day pool) this is comfortable.
    """
    from app.db import SessionLocal
    from app.models import SourceMonitor
    from app.services.monitors import _run_search_monitor

    log.info("search_monitors_periodic: starting")
    try:
        with SessionLocal() as db:
            monitors = (
                db.query(SourceMonitor)
                .filter(SourceMonitor.monitor_type == "search_query",
                        SourceMonitor.active == True)  # noqa: E712
                .all()
            )
            total_added = 0
            errors = 0
            for m in monitors:
                try:
                    total_added += _run_search_monitor(db, m)
                except Exception:
                    errors += 1
                    log.exception("search_monitors_periodic: monitor %d failed", m.id)
            log.info(
                "search_monitors_periodic: done — monitors=%d added=%d errors=%d",
                len(monitors), total_added, errors,
            )
    except Exception:
        log.exception("search_monitors_periodic: outer failure")


def _run_landscape_clustering_refresh() -> None:
    """Daily HDBSCAN/UMAP recompute over the candidate-frames table.

    Keeps the /landscape map + the triage pipeline working on fresh
    cluster structure even if nobody loads the page for 25+ hours. The
    LLM/embedding cost is essentially zero because the embedding layer
    caches per-text; this job just re-runs UMAP + HDBSCAN on already-
    embedded points (~1-2 seconds).

    Sequenced BEFORE the triage safety net so that any triage pass
    during the next 24h operates on the freshest possible clusters.
    """
    from app.db import SessionLocal
    from app.services.narrative_landscape import refresh_landscape

    log.info("landscape_clustering_refresh: starting")
    try:
        with SessionLocal() as db:
            result = refresh_landscape(db, days_back=21)
        n_total = result.get("n_total", 0)
        n_clustered = result.get("n_clustered", 0)
        clusters = len(result.get("clusters", []))
        err = result.get("error")
        if err:
            log.warning("landscape_clustering_refresh: completed with error: %s", err)
        log.info(
            "landscape_clustering_refresh: %d total candidate frames, "
            "%d clustered into %d clusters", n_total, n_clustered, clusters,
        )
    except Exception:
        log.exception("landscape_clustering_refresh: failed")


def _run_narrative_triage_safety_net() -> None:
    """Daily safety-net for the hands-off auto-promote workflow.

    The user's primary trigger is the "Run AI triage" button on /review.
    This job catches anything that's been pending more than 24h — e.g. the
    user was offline or simply forgot. It does NOT run preemptively; if
    there's nothing stale, it no-ops.

    Why a safety net instead of an active schedule:
      - Predictable cost (~$0.20–0.50 per pass; only fires when work exists)
      - User stays in control of when narratives get auto-created
      - Prevents the queue from silently piling up over weekends or
        vacation periods
    """
    from datetime import timedelta
    from app.db import SessionLocal
    from app.models import ProposedClusterTriage
    from app.services.narrative_triage import run_triage_pass

    log.info("narrative_triage_safety_net: starting")
    try:
        with SessionLocal() as db:
            cutoff = datetime.utcnow() - timedelta(hours=24)
            stale_count = db.query(ProposedClusterTriage).filter(
                ProposedClusterTriage.applied_at.is_(None),
                ProposedClusterTriage.dismissed_at.is_(None),
                ProposedClusterTriage.created_at < cutoff,
            ).count()
            if stale_count == 0:
                log.info(
                    "narrative_triage_safety_net: 0 verdicts pending >24h, "
                    "no-op (manual triage is keeping up)"
                )
                return
            log.info(
                "narrative_triage_safety_net: %d verdicts pending >24h, "
                "running hands-off triage pass", stale_count,
            )
            result = run_triage_pass(
                db, days_back=21, force_refresh=False, hands_off=True,
            )
            executed = result.get("auto_executed", [])
            log.info(
                "narrative_triage_safety_net: done. %d auto-executed "
                "(%d promote, %d merge), %d need review, %.1fs",
                len(executed),
                sum(1 for x in executed if x["action"] == "auto_promote"),
                sum(1 for x in executed if x["action"] == "auto_merge"),
                result.get("human_review", 0),
                result.get("elapsed_seconds", 0),
            )
    except Exception:
        log.exception("narrative_triage_safety_net: failed")


def _run_candidate_frame_promoter() -> None:
    """Daily: cluster the candidate_frames staging table and log how many
    promotable suggestions surface. Does NOT auto-promote — the UI surfaces
    pending clusters for human review on /api/narrative-frames/candidate-frames/pending.

    Why a scheduled job at all if nothing's written? Two reasons:
      1. The clustering uses Gemini embeddings, which can be slow at the
         lower-tier API limits. Running it once daily and caching results
         would be a follow-up; for now we just keep the logs warm so we
         can spot regressions ("clustering produced 0 suggestions for a
         week — did the LLM stop generating candidate_frames?").
      2. Operations visibility: this log line is the only signal that the
         AI's been noticing emerging narratives. Worth having in the
         scheduler so it shows up alongside the other daily jobs.
    """
    from app.db import SessionLocal

    log.info("candidate_frame_promoter_daily: starting")
    try:
        from app.services.candidate_frame_promoter import refresh_cache, _CACHE
        with SessionLocal() as db:
            count = refresh_cache(db, days_back=21)
        names = [s["suggested_name"] for s in (_CACHE["suggestions"] or [])[:5]]
        log.info(
            "candidate_frame_promoter_daily: %d promotable clusters "
            "cached (top: %s)", count, names,
        )
    except Exception:
        log.exception("candidate_frame_promoter_daily: failed")


def _run_rematch_after_frame_edit() -> None:
    """Background rematch_all triggered by a frame CRUD event.

    Uses the existing _rematch_lock in narrative_frames to skip if a rematch
    is already running. The lock means burst edits land in the same rematch
    (debounce naturally) rather than queueing serially.
    """
    import time
    from app.db import SessionLocal
    from app.services import narrative_frames as nf

    log.info("=== AUTO-REMATCH FIRING (after frame edit) ===")
    t0 = time.time()
    try:
        with SessionLocal() as db:
            total = nf.rematch_all(db)
        log.info(
            "=== AUTO-REMATCH DONE: %d matches written in %.1fs ===",
            total, time.time() - t0,
        )
    except Exception:
        log.exception("=== AUTO-REMATCH FAILED after %.1fs ===", time.time() - t0)


def schedule_rematch_after_frame_edit(debounce_seconds: int = 30) -> None:
    """Public helper called by /api/narrative-frames CRUD routes.

    Schedules a background rematch `debounce_seconds` from now. Uses
    `replace_existing=True` on a fixed job id, so rapid successive edits
    all collapse to a single rematch fired once the user stops editing.

    No-op when the scheduler isn't running (e.g. tests or early boot) —
    the daily rematch_recent still catches drift in that case.
    """
    from datetime import datetime as _dt, timedelta as _td
    if _scheduler is None or not getattr(_scheduler, "running", False):
        log.debug("schedule_rematch_after_frame_edit: scheduler not running — skipping")
        return
    run_at = _dt.utcnow() + _td(seconds=debounce_seconds)
    _scheduler.add_job(
        _run_rematch_after_frame_edit,
        trigger="date",
        run_date=run_at,
        id="rematch_after_frame_edit",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    log.info(
        "=== AUTO-REMATCH SCHEDULED: will fire at %s (debounce %ds, replace_existing=true) ===",
        run_at.strftime("%H:%M:%S"), debounce_seconds,
    )


def _run_variant_clustering() -> None:
    """Daily variant clustering across all narrative frames.

    Reads cached quote embeddings from NarrativeFrameMention, groups quotes
    into variants via agglomerative complete-linkage clustering on cosine
    distance (threshold calibrated per-campaign — see
    scripts/calibrate_variant_threshold.py), and writes FrameVariant rows.

    Cost: free for clustering (deterministic on cached embeddings). LLM cost
    only for naming new clusters via the judge provider — ~$0.001 per cluster.
    Full re-cluster strategy is idempotent and safe to run repeatedly.
    """
    from app.db import SessionLocal
    from app.services import frame_variants as fv

    log.info("variant_clustering_daily: starting")
    try:
        with SessionLocal() as db:
            result = fv.cluster_all_frames(db)
        log.info(
            "variant_clustering_daily: done — %d frames processed, %d variants total",
            result.get("frames_processed", 0),
            result.get("total_variants_created", 0),
        )
    except Exception:
        log.exception("variant_clustering_daily: failed")


def _run_frame_dedup() -> None:
    """Daily LLM audit of narrative frames for semantic duplicates.

    Replaces the per-ingest call this function used to make. Frames don't
    change every 30 minutes, so running once a day cuts ~48 LLM calls/day
    down to 1 while still catching duplicates within a working day.
    """
    from app.db import SessionLocal
    from app.services import narrative_frames as nf_svc

    log.info("frame_dedup_daily: starting")
    try:
        with SessionLocal() as db:
            result = nf_svc.audit_duplicates(db)
        log.info(
            "frame_dedup_daily: done — merged %d duplicate frame(s)",
            result.get("merged", 0),
        )
    except Exception:
        log.exception("frame_dedup_daily: failed")


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
                # Previously: `enqueue_rematch(days_back=365)`. That fired a
                # 365-day rematch on EVERY clean restart — the single most
                # expensive operation in the system, gated only by "no
                # unscored articles". Restart the server twice in a week and
                # you've burned two full rematch passes. The right trigger
                # for a rematch is an explicit user action (the Rematch
                # button in the UI calls /api/narrative-frames/rematch), or
                # a real signal that frames have changed — not server
                # uptime hygiene.
                log.info(
                    "pipeline_resume: all articles scored — no startup work to do "
                    "(rematch is now manual-only; use the UI button)"
                )
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


def _run_feed_prune() -> None:
    """Weekly: deactivate RSS feeds that ingested substantial volume but
    produced zero race-relevant survivors over the prior 30 days. Search-
    query feeds (Google News, Reddit) and YouTube channels are exempt."""
    from app.db import SessionLocal
    from app.services.feed_prune import prune_zero_yield_feeds

    log.info("feed_prune: starting (dry_run=False — applying)")
    try:
        with SessionLocal() as db:
            result = prune_zero_yield_feeds(db, dry_run=False)
        if result.get("pruned"):
            log.info("feed_prune: deactivated %d feed(s) — %s",
                     result["pruned"],
                     [a["name"] for a in result.get("actions", [])])
        else:
            log.info("feed_prune: nothing to prune (%s)",
                     {k: v for k, v in result.items() if k.startswith("skipped")})
    except Exception:
        log.exception("feed_prune failed")


def _run_feed_discovery_yield() -> None:
    """Weekly: add direct RSS feeds for publishers proven to cover the race
    via Google News search survivors. Complements GDELT outlet discovery
    (which is broader / noisier) with yield-tested publishers."""
    from app.db import SessionLocal
    from app.services.feed_discovery_yield import (
        discover_feeds_from_google_news_yield,
    )

    log.info("feed_discovery_yield: starting (dry_run=False — applying)")
    try:
        with SessionLocal() as db:
            result = discover_feeds_from_google_news_yield(db, dry_run=False)
        if result.get("added"):
            log.info("feed_discovery_yield: added %d feed(s) — %s",
                     result["added"],
                     [a["domain"] for a in result.get("actions", [])])
        else:
            log.info("feed_discovery_yield: nothing to add "
                     "(candidates=%d, probed=%d, no_feed_found=%d)",
                     result.get("candidates", 0),
                     result.get("probed", 0),
                     result.get("no_feed_found", 0))
    except Exception:
        log.exception("feed_discovery_yield failed")


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
            throttled = result.get("throttled_queries", 0)
            if throttled:
                log.warning(
                    "GDELT realtime: added=%d skipped=%d errors=%d THROTTLED=%d (last: %s)",
                    result.get("added", 0), result.get("skipped", 0),
                    result.get("errors", 0), throttled,
                    result.get("last_throttle_reason", "?"),
                )
            else:
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


def _run_race_sentiment_market_sync() -> None:
    """Frequent: refresh prediction-market prices (Polymarket, Kalshi).

    Markets reprice continuously around news events; a 2h cadence
    captures meaningful intra-day shifts without hammering upstreams.
    Each connector failure is recorded on the row's last_sync_error
    column so the UI can surface it; a single bad source never blocks
    the others.
    """
    from app.db import SessionLocal
    from app.services.race_sentiment_sync import sync_all

    log.info("Scheduled race sentiment MARKET sync starting")
    try:
        with SessionLocal() as db:
            results = sync_all(db, source_types=("market",))
        log.info(
            "Scheduled race sentiment MARKET sync complete: synced=%s failed=%s",
            results.get("synced"), results.get("failed"),
        )
    except Exception:
        log.exception("Scheduled race sentiment MARKET sync failed with an unhandled exception")


def _run_race_sentiment_forecaster_sync() -> None:
    """Twice-daily: refresh forecaster ratings (Cook, Sabato, Inside Elections).

    Forecaster ratings change weekly at most, so 12h is plenty. Cook
    and Sabato are sourced via 270toWin (their own sites are CF-blocked);
    the 270toWin pages are ~7MB each, so we keep the cadence low to be
    polite. Each connector failure is recorded on the row so the UI can
    surface it; a single bad source never blocks the others.
    """
    from app.db import SessionLocal
    from app.services.race_sentiment_sync import sync_all

    log.info("Scheduled race sentiment FORECASTER sync starting")
    try:
        with SessionLocal() as db:
            results = sync_all(db, source_types=("rating",))
        log.info(
            "Scheduled race sentiment FORECASTER sync complete: synced=%s failed=%s",
            results.get("synced"), results.get("failed"),
        )
    except Exception:
        log.exception("Scheduled race sentiment FORECASTER sync failed with an unhandled exception")


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
    # Reddit: every 30 minutes. The unauthed JSON API allows ~60 req/min;
    # our run does ~20-30 req each (subreddits × terms + site-wide + comments),
    # well under the threshold. 30-min cadence catches local-subreddit
    # discussion within half an hour of posting.
    _scheduler.add_job(
        _run_reddit,
        trigger="interval",
        minutes=30,
        id="reddit_auto",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    # Mastodon: every 30 minutes — polls public hashtag timelines from a small
    # set of high-signal instances (journa.host, mastodon.social, etc.)
    # No auth needed. Configurable via MASTODON_* env vars. Each run is
    # ~25 lightweight HTTP requests across all instances.
    _scheduler.add_job(
        _run_mastodon,
        trigger="interval",
        minutes=30,
        id="mastodon_auto",
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
    # Race sentiment: split cadence — prediction markets reprice on news
    # events and want intra-day refresh; forecaster ratings change weekly
    # at most and don't justify it. Two jobs let each source type set its
    # own polling cost vs. responsiveness trade-off.
    _scheduler.add_job(
        _run_race_sentiment_market_sync,
        trigger="interval",
        hours=2,
        id="race_sentiment_markets",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    _scheduler.add_job(
        _run_race_sentiment_forecaster_sync,
        trigger="interval",
        hours=12,
        id="race_sentiment_forecasters",
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
    # RSS feed pruning: weekly — soft-deactivate RSS feeds (outlets, not
    # search queries) that ingested substantial volume but produced zero
    # race-relevant survivors. Self-tunes the outlet roster alongside the
    # outlet_rediscovery_monthly job.
    _scheduler.add_job(
        _run_feed_prune,
        trigger="interval",
        days=7,
        id="feed_prune_weekly",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    # Google News yield discovery: weekly — add direct RSS feeds for
    # publishers proven to cover the race via Google News search survivors.
    # Counterpart to feed_prune; together they tune the outlet roster
    # based on actual race-relevance yield.
    _scheduler.add_job(
        _run_feed_discovery_yield,
        trigger="interval",
        days=7,
        id="feed_discovery_yield_weekly",
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
    # Orphan garbage-collection: every 6 hours. Defense-in-depth backstop
    # for any cleanup path that doesn't use safe_deletes helpers.
    _scheduler.add_job(
        _run_orphan_gc,
        trigger="interval",
        hours=6,
        id="orphan_gc_periodic",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    # Search-query monitors: every 30 minutes. Previously these only ran
    # ONCE on creation and went stale forever. With Tavily configured
    # they now re-fire every cycle to keep search-backed signal fresh.
    _scheduler.add_job(
        _run_search_monitors,
        trigger="interval",
        minutes=30,
        id="search_monitors_periodic",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    # Frame deduplication: daily — LLM audit that merges semantically duplicate
    # narrative frames. Used to run after every 30-min RSS ingest, but frames
    # rarely change that fast and the per-ingest cost (~48 LLM calls/day) was
    # wasteful. One pass per day is plenty.
    _scheduler.add_job(
        _run_frame_dedup,
        trigger="interval",
        hours=24,
        id="frame_dedup_daily",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    # Variant clustering: daily — re-cluster quotes per frame into named variants
    # via agglomerative complete-linkage on cached embeddings. Powers the
    # variant timeline chart on NarrativeDetail. Clustering itself is
    # deterministic and free; LLM cost only for naming new clusters.
    _scheduler.add_job(
        _run_variant_clustering,
        trigger="interval",
        hours=24,
        id="variant_clustering_daily",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    # Candidate-frame promoter: daily — cluster the candidate_frames staging
    # rows the per-article LLM has been writing. Surfaces promotable
    # suggestions for human review via /api/narrative-frames/candidate-frames/pending.
    # No LLM cost (uses cached embeddings), no auto-writes.
    _scheduler.add_job(
        _run_candidate_frame_promoter,
        trigger="interval",
        hours=24,
        id="candidate_frame_promoter_daily",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    # Also run once at startup so the cache is warm for the first UI load.
    _scheduler.add_job(
        _run_candidate_frame_promoter,
        trigger="date",
        id="candidate_frame_promoter_startup",
        replace_existing=True,
    )
    # V13.10f — Daily HDBSCAN clustering refresh. Runs BEFORE the triage
    # safety net so triage operates on fresh cluster structure even if
    # nobody loaded /landscape recently. Essentially zero cost (no LLM,
    # embeddings cached). Registered first so its initial fire (24h
    # after startup) precedes the safety-net's fire by a few seconds.
    _scheduler.add_job(
        _run_landscape_clustering_refresh,
        trigger="interval",
        hours=24,
        id="landscape_clustering_refresh_daily",
        max_instances=1,
        coalesce=True,
        replace_existing=True,
    )
    # Also fire once at startup so the cache is warm for the first UI load.
    _scheduler.add_job(
        _run_landscape_clustering_refresh,
        trigger="date",
        id="landscape_clustering_refresh_startup",
        replace_existing=True,
    )
    # V13.10e — Daily safety-net for the hands-off auto-promote workflow.
    # No-ops when nothing's been pending >24h; spends ~$0.20–0.50 in LLM
    # cost only when the queue actually has stale work. Matches the
    # established 24h-interval pattern used by other daily jobs above.
    _scheduler.add_job(
        _run_narrative_triage_safety_net,
        trigger="interval",
        hours=24,
        id="narrative_triage_safety_net_daily",
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

    # Bluesky firehose: long-running asyncio task on the same event loop as
    # the scheduler. Subscribes to the public jetstream, filters by campaign
    # keywords, and ingests matched posts in real time. Complements (does
    # NOT replace) the per-profile polling in bluesky_scraper.py.
    if os.environ.get("BLUESKY_FIREHOSE_ENABLED", "true").lower() != "false":
        try:
            from app.services.bluesky_firehose import start_firehose
            start_firehose()
        except Exception:
            log.exception("Failed to start Bluesky firehose")

    log.info("RSS ingestion scheduler started")


def stop_scheduler() -> None:
    """Stop the background scheduler gracefully.  Called during app shutdown."""
    global _scheduler

    if _scheduler is not None:
        log.info("Stopping RSS ingestion scheduler")
        _scheduler.shutdown(wait=False)
        _scheduler = None
        log.info("RSS ingestion scheduler stopped")
    try:
        from app.services.bluesky_firehose import stop_firehose
        stop_firehose()
    except Exception:
        log.exception("Failed to stop Bluesky firehose cleanly")
