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
import os
import re
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Known Nitter instances, ordered by historical reliability. Probed
# top-to-bottom; the first that serves a valid RSS feed for /username/rss wins.
#
# IMPORTANT: public Nitter instances are volatile — Twitter's 2023 guest-API
# shutdown killed most of them, and survivors come and go weekly. This default
# list is a *starting point*, not ground truth. Override it (e.g. to point at a
# self-hosted instance, or to drop dead hosts) via the NITTER_INSTANCES env var
# — a comma-separated host list — without a code change:
#     NITTER_INSTANCES=nitter.example.org,nitter.poast.org
# The refresh job below self-heals already-registered feeds when their pinned
# instance dies, but it can only migrate to a host in THIS list.
_DEFAULT_NITTER_INSTANCES = [
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


def _load_instances() -> list[str]:
    raw = os.getenv("NITTER_INSTANCES", "").strip()
    if raw:
        hosts = [h.strip().lower() for h in raw.split(",") if h.strip()]
        if hosts:
            return hosts
    return list(_DEFAULT_NITTER_INSTANCES)


NITTER_INSTANCES = _load_instances()

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


def _is_nitter_feed_url(url: str | None) -> bool:
    """True if `url` looks like a Nitter RSS feed this module manages.

    Matches any host containing "nitter", the twiiit.com aggregator, or any
    host currently in NITTER_INSTANCES — so it still recognizes a feed pinned
    to an instance that has since been removed from the list (exactly the
    stale-feed case we want to heal)."""
    if not url:
        return False
    host = urlparse(url).netloc.lower().split("@")[-1].split(":")[0]
    if not host:
        return False
    return "nitter" in host or host == "twiiit.com" or host in set(NITTER_INSTANCES)


def _username_from_nitter_url(url: str) -> str | None:
    """Pull the handle out of a `https://<instance>/<username>/rss` URL."""
    m = re.search(r"https?://[^/]+/([A-Za-z0-9_]{1,50})/rss\b", url, re.IGNORECASE)
    return m.group(1) if m else None


def refresh_stale_twitter_feeds(db) -> dict:
    """Self-heal registered Nitter feeds whose instance has gone dark.

    The failure mode this fixes: ensure_twitter_feed() pins a feed to whichever
    instance resolved first (e.g. https://nitter.net/<user>/rss) and never
    revisits it. When that single instance dies, the feed silently stops
    delivering — invisible because no error is raised; the feed just goes quiet.

    For each managed Nitter feed we re-probe its *current* instance. If it still
    serves the handle, leave it. If it's dead, re-resolve the handle against the
    full instance list and rewrite RssFeed.url in place to the new working host.
    If nothing resolves, leave the URL untouched so it can recover on a later
    run rather than being dropped.

    Returns counts: {checked, healthy, migrated, dead}.
    """
    from app.models import RssFeed

    feeds = [f for f in db.query(RssFeed).all() if _is_nitter_feed_url(f.url)]
    checked = healthy = migrated = dead = 0

    for feed in feeds:
        username = _username_from_nitter_url(feed.url)
        if not username:
            continue
        checked += 1
        host = urlparse(feed.url).netloc.lower().split("@")[-1].split(":")[0]

        # Still alive on its current instance? Nothing to do.
        if _probe_nitter_instance(host, username):
            healthy += 1
            continue

        # Current instance is dark — try to migrate to a working one.
        new_url = resolve_nitter_rss(username)
        if new_url and new_url != feed.url:
            logger.info(
                "twitter_scraper: migrating @%s feed %s → %s (old instance dark)",
                username, feed.url, new_url,
            )
            feed.url = new_url
            migrated += 1
        elif not new_url:
            logger.warning(
                "twitter_scraper: @%s feed stale and no instance resolved (left as-is)",
                username,
            )
            dead += 1

    if migrated:
        db.commit()
    return {"checked": checked, "healthy": healthy, "migrated": migrated, "dead": dead}
