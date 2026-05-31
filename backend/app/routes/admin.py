"""Admin / workspace management endpoints."""
from app.services import rescore as rescore_svc
import json
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import DATABASE_URL, engine, get_db, pool_stats
from app.models import (
    CampaignConfig, SourceItem, Issue, IssueMention,
    Opponent, OpponentActivity, RssFeed, SourceMonitor,
    NarrativeFrame, NarrativeFrameMention,
    StoryCluster, FrameClusterMatch, ClusterOpponentActivity,
    CandidateFrame, FrameVariant, FrameStageHistory,
    GoogleTrendSnapshot, GdeltToneSnapshot,
)
from app.schemas import (
    ReanalyzeSourcesRequest,
    ReanalyzeSourcesResult,
    RescoreArticlesRequest,
    ResetWorkspaceRequest,
    ResetWorkspaceResult,
)
from app.services.access_codes import require_admin
from app.services.reanalysis import ReanalysisOptions, reanalyze_sources

router = APIRouter()

# Dependency list applied to all LLM-cost / destructive admin endpoints below.
# Read-only observability endpoints (dbstats, rescore-status) intentionally
# stay open to non-admin users so the rest of the team can see what's running.
_admin_only = [Depends(require_admin)]


@router.get("/admin/dbstats")
def db_stats():
    """Operational view of database health — pool state, active queries,
    long-running statements, lock waits. Dialect-aware: SQLite returns
    a minimal stub; Postgres surfaces `pg_stat_activity` slices.

    Use during the SQLite → Postgres migration soak test (Phase 2.5) and
    after Phase 4 cutover to spot pool exhaustion, lock pile-ups, or
    rogue long-running queries without tailing the server log.
    """
    pool = engine.pool
    info = {
        "url_dialect": engine.dialect.name,
        "url_redacted": _redact_url(DATABASE_URL),
        "pool": {
            "size": pool.size() if hasattr(pool, "size") else None,
            "checked_out": pool.checkedout() if hasattr(pool, "checkedout") else None,
            "overflow": pool.overflow() if hasattr(pool, "overflow") else None,
            "lifetime_connects": pool_stats.connects,
            "lifetime_checkouts": pool_stats.checkouts,
            "lifetime_checkins": pool_stats.checkins,
            "lifetime_invalidations": pool_stats.invalidations,
            "last_invalidate_at": (
                pool_stats.last_invalidate_at.isoformat() + "Z"
                if pool_stats.last_invalidate_at else None
            ),
            "last_invalidate_reason": pool_stats.last_invalidate_reason,
        },
    }

    if engine.dialect.name == "postgresql":
        info["postgres"] = _postgres_stats()
    elif engine.dialect.name == "sqlite":
        info["sqlite"] = _sqlite_stats()

    return info


def _redact_url(url: str) -> str:
    """Hide any password in the URL before returning to the client."""
    import re
    return re.sub(r"://([^:/@]+):([^@]+)@", r"://\1:****@", url)


