"""
Auto-prune RSS feeds that have proven unproductive.

Pairs with auto-discovery (GDELT-based today, Google-News-yield-based later):
discovery adds outlets → prune removes the ones that never produce race-relevant
content → discovery can re-add them later if they start producing again.

Conservative by design: only deactivates feeds with substantial volume AND
zero survivors over a multi-week window. Search-query feeds (Google News,
Reddit) and direct campaign sources (YouTube channels) are exempt — their
quiet stretches reflect news cycles, not feed quality.
"""
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import case, func

from app.models import RssFeed, SourceItem

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Feed-name prefixes/substrings whose feeds are exempt from auto-prune.
# These are search queries or direct-campaign sources where low yield reflects
# external factors (news cycle, off-season) rather than feed quality.
EXEMPT_PATTERNS = (
    "Google News",
    "YouTube:",
    "Reddit ",
    "Reddit via",
)


def _is_exempt(feed_name: str) -> bool:
    return any(p in feed_name for p in EXEMPT_PATTERNS)


def prune_zero_yield_feeds(
    db: "Session",
    *,
    window_days: int = 30,
    min_volume: int = 30,
    dry_run: bool = True,
) -> dict:
    """Soft-deactivate RSS feeds that ingested >= `min_volume` items over the
    last `window_days` and produced zero race-relevant survivors.

    Eligibility (ALL must hold):
      • Feed is currently active
      • Feed name does not match an exempt pattern (search queries, YouTube, Reddit)
      • >= `min_volume` items ingested under this feed's name in the window
      • ZERO of those items have archived_as_irrelevant = False

    `dry_run=True` (default) returns the would-prune list without writing.
    `dry_run=False` flips active=False on each pruned feed.
    """
    cutoff = datetime.utcnow() - timedelta(days=window_days)

    # Per-feed-name stats over the window.
    stats_rows = (
        db.query(
            SourceItem.source_name.label("source_name"),
            func.count(SourceItem.id).label("total"),
            func.sum(case((SourceItem.archived_as_irrelevant == False, 1), else_=0))  # noqa: E712
                .label("survived"),
        )
        .filter(SourceItem.ingested_at >= cutoff)
        .filter(SourceItem.source_name.isnot(None))
        .group_by(SourceItem.source_name)
        .all()
    )
    stats_by_name = {
        s.source_name: {"total": int(s.total), "survived": int(s.survived or 0)}
        for s in stats_rows
    }

    feeds = db.query(RssFeed).filter(RssFeed.active == True).all()  # noqa: E712

    actions: list[dict] = []
    skipped_exempt = 0
    skipped_low_volume = 0
    skipped_has_survivors = 0
    skipped_no_match = 0

    for feed in feeds:
        if _is_exempt(feed.name):
            skipped_exempt += 1
            continue
        stat = stats_by_name.get(feed.name)
        if stat is None:
            skipped_no_match += 1
            continue
        if stat["total"] < min_volume:
            skipped_low_volume += 1
            continue
        if stat["survived"] > 0:
            skipped_has_survivors += 1
            continue

        reason = (
            f"auto-prune: 0/{stat['total']} items cleared relevance "
            f"in last {window_days}d"
        )
        actions.append({
            "feed_id": feed.id,
            "name": feed.name,
            "url": feed.url,
            "total_items": stat["total"],
            "reason": reason,
        })
        if dry_run:
            logger.info(
                "feed_prune: WOULD deactivate id=%d (%s) — %s",
                feed.id, feed.name, reason,
            )
        else:
            feed.active = False
            logger.info(
                "feed_prune: deactivated id=%d (%s) — %s",
                feed.id, feed.name, reason,
            )

    if not dry_run and actions:
        db.commit()

    return {
        "dry_run": dry_run,
        "window_days": window_days,
        "min_volume": min_volume,
        "active_feeds_reviewed": len(feeds),
        "pruned": len(actions),
        "actions": actions,
        "skipped_exempt": skipped_exempt,
        "skipped_low_volume": skipped_low_volume,
        "skipped_has_survivors": skipped_has_survivors,
        "skipped_no_match": skipped_no_match,
    }
