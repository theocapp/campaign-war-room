"""Routes for non-RSS ingest triggers: crawler and Reddit."""
from datetime import datetime
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db

router = APIRouter()

# In-memory last-run timestamps — reset on server restart (intentional: causes
# a fresh run on first app open after a restart, which is always desirable).
_last_crawl_at: datetime | None = None
_last_reddit_at: datetime | None = None


@router.get("/ingest/status")
def ingest_status():
    """Return when the crawler and Reddit ingester last ran."""
    return {
        "last_crawl_at":  _last_crawl_at.isoformat()  if _last_crawl_at  else None,
        "last_reddit_at": _last_reddit_at.isoformat() if _last_reddit_at else None,
    }


@router.post("/ingest/crawl")
def trigger_crawl(db: Session = Depends(get_db)):
    """Immediately crawl all active webpage monitors."""
    global _last_crawl_at
    from app.services.ingestion_crawler import crawl_all_webpage_monitors
    results = crawl_all_webpage_monitors(db)
    _last_crawl_at = datetime.utcnow()
    return {
        "monitors_crawled": len(results),
        "total_added": sum(r.added for r in results),
        "total_skipped": sum(r.skipped for r in results),
        "total_errors": sum(r.errors for r in results),
    }


@router.post("/ingest/reddit")
def trigger_reddit(db: Session = Depends(get_db)):
    """Immediately run the Reddit ingester."""
    global _last_reddit_at
    from app.services.ingestion_reddit import ingest_reddit
    result = ingest_reddit(db)
    _last_reddit_at = datetime.utcnow()
    return {
        "subreddits_searched": result.subreddits_searched,
        "posts_found": result.posts_found,
        "added": result.added,
        "skipped": result.skipped,
        "errors": result.errors,
    }
