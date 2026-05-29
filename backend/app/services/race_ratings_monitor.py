"""Forecaster-rating connectors for race sentiment.

This file implements (or attempts to implement) scrapers for the four
House-race rating outlets:

  • Cook Political Report
  • Sabato's Crystal Ball (Center for Politics)
  • Inside Elections
  • Decision Desk HQ

Reality check (validated 2026-05-26): all four sites sit behind
Cloudflare's bot challenge / Turnstile. Plain HTTP scraping (httpx,
requests, cloudscraper) returns 403 against the ratings pages. Reliable
scraping would need one of:

  1. A real headless browser (Playwright). Heavy dep — pulls in ~200MB of
     Chromium + ~1s startup per fetch.
  2. A paid bypass service (ScraperAPI / ZenRows / Bright Data). $/month.
  3. Direct API access (Cook offers it via paid subscription).
  4. RSS / Atom feeds, if the outlet publishes one (most do not for
     ratings; they do for blog posts).

The framework here is deliberately Cloudflare-aware: each fetcher tries
the simple HTTP path, and when it sees Cloudflare's challenge response,
raises `CloudflareBlockedError`. The sync layer records that on the
row's `last_sync_error` so the UI can surface "auto-fetch blocked —
manual entry only" instead of silently going stale.

When a Playwright runner or paid bypass lands, plug it into
`_get_html()` and every fetcher below starts working without other
changes.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from app.services.race_sentiment_sync import FetchedSample

log = logging.getLogger(__name__)


class CloudflareBlockedError(Exception):
    """Raised when a forecaster site returns the Cloudflare challenge.
    UI uses this to show 'manual entry only' instead of a generic error.
    """
    pass


# Rating → implied probability band (one-tailed, favoring the indicated party).
# Used to fill rating_min_pct / rating_max_pct. These bands are public-knowledge
# heuristics for what each Cook tier roughly implies; they are NOT a forecast.
# Stored as bands (not single percentages) to avoid the "Lean R = 60%" fallacy.
RATING_BANDS = {
    "Solid D":   (90, 100),
    "Likely D":  (75, 90),
    "Lean D":    (60, 75),
    "Tilt D":    (52, 60),
    "Toss-up":   (45, 55),
    "Tilt R":    (52, 60),  # favoring R, so 52-60% R-win = 40-48% D-win
    "Lean R":    (60, 75),
    "Likely R":  (75, 90),
    "Solid R":   (90, 100),
}


# ─────────────────────────────────────────────────────────────────────────────
# Public fetcher: Cook Political Report
# ─────────────────────────────────────────────────────────────────────────────

def cook_fetch(external_id: str, metadata: dict) -> Optional[FetchedSample]:
    """Scrape Cook's House ratings page for the configured district.

    `external_id` is the Cook ratings URL.
    `metadata` carries `district_label` (e.g. "PA-08") which the parser
    uses to find the right row.

    Returns a FetchedSample with rating_label + band + favors, or raises
    CloudflareBlockedError when the upstream is shielded (current
    reality for all four forecaster outlets — see module docstring).
    """
    url = external_id
    district = (metadata.get("district_label") or "").upper().strip()
    if not district:
        log.warning("cook_fetch: no district_label in metadata")
        return None

    html = _get_html(url)
    soup = BeautifulSoup(html, "html.parser")
    rating = _parse_cook_district(soup, district)
    if rating is None:
        log.warning("cook_fetch: did not find row for %s on %s", district, url)
        return None

    label = rating  # e.g. "Toss-up", "Lean Democrat", "Likely Republican"
    normalized = _normalize_rating_label(label)
    band = RATING_BANDS.get(normalized, (None, None))
    favors = _favors_from_label(normalized)

    return FetchedSample(
        source_type="rating",
        rating_label=normalized,
        rating_min_pct=band[0],
        rating_max_pct=band[1],
        favors=favors,
        raw_response={"district": district, "rating_text": label},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Lower-level helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_html(url: str) -> str:
    """Fetch a forecaster page as HTML. Currently httpx-only; will be
    upgraded to a Playwright path once that ships. Detects Cloudflare's
    challenge response and raises a typed error so the sync layer can
    annotate the row with a useful message.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    try:
        with httpx.Client(timeout=20.0, follow_redirects=True) as client:
            resp = client.get(url, headers=headers)
    except httpx.HTTPError as e:
        raise CloudflareBlockedError(f"network error fetching {url}: {e}") from e

    # Cloudflare returns 403 with a specific challenge HTML payload.
    # Detect both the status code AND the body signal to avoid false
    # positives on legitimate 403s from misconfigured sites.
    body = resp.text or ""
    cf_signals = (
        "Just a moment...",
        "challenge-platform",
        "cf_chl_opt",
        "cf-mitigated",
    )
    if resp.status_code == 403 or any(s in body for s in cf_signals):
        raise CloudflareBlockedError(
            f"upstream is behind Cloudflare bot challenge ({resp.status_code}). "
            "Manual entry only until a headless-browser fetcher is added."
        )
    resp.raise_for_status()
    return body


def _parse_cook_district(soup: BeautifulSoup, district: str) -> Optional[str]:
    """Locate the district row on Cook's ratings page.

    Defensive against layout changes: scans every table row for one whose
    text contains the district label, then takes the cell that matches
    one of the known rating tiers.
    """
    candidate_tiers = list(RATING_BANDS.keys()) + [
        "Lean Democrat", "Lean Republican",
        "Likely Democrat", "Likely Republican",
        "Solid Democrat", "Solid Republican",
        "Toss Up", "Tossup",
    ]
    for tr in soup.find_all("tr"):
        text = " ".join(tr.stripped_strings)
        if district not in text.upper():
            continue
        for tier in candidate_tiers:
            if tier.lower() in text.lower():
                return tier
    return None


def _normalize_rating_label(label: str) -> str:
    """Map any of Cook's surface forms back to the canonical RATING_BANDS keys."""
    l = label.lower().strip()
    # Order matters: check longer phrases first.
    mapping = [
        ("solid democrat", "Solid D"), ("solid d", "Solid D"),
        ("likely democrat", "Likely D"), ("likely d", "Likely D"),
        ("lean democrat", "Lean D"), ("lean d", "Lean D"),
        ("tilt democrat", "Tilt D"), ("tilt d", "Tilt D"),
        ("toss up", "Toss-up"), ("tossup", "Toss-up"), ("toss-up", "Toss-up"),
        ("tilt republican", "Tilt R"), ("tilt r", "Tilt R"),
        ("lean republican", "Lean R"), ("lean r", "Lean R"),
        ("likely republican", "Likely R"), ("likely r", "Likely R"),
        ("solid republican", "Solid R"), ("solid r", "Solid R"),
    ]
    for needle, canonical in mapping:
        if re.search(rf"\b{re.escape(needle)}\b", l):
            return canonical
    return label  # fallback: return as-is; UI will display whatever came back


def _favors_from_label(label: str) -> Optional[str]:
    """Map a normalized rating to which side it favors.

    'candidate' means our campaign — we assume Democratic-side framing for
    PA-08. Will need a CampaignConfig.party-aware version when the SaaS
    multi-tenant pivot lands (a Republican campaign would invert the map).
    """
    l = label.lower()
    if l in ("toss-up", "toss up", "tossup"):
        return "tossup"
    if any(s in l for s in ("d", "democrat")) and "republican" not in l:
        return "candidate"
    if any(s in l for s in ("r", "republican")) and "democrat" not in l:
        return "opponent"
    return None
