"""Reddit ingestion via public JSON API — no credentials required.

Two ingestion modes:

  1. Per-subreddit search (original). For each (sub, term) pair, hit
     reddit.com/r/{sub}/search.json. Catches what's discussed in subs we
     thought to add.

  2. Site-wide search (added). One call per term against
     reddit.com/search.json — finds discussion in subs we DIDN'T pre-curate.
     Especially useful for small races where the conversation is in tiny
     local subs we'd never enumerate.

  3. Per-district subreddits (added). Subreddit list is augmented with
     district-derived names ("scranton", "wilkesbarre", "nepa", state
     code) when the campaign config has a district set.

  4. Comment polling on matching submissions (added). For each new
     submission we ingest, also fetch its top comments. Local races
     often have more political signal in the comments than the OP.

Configurable via REDDIT_SUBREDDITS env var (comma-separated). Comments
can be disabled via REDDIT_COMMENTS_ENABLED=false.
"""
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_SUBREDDITS = ["pennsylvania", "Scranton", "nepa", "politics"]
_POST_LIMIT = 25
_COMMENT_LIMIT = 10
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


def _district_derived_subs(db) -> list[str]:
    """Best-effort: derive subreddit names from campaign config location/district.

    A heuristic, not a lookup. Returns subreddit slugs that MIGHT exist.
    Reddit will simply 404 them if they don't, and the caller handles that.
    """
    from app.models import CampaignConfig
    config = db.query(CampaignConfig).first()
    subs: list[str] = []
    if not config:
        return subs

    # Pull tokens from location ("Scranton, PA") and district ("PA-08").
    for raw in [config.location, config.district]:
        if not raw:
            continue
        # State code at start of "PA-08" or end of "Scranton, PA" → PA → r/Pennsylvania
        state_match = re.search(r"\b([A-Z]{2})\b", raw)
        if state_match:
            from app.services.source_discovery import _parse_state_code  # type: ignore
            try:
                # State subs use the full state name conventionally.
                STATE_FULL = {
                    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
                    "CA": "California", "CO": "Colorado", "CT": "Connecticut",
                    "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
                    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
                    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine",
                    "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
                    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri",
                    "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "NewHampshire",
                    "NJ": "newjersey", "NM": "NewMexico", "NY": "newyork",
                    "NC": "NorthCarolina", "ND": "NorthDakota", "OH": "Ohio",
                    "OK": "Oklahoma", "OR": "oregon", "PA": "pennsylvania",
                    "RI": "RhodeIsland", "SC": "SouthCarolina", "SD": "SouthDakota",
                    "TN": "Tennessee", "TX": "texas", "UT": "Utah", "VT": "vermont",
                    "VA": "Virginia", "WA": "Washington", "WV": "WestVirginia",
                    "WI": "wisconsin", "WY": "wyoming",
                }
                full = STATE_FULL.get(state_match.group(1))
                if full and full not in subs:
                    subs.append(full)
            except Exception:
                pass

        # City name from "Scranton, PA" → "Scranton" → r/Scranton
        # Take the first comma-separated token, strip non-letters.
        first = raw.split(",")[0].strip()
        first = re.sub(r"[^A-Za-z]", "", first)
        if first and len(first) >= 3 and first not in subs:
            subs.append(first)

    return subs


def _search_site_wide(term: str, limit: int = 25) -> list[dict]:
    """Reddit's site-wide search — finds posts in subs we didn't enumerate.

    Returns the same shape as _search_subreddit. Note `restrict_sr=0` is
    implicit (no restrict_sr param = search everywhere). We sort by `new`
    and constrain to last week to avoid getting flooded with old threads.
    """
    url = "https://www.reddit.com/search.json"
    params = {
        "q": term,
        "sort": "new",
        "limit": str(limit),
        "t": "week",
    }
    try:
        resp = httpx.get(url, params=params, headers=_HEADERS, timeout=15, follow_redirects=True)
        if resp.status_code == 429:
            logger.warning("Reddit: rate limited on site-wide search — backing off 60s")
            time.sleep(60)
            return []
        if resp.status_code != 200:
            logger.warning("Reddit: site-wide search returned %d", resp.status_code)
            return []
        return resp.json().get("data", {}).get("children", [])
    except Exception as exc:
        logger.warning("Reddit: site-wide search failed for %r: %s", term, exc)
        return []


