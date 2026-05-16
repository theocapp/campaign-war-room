"""Reddit ingestion via PRAW (free official API).

Reads from subreddits relevant to NEPA/PA-08 and searches for candidate and
opponent name mentions.  Each Reddit post becomes a SourceItem with
source_type="social".

Authentication: Reddit's "read-only script app" — set in .env:
    REDDIT_CLIENT_ID=...
    REDDIT_CLIENT_SECRET=...
    REDDIT_USER_AGENT=CampaignWarRoom/1.0

If credentials are not set, the ingester logs a warning and returns 0.

SUBREDDITS searched (configurable via REDDIT_SUBREDDITS env var, comma-separated):
    pennsylvania, Scranton, nepa, politics (fallback: all four)
"""
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_SUBREDDITS = ["pennsylvania", "Scranton", "nepa", "politics"]
_POST_LIMIT = 25  # per subreddit per search term


@dataclass
class RedditIngestResult:
    subreddits_searched: int
    posts_found: int
    added: int
    skipped: int
    errors: int


def _get_reddit():
    """Return a praw.Reddit read-only instance or None if credentials missing."""
    import praw  # lazy import so app starts even if praw not installed
    client_id = os.getenv("REDDIT_CLIENT_ID", "").strip()
    client_secret = os.getenv("REDDIT_CLIENT_SECRET", "").strip()
    user_agent = os.getenv("REDDIT_USER_AGENT", "CampaignWarRoom/1.0").strip()

    if not client_id or not client_secret:
        logger.warning(
            "Reddit credentials not set (REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET). "
            "Skipping Reddit ingestion."
        )
        return None

    return praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=user_agent,
        read_only=True,
    )


def _search_terms(db) -> list[str]:
    """Build search terms from CampaignConfig: candidate + opponent names."""
    from app.models import CampaignConfig, Opponent
    campaign = db.query(CampaignConfig).first()
    terms: list[str] = []
    if campaign and campaign.candidate_name:
        terms.append(campaign.candidate_name)
    for opp in db.query(Opponent).all():
        if opp.name and opp.name not in terms:
            terms.append(opp.name)
    return terms


def _post_url(submission) -> str:
    return f"https://www.reddit.com{submission.permalink}"


def _post_text(submission) -> str:
    title = submission.title or ""
    selftext = (submission.selftext or "").strip()
    if selftext and selftext not in ("[deleted]", "[removed]"):
        return f"{title}\n\n{selftext}"
    return title


def _utc_from_timestamp(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)


def ingest_reddit(db) -> RedditIngestResult:
    """Search Reddit for campaign-relevant posts and ingest them.

    Dedupes by source_url (Reddit permalink). Only ingests posts with at least
    one search term in the title or body. Returns counts.
    """
    from app.models import SourceItem
    from app.services.ingestion import ingest_text

    reddit = _get_reddit()
    if reddit is None:
        return RedditIngestResult(0, 0, 0, 0, 0)

    terms = _search_terms(db)
    if not terms:
        logger.info("Reddit ingestion: no search terms available (no campaign config?)")
        return RedditIngestResult(0, 0, 0, 0, 0)

    subreddit_names = [
        s.strip()
        for s in os.getenv("REDDIT_SUBREDDITS", ",".join(_DEFAULT_SUBREDDITS)).split(",")
        if s.strip()
    ]

    posts_found = added = skipped = errors = 0

    for sub_name in subreddit_names:
        try:
            subreddit = reddit.subreddit(sub_name)
            for term in terms:
                try:
                    for submission in subreddit.search(term, limit=_POST_LIMIT, sort="new"):
                        posts_found += 1
                        url = _post_url(submission)

                        if db.query(SourceItem).filter_by(source_url=url).first():
                            skipped += 1
                            continue

                        text = _post_text(submission)
                        if not text.strip():
                            skipped += 1
                            continue

                        try:
                            ingest_text(
                                db,
                                title=submission.title[:200],
                                raw_text=text[:4000],
                                source_name=f"Reddit r/{sub_name}",
                                source_type="social",
                                source_url=url,
                                published_at=_utc_from_timestamp(submission.created_utc),
                                source_author=str(submission.author) if submission.author else None,
                            )
                            added += 1
                        except Exception as exc:
                            logger.warning("Reddit: ingest_text failed for %s: %s", url, exc)
                            errors += 1

                except Exception as exc:
                    logger.warning("Reddit search failed (r/%s, term=%r): %s", sub_name, term, exc)
                    errors += 1
        except Exception as exc:
            logger.warning("Reddit: could not access subreddit %r: %s", sub_name, exc)
            errors += 1

    logger.info(
        "Reddit ingest: subreddits=%d terms=%d found=%d added=%d skipped=%d errors=%d",
        len(subreddit_names), len(terms), posts_found, added, skipped, errors,
    )
    return RedditIngestResult(
        subreddits_searched=len(subreddit_names),
        posts_found=posts_found,
        added=added,
        skipped=skipped,
        errors=errors,
    )
