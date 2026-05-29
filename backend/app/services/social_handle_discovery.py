"""Social handle discovery — find Instagram and Facebook handles for a
named person via web search, ranked by signal strength.

Design notes:
- The bare handle (e.g. "mayorpaigecognetti") is what the rest of the system
  stores; the platform URL prefix is added at feed-generation time. So
  this module's job is "given a name + optional location, return the most
  plausible bare handles for IG and FB, each with a snippet and confidence
  the caller can show the user for one-click confirmation."
- Generalizes to any race — no hard-coded handles or per-candidate
  patterns. The only inputs are the strings the user typed into Setup.
- We don't try to log into either platform. We rely on the existing
  `search_provider` (Tavily by default) to surface profile URLs, then
  extract handles from those URLs.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import urlparse, unquote

from app.services.search_provider import get_search_provider, SearchResult

logger = logging.getLogger(__name__)


# Path segments that are NOT a handle — these are platform-internal pages
# (Instagram's reels, explore, accounts, etc.; Facebook's groups, events,
# watch, etc.). If we see a URL like instagram.com/reel/XYZ, "reel" is not
# the user we're after.
_INSTAGRAM_NON_HANDLE_SEGMENTS = {
    "p", "reel", "reels", "tv", "stories", "explore", "accounts", "direct",
    "about", "developer", "press", "api", "legal", "privacy",
}
_FACEBOOK_NON_HANDLE_SEGMENTS = {
    "pages", "groups", "events", "watch", "photo", "photos", "video",
    "videos", "story.php", "permalink.php", "sharer", "sharer.php",
    "policies", "help", "marketplace", "gaming", "business", "ads",
    "login", "recover", "reg", "tr", "search", "hashtag", "stories",
}
# A valid Instagram username is letters, digits, periods, and underscores.
_INSTAGRAM_HANDLE_RE = re.compile(r"^[A-Za-z0-9._]+$")
# Facebook page slugs allow letters, digits, periods, hyphens. The
# numeric `profile.php?id=...` variant is a separate case below.
_FACEBOOK_PAGE_RE = re.compile(r"^[A-Za-z0-9.\-]+$")


@dataclass
class HandleCandidate:
    handle: str          # bare identifier — what we store in DB
    url: str             # canonical profile URL we'd show the user
    snippet: str | None  # search-result text excerpt for user confirmation
    confidence: str      # "high" | "medium" | "low"
    score: float         # raw score so callers can re-rank if needed


def _instagram_handle_from_url(url: str) -> str | None:
    """Pull the user handle out of an instagram.com URL, or None if it
    doesn't look like a user profile.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    host = parsed.netloc.lower().lstrip("www.")
    if "instagram.com" not in host:
        return None
    segments = [s for s in parsed.path.split("/") if s]
    if not segments:
        return None
    first = unquote(segments[0])
    if first.lower() in _INSTAGRAM_NON_HANDLE_SEGMENTS:
        return None
    if not _INSTAGRAM_HANDLE_RE.match(first):
        return None
    if len(first) > 30:
        # Instagram max username length is 30.
        return None
    return first


