import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db import init_db
from app.routes import (
    dashboard, sources, opponents,
    campaign, setup, rss_feeds, review_queue, source_templates,
    admin, source_packs, source_reminders, race_import,
    races, narrative_frames, outlets, ingest, source_monitors,
    analytics, topic_regions, narrative_triage, entity_network, entity_review,
    extractor_drift, claims, claim_records, race_sentiment,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    from app.db import SessionLocal
    from app.seed import seed
    from app.services.scheduler import start_scheduler, stop_scheduler
    with SessionLocal() as db:
        seed(db)
    start_scheduler()

    # Probe each configured LLM provider with a tiny test call so dead
    # models surface immediately at boot rather than only on the next
    # ingestion cycle. Result lines go to the uvicorn log; any provider
    # that returns a deprecation error also gets a loud ERROR-level
    # marker line via _maybe_log_model_deprecation. Runs in a background
    # thread so it doesn't block app readiness — probing 6+ providers
    # over the network can take 5-15s.
    import threading
    def _startup_llm_probe():
        import time, logging as _logging
        time.sleep(2)  # let the rest of startup settle first
        try:
            from app.services.llm_provider import probe_configured_providers
            _llm_log = _logging.getLogger("app.services.llm_provider")
            results = probe_configured_providers()
            _llm_log.info("LLM provider health check at startup:")
            for label, status in results.items():
                level = _logging.ERROR if status == "deprecated" else _logging.INFO
                _llm_log.log(level, "  %-50s → %s", label, status)
        except Exception as e:
            _logging.getLogger("app.services.llm_provider").warning(
                "LLM provider startup probe failed: %s", e
            )
    threading.Thread(target=_startup_llm_probe, daemon=True).start()

    # Clear any accumulated review-queue backlog on startup
    import threading
    def _startup_auto_review():
        import time; time.sleep(3)  # wait for DB to fully settle
        from app.db import SessionLocal
        from app.services.auto_review import auto_review_queue
        with SessionLocal() as db:
            auto_review_queue(db)
    threading.Thread(target=_startup_auto_review, daemon=True).start()

    try:
        yield
    finally:
        stop_scheduler()


app = FastAPI(title="Campaign War Room AI", version="0.3.0", lifespan=lifespan)

_CORS_ORIGINS = [
    o.strip() for o in os.environ.get(
        "CORS_ALLOW_ORIGINS",
        "http://localhost:5173,http://localhost:4173",
    ).split(",") if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_CORS_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

for router in [
    dashboard.router,
    sources.router,
    opponents.router,
    campaign.router,
    setup.router,
    rss_feeds.router,
    review_queue.router,
    source_templates.router,
    admin.router,
    source_packs.router,
    source_reminders.router,
    race_import.router,
    races.router,
    narrative_frames.router,
    outlets.router,
    ingest.router,
    source_monitors.router,
    analytics.router,
    topic_regions.router,
    narrative_triage.router,
    entity_network.router,
    entity_review.router,
    extractor_drift.router,
    claims.router,
    claim_records.router,
    race_sentiment.router,
]:
    app.include_router(router, prefix="/api")

@app.get("/health")
def health():
    return {"status": "ok", "service": "Campaign War Room AI", "version": "0.3.0"}


@app.get("/api/system/llm-status")
def llm_status():
    from app.services.llm_provider import get_provider_status
    return get_provider_status()


@app.get("/api/system/scheduler-health")
def scheduler_health():
    """Scheduler health observability.

    Surfaces last RSS success / skip / error timestamps so the user can
    see "RSS hasn't fired successfully in 3 hours" without tailing
    uvicorn logs. Catches the misleading-signal pattern where APScheduler
    updates last_run_at even when the job throws/skips silently.
    """
    from app.services.scheduler import get_scheduler_health
    return get_scheduler_health()


@app.get("/api/system/firehose-status")
def firehose_status():
    """Bluesky firehose runtime observability.

    Returns connection state, event/match counters, last error, and the
    active keyword set. Used to verify the firehose actually started
    (the prior get_event_loop() bug silently no-op'd the start hook
    and we had no way to tell from outside the process)."""
    from app.services.bluesky_firehose import get_stats, _build_keyword_set
    from app.db import SessionLocal
    stats = get_stats()
    try:
        with SessionLocal() as db:
            stats["active_keywords"] = sorted(_build_keyword_set(db))
    except Exception as e:
        stats["active_keywords_error"] = str(e)
    return stats
