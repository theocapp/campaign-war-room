from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import IssueMention, SourceItem
from app.schemas import IssueOut, SourceItemOut, SourceItemDetail, RSSFeedIn, TextSourceIn, URLSourceIn
from app.services import ingestion
from app.services.snapshots import build_source_snapshot, source_out

router = APIRouter()


@router.get("/sources", response_model=list[SourceItemOut])
def list_sources(
    source_type: str | None = None,
    urgency: str | None = None,
    source_filter: str = "all",
    limit: int = 50,
    db: Session = Depends(get_db),
):
    q = db.query(SourceItem).order_by(SourceItem.published_at.desc())
    if source_type:
        q = q.filter(SourceItem.source_type == source_type)
    if urgency:
        q = q.filter(SourceItem.urgency == urgency)
    if source_filter == "relevant":
        q = q.filter(SourceItem.archived_as_irrelevant == False)  # noqa: E712
        q = q.filter(SourceItem.race_relevance_score >= 40)
    elif source_filter == "review_queue":
        q = q.filter(SourceItem.archived_as_irrelevant == False)  # noqa: E712
        q = q.filter(SourceItem.reviewed == False)  # noqa: E712
        q = q.filter(SourceItem.dismissed == False)  # noqa: E712
        q = q.filter(
            (SourceItem.race_relevance_score >= 40)
            | (SourceItem.actionability_label.in_(["review", "respond"]))
        )
    elif source_filter == "archived":
        q = q.filter(SourceItem.archived_as_irrelevant == True)  # noqa: E712
    return [source_out(item) for item in q.limit(min(limit, 200)).all()]


@router.get("/sources/{source_id}", response_model=SourceItemDetail)
def get_source(source_id: int, db: Session = Depends(get_db)):
    item = db.get(SourceItem, source_id)
    if not item:
        raise HTTPException(status_code=404, detail="Source not found")
    detail = SourceItemDetail.model_validate(item)
    detail.summary = source_out(item).summary
    detail.snapshot = build_source_snapshot(db, item)
    related = [
        IssueOut.model_validate(m.issue)
        for m in item.issue_mentions
        if m.issue
    ]
    detail.related_issues = related
    return detail


@router.post("/sources/rss", response_model=list[SourceItemOut])
def add_rss_feed(body: RSSFeedIn, db: Session = Depends(get_db)):
    result = ingestion.ingest_rss(db, body.url, body.label)
    return [source_out(item) for item in result.items]


@router.post("/sources/text", response_model=SourceItemOut)
def add_text_source(body: TextSourceIn, db: Session = Depends(get_db)):
    item = ingestion.ingest_text(
        db,
        title=body.title,
        raw_text=body.raw_text,
        source_name=body.source_name or "Manual Entry",
        source_type=body.source_type,
        source_url=body.source_url,
        published_at=body.published_at,
    )
    return source_out(item)


@router.post("/sources/url", response_model=SourceItemOut)
def add_url_source(body: URLSourceIn, db: Session = Depends(get_db)):
    item = ingestion.ingest_url(db, body.url, body.source_type)
    if not item:
        raise HTTPException(status_code=422, detail="Could not fetch or parse the URL")
    return source_out(item)
