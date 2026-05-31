"""Recover full article body when the RSS-provided summary is too short.

Two scenarios drive this:

1. **Google News intermediary feeds** (`news.google.com/rss/articles/CBMi...`).
   Around 2026-05-26 Google stopped including body excerpts in these feeds
   — every entry now arrives with only a `~50-100 char` title+outlet string.
   For those entries we attempt to decode the Google News redirect URL into
   the underlying publisher URL, then fetch+extract the body.

2. **Direct publisher feeds** where the RSS itself only carries a short
   excerpt (`<200 chars`). Most Wordpress-style `/feed/` endpoints fall in
   this bucket. When `entry.link` is already a publisher URL, we can fetch
   it and extract the full body without any decoding step.

The Google News decoder is **best-effort**: Google has actively been
breaking the documented decode paths (the page used to expose
`data-n-a-sg`/`data-n-a-ts` attributes; that surface has been removed in
the modern format). When the decoder fails this module returns `None` and
the caller falls back to whatever raw_text the RSS provided — never breaks
ingestion.

A process-wide LRU cache memoizes successful resolutions for the
lifetime of the worker. The cache survives the slow batchexecute
roundtrip but not a restart; that's fine — the redirect resolution is
deterministic, so a restart just re-pays the cost on the first hit.
"""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from typing import Optional, Tuple
from urllib.parse import quote, urlparse

import httpx

logger = logging.getLogger(__name__)


