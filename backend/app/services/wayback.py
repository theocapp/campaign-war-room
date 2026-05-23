"""Web archive fallback fetchers.

Two snapshot systems are tried in sequence by `try_archive_fallbacks()`:
  1. Wayback Machine (web.archive.org) — broad coverage, slow CDX API
  2. archive.today / archive.ph — different crawler, often catches what
     Wayback missed (especially recent paywall content)

Used by:
  - gdelt_backfill: recovers paywalled URLs returned by GDELT
  - ingest_url: rescues normal-ingestion URLs that fail or extract poorly

Both return raw HTML on success (or None on miss). The caller is responsible
for HTML parsing.
"""
import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

WAYBACK_CDX_API = "http://web.archive.org/cdx/search/cdx"
WAYBACK_FETCH = "https://web.archive.org/web/{timestamp}/{url}"

# archive.today supports several mirror domains. We try .ph first (most
# reliable), with .today as backup. They share the same snapshot store.
ARCHIVE_TODAY_HOSTS = ["archive.ph", "archive.today"]

_UA = "Mozilla/5.0 (compatible; CampaignWarRoom/1.0)"
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def wayback_fetch(url: str, timeout: float = 20.0) -> Optional[str]:
    """Try to retrieve a cached copy of `url` from the Wayback Machine.

    Returns the raw HTML text (with Wayback toolbar markup) if a 200-status
    snapshot exists, otherwise None.

    Cheap on success (~2s typical), but CDX queries can be slow under load —
    we use a generous timeout. Never raises.
    """
    if not url:
        return None
    try:
        cdx = httpx.get(
            WAYBACK_CDX_API,
            params={
                "url": url,
                "output": "json",
                "limit": "1",
                "fl": "timestamp,statuscode",
                "filter": "statuscode:200",
                "collapse": "digest",
            },
            timeout=timeout,  # CDX itself can be slow; give it the full budget
        )
        rows = cdx.json()
        if not rows or len(rows) < 2:  # first row is header
            return None
        timestamp = rows[1][0]
        archived_url = WAYBACK_FETCH.format(timestamp=timestamp, url=url)
        resp = httpx.get(
            archived_url, timeout=timeout, follow_redirects=True,
            headers={"User-Agent": _UA},
        )
        resp.raise_for_status()
        return resp.text
    except Exception as exc:
        logger.debug("wayback_fetch failed for %s: %s", url, exc)
        return None


def archive_today_fetch(url: str, timeout: float = 15.0) -> Optional[str]:
    """Try to retrieve a cached copy of `url` from archive.today / archive.ph.

    Uses the Memento TimeMap (RFC 7089) endpoint to find snapshots, picks
    the most recent, and fetches it. Returns raw HTML or None.

    archive.today fronts requests with Cloudflare and may challenge plain
    crawler UAs, so we use a browser-like UA. Never raises.
    """
    if not url:
        return None
    for host in ARCHIVE_TODAY_HOSTS:
        try:
            # TimeMap returns a memento list — plain text, parse manually.
            tm = httpx.get(
                f"https://{host}/timemap/{url}",
                timeout=timeout,
                follow_redirects=True,
                headers={"User-Agent": _BROWSER_UA, "Accept": "application/link-format"},
            )
            if tm.status_code >= 400 or not tm.text.strip():
                continue
            # archive.today returns HTML (not link-format) when rate-limited.
            if not tm.text.lstrip().startswith("<"):
                pass  # link-format response
            else:
                # First char was <  — could be either HTML or link-format
                # (link-format starts with "<https://..."). Quick check:
                if "<!DOCTYPE" in tm.text[:200] or "<html" in tm.text[:200]:
                    # Rate-limited or error HTML
                    continue
            # Parse memento URLs. archive.today uses rel="memento", "first memento",
            # and "last memento". Accept any of them.
            import re
            mementos = re.findall(
                r'<(https?://[^>]+)>;\s*rel="(?:first |last )?memento";\s*datetime="([^"]+)"',
                tm.text,
            )
            if not mementos:
                continue
            # Sort by datetime, pick the most recent.
            from email.utils import parsedate_to_datetime
            def _parsed(d):
                try:
                    return parsedate_to_datetime(d)
                except Exception:
                    return None
            sorted_mems = sorted(
                ((u, _parsed(d)) for u, d in mementos if _parsed(d)),
                key=lambda x: x[1],
                reverse=True,
            )
            if not sorted_mems:
                continue
            snapshot_url = sorted_mems[0][0]
            # Fetch the actual snapshot HTML.
            resp = httpx.get(
                snapshot_url, timeout=timeout, follow_redirects=True,
                headers={"User-Agent": _BROWSER_UA},
            )
            resp.raise_for_status()
            return resp.text
        except Exception as exc:
            logger.debug("archive_today_fetch failed for %s via %s: %s", url, host, exc)
            continue
    return None


def archive_today_strip_chrome(html: str) -> str:
    """Best-effort removal of archive.today's wrapper toolbar so downstream
    HTML parsing sees only the article body. archive.today injects:
      - A toolbar <div id="HEADER"> at the top
      - <script> blocks for analytics
    We strip the most invasive elements; remaining nav stays harmless for
    text extraction.
    """
    if not html:
        return html
    import re
    # Remove archive.today's header bar (typical id values)
    html = re.sub(
        r'<div id="(HEADER|SHARE_LONGLINK|TOOLBAR)".*?</div>',
        '', html, flags=re.DOTALL | re.IGNORECASE,
    )
    return html


def try_archive_fallbacks(url: str) -> tuple[Optional[str], Optional[str]]:
    """Try web archive snapshots in priority order. Returns (html, source).

    source is one of:
      - "wayback"  — snapshot from Wayback Machine
      - "archive_today" — snapshot from archive.today / archive.ph
      - None — both failed
    """
    if not url:
        return None, None
    raw = wayback_fetch(url)
    if raw and len(raw) > 500:
        return wayback_strip_toolbar(raw), "wayback"
    raw = archive_today_fetch(url)
    if raw and len(raw) > 500:
        return archive_today_strip_chrome(raw), "archive_today"
    return None, None


def wayback_strip_toolbar(html: str) -> str:
    """Best-effort removal of Wayback Machine's wrapping markup so downstream
    HTML parsing sees only the original article content.

    Wayback injects:
      - <!-- BEGIN WAYBACK TOOLBAR INSERT --> ... <!-- END WAYBACK TOOLBAR INSERT -->
      - rewrites links to /web/<timestamp>/<original_url>
    We only strip the toolbar comment block here; link rewriting is harmless
    for text extraction.
    """
    if not html:
        return html
    import re
    return re.sub(
        r"<!--\s*BEGIN WAYBACK TOOLBAR INSERT\s*-->.*?"
        r"<!--\s*END WAYBACK TOOLBAR INSERT\s*-->",
        "", html, flags=re.DOTALL | re.IGNORECASE,
    )
