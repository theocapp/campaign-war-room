"""trafilatura-based article crawler for outlets without RSS feeds.

Usage:
    Called by the /api/ingest/crawl endpoint and the background scheduler.
    Each SourceMonitor with monitor_type="webpage" and a URL is treated as a
    seed page to crawl (homepage, index, or section page).  trafilatura finds
    linked article URLs on that page, then fetches and extracts each one.

trafilatura is strictly better than our hand-rolled HTML extractor for article
body text (handles paywalls, shadow DOM, pagination indicators, etc.), so we
use it here instead of the existing _clean_html_with_quality path.
"""
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# trafilatura raises no import-time side effects
import trafilatura
from trafilatura.settings import use_config
from trafilatura.sitemaps import sitemap_search

# Silence trafilatura's noisy WARNING logs during normal operation
logging.getLogger("trafilatura").setLevel(logging.ERROR)

# Trafilatura config: favour precision over recall, 30s timeout per page
_TRAF_CONFIG = use_config()
_TRAF_CONFIG.set("DEFAULT", "DOWNLOAD_TIMEOUT", "20")


@dataclass
class CrawlResult:
    outlet_domain: str
    attempted: int
    added: int
    skipped: int
    errors: int


def _domain(url: str) -> str:
    try:
        netloc = urlparse(url).netloc
        return netloc.removeprefix("www.")
    except Exception:
        return url


def _discover_article_urls(seed_url: str, max_urls: int = 30) -> list[str]:
    """Return article URLs discovered from a seed page or sitemap."""
    found: list[str] = []
    # Try sitemap first (fast, structured)
    try:
        sitemap_urls = list(sitemap_search(seed_url))
        if sitemap_urls:
            return sitemap_urls[:max_urls]
    except Exception:
        pass

    # Fall back: fetch the seed page and extract links with trafilatura
    try:
        downloaded = trafilatura.fetch_url(seed_url)
        if downloaded:
            links = trafilatura.extract_metadata(downloaded)
            # trafilatura.extract_metadata doesn't give links, use bare_extraction
            # for internal links discovery via find_links
            from trafilatura.utils import load_html
            from trafilatura.htmlprocessing import prune_html
            tree = load_html(downloaded)
            if tree is not None:
                base = seed_url.rstrip("/")
                base_domain = _domain(seed_url)
                for a in tree.iter("a"):
                    href = a.get("href", "")
                    if not href:
                        continue
                    if href.startswith("/"):
                        href = f"{urlparse(seed_url).scheme}://{urlparse(seed_url).netloc}{href}"
                    if _domain(href) == base_domain and href not in found:
                        # Rough article URL heuristic: has a path with > 2 segments
                        path = urlparse(href).path
                        if path and len(path.strip("/").split("/")) >= 2:
                            found.append(href)
                    if len(found) >= max_urls:
                        break
    except Exception as exc:
        logger.warning("Link discovery failed for %s: %s", seed_url, exc)
    return found


def crawl_url(db, url: str, source_type: str = "news", outlet_id: Optional[int] = None) -> bool:
    """Fetch a single article URL with trafilatura, then ingest it.

    Returns True if a new SourceItem was created, False if deduped or failed.
    Callers are responsible for committing.
    """
    from app.models import SourceItem
    from app.services.ingestion import ingest_text

    # Dedup by URL before fetching
    if db.query(SourceItem).filter_by(source_url=url).first():
        return False

    try:
        downloaded = trafilatura.fetch_url(url, config=_TRAF_CONFIG)
        if not downloaded:
            logger.debug("trafilatura: empty response for %s", url)
            return False

        # Extract metadata + body text
        meta = trafilatura.extract_metadata(downloaded)
        text = trafilatura.extract(
            downloaded,
            config=_TRAF_CONFIG,
            include_comments=False,
            include_tables=False,
            no_fallback=False,
            favor_precision=True,
        )

        if not text or len(text.split()) < 30:
            logger.debug("trafilatura: insufficient body text for %s (%d words)", url, len((text or "").split()))
            return False

        title = (meta.title or "").strip() if meta else ""
        if not title:
            slug = url.rstrip("/").split("/")[-1]
            title = slug.replace("-", " ").replace("_", " ").title() or url

        source_name = (meta.sitename or "").strip() if meta else ""
        if not source_name:
            source_name = _domain(url)

        author = (meta.author or "").strip() if meta else None

        published_at: Optional[datetime] = None
        if meta and meta.date:
            try:
                from dateutil import parser as dp
                published_at = dp.parse(meta.date).replace(tzinfo=None)
            except Exception:
                pass

        item = ingest_text(
            db,
            title=title[:200],
            raw_text=text[:4000],
            source_name=source_name,
            source_type=source_type,
            source_url=url,
            published_at=published_at,
            source_author=author,
        )
        if outlet_id and item:
            item.outlet_id = outlet_id
            db.commit()
        return item is not None
    except Exception as exc:
        logger.warning("crawl_url failed for %s: %s", url, exc)
        return False


def crawl_monitor(db, monitor) -> CrawlResult:
    """Crawl all article URLs discovered from a SourceMonitor's seed URL.

    monitor must have monitor_type="webpage" and a non-empty URL.
    Returns a CrawlResult with counts.
    """
    from app.models import Outlet, SourceItem

    if not monitor.url:
        return CrawlResult(outlet_domain="", attempted=0, added=0, skipped=0, errors=0)

    domain = _domain(monitor.url)
    outlet = db.query(Outlet).filter_by(domain=domain).first()
    outlet_id = outlet.id if outlet else None
    source_type = monitor.source_type or "news"

    article_urls = _discover_article_urls(monitor.url)
    logger.info("crawl_monitor: %s — discovered %d URLs", monitor.name, len(article_urls))

    added = skipped = errors = 0
    for url in article_urls:
        existing = db.query(SourceItem).filter_by(source_url=url).first()
        if existing:
            skipped += 1
            continue
        try:
            if crawl_url(db, url, source_type=source_type, outlet_id=outlet_id):
                added += 1
            else:
                skipped += 1
        except Exception as exc:
            logger.warning("crawl_monitor: error on %s: %s", url, exc)
            errors += 1

    monitor.last_checked_at = datetime.utcnow()
    db.commit()
    return CrawlResult(
        outlet_domain=domain,
        attempted=len(article_urls),
        added=added,
        skipped=skipped,
        errors=errors,
    )


def crawl_all_webpage_monitors(db) -> list[CrawlResult]:
    """Run crawl_monitor on all active webpage monitors. Called by scheduler."""
    from app.models import SourceMonitor
    monitors = (
        db.query(SourceMonitor)
        .filter(SourceMonitor.monitor_type == "webpage", SourceMonitor.active == True)
        .all()
    )
    results = []
    for monitor in monitors:
        try:
            result = crawl_monitor(db, monitor)
            results.append(result)
            logger.info(
                "crawl: %s — added=%d skipped=%d errors=%d",
                result.outlet_domain, result.added, result.skipped, result.errors,
            )
        except Exception:
            logger.exception("crawl_all: unhandled error on monitor %r", monitor.name)
    return results