# Headers tuned to bypass Google's geo-detection consent gate on EU-routed
# servers. The CONSENT + SOCS cookies short-circuit the consent.google.com
# redirect; the Chrome UA + Accept-Language steer the response into the
# US-en variant which is the one the decoder logic targets.
_GNEWS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
    "Cookie": (
        "CONSENT=YES+cb.20210720-07-p0.en+FX+410; "
        "SOCS=CAISHAgBEhJnd3NfMjAyMjA3MTAtMF9SQzEaAm5vKAE"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Headers for the publisher fetch — different UA pattern that's less
# likely to trip CDN bot filters (some publishers 403 generic Mozilla/5.0
# but accept the standard Chrome UA).
_PUBLISHER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Tight timeouts: this runs inline in the RSS ingest loop, and we'd rather
# skip a slow recovery than block the entire feed cycle. Decode is cheap
# (a few KB), publisher fetch is the long pole.
_DECODE_TIMEOUT_S = 8.0
_FETCH_TIMEOUT_S = 12.0

# Skip recovery for raw_text already this long — the RSS already carried a
# reasonable excerpt and the body-fetch overhead isn't worth it.
RECOVERY_THRESHOLD_CHARS = 200


def is_google_news_redirect(url: Optional[str]) -> bool:
    """True if `url` points at a Google News article redirect.

    Covers both URL shapes Google emits: `/articles/...` (the modern web
    format) and `/rss/articles/...` (the format feedparser receives).
    """
    if not url:
        return False
    try:
        p = urlparse(url)
    except Exception:
        return False
    if p.hostname != "news.google.com":
        return False
    parts = p.path.strip("/").split("/")
    if len(parts) >= 2 and parts[-2] in ("articles", "read"):
        return True
    if len(parts) >= 3 and parts[0] == "rss" and parts[-2] in ("articles", "read"):
        return True
    return False


def _extract_base64_id(url: str) -> Optional[str]:
    """Pull the CBMi... article ID out of a Google News redirect URL."""
    try:
        p = urlparse(url)
        parts = p.path.strip("/").split("/")
        if parts and parts[-2] in ("articles", "read"):
            return parts[-1]
    except Exception:
        pass
    return None


def _fetch_decoding_params(b64: str) -> Tuple[Optional[str], Optional[str]]:
    """Get the (signature, timestamp) needed by the batchexecute decoder.

    Returns (None, None) if the page no longer exposes the data attributes
    (which is the current observed state — Google removed them — but the
    function survives a future re-introduction or alternate page format).
    """
    for path in (
        f"https://news.google.com/articles/{b64}",
        f"https://news.google.com/rss/articles/{b64}",
    ):
        try:
            r = httpx.get(
                path,
                headers=_GNEWS_HEADERS,
                timeout=_DECODE_TIMEOUT_S,
                follow_redirects=True,
            )
            if r.status_code != 200:
                continue
            sig_m = re.search(r'data-n-a-sg="([^"]+)"', r.text)
            ts_m = re.search(r'data-n-a-ts="([^"]+)"', r.text)
            if sig_m and ts_m:
                return sig_m.group(1), ts_m.group(1)
        except Exception as exc:
            logger.debug("gnews decode-params fetch failed for %s: %s", path[:60], exc)
    return None, None


def _decode_via_batchexecute(b64: str, sig: str, ts: str) -> Optional[str]:
    """POST to Google's batchexecute endpoint to translate the article ID
    into the underlying publisher URL.
    """
    payload = [
        "Fbv4je",
        f'["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{b64}",{ts},"{sig}"]',
    ]
    body = f"f.req={quote(json.dumps([[payload]]))}"
    try:
        r = httpx.post(
            "https://news.google.com/_/DotsSplashUi/data/batchexecute",
            headers={
                **_GNEWS_HEADERS,
                "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8",
            },
            content=body,
            timeout=_DECODE_TIMEOUT_S,
        )
        r.raise_for_status()
        # Response format: )]}'\n\n<json arr>...
        raw = r.text
        if "\n\n" not in raw:
            return None
        chunk = raw.split("\n\n", 1)[1]
        outer = json.loads(chunk)[:-2]
        inner = json.loads(outer[0][2])
        return inner[1]
    except Exception as exc:
        logger.debug("gnews batchexecute failed: %s", exc)
        return None


# Process-wide cache. lru_cache keyed on the raw URL — different redirects
# are different cache entries. Bounded so the worker doesn't grow unbounded
# over long-running uptime.
@lru_cache(maxsize=2048)
def resolve_google_news_url(url: str) -> Optional[str]:
    """Resolve a Google News redirect URL to the underlying publisher URL.

    Returns None on any failure — caller treats that as "no recovery
    possible, keep the RSS-provided raw_text." Cached for the process
    lifetime so we don't re-decode the same article repeatedly.

    NOTE: this is **best-effort**. As of 2026-05-30 Google's article page
    no longer exposes the `data-n-a-sg`/`data-n-a-ts` attributes the
    batchexecute path depends on, so this returns None for most inputs in
    practice. The function survives a future format change or alternate
    page route — if Google reintroduces a working decode path, this stops
    returning None automatically.
    """
    b64 = _extract_base64_id(url)
    if not b64:
        return None
    sig, ts = _fetch_decoding_params(b64)
    if not (sig and ts):
        return None
    return _decode_via_batchexecute(b64, sig, ts)


def fetch_publisher_body(
    url: str,
    *,
    min_words: int = 40,
) -> Optional[str]:
    """Fetch `url` and extract its body text via the standard readability
    pipeline. Returns the extracted body, or None if the body is too short
    to be useful (`< min_words`) or the fetch failed.

    Reuses the same html-cleaning helpers as `ingest_url` so quality
    behavior stays consistent between the inline-RSS path and the
    deferred-body-recovery path.
    """
    try:
        r = httpx.get(
            url,
            headers=_PUBLISHER_HEADERS,
            timeout=_FETCH_TIMEOUT_S,
            follow_redirects=True,
        )
        if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
            return None
        html = r.text
    except Exception as exc:
        logger.debug("publisher body fetch failed for %s: %s", url[:80], exc)
        return None

    # Import locally to avoid a circular import (ingestion imports this module).
    from app.services.ingestion import (
        _clean_html_with_quality,
        _try_readability_extraction,
    )

    try:
        _, body_text, _, _, _ = _clean_html_with_quality(html)
        if body_text and len(body_text.split()) >= min_words:
            return body_text
        # Standard extractor returned weak content — try readability rescue.
        _, read_body = _try_readability_extraction(html)
        if read_body and len(read_body.split()) >= min_words:
            return read_body
    except Exception as exc:
        logger.debug("body extraction failed for %s: %s", url[:80], exc)
    return None


def recover_body(
    rss_link: str,
    rss_raw_text: str,
    *,
    publisher_domain: Optional[str] = None,
    title: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Best-effort body recovery for an RSS entry.

    Returns `(recovered_body, resolved_url)`. Either or both may be None.

    - `recovered_body` is the full article text on success, None on
      failure. The caller assigns it to `SourceItem.raw_text` if non-None.
    - `resolved_url` is the underlying publisher URL when the input was a
      Google News redirect AND we successfully resolved it (either via
      decoder OR title-based search). Useful for downstream paths that
      need to attribute to the correct domain (e.g., the YouTube
      transcript fetcher needs the real youtube.com URL).

    Caller still owns dedup — we do NOT change the SourceItem's
    `source_url`; the Google News URL stays as the dedup key.

    Strategy chain for Google News redirects:
      1. batchexecute decoder (broken since 2026-05-26 — Google removed
         the data attrs the documented mechanism depends on)
      2. title-based publisher search (only if `publisher_domain` and
         `title` are supplied) — fall back when the decoder gives up

    The function short-circuits with `(None, None)` if `rss_raw_text` is
    already long enough — we only spend HTTP budget on items that need it.
    """
    if rss_raw_text and len(rss_raw_text) >= RECOVERY_THRESHOLD_CHARS:
        return None, None

    target_url = rss_link
    resolved_url: Optional[str] = None
    if is_google_news_redirect(rss_link):
        decoded = resolve_google_news_url(rss_link)
        if decoded:
            resolved_url = decoded
            target_url = decoded
        elif publisher_domain and title:
            # Decoder failed — fall back to publisher-site title search.
            # Verifies the candidate URL's title against `title` before
            # returning, so we don't pull an unrelated article that
            # shared keywords.
            searched = search_publisher_for_article(publisher_domain, title)
            if searched:
                resolved_url = searched
                target_url = searched
            else:
                return None, None
        else:
            return None, None

    body = fetch_publisher_body(target_url)
    if body:
        return body, resolved_url
    return None, resolved_url


# ── Title-based publisher search ─────────────────────────────────────────

# Title similarity threshold for accepting a search result as the right
# article. Lower than the dedup threshold (0.90) because publisher
# rendered titles sometimes drop/add words vs. the RSS-provided title
# (truncation, em-dash punctuation, "FIRST LOOK:" prefixes, etc.).
_PUBLISHER_SEARCH_TITLE_THRESHOLD = 0.85

# Cap search-result candidates we'll fetch per strategy. Each verify is
# one HTTP request; with 3 strategies × 3 candidates = at most 9 fetches
# per stub. Most stubs won't need all 9 — we return on first verified
# match.
_MAX_CANDIDATES_PER_STRATEGY = 3


def _normalize_search_title(title: str) -> str:
    """Strip the Google-News-style ` - Publisher Name` suffix for cleaner
    search queries. Mirrors `dedup_merge.normalize_title` but kept local
    to avoid circular imports — the body-recovery module is imported
    from ingestion.py, dedup_merge is too, but pulling normalize_title
    out into a shared helper isn't worth the indirection for two callers.
    """
    if not title:
        return ""
    s = title.strip()
    if " - " in s:
        head, _, tail = s.rpartition(" - ")
        if 1 < len(tail) <= 40 and len(head) > 20:
            s = head
    return s.strip()


def _title_similarity(a: str, b: str) -> float:
    """SequenceMatcher ratio on case-insensitive normalized titles."""
    from difflib import SequenceMatcher
    na = _normalize_search_title(a or "").lower()
    nb = _normalize_search_title(b or "").lower()
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def _extract_candidate_links_from_html(
    html: str, publisher_domain: str, title: Optional[str] = None,
) -> list[str]:
    """Pull plausible article links from a publisher's search results page
    and rank them by title-keyword overlap with the URL slug.

    Why rank: WordPress search pages link to a *lot* of non-article URLs
    (CSS bundles, plugin assets, category index pages, RSS feeds, etc.).
    A naive first-N-on-page extractor picks those up because they appear
    before the actual results in the HTML head/nav. The article slug for
    a real match contains the title's distinctive words (e.g.
    `rep-bresnahan-introduces-legislation-to-simplify-veterans-claims`),
    so ranking by slug-keyword overlap surfaces the right URL even when
    it's the 30th href on the page.

    Filters first (cheap), ranks second (only over filtered survivors),
    returns the top `_MAX_CANDIDATES_PER_STRATEGY`.
    """
    import re
    from urllib.parse import urljoin

    raw_links = re.findall(r'href=["\']([^"\']+)["\']', html)

    pub_lower = publisher_domain.lower()
    # Distinctive keywords from the title — ≥4 chars, lowercased,
    # punctuation stripped. The ≥4 cutoff drops articles/prepositions
    # ("the", "of", "in") that match too many things.
    if title:
        title_norm = _normalize_search_title(title).lower()
        title_words = set(
            w.strip(".,'\"!?;:()[]")
            for w in re.split(r"[\s-]+", title_norm)
            if len(w.strip(".,'\"!?;:()[]")) >= 4
        )
    else:
        title_words = set()

    # Path patterns that are NEVER articles — extension list grew when
    # tested against timesleader.com (a typical WordPress site).
    _BAD_SEGMENTS = (
        "/category/", "/tag/", "/author/", "/page/",
        "/wp-content/", "/wp-includes/", "/wp-admin/", "/wp-json/",
        "/feed", "/rss", "/comments/",
        "/login", "/register", "/privacy", "/terms",
        "/product/", "/product-category/", "/shop/",
        "/bookcase/", "/editions/",
        ".css", ".js", ".jpg", ".png", ".gif", ".svg", ".webp", ".ico",
        ".xml", ".pdf", ".woff", ".ttf",
    )

    filtered: list[tuple[str, str]] = []  # (absolute_url, lowercase_path)
    seen: set[str] = set()
    for raw in raw_links:
        if not raw or raw.startswith("#") or raw.startswith("mailto:") or raw.startswith("javascript:"):
            continue
        absolute = urljoin(f"https://{publisher_domain}/", raw)
        if pub_lower not in absolute.lower():
            continue
        # Subdomain check — `editions.timesleader.com` is the publisher's
        # but not the article surface, so reject subdomains other than
        # bare and `www.`.
        from urllib.parse import urlparse
        host = urlparse(absolute).netloc.lower()
        if host not in (pub_lower, f"www.{pub_lower}"):
            continue
        dedup_key = absolute.split("?")[0].split("#")[0]
        if dedup_key in seen:
            continue
        path = urlparse(dedup_key).path.lower()
        if path in ("", "/", "/home", "/contact", "/about", "/subscribe"):
            continue
        if any(seg in path for seg in _BAD_SEGMENTS):
            continue
        segments = [s for s in path.strip("/").split("/") if s]
        if len(segments) < 2 and not any(s.isdigit() for s in segments):
            continue
        seen.add(dedup_key)
        filtered.append((absolute, path))

    if not filtered:
        return []

    if not title_words:
        # No title to rank against — fall back to first N in DOM order.
        return [u for u, _ in filtered[:_MAX_CANDIDATES_PER_STRATEGY]]

    # Rank by count of title keywords that appear in the URL path.
    # Most relevant article appears first. URLs with zero matching
    # keywords are dropped — verification would reject them anyway and
    # we'd waste an HTTP round trip.
    scored: list[tuple[int, str]] = []
    for url, path in filtered:
        slug_words = set(re.split(r"[\W_]+", path))
        score = sum(1 for w in title_words if w in slug_words)
        if score > 0:
            scored.append((score, url))
    scored.sort(key=lambda x: -x[0])
    return [u for _, u in scored[:_MAX_CANDIDATES_PER_STRATEGY]]


def _verify_candidate_url(url: str, expected_title: str) -> Optional[str]:
    """Fetch `url`, extract its page title, and return the body if the
    page title is similar enough to `expected_title`. Returns None on
    any failure: fetch error, low similarity, no extractable title.
    """
    try:
        r = httpx.get(
            url,
            headers=_PUBLISHER_HEADERS,
            timeout=_FETCH_TIMEOUT_S,
            follow_redirects=True,
        )
        if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
            return None
        html = r.text
    except Exception as exc:
        logger.debug("publisher search verify fetch failed for %s: %s", url[:80], exc)
        return None

    from app.services.ingestion import (
        _clean_html_with_quality,
        _try_readability_extraction,
    )

    # Extract title for verification + body for return.
    try:
        page_title, body_text, _, _, _ = _clean_html_with_quality(html)
        if not body_text or len(body_text.split()) < 40:
            # Try readability rescue for thin/poor extractions.
            read_title, read_body = _try_readability_extraction(html)
            if read_body and len(read_body.split()) >= 40:
                page_title = read_title or page_title
                body_text = read_body
            else:
                return None
    except Exception as exc:
        logger.debug("publisher search verify extraction failed for %s: %s", url[:80], exc)
        return None

    sim = _title_similarity(expected_title, page_title or "")
    if sim < _PUBLISHER_SEARCH_TITLE_THRESHOLD:
        logger.debug(
            "publisher search verify rejected %s — title similarity %.3f < %.2f",
            url[:80], sim, _PUBLISHER_SEARCH_TITLE_THRESHOLD,
        )
        return None
    return body_text


def search_publisher_for_article(
    publisher_domain: str,
    title: str,
) -> Optional[str]:
    """Find an article URL on `publisher_domain` matching `title`.

    Tries three strategies in order. Returns the first verified-match
    URL or None.

      1. WordPress-style `?s=`: most US local papers run WP. Hit rate
         ~70% on WP-backed sites in spot-check.
      2. Generic `/search?q=`: some non-WP CMSes use this. Cheap to try.
      3. Sitemap probe: load `sitemap.xml`, scan for URLs whose slug
         contains the title's distinctive keywords. Slower but works
         when on-site search is broken or absent.

    Each candidate URL is title-verified via `_verify_candidate_url`
    before being accepted — guards against false matches where the
    publisher's search returns a recent article that shares keywords.

    Best-effort with graceful failure: if the publisher 403s us at the
    WAF (like Citizens' Voice, Times-Tribune, Standard-Speaker for the
    PA-08 campaign), each strategy returns None and we return None.
    """
    from urllib.parse import quote_plus

    clean_title = _normalize_search_title(title)
    if not clean_title or not publisher_domain:
        return None

    encoded = quote_plus(clean_title)

    strategies = [
        ("wp-search", f"https://{publisher_domain}/?s={encoded}"),
        ("generic-search", f"https://{publisher_domain}/search?q={encoded}"),
    ]

    for strategy_name, search_url in strategies:
        try:
            r = httpx.get(
                search_url,
                headers=_PUBLISHER_HEADERS,
                timeout=_FETCH_TIMEOUT_S,
                follow_redirects=True,
            )
            if r.status_code != 200 or "html" not in r.headers.get("content-type", ""):
                continue
        except Exception as exc:
            logger.debug(
                "publisher search [%s] failed for %s: %s",
                strategy_name, publisher_domain, exc,
            )
            continue

        candidates = _extract_candidate_links_from_html(r.text, publisher_domain, title=title)
        for candidate in candidates:
            body = _verify_candidate_url(candidate, title)
            if body:
                logger.info(
                    "publisher search [%s] resolved %s for title=%r",
                    strategy_name, candidate[:80], clean_title[:60],
                )
                return candidate

    # Sitemap strategy — last resort. Doesn't always exist or include
    # recent articles; we look for a URL whose slug contains the title's
    # main keywords.
    try:
        r = httpx.get(
            f"https://{publisher_domain}/sitemap.xml",
            headers=_PUBLISHER_HEADERS,
            timeout=_FETCH_TIMEOUT_S,
            follow_redirects=True,
        )
        if r.status_code == 200:
            import re
            loc_urls = re.findall(r"<loc>([^<]+)</loc>", r.text)
            # Cheap keyword filter: title's distinctive word stems in URL slug.
            keywords = [w.lower() for w in clean_title.split() if len(w) >= 5][:3]
            for url in loc_urls:
                if all(kw in url.lower() for kw in keywords):
                    body = _verify_candidate_url(url, title)
                    if body:
                        logger.info(
                            "publisher search [sitemap] resolved %s for title=%r",
                            url[:80], clean_title[:60],
                        )
                        return url
    except Exception as exc:
        logger.debug(
            "publisher search [sitemap] failed for %s: %s",
            publisher_domain, exc,
        )

    return None


def recover_stub_bodies(
    db,
    *,
    window_hours: int = 96,
    max_items: int = 1000,
    min_chars: int = 200,
    commit_every: int = 25,
) -> dict:
    """One-shot pass over recently-ingested short-body items.

    Use case: the 2026-05-26 Google News body-excerpt collapse left several
    hundred items in the corpus with title-only `raw_text`. Forward
    ingestion now runs `recover_body` inline (see `ingestion.py:1197`),
    but already-persisted rows need a separate sweep.

    For each candidate item this:
      1. Calls `recover_body(item.source_url, item.raw_text)` — same code
         path as forward ingest, so success rates match.
      2. On a non-None body, updates `raw_text` AND clears `summary` so
         the existing `rescore_svc` (with `only_unscored=True`) picks the
         item up for full re-analysis. Doing the re-score inline here
         would couple two long-running operations and complicate
         cancellation; clearing `summary` defers the LLM work to the
         existing background worker.

    Args:
      window_hours: how far back to look. Default 96h = May 26 onward
                    relative to a 2026-05-30 run; bump for wider windows.
      max_items: cap so a single invocation doesn't tie up the worker
                 for an unbounded amount of time. 1000 items at
                 ~3s/item = ~50 min.
      min_chars: items with raw_text already at least this long are
                 skipped — match the threshold `recover_body` uses
                 internally so we don't waste cycles on items that
                 wouldn't get rewritten anyway.
      commit_every: flush + commit recovered items in batches of this
                 size, instead of waiting until the end of the loop. A
                 mid-loop crash (publisher timeout cascade, OOM, etc.)
                 then loses at most `commit_every-1` items of progress
                 instead of all of them. 25 keeps per-item overhead
                 low while bounding the loss window to ~1-2 min of work.

    Returns a summary dict; idempotent (re-runs skip items already
    rewritten — they'll be ≥ min_chars now and short-circuit).
    """
    from datetime import datetime, timedelta
    from sqlalchemy import func
    from app.models import SourceItem

    cutoff = datetime.utcnow() - timedelta(hours=window_hours)
    items = (
        db.query(SourceItem)
        .filter(
            SourceItem.created_at >= cutoff,
            SourceItem.raw_text.isnot(None),
            func.length(SourceItem.raw_text) < min_chars,
            SourceItem.archived_as_irrelevant == False,  # noqa: E712
        )
        .order_by(SourceItem.created_at.desc())
        .limit(max_items)
        .all()
    )

    recovered = 0
    no_recovery = 0
    youtube_resolved = 0  # decoded a youtube.com URL but body fetch failed
    pending_since_commit = 0
    for item in items:
        if not item.source_url:
            no_recovery += 1
            continue
        body, resolved_url = recover_body(
            item.source_url,
            item.raw_text or "",
            publisher_domain=item.publisher_domain,
            title=item.title,
        )
        if body:
            item.raw_text = body[:4000]
            # Clear `summary` so `rescore_svc(only_unscored=True)` reruns the
            # LLM. Don't touch race_relevance_score etc. here — letting the
            # rescore overwrite them is the cleaner separation of concerns.
            item.summary = None
            recovered += 1
            pending_since_commit += 1
            # If we also got a youtube.com URL we could now extract a
            # transcript. The transcript path is in `ingestion._fetch_youtube_transcript`;
            # invoking it here would double the per-item time. Skip for
            # this pass — the forward path handles it for new items, and
            # a separate transcript-only sweep can target the backlog.
            if resolved_url and "youtube.com" in resolved_url:
                youtube_resolved += 1
            # Batched commit: persist progress every `commit_every`
            # successfully-recovered items so a mid-loop crash doesn't
            # lose everything. We commit on RECOVERED count, not iteration
            # count — no point opening tx churn for items that returned
            # None from recover_body.
            if pending_since_commit >= commit_every:
                db.commit()
                pending_since_commit = 0
        else:
            no_recovery += 1
    # Final commit catches anything still pending.
    if pending_since_commit > 0:
        db.commit()
    return {
        "checked": len(items),
        "recovered": recovered,
        "no_recovery": no_recovery,
        "youtube_resolved_no_body": youtube_resolved,
        "window_hours": window_hours,
    }
