"""
Auto-discover RSS feeds for publishers that have proven they cover this race.

Companion to feed_discovery (GDELT-based) and feed_prune. Where feed_discovery
guesses outlets from any GDELT mention, this module discovers outlets that
have actually produced race-relevant articles via Google News search queries.

Pipeline:
  1. Pull recent survivors with a known publisher_domain from search-query
     sources (Google News topical + named search; not Reddit, not direct feeds)
  2. Group by domain, count survivors
  3. Skip domains already covered by an active or inactive feed
     (inactive = previously pruned; respect the prune verdict)
  4. Skip blocklisted aggregator/social domains
  5. For each domain with >= min_survivors hits, probe for a working RSS feed
     using the existing feed_discovery._probe_domain helper
  6. Add discovered feeds with name "Auto-yield: {domain}" so they're
     distinguishable from manual/GDELT-discovered feeds
"""
import logging
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import func

from app.models import RssFeed, SourceItem
from app.services.feed_discovery import (
    _BLOCKLIST,
    _bare_domain,
    _probe_domain,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Be polite to publisher servers when probing for RSS endpoints.
PROBE_DELAY = 0.5


def _existing_feed_domains(db: "Session") -> set[str]:
    """All bare domains we already have a feed for (active or inactive).
    Inactive = previously pruned; we deliberately don't auto-reactivate."""
    urls = db.query(RssFeed.url).all()
    return {d for d in (_bare_domain(u[0]) for u in urls) if d}


def discover_feeds_from_google_news_yield(
    db: "Session",
    *,
    window_days: int = 90,
    min_survivors: int = 3,
    dry_run: bool = True,
) -> dict:
    """Add direct RSS feeds for publishers whose articles have survived
    auto-review when found via Google News search.

    Eligibility for a domain (ALL must hold):
      • >= `min_survivors` survivor articles in the last `window_days`
        sourced from a Google-News-search feed (not from a publisher-
        named Google News Feed, which is already direct)
      • Domain not blocklisted (social, aggregator, CDN)
      • Domain not already in rss_feeds (active OR inactive)
      • RSS probe finds a working feed at the domain

    `dry_run=True` (default) returns the would-add list without writing.
    """
    cutoff = datetime.utcnow() - timedelta(days=window_days)

    # Pull survivor counts per publisher_domain from search-query sources.
    # We exclude publisher-named "X — Google News Feed" entries since those
    # are already direct subscriptions, not discoveries.
    rows = (
        db.query(
            SourceItem.publisher_domain.label("domain"),
            func.count(SourceItem.id).label("survivor_count"),
        )
        .filter(SourceItem.publisher_domain.isnot(None))
        .filter(SourceItem.archived_as_irrelevant == False)  # noqa: E712
        .filter(SourceItem.ingested_at >= cutoff)
        .filter(
            (SourceItem.source_name.like("Google News:%"))
            | (SourceItem.source_name.like("Google News —%"))
        )
        .group_by(SourceItem.publisher_domain)
        .all()
    )

    domain_yields = {
        r.domain: int(r.survivor_count)
        for r in rows
        if r.domain and int(r.survivor_count) >= min_survivors
    }

    existing = _existing_feed_domains(db)

    # Filter candidates and order most-prolific first
    candidates = sorted(
        (
            (domain, count) for domain, count in domain_yields.items()
            if not any(domain == b or domain.endswith("." + b) for b in _BLOCKLIST)
            and domain not in existing
        ),
        key=lambda x: -x[1],
    )

    skipped_blocklisted = sum(
        1 for d in domain_yields
        if any(d == b or d.endswith("." + b) for b in _BLOCKLIST)
    )
    skipped_existing = sum(
        1 for d in domain_yields
        if d in existing
        and not any(d == b or d.endswith("." + b) for b in _BLOCKLIST)
    )

    logger.info(
        "feed_discovery_yield: %d candidate domains "
        "(blocklisted=%d, existing_feed=%d) → probing %d",
        len(domain_yields), skipped_blocklisted, skipped_existing, len(candidates),
    )

    actions: list[dict] = []
    probed = no_feed_found = errors = 0

    for domain, survivor_count in candidates:
        probed += 1
        root = f"https://{domain}"
        try:
            feed_url = _probe_domain(root)
            if not feed_url:
                no_feed_found += 1
                logger.debug("feed_discovery_yield: no RSS found for %s", root)
                time.sleep(PROBE_DELAY)
                continue

            name = f"Auto-yield: {domain}"
            action = {
                "domain": domain,
                "feed_url": feed_url,
                "name": name,
                "survivor_count": survivor_count,
            }
            actions.append(action)

            if dry_run:
                logger.info(
                    "feed_discovery_yield: WOULD add '%s' (%d survivors) → %s",
                    name, survivor_count, feed_url,
                )
            else:
                feed = RssFeed(
                    name=name,
                    url=feed_url,
                    source_type="news",
                    active=True,
                )
                db.add(feed)
                db.commit()
                logger.info(
                    "feed_discovery_yield: added '%s' (%d survivors) → %s",
                    name, survivor_count, feed_url,
                )
        except Exception as exc:
            errors += 1
            logger.warning(
                "feed_discovery_yield: error probing %s: %s", domain, exc,
            )
            try:
                db.rollback()
            except Exception:
                pass
        time.sleep(PROBE_DELAY)

    return {
        "dry_run": dry_run,
        "window_days": window_days,
        "min_survivors": min_survivors,
        "candidates": len(domain_yields),
        "skipped_blocklisted": skipped_blocklisted,
        "skipped_existing_feed": skipped_existing,
        "probed": probed,
        "no_feed_found": no_feed_found,
        "added": len(actions),
        "errors": errors,
        "actions": actions,
    }


def backfill_publisher_domain_from_google_news(
    db: "Session",
    *,
    window_days: int = 30,
) -> dict:
    """One-time backfill: refetch active Google News feeds and populate
    publisher_domain on existing items by matching titles.

    Google News RSS only retains the most recent ~50-100 entries per feed,
    so this recovers a useful chunk of the most recent survivors but not
    everything historical. Going forward, new ingests will populate the
    column directly (see ingestion._ingest_rss_entries).
    """
    import feedparser
    import httpx
    from urllib.parse import urlparse
    import re

    cutoff = datetime.utcnow() - timedelta(days=window_days)

    # All active Google News feeds (queries + topical searches; not the
    # publisher-named ones, those already have direct outlet linkage).
    feeds = (
        db.query(RssFeed)
        .filter(RssFeed.active == True)  # noqa: E712
        .filter(
            (RssFeed.url.like("%news.google.com%"))
            | (RssFeed.name.like("Google News:%"))
            | (RssFeed.name.like("Google News —%"))
        )
        .all()
    )

    # Build {normalized_title: publisher_domain} map from currently-fetchable
    # entries across all Google News feeds.
    title_to_domain: dict[str, str] = {}
    feeds_fetched = 0
    for feed in feeds:
        try:
            resp = httpx.get(feed.url, timeout=20, follow_redirects=True, headers={
                "User-Agent": "Mozilla/5.0 (compatible; CampaignWarRoom/1.0)",
            })
            if resp.status_code != 200:
                continue
            parsed = feedparser.parse(resp.text)
            for entry in parsed.entries:
                src_href = (entry.get("source") or {}).get("href") or ""
                if not src_href:
                    continue
                netloc = urlparse(src_href).netloc.lower()
                domain = re.sub(r"^www\.", "", netloc)
                if not domain:
                    continue
                title = (entry.get("title") or "").strip().lower()
                if title:
                    title_to_domain.setdefault(title, domain)
            feeds_fetched += 1
        except Exception as exc:
            logger.warning("backfill_publisher_domain: feed=%s err=%s", feed.url, exc)

    # Update matching source_items.
    candidates = (
        db.query(SourceItem)
        .filter(SourceItem.publisher_domain.is_(None))
        .filter(SourceItem.ingested_at >= cutoff)
        .filter(
            (SourceItem.source_name.like("Google News:%"))
            | (SourceItem.source_name.like("Google News —%"))
        )
        .all()
    )

    updated = 0
    for item in candidates:
        t = (item.title or "").strip().lower()
        if not t:
            continue
        domain = title_to_domain.get(t)
        if domain:
            item.publisher_domain = domain
            updated += 1

    if updated:
        db.commit()

    logger.info(
        "backfill_publisher_domain: fetched %d feeds, mapped %d titles, "
        "updated %d/%d candidates",
        feeds_fetched, len(title_to_domain), updated, len(candidates),
    )
    return {
        "feeds_fetched": feeds_fetched,
        "titles_in_map": len(title_to_domain),
        "candidates_considered": len(candidates),
        "updated": updated,
    }
