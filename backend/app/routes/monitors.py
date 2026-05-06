import json
from datetime import datetime
import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import CampaignConfig, Opponent, RssFeed, SourceItem, SourceMonitor
from app.schemas import (
    GenerateMonitorsRequest,
    GenerateMonitorsResult,
    IngestSearchMonitorsResult,
    MonitorIngestItem,
    MonitorIngestResult,
    SourceMonitorBase,
    SourceMonitorCreate,
    SourceMonitorOut,
    SourceMonitorUpdate,
)
from app.services import ingestion
from app.services.search_provider import get_search_provider
from app.services.source_discovery import generate_monitors_for_campaign

router = APIRouter()

MONITOR_TYPES = {"rss", "search_query", "manual", "webpage"}


def _json_list(value: list[str] | None) -> str | None:
    return json.dumps(value) if value is not None else None


def _to_create(data: SourceMonitorBase | dict) -> dict:
    values = data.model_dump() if hasattr(data, "model_dump") else dict(data)
    if values.get("monitor_type") not in MONITOR_TYPES:
        raise HTTPException(status_code=422, detail="Invalid monitor_type")
    values["required_terms"] = _json_list(values.get("required_terms"))
    values["excluded_terms"] = _json_list(values.get("excluded_terms"))
    return values


def _duplicate_query(db: Session, values: dict):
    q = db.query(SourceMonitor).filter(SourceMonitor.monitor_type == values["monitor_type"])
    clauses = []
    if values.get("query"):
        clauses.append(SourceMonitor.query == values["query"])
    if values.get("url"):
        clauses.append(SourceMonitor.url == values["url"])
    clauses.append(SourceMonitor.name == values["name"])
    return q.filter(or_(*clauses)).first()


def _ensure_rss_feed(db: Session, monitor: SourceMonitor) -> None:
    if monitor.monitor_type != "rss" or not monitor.url:
        return
    existing = db.query(RssFeed).filter_by(url=monitor.url).first()
    if existing:
        return
    db.add(RssFeed(name=monitor.name, url=monitor.url, source_type=monitor.source_type or "news"))


def _ingest_rss_monitor(db: Session, monitor: SourceMonitor) -> MonitorIngestResult:
    if not monitor.url:
        raise HTTPException(status_code=422, detail="RSS monitor needs a URL before ingestion")
    result = ingestion.ingest_rss(db, monitor.url, monitor.name)
    monitor.last_checked_at = datetime.utcnow()
    monitor.updated_at = datetime.utcnow()
    db.commit()
    return MonitorIngestResult(
        monitor_id=monitor.id,
        monitor_name=monitor.name,
        monitor_type=monitor.monitor_type,
        added_count=result.added,
        skipped_count=result.skipped,
        failed_count=0,
        results=[
            MonitorIngestItem(
                title=item.title,
                url=item.source_url,
                status="added",
                source_id=item.id,
                relevance_label=item.race_relevance_label,
                relevance_score=item.race_relevance_score,
                archived_as_irrelevant=item.archived_as_irrelevant,
            )
            for item in result.items
        ],
    )


def _soft_duplicate(db: Session, title: str | None, source_name: str | None) -> SourceItem | None:
    title_key = re.sub(r"\s+", " ", (title or "").strip()).lower()
    source_key = re.sub(r"\s+", " ", (source_name or "").strip()).lower()
    if not title_key or not source_key:
        return None
    candidates = db.query(SourceItem).filter(SourceItem.source_name.isnot(None)).limit(500).all()
    for item in candidates:
        item_title = re.sub(r"\s+", " ", (item.title or "").strip()).lower()
        item_source = re.sub(r"\s+", " ", (item.source_name or "").strip()).lower()
        if item_title == title_key and item_source == source_key:
            return item
    return None


