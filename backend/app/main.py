from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.db import init_db
from app.routes import (
    dashboard, sources, issues, opponents, canvassing,
    talking_points, campaign, setup, rss_feeds, review_queue, source_templates,
    admin, source_packs, source_reminders, race_import,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    from app.db import SessionLocal
    from app.seed import seed
    with SessionLocal() as db:
        seed(db)
    yield


app = FastAPI(title="Campaign War Room AI", version="0.2.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:4173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

for router in [
    dashboard.router,
    sources.router,
    issues.router,
    opponents.router,
    canvassing.router,
    talking_points.router,
    campaign.router,
    setup.router,
    rss_feeds.router,
    review_queue.router,
    source_templates.router,
    admin.router,
    source_packs.router,
    source_reminders.router,
    race_import.router,
]:
    app.include_router(router, prefix="/api")

app.version = "0.3.0"


@app.get("/health")
def health():
    return {"status": "ok", "service": "Campaign War Room AI", "version": "0.2.0"}
