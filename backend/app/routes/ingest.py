"""Routes for non-RSS ingest triggers: crawler and Reddit."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db

router = APIRouter()


@router.post("/ingest/crawl")
def trigger_crawl(db: Session = Depends(get_db)):
    """Immediately crawl all active webpage monitors.

    This is a synchronous call — it blocks until all monitors are crawled.
    For production use the scheduler runs this every 6 hours automatically.
    """
    from app.services.ingestion_crawler import crawl_all_webpage_monitors
    results = crawl_all_webpage_monitors(db)
    return {
        "monitors_crawled": len(results),
        "total_added": sum(r.added for r in results),
        "total_skipped": sum(r.skipped for r in results),
        "total_errors": sum(r.errors for r in results),
        "details": [
            {
                "outlet": r.outlet_domain,
                "attempted": r.attempted,
                "added": r.added,
                "skipped": r.skipped,
                "errors": r.errors,
            }
            for r in results
        ],
    }


@router.post("/ingest/reddit")
def trigger_reddit(db: Session = Depends(get_db)):
    """Immediately run the Reddit ingester.

    Searches configured subreddits for candidate and opponent name mentions.
    The scheduler runs this every 2 hours automatically.
    """
    from app.services.ingestion_reddit import ingest_reddit
    result = ingest_reddit(db)
    return {
        "subreddits_searched": result.subreddits_searched,
        "posts_found": result.posts_found,
        "added": result.added,
        "skipped": result.skipped,
        "errors": result.errors,
    }