def _ingest_search_monitor(db: Session, monitor: SourceMonitor) -> MonitorIngestResult:
    if not monitor.query:
        raise HTTPException(status_code=422, detail="Search-query monitor needs a query before ingestion")
    provider = get_search_provider()
    try:
        response = provider.search(monitor.query, limit=10)
    except Exception as exc:
        message = f"Search provider failed: {exc}"
        monitor.last_checked_at = datetime.utcnow()
        monitor.updated_at = datetime.utcnow()
        db.commit()
        return MonitorIngestResult(
            monitor_id=monitor.id,
            monitor_name=monitor.name,
            monitor_type=monitor.monitor_type,
            provider=getattr(provider, "name", "unknown"),
            message=message,
            failed_count=1,
            results=[MonitorIngestItem(status="failed", reason=message)],
        )
    items: list[MonitorIngestItem] = []
    added = skipped = failed = 0

    for result in response.results[:10]:
        if not result.url:
            failed += 1
            items.append(MonitorIngestItem(title=result.title, status="failed", reason="Search result had no URL"))
            continue
        existing = db.query(SourceItem).filter_by(source_url=result.url).first()
        if existing:
            skipped += 1
            items.append(MonitorIngestItem(
                title=result.title,
                url=result.url,
                status="skipped",
                source_id=existing.id,
                reason="Duplicate URL",
                relevance_label=existing.race_relevance_label,
                relevance_score=existing.race_relevance_score,
                archived_as_irrelevant=existing.archived_as_irrelevant,
            ))
            continue
        existing = _soft_duplicate(db, result.title, result.source_name)
        if existing:
            skipped += 1
            items.append(MonitorIngestItem(
                title=result.title,
                url=result.url,
                status="skipped",
                source_id=existing.id,
                reason="Duplicate title and source",
                relevance_label=existing.race_relevance_label,
                relevance_score=existing.race_relevance_score,
                archived_as_irrelevant=existing.archived_as_irrelevant,
            ))
            continue
        created = ingestion.ingest_url(db, result.url, monitor.source_type or "news")
        if not created:
            failed += 1
            items.append(MonitorIngestItem(
                title=result.title,
                url=result.url,
                status="failed",
                reason="Could not fetch or parse result URL",
            ))
            continue
        added += 1
        items.append(MonitorIngestItem(
            title=created.title,
            url=created.source_url,
            status="added",
            source_id=created.id,
            relevance_label=created.race_relevance_label,
            relevance_score=created.race_relevance_score,
            archived_as_irrelevant=created.archived_as_irrelevant,
        ))

    monitor.last_checked_at = datetime.utcnow()
    monitor.updated_at = datetime.utcnow()
    db.commit()
    return MonitorIngestResult(
        monitor_id=monitor.id,
        monitor_name=monitor.name,
        monitor_type=monitor.monitor_type,
        provider=response.provider,
        message=response.message,
        added_count=added,
        skipped_count=skipped,
        failed_count=failed,
        results=items,
    )


@router.get("/monitors", response_model=list[SourceMonitorOut])
def list_monitors(monitor_type: str | None = None, db: Session = Depends(get_db)):
    q = db.query(SourceMonitor).order_by(SourceMonitor.created_at.desc())
    if monitor_type:
        q = q.filter(SourceMonitor.monitor_type == monitor_type)
    return q.all()


@router.post("/monitors", response_model=SourceMonitorOut, status_code=201)
def create_monitor(body: SourceMonitorCreate, db: Session = Depends(get_db)):
    values = _to_create(body)
    existing = _duplicate_query(db, values)
    if existing:
        raise HTTPException(status_code=409, detail="Monitor already exists")
    monitor = SourceMonitor(**values)
    db.add(monitor)
    db.flush()
    _ensure_rss_feed(db, monitor)
    db.commit()
    db.refresh(monitor)
    return monitor