def _postgres_stats() -> dict:
    """Snapshot of pg_stat_activity for connections to this database, plus
    a count of any locks waiting > 1 second."""
    with engine.connect() as conn:
        # Active queries longer than 1s on this DB
        slow = conn.execute(text("""
            SELECT pid,
                   EXTRACT(EPOCH FROM (now() - query_start))::int AS runtime_seconds,
                   state,
                   wait_event_type,
                   wait_event,
                   LEFT(query, 200) AS query
            FROM pg_stat_activity
            WHERE datname = current_database()
              AND state != 'idle'
              AND query_start IS NOT NULL
              AND now() - query_start > interval '1 second'
            ORDER BY query_start ASC
            LIMIT 10
        """)).fetchall()

        # Lock waiters
        lock_waits = conn.execute(text("""
            SELECT pid, wait_event_type, wait_event, state,
                   EXTRACT(EPOCH FROM (now() - state_change))::int AS waiting_seconds,
                   LEFT(query, 200) AS query
            FROM pg_stat_activity
            WHERE datname = current_database()
              AND wait_event_type = 'Lock'
            ORDER BY state_change ASC
            LIMIT 10
        """)).fetchall()

        # Per-DB summary stats
        summary = conn.execute(text("""
            SELECT numbackends, xact_commit, xact_rollback,
                   blks_read, blks_hit, deadlocks
            FROM pg_stat_database
            WHERE datname = current_database()
        """)).fetchone()

    return {
        "connections_in_use": summary.numbackends if summary else None,
        "lifetime_commits": summary.xact_commit if summary else None,
        "lifetime_rollbacks": summary.xact_rollback if summary else None,
        "lifetime_deadlocks": summary.deadlocks if summary else None,
        "cache_hit_ratio": (
            round(summary.blks_hit / (summary.blks_hit + summary.blks_read) * 100, 2)
            if summary and (summary.blks_hit + summary.blks_read) > 0 else None
        ),
        "slow_queries": [
            {
                "pid": r.pid,
                "runtime_seconds": r.runtime_seconds,
                "state": r.state,
                "wait_event": f"{r.wait_event_type}:{r.wait_event}" if r.wait_event else None,
                "query": r.query,
            }
            for r in slow
        ],
        "lock_waiters": [
            {
                "pid": r.pid,
                "waiting_seconds": r.waiting_seconds,
                "wait_event": f"{r.wait_event_type}:{r.wait_event}",
                "state": r.state,
                "query": r.query,
            }
            for r in lock_waits
        ],
    }


def _sqlite_stats() -> dict:
    """SQLite has no equivalent of pg_stat_activity. Surface just the
    journal mode + a row count for the biggest table as a smoke check."""
    with engine.connect() as conn:
        journal = conn.execute(text("PRAGMA journal_mode")).scalar()
        wal_autocheckpoint = conn.execute(text("PRAGMA wal_autocheckpoint")).scalar()
        article_count = conn.execute(text("SELECT COUNT(*) FROM source_items")).scalar()
    return {
        "journal_mode": journal,
        "wal_autocheckpoint": wal_autocheckpoint,
        "source_items_count": article_count,
    }


