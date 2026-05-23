"""Auto-discover RSS feeds from domains GDELT found covering the race.

After the GDELT backfill completes we have a set of article URLs from outlets
that demonstrably cover the campaign. This module extracts their domains,
tries to find a working RSS feed URL for each, and creates RssFeed rows so
future coverage is ingested in real-time via the normal RSS pipeline.

RSS probe strategy (tried in order, stops at first hit):
  1. Parse <link rel="alternate" type="application/rss+xml"> from the homepage
  2. Common path patterns: /feed, /rss, /feed.xml, /rss.xml, /news/feed, /index.rss
  3. WordPress catch-all: /?feed=rss2

A URL is accepted only if it parses as valid RSS/Atom with at least one item.
"""

import logging
import re
import time
from datetime import datetime
from urllib.parse import urlparse

import httpx
import feedparser

logger = logging.getLogger(__name__)

# Progress tracker — read by the pipeline-status endpoint
feed_discovery_progress: dict = {
    "running": False, "done": False,
    "probed": 0, "created": 0, "started_at": None, "finished_at": None,
}

# Seconds between probe attempts — be polite to target servers
PROBE_DELAY = 0.5

# Common RSS path patterns to try, in order of popularity
RSS_PATH_CANDIDATES = [
    "/feed",
    "/feed/",
    "/rss",
    "/rss.xml",
    "/feed.xml",
    "/feeds/posts/default",   # Blogger
    "/news/feed",
    "/index.rss",
    "/?feed=rss2",            # WordPress fallback
    "/atom.xml",
]

# Domains we never want to add as feeds (aggregators, CDNs, social, trackers)
_BLOCKLIST = {
    "google.com", "facebook.com", "twitter.com", "x.com", "youtube.com",
    "wikipedia.org", "archive.org", "web.archive.org", "reddit.com",
    "linkedin.com", "instagram.com", "tiktok.com", "apple.com",
    "feedburner.com", "feedblitz.com", "amazonaws.com", "cloudfront.net",
    "akamaihd.net", "wp.com",
}


def _root_domain(url: str) -> str | None:
    """Return just the scheme + host from a URL, e.g. 'https://thetimes-tribune.com'."""
    try:
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.netloc:
            return None
        return f"{p.scheme}://{p.netloc}"
    except Exception:
        return None


def _bare_domain(url: str) -> str | None:
    """Return the netloc without www, e.g. 'thetimes-tribune.com'."""
    try:
        host = urlparse(url).netloc.lower()
        return host.removeprefix("www.")
    except Exception:
        return None


def _is_valid_feed(url: str, timeout: int = 10) -> bool:
    """Fetch `url` and return True if it parses as RSS/Atom with ≥1 entry."""
    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (compatible; CampaignWarRoom/1.0; +https://github.com)",
            "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
        })
        if resp.status_code not in (200, 301, 302):
            return False
        parsed = feedparser.parse(resp.text)
        return bool(parsed.entries)
    except Exception:
        return False


def _discover_feed_from_html(root: str, timeout: int = 10) -> str | None:
    """Try to extract RSS URL from <link rel="alternate"> in the homepage HTML."""
    try:
        resp = httpx.get(root, timeout=timeout, follow_redirects=True, headers={
            "User-Agent": "Mozilla/5.0 (compatible; CampaignWarRoom/1.0)",
        })
        if resp.status_code != 200:
            return None
        html = resp.text
        # Look for <link rel="alternate" type="application/rss+xml" href="...">
        matches = re.findall(
            r'<link[^>]+type=["\']application/(?:rss|atom)\+xml["\'][^>]+href=["\']([^"\']+)["\']',
            html, re.IGNORECASE,
        )
        if not matches:
            # Also try href-before-type ordering
            matches = re.findall(
                r'<link[^>]+href=["\']([^"\']+)["\'][^>]+type=["\']application/(?:rss|atom)\+xml["\']',
                html, re.IGNORECASE,
            )
        for href in matches:
            href = href.strip()
            if not href.startswith("http"):
                href = root.rstrip("/") + "/" + href.lstrip("/")
            if _is_valid_feed(href):
                return href
    except Exception:
        pass
    return None


