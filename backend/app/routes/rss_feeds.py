from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import RssFeed
from app.schemas import RssFeedOut, RssFeedCreate, RssFeedUpdate, RssFeedIngestResult, SourceItemOut
from app.services import ingestion
from app.services.rss_ingestion import ingest_lock
from app.services.snapshots import source_out

router = APIRouter()


@router.get("/rss-feeds", response_model=list[RssFeedOut])
def list_feeds(db: Session = Depends(get_db)):
    return db.query(RssFeed).order_by(RssFeed.created_at.desc()).all()


@router.get("/rss-feeds/last-synced")
def last_synced(db: Session = Depends(get_db)):
    latest = (
        db.query(RssFeed.last_fetched_at)
        .filter(RssFeed.last_fetched_at.isnot(None))
        .order_by(RssFeed.last_fetched_at.desc())
        .first()
    )
    return {"last_synced_at": latest[0].isoformat() if latest and latest[0] else None}


@router.post("/rss-feeds", response_model=RssFeedOut, status_code=201)
def create_feed(body: RssFeedCreate, db: Session = Depends(get_db)):
    if db.query(RssFeed).filter_by(url=body.url).first():
        raise HTTPException(status_code=409, detail="Feed URL already exists")
    feed = RssFeed(name=body.name, url=body.url, source_type=body.source_type)
    db.add(feed)
    db.commit()
    db.refresh(feed)
    return feed


@router.post("/rss-feeds/ingest-all")
def ingest_all_feeds(db: Session = Depends(get_db)):
    if not ingest_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="Ingestion already running (scheduled job active) — try again shortly",
        )
    try:
        feeds = db.query(RssFeed).filter_by(active=True).all()
        results = []
        from app.services.rss_ingestion import mark_rss_feed_fetched
        for feed in feeds:
            try:
                r = ingestion.ingest_rss(db, feed.url, feed.name)
                feed.last_fetched_at = mark_rss_feed_fetched(db, feed.url)
                results.append({
                    "feed_id": feed.id,
                    "feed_name": feed.name,
                    "added_count": r.added,
                    "skipped_count": r.skipped,
                    "error_count": 0,
                })
            except Exception:
                results.append({
                    "feed_id": feed.id,
                    "feed_name": feed.name,
                    "added_count": 0,
                    "skipped_count": 0,
                    "error_count": 1,
                })
        db.commit()
        try:
            from app.services import briefing_summary
            briefing_summary.invalidate()
        except Exception:
            pass
        return {"feeds_processed": len(feeds), "results": results}
    finally:
        ingest_lock.release()


@router.put("/rss-feeds/{feed_id}", response_model=RssFeedOut)
def update_feed(feed_id: int, body: RssFeedUpdate, db: Session = Depends(get_db)):
    feed = db.get(RssFeed, feed_id)
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")
    if body.name is not None:
        feed.name = body.name
    if body.active is not None:
        feed.active = body.active
    if body.source_type is not None:
        feed.source_type = body.source_type
    db.commit()
    db.refresh(feed)
    return feed


@router.delete("/rss-feeds/{feed_id}", status_code=204)
def delete_feed(feed_id: int, db: Session = Depends(get_db)):
    feed = db.get(RssFeed, feed_id)
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")
    db.delete(feed)
    db.commit()


@router.post("/rss-feeds/{feed_id}/ingest", response_model=RssFeedIngestResult)
def ingest_feed(feed_id: int, db: Session = Depends(get_db)):
    feed = db.get(RssFeed, feed_id)
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")
    result = ingestion.ingest_rss(db, feed.url, feed.name)
    from app.services.rss_ingestion import mark_rss_feed_fetched
    feed.last_fetched_at = mark_rss_feed_fetched(db, feed.url)
    db.commit()
    return RssFeedIngestResult(
        feed_id=feed_id,
        added_count=result.added,
        skipped_count=result.skipped,
        error_count=0,
        added_items=[source_out(s) for s in result.items],
    )