def _fetch_comments(permalink: str, limit: int = _COMMENT_LIMIT) -> list[dict]:
    """Fetch top comments for a submission. Reddit returns [post, comments]
    when you append .json to a permalink. We pull comments[0..limit]."""
    url = f"https://www.reddit.com{permalink}.json"
    params = {"limit": str(limit), "sort": "top"}
    try:
        resp = httpx.get(url, params=params, headers=_HEADERS, timeout=15, follow_redirects=True)
        if resp.status_code != 200:
            return []
        data = resp.json()
        if not isinstance(data, list) or len(data) < 2:
            return []
        return (data[1].get("data") or {}).get("children") or []
    except Exception as exc:
        logger.debug("Reddit: comment fetch failed for %s: %s", permalink, exc)
        return []


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


def _ingest_submission(db, post: dict, source_label: str) -> tuple[bool, str]:
    """Insert one submission as a SourceItem. Returns (was_inserted, url).
    Mirrors the old loop body so site-wide + per-sub paths share logic.
    """
    from app.models import SourceItem
    from app.services.ingestion import ingest_text

    permalink = post.get("permalink", "")
    if not permalink:
        return (False, "")
    url = f"https://www.reddit.com{permalink}"
    if db.query(SourceItem).filter_by(source_url=url).first():
        return (False, url)

    title = (post.get("title") or "").strip()
    selftext = (post.get("selftext") or "").strip()
    if selftext in ("[deleted]", "[removed]", ""):
        text = title
    else:
        text = f"{title}\n\n{selftext}"
    if not text.strip():
        return (False, url)

    author = post.get("author") or None
    created_utc = post.get("created_utc") or 0
    try:
        ingest_text(
            db,
            title=title[:200],
            raw_text=text[:4000],
            source_name=source_label,
            source_type="social",
            source_url=url,
            published_at=_utc_from_timestamp(float(created_utc)) if created_utc else None,
            source_author=str(author) if author and author != "None" else None,
        )
        return (True, url)
    except Exception as exc:
        logger.warning("Reddit: ingest_text failed for %s: %s", url, exc)
        return (False, url)


def _ingest_comments(db, permalink: str, parent_sub: str, terms: list[str]) -> int:
    """Fetch top comments for a submission and ingest the ones whose text
    contains a campaign keyword. Returns count ingested.
    """
    from app.models import SourceItem
    from app.services.ingestion import ingest_text

    children = _fetch_comments(permalink, _COMMENT_LIMIT)
    if not children:
        return 0

    term_lower = [t.lower() for t in terms]
    added = 0
    for child in children:
        if child.get("kind") != "t1":  # only comments, not "more"-stubs
            continue
        cdata = child.get("data") or {}
        body = (cdata.get("body") or "").strip()
        if not body or body in ("[deleted]", "[removed]"):
            continue
        # Filter — only ingest a comment if it mentions a campaign term.
        # The PARENT submission is already relevant; the comment is only
        # interesting if it adds substantive content.
        if not any(t in body.lower() for t in term_lower):
            continue

        cperm = cdata.get("permalink", "")
        if not cperm:
            continue
        curl = f"https://www.reddit.com{cperm}"
        if db.query(SourceItem).filter_by(source_url=curl).first():
            continue

        try:
            ingest_text(
                db,
                title=body[:120].strip() + ("…" if len(body) > 120 else ""),
                raw_text=body[:4000],
                source_name=f"Reddit r/{parent_sub} (comment)",
                source_type="social",
                source_url=curl,
                published_at=_utc_from_timestamp(float(cdata.get("created_utc") or 0)) if cdata.get("created_utc") else None,
                source_author=str(cdata.get("author") or "") or None,
            )
            added += 1
        except Exception as exc:
            logger.debug("Reddit comment ingest failed: %s", exc)
    return added


def _probe_reddit_access() -> bool:
    """Return True if Reddit's unauthed JSON API is reachable.

    Reddit has progressively restricted unauthed access (most endpoints
    return 403 as of mid-2024+). When that's the case we want to fast-fail
    the whole run instead of making dozens of doomed requests. The fix is
    OAuth (set REDDIT_CLIENT_ID + REDDIT_CLIENT_SECRET + REDDIT_USERNAME
    + REDDIT_PASSWORD env vars and have the code switch to OAuth auth).

    Probes TWO endpoints because Reddit's regional CDN sometimes 403s
    one path while serving another — a single-probe false-fail would
    skip the entire ingest cycle. If either succeeds, we proceed.
    """
    probe_urls = [
        "https://www.reddit.com/r/announcements/.json?limit=1",
        "https://www.reddit.com/r/politics/.json?limit=1",
    ]
    for url in probe_urls:
        try:
            resp = httpx.get(
                url, headers=_HEADERS, timeout=8, follow_redirects=True,
            )
            if resp.status_code == 200:
                return True
        except Exception:
            continue
    return False