def _probe_domain(root: str) -> str | None:
    """Return the first working RSS feed URL for `root`, or None."""
    # Step 1: parse homepage HTML for <link rel="alternate">
    feed_url = _discover_feed_from_html(root)
    if feed_url:
        return feed_url

    # Step 2: try common path patterns
    for path in RSS_PATH_CANDIDATES:
        url = root.rstrip("/") + path
        if _is_valid_feed(url):
            return url
        time.sleep(0.1)

    return None


def discover_feeds_from_gdelt(db, *, min_articles: int = 2) -> dict:
    """Find RSS feeds for domains GDELT discovered, create RssFeed rows.

    Args:
        min_articles: only probe domains where GDELT found ≥ N articles
                      (filters noise / one-off hits from low-quality domains).

    Returns a summary dict: { created, probed, skipped_existing, errors }.
    """
    from sqlalchemy import func
    from app.models import RssFeed, SourceItem

    # 1. Collect domains GDELT backfill / realtime ingested
    rows = (
        db.query(
            func.lower(SourceItem.source_url).label("url"),
        )
        .filter(SourceItem.source_type.in_(["gdelt_backfill", "gdelt_realtime"]))
        .filter(SourceItem.source_url.isnot(None))
        .all()
    )

    # Count articles per domain
    domain_counts: dict[str, int] = {}
    domain_roots: dict[str, str] = {}
    for (url,) in rows:
        bare = _bare_domain(url)
        root = _root_domain(url)
        if not bare or not root:
            continue
        if any(bare == b or bare.endswith("." + b) for b in _BLOCKLIST):
            continue
        domain_counts[bare] = domain_counts.get(bare, 0) + 1
        domain_roots[bare] = root  # keep last-seen root (scheme may vary; prefer https)

    # Prefer https roots
    for (url,) in rows:
        bare = _bare_domain(url)
        root = _root_domain(url)
        if bare and root and root.startswith("https://"):
            domain_roots[bare] = root

    # 2. Filter to domains with enough articles
    candidates = [
        (bare, domain_roots[bare])
        for bare, count in domain_counts.items()
        if count >= min_articles
    ]
    candidates.sort(key=lambda x: -domain_counts[x[0]])  # most-covered first

    # 3. Skip domains already covered by an existing RssFeed
    existing_feeds = db.query(RssFeed.url).all()
    existing_domains = {_bare_domain(url) for (url,) in existing_feeds if url}

    to_probe = [
        (bare, root) for bare, root in candidates
        if bare not in existing_domains
    ]

    logger.info(
        "feed_discovery: %d candidate domains (%d already have feeds) → probing %d",
        len(candidates), len(candidates) - len(to_probe), len(to_probe),
    )

    feed_discovery_progress["running"] = True
    feed_discovery_progress["done"] = False
    feed_discovery_progress["probed"] = 0
    feed_discovery_progress["created"] = 0
    feed_discovery_progress["started_at"] = datetime.utcnow().isoformat()
    feed_discovery_progress["finished_at"] = None

    created = probed = errors = 0

    for bare, root in to_probe:
        probed += 1
        try:
            feed_url = _probe_domain(root)
            if feed_url:
                name = bare.split(".")[0].replace("-", " ").title()
                feed = RssFeed(name=name, url=feed_url, source_type="news", active=True)
                db.add(feed)
                db.commit()
                created += 1
                feed_discovery_progress["created"] = created
                logger.info("feed_discovery: created feed '%s' → %s", name, feed_url)
            else:
                logger.debug("feed_discovery: no feed found for %s", root)
        except Exception as exc:
            logger.warning("feed_discovery: error probing %s: %s", root, exc)
            errors += 1
            try:
                db.rollback()
            except Exception:
                pass
        probed += 1
        feed_discovery_progress["probed"] = probed
        time.sleep(PROBE_DELAY)

    feed_discovery_progress["running"] = False
    feed_discovery_progress["done"] = True
    feed_discovery_progress["finished_at"] = datetime.utcnow().isoformat()

    logger.info(
        "feed_discovery: done — probed=%d created=%d errors=%d",
        probed, created, errors,
    )
    return {
        "probed": probed,
        "created": created,
        "skipped_existing": len(candidates) - len(to_probe),
        "errors": errors,
    }