@router.post("/monitors/generate", response_model=GenerateMonitorsResult)
def generate_monitors(body: GenerateMonitorsRequest, db: Session = Depends(get_db)):
    campaign = db.query(CampaignConfig).first()
    if not campaign:
        raise HTTPException(status_code=422, detail="Campaign profile is required")
    suggestions = [SourceMonitorBase(**s) for s in generate_monitors_for_campaign(campaign, db.query(Opponent).all())]
    if not body.apply:
        return GenerateMonitorsResult(suggestions=suggestions)

    if body.replace_existing:
        db.query(SourceMonitor).delete()
        db.flush()

    created: list[SourceMonitor] = []
    skipped = 0
    for suggestion in suggestions:
        values = _to_create(suggestion)
        if _duplicate_query(db, values):
            skipped += 1
            continue
        monitor = SourceMonitor(**values)
        db.add(monitor)
        db.flush()
        _ensure_rss_feed(db, monitor)
        created.append(monitor)
    db.commit()
    for monitor in created:
        db.refresh(monitor)
    return GenerateMonitorsResult(
        suggestions=suggestions,
        created_count=len(created),
        skipped_duplicates=skipped,
        monitors=created,
    )


@router.post("/monitors/ingest-search", response_model=IngestSearchMonitorsResult)
def ingest_all_search_monitors(db: Session = Depends(get_db)):
    monitors = (
        db.query(SourceMonitor)
        .filter(SourceMonitor.monitor_type == "search_query")
        .filter(SourceMonitor.active == True)  # noqa: E712
        .order_by(SourceMonitor.created_at.asc())
        .all()
    )
    results: list[MonitorIngestResult] = []
    for monitor in monitors:
        try:
            results.append(_ingest_search_monitor(db, monitor))
        except Exception as exc:
            results.append(MonitorIngestResult(
                monitor_id=monitor.id,
                monitor_name=monitor.name,
                monitor_type=monitor.monitor_type,
                message=f"Monitor ingestion failed: {exc}",
                failed_count=1,
                results=[MonitorIngestItem(status="failed", reason=str(exc))],
            ))
    return IngestSearchMonitorsResult(
        monitor_count=len(monitors),
        added_count=sum(r.added_count for r in results),
        skipped_count=sum(r.skipped_count for r in results),
        failed_count=sum(r.failed_count for r in results),
        results=results,
    )


@router.put("/monitors/{monitor_id}", response_model=SourceMonitorOut)
def update_monitor(monitor_id: int, body: SourceMonitorUpdate, db: Session = Depends(get_db)):
    monitor = db.get(SourceMonitor, monitor_id)
    if not monitor:
        raise HTTPException(status_code=404, detail="Monitor not found")
    values = body.model_dump(exclude_unset=True)
    if "monitor_type" in values and values["monitor_type"] not in MONITOR_TYPES:
        raise HTTPException(status_code=422, detail="Invalid monitor_type")
    for field, value in values.items():
        if field in {"required_terms", "excluded_terms"}:
            value = _json_list(value)
        setattr(monitor, field, value)
    monitor.updated_at = datetime.utcnow()
    _ensure_rss_feed(db, monitor)
    db.commit()
    db.refresh(monitor)
    return monitor


@router.delete("/monitors/{monitor_id}", status_code=204)
def delete_monitor(monitor_id: int, db: Session = Depends(get_db)):
    monitor = db.get(SourceMonitor, monitor_id)
    if not monitor:
        raise HTTPException(status_code=404, detail="Monitor not found")
    db.delete(monitor)
    db.commit()


@router.post("/monitors/{monitor_id}/mark-checked", response_model=SourceMonitorOut)
def mark_monitor_checked(monitor_id: int, db: Session = Depends(get_db)):
    monitor = db.get(SourceMonitor, monitor_id)
    if not monitor:
        raise HTTPException(status_code=404, detail="Monitor not found")
    monitor.last_checked_at = datetime.utcnow()
    monitor.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(monitor)
    return monitor


@router.post("/monitors/{monitor_id}/ingest", response_model=MonitorIngestResult)
def ingest_monitor(monitor_id: int, db: Session = Depends(get_db)):
    monitor = db.get(SourceMonitor, monitor_id)
    if not monitor:
        raise HTTPException(status_code=404, detail="Monitor not found")
    if monitor.monitor_type == "search_query":
        return _ingest_search_monitor(db, monitor)
    if monitor.monitor_type != "rss":
        raise HTTPException(
            status_code=422,
            detail="Search-query ingestion is not configured yet. Add a search API provider or paste matching articles manually.",
        )
    return _ingest_rss_monitor(db, monitor)
