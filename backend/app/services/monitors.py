import json
import re
from datetime import datetime, timedelta

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models import CampaignConfig, Opponent, RssFeed, SourceItem, SourceMonitor
from app.services import ingestion
from app.services.search_provider import get_search_provider
from app.services.source_discovery import generate_monitors_for_campaign, _gnews_url_with_dates, _candidate_last_name


def _json_list(value: list | None) -> str | None:
    return json.dumps(value) if value is not None else None


def _to_values(suggestion: dict) -> dict:
    values = dict(suggestion)
    values["required_terms"] = _json_list(values.get("required_terms"))
    values["excluded_terms"] = _json_list(values.get("excluded_terms"))
    return values


def _duplicate_query(db: Session, values: dict) -> SourceMonitor | None:
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
    if db.query(RssFeed).filter_by(url=monitor.url).first():
        return
    db.add(RssFeed(name=monitor.name, url=monitor.url, source_type=monitor.source_type or "news"))


def _soft_duplicate(db: Session, title: str | None, source_name: str | None) -> SourceItem | None:
    title_key = re.sub(r"\s+", " ", (title or "").strip()).lower()
    source_key = re.sub(r"\s+", " ", (source_name or "").strip()).lower()
    if not title_key or not source_key:
        return None
    for item in db.query(SourceItem).filter(SourceItem.source_name.isnot(None)).limit(500).all():
        if (
            re.sub(r"\s+", " ", (item.title or "").strip()).lower() == title_key
            and re.sub(r"\s+", " ", (item.source_name or "").strip()).lower() == source_key
        ):
            return item
    return None


def _run_search_monitor(db: Session, monitor: SourceMonitor) -> int:
    if not monitor.query:
        return 0
    provider = get_search_provider()
    try:
        response = provider.search(monitor.query, limit=10)
    except Exception:
        monitor.last_checked_at = datetime.utcnow()
        monitor.updated_at = datetime.utcnow()
        db.commit()
        return 0
    added = 0
    for result in response.results[:10]:
        if not result.url:
            continue
        if db.query(SourceItem).filter_by(source_url=result.url).first():
            continue
        if _soft_duplicate(db, result.title, result.source_name):
            continue
        if ingestion.ingest_url(db, result.url, monitor.source_type or "news"):
            added += 1
    monitor.last_checked_at = datetime.utcnow()
    monitor.updated_at = datetime.utcnow()
    db.commit()
    return added


def run_historical_backfill(db: Session) -> dict:
    """One-time 90-day Google News backfill on campaign initialization.

    Breaks 90 days into 3 monthly windows and fetches each key query per window.
    Marks CampaignConfig.historical_backfill_completed = True when done.
    Safe to call multiple times — skips if already completed.
    """
    campaign = db.query(CampaignConfig).first()
    if not campaign or campaign.historical_backfill_completed:
        return {"skipped": True}

    opponents = db.query(Opponent).all()
    candidate = campaign.candidate_name

    queries = []
    if candidate:
        cand_last = _candidate_last_name(candidate)
        if cand_last:
            queries.append(cand_last)
    for opp in opponents:
        opp_last = _candidate_last_name(opp.name)
        if opp_last:
            queries.append(opp_last)
    if campaign.district:
        queries.append(campaign.district)

    now = datetime.utcnow()
    windows = []
    for i in range(3):
        before = now - timedelta(days=i * 30)
        after = now - timedelta(days=(i + 1) * 30)
        windows.append((after.strftime("%Y-%m-%d"), before.strftime("%Y-%m-%d")))

    total_added = 0
    for query in queries:
        for after_date, before_date in windows:
            url = _gnews_url_with_dates(query, after_date, before_date)
            try:
                result = ingestion.ingest_rss(db, url, label=f"Backfill: {query} ({after_date})")
                total_added += result.added
            except Exception:
                pass

    campaign.historical_backfill_completed = True
    db.commit()
    return {"added": total_added, "queries": len(queries), "windows": len(windows)}


def auto_setup_monitors(db: Session) -> dict:
    """Generate monitors for the current campaign and ingest new search monitors.

    Idempotent: duplicate monitors are skipped. Safe to call on every campaign save.
    Returns counts for generated/skipped monitors and ingested source items.
    """
    campaign = db.query(CampaignConfig).first()
    if not campaign:
        return {
            "generated": 0,
            "skipped": 0,
            "search_monitors_ingested": 0,
            "sources_ingested": 0,
            "ingested": 0,
        }

    suggestions = generate_monitors_for_campaign(campaign, db.query(Opponent).all())

    created: list[SourceMonitor] = []
    skipped = 0
    for suggestion in suggestions:
        values = _to_values(suggestion)
        if _duplicate_query(db, values):
            skipped += 1
            continue
        monitor = SourceMonitor(**values)
        db.add(monitor)
        db.flush()
        _ensure_rss_feed(db, monitor)
        created.append(monitor)
    db.commit()

    search_monitors = [m for m in created if m.monitor_type == "search_query" and m.active]
    sources_ingested = 0
    for monitor in search_monitors:
        try:
            sources_ingested += _run_search_monitor(db, monitor)
        except Exception:
            pass

    # Immediately ingest any newly created RSS feeds so content appears without
    # waiting for the next scheduler tick.
    new_rss_feeds = [
        db.query(RssFeed).filter_by(url=m.url).first()
        for m in created
        if m.monitor_type == "rss" and m.url
    ]
    new_rss_feeds = [f for f in new_rss_feeds if f]
    for feed in new_rss_feeds:
        try:
            result = ingestion.ingest_rss(db, feed.url, feed.name)
            sources_ingested += result.added
            feed.last_fetched_at = datetime.utcnow()
            db.commit()
        except Exception:
            pass

    return {
        "generated": len(created),
        "skipped": skipped,
        "search_monitors_ingested": len(search_monitors),
        "sources_ingested": sources_ingested,
        "ingested": sources_ingested,
    }