def ingest_reddit(db) -> RedditIngestResult:
    """Search Reddit for campaign-relevant posts and ingest them.

    Now runs three passes:
      1. Per-subreddit search across the configured + district-derived sub list
      2. Site-wide search to catch discussion in subs we didn't enumerate
      3. For every NEW submission ingested above, fetch its top comments and
         ingest any that mention a campaign term (configurable via
         REDDIT_COMMENTS_ENABLED env var, default true).
    """
    # Fast-fail if Reddit's blocking us (their post-2024 anti-bot stance
    # rejects most unauthed JSON requests). One probe, done.
    if not _probe_reddit_access():
        logger.warning(
            "Reddit: unauthed access blocked (403). Set OAuth credentials "
            "via REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET env vars to enable "
            "(see https://www.reddit.com/prefs/apps — create a 'script' app). "
            "Skipping this run."
        )
        return RedditIngestResult(0, 0, 0, 0, 0)

    terms = _search_terms(db)
    if not terms:
        logger.info("Reddit: no search terms (no campaign config)")
        return RedditIngestResult(0, 0, 0, 0, 0)

    # Configured subs + district-derived subs (deduped).
    configured = [
        s.strip()
        for s in os.getenv("REDDIT_SUBREDDITS", ",".join(_DEFAULT_SUBREDDITS)).split(",")
        if s.strip()
    ]
    derived = _district_derived_subs(db)
    subreddit_names: list[str] = list(dict.fromkeys(configured + derived))

    comments_enabled = os.getenv("REDDIT_COMMENTS_ENABLED", "true").lower() != "false"

    posts_found = added = skipped = errors = 0
    submissions_to_walk_for_comments: list[tuple[str, str]] = []  # (permalink, sub)

    # ---- Pass 1: per-subreddit search ----
    for sub in subreddit_names:
        for term in terms:
            children = _search_subreddit(sub, term, _POST_LIMIT)
            for child in children:
                post = child.get("data", {})
                posts_found += 1
                inserted, url = _ingest_submission(db, post, f"Reddit r/{sub}")
                if inserted:
                    added += 1
                    permalink = post.get("permalink", "")
                    if permalink:
                        submissions_to_walk_for_comments.append((permalink, sub))
                else:
                    skipped += 1
            time.sleep(_REQUEST_DELAY)

    # ---- Pass 2: site-wide search ----
    # One request per term — catches Reddit-wide discussion that didn't
    # surface in our enumerated subs. The "site-wide" label is used so
    # the originating sub still shows in source_name (it's per-post).
    for term in terms:
        children = _search_site_wide(term, _POST_LIMIT)
        for child in children:
            post = child.get("data", {})
            posts_found += 1
            sub = post.get("subreddit") or "all"
            inserted, url = _ingest_submission(db, post, f"Reddit r/{sub} (site search)")
            if inserted:
                added += 1
                permalink = post.get("permalink", "")
                if permalink:
                    submissions_to_walk_for_comments.append((permalink, sub))
            else:
                skipped += 1
        time.sleep(_REQUEST_DELAY)

    # ---- Pass 3: comments on the newly-ingested submissions ----
    comments_added = 0
    if comments_enabled and submissions_to_walk_for_comments:
        for permalink, sub in submissions_to_walk_for_comments:
            comments_added += _ingest_comments(db, permalink, sub, terms)
            time.sleep(_REQUEST_DELAY)
    added += comments_added

    logger.info(
        "Reddit ingest: subreddits=%d terms=%d found=%d added=%d (submissions=%d, comments=%d) skipped=%d errors=%d",
        len(subreddit_names), len(terms), posts_found,
        added, added - comments_added, comments_added, skipped, errors,
    )
    return RedditIngestResult(
        subreddits_searched=len(subreddit_names),
        posts_found=posts_found,
        added=added,
        skipped=skipped,
        errors=errors,
    )
