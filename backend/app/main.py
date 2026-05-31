import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.db import init_db
from app.routes import (
    auth,
    dashboard, sources, opponents,
    campaign, setup, rss_feeds, review_queue, source_templates,
    admin, source_packs, source_reminders, race_import,
    races, narrative_frames, outlets, ingest, source_monitors,
    analytics, topic_regions, narrative_triage, entity_network, entity_review,
    extractor_drift, claims, claim_records, race_sentiment,
    global_search, text_overrides, entities, health,
)
from app.services.access_codes import is_auth_configured, lookup_code


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


# Interactive API docs (Swagger / ReDoc / openapi.json) are off by default.
# They enumerate every endpoint + schema, which is recon surface we don't want
# exposed through the public tunnel. Set EXPOSE_API_DOCS=true to turn them on
# (e.g. for local debugging).
_EXPOSE_DOCS = os.environ.get("EXPOSE_API_DOCS", "").lower() in ("1", "true", "yes")

app = FastAPI(
    title="Campaign War Room AI",
    version="0.3.0",
    lifespan=lifespan,
    docs_url="/docs" if _EXPOSE_DOCS else None,
    redoc_url="/redoc" if _EXPOSE_DOCS else None,
    openapi_url="/openapi.json" if _EXPOSE_DOCS else None,
)

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


# Paths that bypass the access-code check. Login + status need to be
# callable without a code (otherwise the login page can't function), and
# the health probe should always work for ops. The docs paths stay listed
# so that *when* EXPOSE_API_DOCS=true they're reachable without a code;
# when docs are disabled (the default) these simply 404, which is harmless.
_AUTH_EXEMPT_PATHS = {
    "/health",
    "/api/auth/verify",
    "/api/auth/me",
    "/docs",
    "/redoc",
    "/openapi.json",
}


@app.middleware("http")
async def access_code_guard(request: Request, call_next):
    """Block /api/* requests that don't carry a valid access code.

    Fails open when no ACCESS_CODES are configured (dev mode) — the moment
    the user adds any code, the gate snaps shut for everything except the
    exempt paths. Non-/api paths (the Vite dev server, future static
    assets, etc.) are never gated by this middleware; the frontend handles
    its own routing-level redirect to /login.
    """
    if not is_auth_configured():
        return await call_next(request)

    path = request.url.path
    if (
        path in _AUTH_EXEMPT_PATHS
        or not path.startswith("/api/")
        or request.method == "OPTIONS"
    ):
        return await call_next(request)

    # Localhost bypass: when the request originated on the user's own
    # machine (someone typed http://localhost:5174 in the browser, not the
    # public tunnel URL), skip the gate so the user and Claude Code's
    # preview can iterate without juggling codes. Vite's proxy rewrites
    # the Host header to localhost:8000, so we rely on X-Forwarded-Host
    # which Vite sets to the browser-visible hostname. Nothing else on
    # this backend writes that header, so trusting it is safe.
    forwarded_host = (request.headers.get("x-forwarded-host") or "").lower()
    if forwarded_host.startswith("localhost") or forwarded_host.startswith("127.0.0.1"):
        return await call_next(request)

    code = request.headers.get("x-access-code") or request.query_params.get(
        "access_code"
    )
    user = lookup_code(code)
    if user is None:
        return JSONResponse(
            status_code=401, content={"detail": "invalid or missing access code"}
        )
    request.state.user = user
    return await call_next(request)


for router in [
    auth.router,
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
    entities.router,
    extractor_drift.router,
    claims.router,
    claim_records.router,
    race_sentiment.router,
    global_search.router,
    text_overrides.router,
    health.router,
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
