"""Reddit ingestion via public JSON API — no credentials required.

Searches subreddits for candidate and opponent name mentions using Reddit's
unauthenticated JSON endpoint. No app registration needed.

Subreddits searched (configurable via REDDIT_SUBREDDITS env var):
    pennsylvania, Scranton, nepa, politics (fallback defaults)
"""
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_SUBREDDITS = ["pennsylvania", "Scranton", "nepa", "politics"]
_POST_LIMIT = 25
_REQUEST_DELAY = 1.0  # seconds between requests — Reddit rate limit is ~60/min unauthed

_HEADERS = {
    "User-Agent": "CampaignWarRoom/1.0 (political monitoring; contact via github.com)",
    "Accept": "application/json",
}


@dataclass
class RedditIngestResult:
    subreddits_searched: int
    posts_found: int
    added: int
    skipped: int
    errors: int


def _search_terms(db) -> list[str]:
    from app.models import CampaignConfig, Opponent
    campaign = db.query(CampaignConfig).first()
    terms: list[str] = []
    if campaign and campaign.candidate_name:
        terms.append(campaign.candidate_name)
    for opp in db.query(Opponent).all():
        if opp.name and opp.name not in terms:
            terms.append(opp.name)
    return terms


def _utc_from_timestamp(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None)


def _search_subreddit(sub: str, term: str, limit: int = 25) -> list[dict]:
    """Hit Reddit's public JSON search endpoint. Returns list of post dicts."""
    url = f"https://www.reddit.com/r/{sub}/search.json"
    params = {
        "q": term,
        "restrict_sr": "1",
        "sort": "new",
        "limit": str(limit),
        "t": "month",
    }
    try:
        resp = httpx.get(url, params=params, headers=_HEADERS, timeout=15, follow_redirects=True)
        if resp.status_code == 429:
            logger.warning("Reddit: rate limited on r/%s — backing off 60s", sub)
            time.sleep(60)
            return []
        if resp.status_code != 200:
            logger.warning("Reddit: r/%s search returned %d", sub, resp.status_code)
            return []
        data = resp.json()
        return data.get("data", {}).get("children", [])
    except Exception as exc:
        logger.warning("Reddit: request failed for r/%s q=%r: %s", sub, term, exc)
        return []


def ingest_reddit(db) -> RedditIngestResult:
    """Search Reddit for campaign-relevant posts and ingest them."""
    from app.models import SourceItem
    from app.services.ingestion import ingest_text

    terms = _search_terms(db)
    if not terms:
        logger.info("Reddit: no search terms (no campaign config)")
        return RedditIngestResult(0, 0, 0, 0, 0)

    subreddit_names = [
        s.strip()
        for s in os.getenv("REDDIT_SUBREDDITS", ",".join(_DEFAULT_SUBREDDITS)).split(",")
        if s.strip()
    ]

    posts_found = added = skipped = errors = 0

    for sub in subreddit_names:
        for term in terms:
            children = _search_subreddit(sub, term, _POST_LIMIT)
            for child in children:
                post = child.get("data", {})
                permalink = post.get("permalink", "")
                if not permalink:
                    continue

                url = f"https://www.reddit.com{permalink}"
                posts_found += 1

                if db.query(SourceItem).filter_by(source_url=url).first():
                    skipped += 1
                    continue

                title = (post.get("title") or "").strip()
                selftext = (post.get("selftext") or "").strip()
                if selftext in ("[deleted]", "[removed]", ""):
                    text = title
                else:
                    text = f"{title}\n\n{selftext}"

                if not text.strip():
                    skipped += 1
                    continue

                try:
                    author = post.get("author") or None
                    created_utc = post.get("created_utc") or 0
                    ingest_text(
                        db,
                        title=title[:200],
                        raw_text=text[:4000],
                        source_name=f"Reddit r/{sub}",
                        source_type="social",
                        source_url=url,
                        published_at=_utc_from_timestamp(float(created_utc)) if created_utc else None,
                        source_author=str(author) if author and author != "None" else None,
                    )
                    added += 1
                except Exception as exc:
                    logger.warning("Reddit: ingest_text failed for %s: %s", url, exc)
                    errors += 1

            time.sleep(_REQUEST_DELAY)

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
