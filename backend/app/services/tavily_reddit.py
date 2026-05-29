"""Reddit ingestion via Tavily search API — alternative to Reddit's
unauthed JSON API (which now returns 403 to all non-OAuth clients).

Tavily is already plumbed in services/search_provider.py as the "live web
search" backend for the search-query monitor type. Here we use it
specifically with `site:reddit.com` queries to pull Reddit content into
the SourceItem pipeline without needing Reddit OAuth credentials.

When to use this vs. Google News Reddit feeds:
  - **Google News Reddit feeds** (in source_discovery.py): zero config,
    works immediately, but coverage lags by hours and lacks snippet/date.
  - **Tavily-backed Reddit search** (this module): richer results with
    content snippets, faster recency, structured published_at — but
    consumes Tavily API quota.

The two are complementary: run both. Google News catches what's already
indexed; Tavily catches fresh + comment-thread content.

Enable by setting:
  TAVILY_API_KEY=...        (gets you to the Tavily live search backend)
  SEARCH_PROVIDER=tavily    (already-needed; the search_provider module checks this)
"""
from __future__ import annotations
import logging
import os
import time
from dataclasses import dataclass

from app.services.search_provider import get_search_provider, MockSearchProvider

logger = logging.getLogger(__name__)


@dataclass
class TavilyRedditResult:
    queries_run: int
    results_found: int
    added: int
    skipped: int
    errors: int


def _search_terms(db) -> list[str]:
    """Same shape as ingestion_reddit._search_terms — keep them in sync."""
    from app.models import CampaignConfig, Opponent
    campaign = db.query(CampaignConfig).first()
    terms: list[str] = []
    if campaign and campaign.candidate_name:
        terms.append(campaign.candidate_name)
    for opp in db.query(Opponent).all():
        if opp.name and opp.name not in terms:
            terms.append(opp.name)
    return terms


def _reddit_pre_llm_keywords(db) -> list[str]:
    """Same shape as mastodon_ingest._campaign_keywords_for_filter — pre-LLM
    keyword gate so we only score Reddit posts that genuinely mention the
    candidate / opponent / district. Reuses the same logic to keep both
    ingest paths consistent."""
    from app.services.mastodon_ingest import _campaign_keywords_for_filter
    return _campaign_keywords_for_filter(db)


def ingest_tavily_reddit(db) -> TavilyRedditResult:
    """Run Tavily search restricted to reddit.com for each campaign term;
    ingest the results as SourceItems.

    Two-stage filter:
      1. Tavily's `site:reddit.com "<term>"` query already pre-filters
         server-side to reddit pages mentioning the term.
      2. Pre-LLM keyword gate: results must contain a race keyword
         (candidate/opponent/district) in title+snippet text before we
         queue them through `ingest_text` (which runs the LLM scoring
         pipeline ~$0.0001/item). Without this gate, every Reddit hit
         on a candidate's name — even when the candidate is mentioned
         once in passing in a giant Reddit comment thread — costs an
         LLM call. The Mastodon ingest path has the same gate (added
         after we saw 187 junk LLM scoring calls in one batch).

    Returns counts. No-op (and logs) if Tavily isn't configured."""
    from app.models import SourceItem
    from app.services.ingestion import ingest_text

    provider = get_search_provider()
    if isinstance(provider, MockSearchProvider):
        logger.info(
            "tavily_reddit: search provider is mock (set SEARCH_PROVIDER=tavily "
            "+ TAVILY_API_KEY to enable). Skipping."
        )
        return TavilyRedditResult(0, 0, 0, 0, 0)
    if provider.name != "tavily":
        logger.info("tavily_reddit: search provider is %s, not tavily — skipping", provider.name)
        return TavilyRedditResult(0, 0, 0, 0, 0)

    terms = _search_terms(db)
    if not terms:
        return TavilyRedditResult(0, 0, 0, 0, 0)

    race_keywords = _reddit_pre_llm_keywords(db)

    queries_run = found = added = skipped = errors = filtered_out = 0

    for term in terms:
        # Reddit-only via site: operator. Tavily honors common Google-style
        # search operators in the query field.
        query = f'site:reddit.com "{term}"'
        try:
            resp = provider.search(query, limit=20)
        except Exception as exc:
            logger.warning("tavily_reddit: search failed for %r: %s", term, exc)
            errors += 1
            continue
        queries_run += 1
        for r in resp.results:
            if not r.url or "reddit.com" not in r.url:
                continue
            found += 1
            if db.query(SourceItem).filter_by(source_url=r.url).first():
                skipped += 1
                continue
            text = (r.snippet or "").strip()
            # Tavily snippet is usually short (~200 chars). Combine with title
            # so the relevance scorer has something to work with. Skip if
            # truly empty.
            if not text and not r.title:
                continue
            # Pre-LLM keyword gate: skip results whose title+snippet text
            # doesn't mention any race keyword. Saves the LLM scoring cost
            # on obviously off-topic results that Tavily happened to return
            # (e.g. another Cognetti / Bresnahan in a different state).
            check_text = f"{r.title or ''} {text}".lower()
            if race_keywords and not any(k in check_text for k in race_keywords):
                filtered_out += 1
                continue
            raw = (r.title or "") + ("\n\n" + text if text else "")
            # Extract Reddit username from URL if present — e.g.
            # https://reddit.com/r/Scranton/comments/abc/title/?context=3
            # → "r/Scranton". For user posts: https://reddit.com/u/foo
            # → "u/foo". Best-effort; missing is fine.
            author = None
            import re as _re
            m = _re.search(r"reddit\.com/(r/[A-Za-z0-9_]+|u/[A-Za-z0-9_-]+|user/[A-Za-z0-9_-]+)", r.url)
            if m:
                author = m.group(1)
            try:
                ingest_text(
                    db,
                    title=(r.title or text or "Reddit post")[:200],
                    raw_text=raw[:4000],
                    source_name=f"Reddit via Tavily (matched: {term})",
                    source_author=author,
                    source_type="social",
                    source_url=r.url,
                    published_at=r.published_at,
                )
                added += 1
            except Exception as exc:
                logger.debug("tavily_reddit: ingest_text failed for %s: %s", r.url, exc)
                errors += 1
        # Courtesy delay between queries.
        time.sleep(0.5)

    logger.info(
        "tavily_reddit: queries=%d found=%d filtered_out=%d added=%d skipped=%d errors=%d",
        queries_run, found, filtered_out, added, skipped, errors,
    )
    return TavilyRedditResult(
        queries_run=queries_run,
        results_found=found,
        added=added,
        skipped=skipped,
        errors=errors,
    )