def _facebook_page_from_url(url: str) -> str | None:
    """Pull the page slug out of a facebook.com URL, or None if it's a
    post/photo/group/etc. instead of a profile/page root.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    host = parsed.netloc.lower().lstrip("www.")
    if "facebook.com" not in host:
        return None
    segments = [s for s in parsed.path.split("/") if s]
    if not segments:
        return None
    first = unquote(segments[0])
    # profile.php?id=NNN is a numeric Facebook ID — that's still a valid
    # actor we can RSS, just under a different URL shape. Caller decides
    # whether to support it; for now we skip and prefer named pages.
    if first.lower() in _FACEBOOK_NON_HANDLE_SEGMENTS:
        return None
    if first.endswith(".php"):
        return None
    if not _FACEBOOK_PAGE_RE.match(first):
        return None
    if len(first) < 3 or len(first) > 50:
        return None
    return first


def _score_candidate(
    handle: str,
    name_tokens: set[str],
    snippet: str | None,
    title: str | None,
    appearances: int,
) -> float:
    """Cheap heuristic score for a candidate handle.

    Signal:
      +2  appears multiple times in result set
      +2  handle text contains at least one name token (last name match)
      +1  search result title contains the person's full name
      +1  snippet mentions the person's full name
      -1  handle is suspiciously short (<4 chars) — likely a wrong match
    """
    score = 0.0
    if appearances >= 2:
        score += 2.0
    handle_lower = handle.lower()
    if any(tok in handle_lower for tok in name_tokens if len(tok) >= 3):
        score += 2.0
    text = " ".join(filter(None, [title or "", snippet or ""])).lower()
    if text:
        full_name = " ".join(sorted(name_tokens))
        if all(tok in text for tok in name_tokens if len(tok) >= 3):
            score += 1.0
        if full_name and full_name in text:
            score += 0.5
    if len(handle) < 4:
        score -= 1.0
    return score


def _confidence_label(score: float) -> str:
    if score >= 4.0:
        return "high"
    if score >= 2.0:
        return "medium"
    return "low"


def _name_tokens(name: str) -> set[str]:
    """Split a name into tokens we'll compare against handle text and
    snippets. Lowercased, alphanumeric only, length >= 2.
    """
    return {tok for tok in re.split(r"[^a-z0-9]+", name.lower()) if len(tok) >= 2}


def _build_candidates(
    results: Iterable[SearchResult],
    name_tokens: set[str],
    platform: str,
    limit: int,
) -> list[HandleCandidate]:
    """Walk search results, extract handles per platform, then rank."""
    extract = (
        _instagram_handle_from_url if platform == "instagram"
        else _facebook_page_from_url
    )
    base_url = (
        "https://www.instagram.com/" if platform == "instagram"
        else "https://www.facebook.com/"
    )
    # Bucket by handle so we can count appearances and grab the best snippet.
    by_handle: dict[str, list[SearchResult]] = {}
    for r in results:
        h = extract(r.url)
        if not h:
            continue
        by_handle.setdefault(h, []).append(r)

    candidates: list[HandleCandidate] = []
    for handle, hits in by_handle.items():
        best = hits[0]
        score = _score_candidate(
            handle=handle,
            name_tokens=name_tokens,
            snippet=best.snippet,
            title=best.title,
            appearances=len(hits),
        )
        candidates.append(HandleCandidate(
            handle=handle,
            url=f"{base_url}{handle}",
            snippet=best.snippet or best.title,
            confidence=_confidence_label(score),
            score=score,
        ))

    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates[:limit]


def discover_social_handles(
    name: str,
    location: str | None = None,
    limit_per_platform: int = 3,
) -> dict[str, list[HandleCandidate]]:
    """Find candidate IG/FB handles for `name`.

    Two search queries are issued — one per platform, each scoped to
    `site:instagram.com` / `site:facebook.com` and including the location
    string when provided (helps disambiguate common names).

    The result is a dict keyed by platform with up to `limit_per_platform`
    ranked candidates each. Callers (the Setup wizard) typically show the
    top candidate as a one-click confirm and the rest under a
    "see other matches" affordance.

    When no search provider is configured (SEARCH_PROVIDER=mock) the
    provider returns an empty result set and this function returns empty
    lists per platform. Callers should surface that as "configure a
    search provider to enable handle discovery" rather than a hard error.
    """
    if not name or not name.strip():
        return {"instagram": [], "facebook": []}

    provider = get_search_provider()
    tokens = _name_tokens(name)
    quoted = f'"{name.strip()}"'
    loc = (location or "").strip()
    out: dict[str, list[HandleCandidate]] = {}
    for platform, host in (("instagram", "instagram.com"), ("facebook", "facebook.com")):
        query = f"{quoted} site:{host}"
        if loc:
            query = f"{query} {loc}"
        response = provider.search(query, limit=10)
        if response.message:
            logger.info("handle discovery %s: %s", platform, response.message)
        out[platform] = _build_candidates(
            response.results, tokens, platform, limit_per_platform
        )
    return out
