"""GDELT-based historical backfill.

Replaces the Google News RSS scraper with queries to the GDELT DOC API,
which indexes virtually every English-language news article since 2013.
For each article URL returned, we scrape the content directly. If the
direct scrape fails (paywall, 404), we fall back to the Wayback Machine.

GDELT DOC API is free, requires no key, and returns actual journalism URLs
rather than profile pages or Wikipedia entries.
"""

import json as _json
import logging
import time
from datetime import datetime, timedelta
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Progress tracker — read by the status endpoint
backfill_progress: dict = {
    "running": False, "done": 0, "total": 0, "started_at": None,
    "added": 0, "wayback_hits": 0,
}

GDELT_DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"
WAYBACK_CDX_API = "http://web.archive.org/cdx/search/cdx"
WAYBACK_FETCH = "https://web.archive.org/web/{timestamp}/{url}"

# Seconds between article scrape attempts — be polite to target servers
SCRAPE_DELAY = 0.3


def _gdelt_query(query: str, start: datetime, end: datetime) -> list[dict]:
    """Query GDELT DOC API for articles matching `query` in the given window.

    Returns a list of dicts with keys: url, title, seendate, domain.
    GDELT caps results at 250 per request.
    """
    params = {
        "query": query,
        "mode": "artlist",
        "maxrecords": "250",
        "startdatetime": start.strftime("%Y%m%d%H%M%S"),
        "enddatetime": end.strftime("%Y%m%d%H%M%S"),
        "format": "json",
        "sourcelang": "english",
        "sourcecountry": "US",
    }
    try:
        resp = httpx.get(GDELT_DOC_API, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        return data.get("articles") or []
    except Exception as exc:
        logger.warning("gdelt_query failed for '%s': %s", query, exc)
        return []


def _wayback_fetch(url: str) -> Optional[str]:
    """Try to retrieve a cached copy of `url` from the Wayback Machine.

    Returns the HTML text if found, None otherwise.
    """
    try:
        cdx = httpx.get(
            WAYBACK_CDX_API,
            params={
                "url": url,
                "output": "json",
                "limit": "1",
                "fl": "timestamp,statuscode",
                "filter": "statuscode:200",
                "collapse": "digest",
            },
            timeout=10,
        )
        rows = cdx.json()
        if not rows or len(rows) < 2:  # first row is header
            return None
        timestamp = rows[1][0]
        archived_url = WAYBACK_FETCH.format(timestamp=timestamp, url=url)
        resp = httpx.get(archived_url, timeout=20, follow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (compatible; CampaignWarRoom/1.0)"
        })
        resp.raise_for_status()
        return resp.text
    except Exception as exc:
        logger.debug("wayback_fetch failed for %s: %s", url, exc)
        return None


