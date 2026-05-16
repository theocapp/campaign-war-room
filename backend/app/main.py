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
]:
    app.include_router(router, prefix="/api")

@app.get("/health")
def health():
    return {"status": "ok", "service": "Campaign War Room AI", "version": "0.3.0"}


@app.get("/api/system/llm-status")
def llm_status():
    from app.services.llm_provider import get_provider_status
    return get_provider_status()
