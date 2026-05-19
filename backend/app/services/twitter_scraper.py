"""Twitter/X profile monitoring via Nitter RSS.

Twitter's public profile pages are JavaScript-rendered and can't be scraped
directly. Nitter instances serve static HTML versions of profiles and provide
proper RSS feeds. Instances come and go as Twitter blocks them, so we maintain
a fallback list and probe each one at resolution time.

Usage: given a Twitter username (e.g. "BresnaCongress"), this module finds a
working Nitter instance and returns the RSS feed URL. That URL is registered as
a normal RssFeed row and picked up by the existing RSS ingestion scheduler.

monitor_type="twitter_profile" stores the Twitter handle in the `query` field.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Known Nitter instances, ordered by historical reliability.
# The list is probed top-to-bottom; the first instance that responds with
# a valid RSS feed for the /username/rss path is used.
NITTER_INSTANCES = [
    "nitter.poast.org",
    "nitter.privacydev.net",
    "nitter.1d4.us",
    "nitter.kavin.rocks",
    "nitter.net",
    "nitter.woodland.cafe",
    "nitter.moomoo.me",
    "twiiit.com",
    "nitter.mint.lgbt",
]

_PROBE_TIMEOUT = 6  # seconds per instance


def extract_twitter_username(value: str | None) -> str | None:
    """Extract a clean Twitter username from a URL or bare handle.

    Accepts any of:
      @BresnaCongress
      BresnaCongress
      https://twitter.com/BresnaCongress
      https://x.com/BresnaCongress
    Returns the bare username without @ or None if unparseable.
    """
    if not value:
        return None
    value = value.strip()
    # URL form
    m = re.search(r"(?:twitter\.com|x\.com)/([A-Za-z0-9_]{1,50})", value, re.IGNORECASE)
    if m:
        return m.group(1)
    # @handle or bare handle
    username = value.lstrip("@").strip()
    if re.fullmatch(r"[A-Za-z0-9_]{1,50}", username):
        return username
    return None


def _probe_nitter_instance(instance: str, username: str) -> str | None:
    """Return the RSS URL if this Nitter instance serves the user's feed, else None."""
    rss_url = f"https://{instance}/{username}/rss"
    try:
        import requests
        r = requests.get(
            rss_url,
            timeout=_PROBE_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0 (compatible; CampaignBot/1.0)"},
            allow_redirects=True,
        )
        if r.status_code != 200:
            return None
        body = r.text[:1000].lower()
        if "<rss" in body or "<feed" in body or "<?xml" in body:
            return rss_url
    except Exception:
        pass
    return None


def resolve_nitter_rss(username: str) -> str | None:
    """Try each Nitter instance in order. Return the first working RSS URL, or None."""
    for instance in NITTER_INSTANCES:
        url = _probe_nitter_instance(instance, username)
        if url:
            logger.info("twitter_scraper: resolved @%s via %s", username, instance)
            return url
        logger.debug("twitter_scraper: %s unavailable for @%s", instance, username)
    logger.warning("twitter_scraper: no working Nitter instance found for @%s", username)
    return None


def ensure_twitter_feed(db, monitor) -> bool:
    """Resolve a twitter_profile monitor to a Nitter RSS feed and register it.

    Called whenever a twitter_profile monitor is created or re-checked.
    Uses the monitor's `query` field as the Twitter username (or `url` as fallback).
    Returns True if an RssFeed row was created or already exists.
    """
    from app.models import RssFeed

    username = extract_twitter_username(monitor.query or monitor.url)
    if not username:
        logger.warning(
            "twitter_scraper: monitor %d has no parseable Twitter handle (query=%r url=%r)",
            monitor.id, monitor.query, monitor.url,
        )
        return False

    # Check if we already have a Nitter feed registered for this handle
    existing = db.query(RssFeed).filter(
        RssFeed.url.like(f"%/{ username }/rss")
    ).first()
    if existing:
        return True

    rss_url = resolve_nitter_rss(username)
    if not rss_url:
        return False

    db.add(RssFeed(
        name=monitor.name,
        url=rss_url,
        source_type=monitor.source_type or "social",
    ))
    db.commit()
    logger.info("twitter_scraper: registered Nitter feed for @%s → %s", username, rss_url)
    return True


def recheck_failed_twitter_monitors(db) -> int:
    """Re-probe Nitter for any twitter_profile monitors that don't yet have a feed.

    Called by the daily scheduler. Returns the number of monitors newly resolved.
    """
    from app.models import RssFeed, SourceMonitor

    monitors = (
        db.query(SourceMonitor)
        .filter(
            SourceMonitor.monitor_type == "twitter_profile",
            SourceMonitor.active == True,  # noqa: E712
        )
        .all()
    )

    resolved = 0
    for monitor in monitors:
        username = extract_twitter_username(monitor.query or monitor.url)
        if not username:
            continue
        # Skip if already has a working feed
        if db.query(RssFeed).filter(RssFeed.url.like(f"%/{username}/rss")).first():
            continue
        if ensure_twitter_feed(db, monitor):
            resolved += 1

    return resolved