@router.post("/admin/reset-workspace", response_model=ResetWorkspaceResult, dependencies=_admin_only)
def reset_workspace(body: ResetWorkspaceRequest, db: Session = Depends(get_db)):
    if body.confirm != "RESET WORKSPACE":
        raise HTTPException(
            status_code=400,
            detail="Confirmation string must be exactly 'RESET WORKSPACE'",
        )

    # Count before deletion for the result summary
    n_sources = db.query(SourceItem).count()
    n_issues = db.query(Issue).count()
    n_opponents = db.query(Opponent).count()
    n_frames = db.query(NarrativeFrame).count()
    n_feeds = db.query(RssFeed).count()

    # Delete in dependency order. We must delete EVERY table that has
    # rows tied to the current campaign — leaving any behind reproduces
    # the orphan-cluster bug from 2026-05-23 (deleted SourceItems left
    # orphan StoryClusters whose ids collided with new auto-increments,
    # blocking all ingestion for 2 hours). In particular: never delete
    # SourceItem without also deleting StoryCluster + FrameClusterMatch
    # + ClusterOpponentActivity + CandidateFrame.
    #
    # Order: children before parents so FK-pointing rows are gone first.
    db.query(IssueMention).delete()
    db.query(OpponentActivity).delete()
    db.query(ClusterOpponentActivity).delete()
    db.query(NarrativeFrameMention).delete()
    db.query(FrameClusterMatch).delete()
    db.query(FrameVariant).delete()
    db.query(FrameStageHistory).delete()
    db.query(CandidateFrame).delete()
    db.query(StoryCluster).delete()
    db.query(SourceItem).delete()
    db.query(Issue).delete()
    db.query(Opponent).delete()
    db.query(NarrativeFrame).delete()
    db.query(SourceMonitor).delete()
    # Time-series snapshot tables (also tied to the current campaign).
    db.query(GoogleTrendSnapshot).delete()
    db.query(GdeltToneSnapshot).delete()

    preserved_feeds = 0
    cleared_feeds = 0
    if body.preserve_feeds:
        preserved_feeds = n_feeds
    else:
        db.query(RssFeed).delete()
        cleared_feeds = n_feeds

    # Replace campaign config
    db.query(CampaignConfig).delete()
    config = CampaignConfig(
        candidate_name=body.candidate_name,
        office=body.office,
        district=body.district,
        party=body.party,
        location=body.location,
        sparse_race_mode=False,
        election_date=body.election_date,
        campaign_message=body.campaign_message,
        key_priorities=json.dumps(body.key_priorities) if body.key_priorities else None,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(config)
    db.commit()

    return ResetWorkspaceResult(
        cleared_sources=n_sources,
        cleared_issues=n_issues,
        cleared_opponents=n_opponents,
        cleared_narrative_frames=n_frames,
        cleared_feeds=cleared_feeds,
        preserved_feeds=preserved_feeds,
        candidate_name=body.candidate_name,
    )


@router.post("/admin/reanalyze-sources", response_model=ReanalyzeSourcesResult, dependencies=_admin_only)
def reanalyze_sources_endpoint(body: ReanalyzeSourcesRequest, db: Session = Depends(get_db)):
    if body.confirm != "REANALYZE SOURCES":
        raise HTTPException(
            status_code=400,
            detail="Confirmation string must be exactly 'REANALYZE SOURCES'",
        )

    return reanalyze_sources(
        db,
        ReanalysisOptions(
            limit=body.limit,
            source_id=body.source_id,
            include_reviewed=body.include_reviewed,
            include_dismissed=body.include_dismissed,
            include_archived=body.include_archived,
            dry_run=body.dry_run,
        ),
    )


@router.post("/admin/rescore-articles", dependencies=_admin_only)
def start_rescore(
    body: RescoreArticlesRequest,
    db: Session = Depends(get_db),
):
    """Start background LLM rescoring of articles. Returns immediately.

    `only_unscored=true` skips articles that already have a summary, so a
    rescore can resume without redoing work already completed. This is the
    safe path — no confirm required.

    `only_unscored=false` (default) rescores EVERY article in the corpus —
    21k+ items at ~2/min, i.e. a multi-day, real-money LLM run. To prevent
    a single misclick from triggering that, the body must include
    `confirm: "RESCORE ALL ARTICLES"` in this case.

    `auto_rematch=true` chains a frame-rematch job once rescoring finishes.
    `max_workers=null` auto-sizes to one worker per loaded LLM key (default).

    Internal callers (campaign.py backfill resume, scheduler.py pipeline
    resume) hit `rescore_svc.start_rescore` directly without going through
    this endpoint, so they're unaffected by the confirm requirement.
    """
    if not body.only_unscored and body.confirm != "RESCORE ALL ARTICLES":
        raise HTTPException(
            status_code=400,
            detail=(
                "Full rescore requires confirm string exactly "
                "'RESCORE ALL ARTICLES' (multi-day LLM run over 21k+ articles). "
                "Pass only_unscored=true to score just new articles without "
                "confirmation."
            ),
        )
    return rescore_svc.start_rescore(
        db,
        only_unscored=body.only_unscored,
        auto_rematch=body.auto_rematch,
        max_workers=body.max_workers,
    )


@router.get("/admin/rescore-status")
def rescore_status():
    """Check progress of the background rescore job."""
    return rescore_svc.get_status()


@router.post("/admin/rescore-stop", dependencies=_admin_only)
def stop_rescore():
    """Stop the background rescore job early."""
    return rescore_svc.stop_rescore()


@router.post("/admin/auto-review", dependencies=_admin_only)
def run_auto_review(db: Session = Depends(get_db)):
    """Immediately triage the review queue: auto-approve high-confidence items,
    auto-dismiss clearly irrelevant ones. Safe to call repeatedly."""
    from app.services.auto_review import auto_review_queue
    return auto_review_queue(db)


@router.post("/admin/discover-outlets", dependencies=_admin_only)
def run_outlet_discovery(force: bool = False, db: Session = Depends(get_db)):
    """Trigger LLM-based outlet discovery for the active campaign.

    force=false (default): only runs for districts not in the hardcoded catalog
                            and not already covered in the DB. No-op on PA-08.
    force=true: runs LLM discovery even for curated districts to augment the
                outlet list. Idempotent — skips outlets that already exist.
    """
    from app.models import CampaignConfig
    from app.services.monitors import _auto_discover_outlets
    from app.services.source_discovery import _parse_state_code

    campaign = db.query(CampaignConfig).first()
    if not campaign or not campaign.district:
        return {"created": 0, "reason": "No campaign config or district not set"}

    state_code = _parse_state_code(campaign.district, campaign.location)
    created = _auto_discover_outlets(
        db,
        district=campaign.district,
        state_code=state_code,
        location=campaign.location,
        candidate=campaign.candidate_name,
        force=force,
    )
    return {
        "created": created,
        "district": campaign.district,
        "force": force,
    }


@router.post("/admin/discover-monitor-urls", dependencies=_admin_only)
def run_monitor_url_discovery(db: Session = Depends(get_db)):
    """Auto-discover URLs for manual website monitor placeholders and convert
    successful ones to webpage monitors.

    Idempotent. Honors a 24h cooldown per monitor (RETRY_COOLDOWN_HOURS) so
    repeated clicks don't hammer the search API. Returns a summary with
    per-monitor outcomes — see monitor_url_discovery.convert_website_manuals_to_webpages
    for the response shape.

    Phase 2 scope: candidate + opponent campaign websites only. Other manual
    placeholders (election boards, council agendas) are not auto-discovered
    yet.
    """
    from app.services.monitor_url_discovery import convert_website_manuals_to_webpages
    return convert_website_manuals_to_webpages(db)


@router.post("/admin/prune-rss-feeds", dependencies=_admin_only)
def run_feed_prune(
    dry_run: bool = True,
    window_days: int = 30,
    min_volume: int = 30,
    db: Session = Depends(get_db),
):
    """Trigger the RSS feed prune (manual mirror of the weekly scheduled job).

    dry_run=true (default): returns the would-prune list without writing.
    dry_run=false: deactivates each eligible feed (active=False).

    A feed is eligible when it ingested >= `min_volume` items over the last
    `window_days` and ZERO were race-relevant. Search-query feeds (Google
    News, Reddit) and YouTube channels are exempt.

    Reactivate any pruned feed via PUT /api/rss-feeds/{id} with {"active": true}.
    """
    from app.services.feed_prune import prune_zero_yield_feeds
    return prune_zero_yield_feeds(
        db,
        window_days=window_days,
        min_volume=min_volume,
        dry_run=dry_run,
    )


@router.post("/admin/discover-feeds-yield", dependencies=_admin_only)
def run_feed_discovery_yield(
    dry_run: bool = True,
    window_days: int = 90,
    min_survivors: int = 3,
    db: Session = Depends(get_db),
):
    """Discover RSS feeds for publishers proven to cover the race via
    Google News search survivors. Manual mirror of the weekly scheduled job.

    dry_run=true (default): returns the would-add list without writing.
    dry_run=false: adds each discovered feed (active=True).

    A domain is eligible when it has >= `min_survivors` race-relevant articles
    via Google News search in the last `window_days`, isn't blocklisted, and
    isn't already in rss_feeds (active or inactive).
    """
    from app.services.feed_discovery_yield import (
        discover_feeds_from_google_news_yield,
    )
    return discover_feeds_from_google_news_yield(
        db,
        window_days=window_days,
        min_survivors=min_survivors,
        dry_run=dry_run,
    )


@router.post("/admin/backfill-publisher-domain", dependencies=_admin_only)
def run_backfill_publisher_domain(window_days: int = 30, db: Session = Depends(get_db)):
    """One-time backfill: refetch active Google News feeds and populate
    publisher_domain on existing items by matching titles.

    Recovers what's currently in each Google News feed's window (~last
    50-100 entries per feed). Items older than the feed window stay NULL;
    going forward, new ingests populate publisher_domain directly.
    """
    from app.services.feed_discovery_yield import (
        backfill_publisher_domain_from_google_news,
    )
    return backfill_publisher_domain_from_google_news(
        db, window_days=window_days
    )


@router.get("/admin/scheduler/jobs", dependencies=_admin_only)
def list_scheduler_jobs():
    """Diagnostic dump of every APScheduler job's `next_run_time`.

    Added 2026-05-30 after the RSS auto-ingest stopped firing in the live
    worker even though GDELT/Reddit on the same scheduler were still
    ticking. With this we can see if a job's `next_run_time` is None
    (paused / unscheduled) or absurdly far in the future (max_instances=1
    stuck thinking a prior run is still active).
    """
    from app.services.scheduler import _scheduler  # noqa
    if _scheduler is None or not getattr(_scheduler, "running", False):
        return {"running": False, "jobs": []}
    out = []
    for job in _scheduler.get_jobs():
        out.append({
            "id": job.id,
            "name": job.name,
            "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
            "trigger": str(job.trigger),
            "max_instances": getattr(job, "max_instances", None),
            "pending": getattr(job, "pending", None),
        })
    out.sort(key=lambda j: j["next_run_time"] or "9999")
    return {"running": True, "jobs": out}


@router.post("/admin/scheduler/run/{job_id}", dependencies=_admin_only)
def trigger_scheduler_job(job_id: str):
    """Force a scheduler job to fire immediately by rewriting its
    `next_run_time` to now.

    Workaround for the symptom that surfaced 2026-05-30: a previously-
    running job leaves the slot held under `max_instances=1` and every
    subsequent tick gets silently skipped. Forcing a new fire bypasses
    APScheduler's interval bookkeeping for one cycle without restarting
    the whole worker.
    """
    from datetime import datetime
    from app.services.scheduler import _scheduler  # noqa
    if _scheduler is None or not getattr(_scheduler, "running", False):
        return {"ok": False, "error": "scheduler not running"}
    job = _scheduler.get_job(job_id)
    if job is None:
        return {"ok": False, "error": f"no such job: {job_id}"}
    _scheduler.modify_job(job_id, next_run_time=datetime.utcnow())
    return {
        "ok": True,
        "job_id": job_id,
        "next_run_time": datetime.utcnow().isoformat(),
    }


@router.post("/admin/youtube-discovery/run", dependencies=_admin_only)
def run_youtube_discovery_endpoint():
    """Find and add YouTube channels for known outlets + the campaign's
    candidate + opponents that don't have a YouTube feed yet.

    Outlets are discovered by scraping their homepage for YouTube
    links. Candidates are discovered via LLM lookup (gpt-4o-mini) with
    strict verification against the channel's recent video titles.
    Both paths are idempotent — re-running skips subjects that already
    have a feed.

    When a candidate's direct channel is added, the matching
    `YouTube: {Surname}` Google News search feed is deactivated since
    it only produces title-only stubs after the 2026-05-26 GN format
    change.
    """
    from app.db import SessionLocal
    from app.services.youtube_discovery import run_youtube_discovery
    with SessionLocal() as db:
        return run_youtube_discovery(db)


@router.post("/admin/dedup-merge", dependencies=_admin_only)
def run_dedup_merge_endpoint(hours_back: int = 96, max_stubs: int = 1000):
    """Post-hoc duplicate detection + merge.

    Finds short-body items in the trailing window that have a
    longer-body sibling already in the DB (same article on a different
    `source_url`), and marks the stub as `archived_as_irrelevant=True`
    with a structured `relevance_reasons` entry pointing at the
    canonical. Safe to re-run — idempotent on already-merged rows.

    See `services/dedup_merge.py` for the matching thresholds and
    safety guards.
    """
    from app.db import SessionLocal
    from app.services.dedup_merge import run_dedup_merge
    with SessionLocal() as db:
        return run_dedup_merge(
            db, hours_back=hours_back, max_stubs=max_stubs,
        )


@router.post("/admin/rescore-recovered-bodies", dependencies=_admin_only)
def rescore_recovered_bodies(window_hours: int = 96):
    """Inline re-analyze items whose raw_text was just rewritten by the
    body-recovery sweep.

    Targets items with `summary IS NULL` AND `raw_text length >= 200`
    AND `created_at >= now - window_hours` — i.e. items where the recovery
    sweep produced a real body but cleared the LLM summary so they need
    re-scoring. Avoids triggering the 14k+ full unscored backlog, which
    would take days at ~2 articles/min.

    Runs synchronously through the same `_process_item` worker the bulk
    rescore uses, so scoring behavior stays identical. Returns when all
    targeted items have been scored or any unrecoverable error fires.
    """
    from app.db import SessionLocal
    from app.models import SourceItem
    from app.services.rescore import _load_providers, _process_item
    from sqlalchemy import func
    from datetime import datetime, timedelta

    cutoff = datetime.utcnow() - timedelta(hours=window_hours)
    with SessionLocal() as db:
        ids = [
            row[0]
            for row in db.query(SourceItem.id)
            .filter(
                SourceItem.created_at >= cutoff,
                SourceItem.raw_text.isnot(None),
                func.length(SourceItem.raw_text) >= 200,
                SourceItem.summary.is_(None),
                SourceItem.archived_as_irrelevant == False,  # noqa: E712
            )
            .order_by(SourceItem.id.asc())
            .all()
        ]

    providers = _load_providers()
    if not providers:
        return {"error": "no LLM providers configured", "checked": 0}

    # Round-robin through providers so we don't hammer one key.
    scored = 0
    errors = 0
    for i, item_id in enumerate(ids):
        provider = providers[i % len(providers)]
        try:
            _process_item(item_id, provider)
            scored += 1
        except Exception:
            errors += 1
    return {"checked": len(ids), "scored": scored, "errors": errors}


@router.post("/admin/recover-stub-bodies", dependencies=_admin_only)
def recover_stub_bodies_endpoint(window_hours: int = 96, max_items: int = 1000):
    """One-shot sweep: for items ingested with short raw_text (< 200 chars)
    in the trailing window, try `recover_body` and rewrite raw_text on
    success.

    Recovered items get `summary = None` so the existing rescore worker
    (`/admin/rescore-articles?only_unscored=true`) re-runs LLM scoring
    against the fuller body.

    Idempotent: re-runs are cheap — items already rewritten skip the
    length filter and never hit `recover_body` twice.
    """
    from app.db import SessionLocal
    from app.services.article_body_recovery import recover_stub_bodies
    with SessionLocal() as db:
        return recover_stub_bodies(
            db, window_hours=window_hours, max_items=max_items,
        )


@router.post("/admin/recover-missed-articles", dependencies=_admin_only)
def recover_missed_articles(days_back: int = 4, force: bool = True):
    """Backfill missed articles via GDELT BigQuery for the last N days.

    Use case: after the 2026-05-26 Google News body-collapse + the
    scheduler-starvation issue, we have a 4-day gap in coverage from
    Google-News-mediated feeds. GDELT indexes the same articles
    independently and we have BigQuery access — this pulls every article
    referencing the candidate's surname (and opponent surnames) over
    the window, dedups against our existing `source_url`, scrapes the
    publisher, and runs the normal ingest pipeline.

    `force=True` bypasses the `extended_backfill_completed` gate because
    that flag is for the one-time historical seed, not for catch-up
    runs like this one.

    Returns the same shape as the existing GDELT backfill — number of
    URLs discovered, added, skipped, failed. Runs synchronously and
    can take several minutes; the UI's pipeline-status endpoint shows
    progress while it's in flight.
    """
    from app.db import SessionLocal
    from app.services.gdelt_bigquery import run_gdelt_bigquery_backfill
    # Open a fresh session — the backfill is long-running and shouldn't
    # share a session with the request lifecycle (the request session
    # is committed/closed by Depends(get_db) after this returns).
    with SessionLocal() as db:
        return run_gdelt_bigquery_backfill(db, force=force, days_back=days_back)