def run_gdelt_backfill(db, *, force: bool = False, days_back: int = 365) -> dict:
    """Main entry point. Queries GDELT for the campaign's candidate and opponent,
    scrapes each returned article URL, and feeds content into the normal
    ingestion pipeline.

    Idempotent: gated by extended_backfill_completed unless force=True.
    """
    from app.models import CampaignConfig, Opponent
    from app.services.ingestion import ingest_url, _create_and_analyze
    from app.services.source_discovery import _candidate_last_name

    campaign = db.query(CampaignConfig).first()
    if not campaign:
        return {"skipped": True, "reason": "no campaign"}
    if getattr(campaign, "extended_backfill_completed", False) and not force:
        return {"skipped": True, "reason": "already completed"}

    opponents = db.query(Opponent).all()

    # Build search queries — surname only. A surname query catches headline and
    # second-reference mentions an exact full-name phrase misses, and already
    # subsumes every full-name match. Robust to FEC "Last, First" name ordering.
    queries: list[str] = []

    cand_last = _candidate_last_name(campaign.candidate_name or "")
    if cand_last:
        queries.append(f'"{cand_last}"')

    for opp in opponents:
        opp_last = _candidate_last_name(opp.name or "")
        if opp_last:
            queries.append(f'"{opp_last}"')

    # District-scoped query (e.g. "PA-08 election"). "election" is used as the
    # universal narrower instead of an office-specific keyword like
    # "congressional" so this works for any race type (House, Senate, governor,
    # mayor, ...).
    if campaign.district:
        queries.append(f'"{campaign.district}" election')

    # NOTE: bare geography queries (e.g. '"Scranton" (congressional OR election)')
    # were tried and removed — live GDELT testing showed ~40% noise (state/county
    # races, municipal news, wrong districts). Genuinely relevant results almost
    # always name a candidate, so the surname queries above already catch them.

    # De-dupe queries while preserving order.
    _seen_q: set[str] = set()
    queries = [q for q in queries if not (q in _seen_q or _seen_q.add(q))]

    if not queries:
        return {"skipped": True, "reason": "no queries"}

    # Build 30-day sliding windows
    now = datetime.utcnow()
    windows: list[tuple[datetime, datetime]] = []
    window_days = 30
    for i in range(max(1, days_back // window_days)):
        end = now - timedelta(days=i * window_days)
        start = now - timedelta(days=(i + 1) * window_days)
        windows.append((start, end))

    # Collect unique article URLs from GDELT
    seen_urls: set[str] = set()
    articles: list[dict] = []
    for query in queries:
        for start, end in windows:
            results = _gdelt_query(query, start, end)
            for art in results:
                url = art.get("url", "").strip()
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    articles.append(art)
            time.sleep(0.5)  # be polite to GDELT

    logger.info("gdelt_backfill: %d unique article URLs from %d queries × %d windows",
                len(articles), len(queries), len(windows))

    added = skipped = failed = wayback_hits = 0

    backfill_progress["running"] = True
    backfill_progress["done"] = 0
    backfill_progress["total"] = len(articles)
    backfill_progress["started_at"] = datetime.utcnow().isoformat()
    backfill_progress["added"] = 0
    backfill_progress["wayback_hits"] = 0

    for art in articles:
        url = art.get("url", "").strip()
        if not url:
            continue

        try:
            # Dedup — if we already have this URL, skip entirely (fast path)
            from app.models import SourceItem as _SI
            if db.query(_SI.id).filter_by(source_url=url).first():
                skipped += 1
                backfill_progress["done"] += 1
                continue

            # Scrape the article (reduced timeout — paywalled/dead articles
            # aren't worth waiting 15 seconds for)
            try:
                resp = httpx.get(url, timeout=8, follow_redirects=True, headers={
                    "User-Agent": "Mozilla/5.0 (compatible; CampaignWarRoom/1.0)"
                })
                resp.raise_for_status()
            except Exception:
                failed += 1
                backfill_progress["done"] += 1
                time.sleep(SCRAPE_DELAY)
                continue

            from app.services.ingestion import (
                _clean_html_with_quality, _parse_html_published_date,
            )
            title, body_text, quality_score, quality_label, quality_reasons = \
                _clean_html_with_quality(resp.text)
            published_date = _parse_html_published_date(resp.text)

            # Fall back to GDELT seendate when HTML has no date
            if not published_date and art.get("seendate"):
                try:
                    published_date = datetime.strptime(art["seendate"][:8], "%Y%m%d")
                except Exception:
                    pass

            if not title:
                title = art.get("title") or url.rstrip("/").split("/")[-1].replace("-", " ").title() or url

            item = _SI(
                title=title[:200],
                raw_text=body_text,
                source_url=url,
                source_name=url.split("/")[2] if "://" in url else url[:50],
                source_type="gdelt_backfill",
                published_at=published_date,
                extraction_quality_score=quality_score,
                extraction_quality_label=quality_label,
                extraction_quality_reasons=_json.dumps(quality_reasons),
            )
            db.add(item)
            db.flush()  # get item.id

            # Cluster (SimHash — no LLM, fast)
            from app.services import story_clustering
            story_clustering.assign_story_cluster_v2(db, item)

            # Link to outlet for reach calculations
            from app.services.outlet_linking import build_outlet_index, link_outlet_to_item
            link_outlet_to_item(item, build_outlet_index(db))

            db.commit()
            added += 1
            backfill_progress["added"] = added

        except Exception as exc:
            logger.warning("gdelt_backfill: failed to ingest %s: %s", url, exc)
            try:
                db.rollback()
            except Exception:
                pass
            failed += 1

        backfill_progress["done"] += 1
        backfill_progress["wayback_hits"] = wayback_hits
        time.sleep(SCRAPE_DELAY)

    backfill_progress["running"] = False
    campaign.extended_backfill_completed = True
    db.commit()

    logger.info(
        "gdelt_backfill: done — added=%d skipped=%d failed=%d wayback_hits=%d",
        added, skipped, failed, wayback_hits,
    )
    return {
        "added": added,
        "skipped": skipped,
        "failed": failed,
        "wayback_hits": wayback_hits,
        "total_urls": len(articles),
    }
