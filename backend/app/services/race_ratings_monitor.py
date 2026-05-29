"""Forecaster-rating connectors for race sentiment.

Three House-race rating outlets are wired up:

  • Cook Political Report             — via 270toWin (mirror)
  • Sabato's Crystal Ball             — via 270toWin (mirror)
  • Inside Elections                  — direct (httpx)

Decision Desk HQ was dropped 2026-05-29 — they do not publish a public
2026 House ratings table; the homepage 200s but no /ratings/* path
resolves, suggesting subscription-gated data.

Reality check (re-validated 2026-05-29 with a headless-Chromium probe
and follow-up sourcing investigation):

  • Cook and Sabato sit behind Cloudflare's IUAM / Turnstile when
    accessed directly. Plain HTTP (httpx, cloudscraper) returns 403,
    AND a headless Chromium with playwright-stealth still hits the
    "Just a moment..." challenge page. Direct bypass would need a paid
    service (ScraperAPI / ZenRows / Bright Data) or Cook's paid API.
  • Workaround: 270toWin (https://www.270towin.com) mirrors both Cook
    and Sabato ratings in machine-readable form (a JSON blob embedded
    in each per-source page, with a one-character `map_code` per
    district). 270toWin is NOT Cloudflare-blocked at the HTTP layer
    and ratings sync within ~24h of the source. Validated 2026-05-29:
    270toWin's Cook PA-08 = "Toss-up" matches the actual Cook page;
    270toWin's Sabato PA-08 = "Lean R" matches the actual Sabato page.
  • Inside Elections returns 200 to a plain httpx GET with a real-
    browser User-Agent — direct scrape works, no proxy needed. (Note:
    270toWin's Inside Elections data was stale during validation, so
    we use the direct source for IE.)

`_get_html()` still detects Cloudflare's IUAM challenge response so
that if 270toWin ever flips on CF protection, the sync layer will
record the row's `last_sync_error` cleanly instead of returning
parser garbage.
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
# 270toWin shared decoder (used by Cook + Sabato)
# ─────────────────────────────────────────────────────────────────────────────
#
# 270toWin's per-source ratings page embeds a JSON blob with one
# `map_code` per district. The codes are single hex-ish characters that
# map to rating tiers via the page's color-palette legend:
#
#     palette-label-1 = Safe   → palette_d4 / color_value "1"
#     palette-label-3 = Likely → palette_d3 / color_value "3"
#     palette-label-2 = Leans  → palette_d2 / color_value "5"
#     palette-label-4 = Tilt   → palette_d1 / color_value "a"
#     tossup          = Toss-up → palette_t  / color_value "0"
#     (R-side mirrors: 2/4/6/b)
#
# Favors here assumes a Democratic-side campaign (PA-08). A future SaaS
# multi-tenant pivot would invert the map for Republican-side campaigns.

_270TOWIN_COLOR_TO_RATING: dict[str, tuple[str, str]] = {
    "1": ("Solid D",  "candidate"),
    "3": ("Likely D", "candidate"),
    "5": ("Lean D",   "candidate"),
    "a": ("Tilt D",   "candidate"),
    "0": ("Toss-up",  "tossup"),
    "2": ("Solid R",  "opponent"),
    "4": ("Likely R", "opponent"),
    "6": ("Lean R",   "opponent"),
    "b": ("Tilt R",   "opponent"),
}


def _parse_270towin_map_code(html: str, state: str, district: str) -> Optional[str]:
    """Pull the single-character `map_code` for (state, district) out of a
    270toWin ratings page's embedded JSON blob.

    The relevant fragment in the page body looks like:

        "district_number":8,"state_fips_code":"42","state_abbr":"PA",
        "state_name":"Pennsylvania", … "map_code":"0"

    We match on district + state abbreviation (not FIPS) because the
    state→FIPS map is something we'd otherwise need to hardcode.
    `re.DOTALL` is required since the per-district record contains
    nested JSON with line breaks in some fields.
    """
    pattern = re.compile(
        rf'"district_number":{re.escape(district)},'
        rf'"state_fips_code":"\d+","state_abbr":"{re.escape(state)}"'
        rf'.*?"map_code":"([^"]+)"',
        re.DOTALL,
    )
    m = pattern.search(html)
    return m.group(1) if m else None


def _270towin_fetch(
    external_id: str, metadata: dict, source_label: str,
) -> Optional[FetchedSample]:
    """Fetch a forecaster rating via 270toWin's mirror page.

    `external_id` is the 270toWin URL for the source (Cook or Sabato).
    `metadata` carries `district_label` (e.g. "PA-08"). Returns None if
    the district isn't present on the page or the map_code is unknown
    (e.g. a new tier code 270toWin starts using).
    """
    url = external_id
    district = (metadata.get("district_label") or "").upper().strip()
    m = re.match(r"^([A-Z]{2})-?(\d+)$", district)
    if not m:
        log.warning("_270towin_fetch (%s): cannot parse district %r",
                    source_label, district)
        return None
    state = m.group(1)
    dist_num = str(int(m.group(2)))  # "08" → "8" to match 270toWin's column format

    html = _get_html(url)
    color = _parse_270towin_map_code(html, state, dist_num)
    if color is None:
        log.warning(
            "_270towin_fetch (%s): no row for %s-%s on %s",
            source_label, state, dist_num, url,
        )
        return None

    decoded = _270TOWIN_COLOR_TO_RATING.get(color)
    if decoded is None:
        log.warning(
            "_270towin_fetch (%s): unknown map_code %r for %s-%s",
            source_label, color, state, dist_num,
        )
        return None

    label, favors = decoded
    band = RATING_BANDS.get(label, (None, None))
    return FetchedSample(
        source_type="rating",
        rating_label=label,
        rating_min_pct=band[0],
        rating_max_pct=band[1],
        favors=favors,
        raw_response={"source": source_label, "district": district, "map_code": color},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Public fetchers: Cook + Sabato (both via 270toWin)
# ─────────────────────────────────────────────────────────────────────────────

def cook_fetch(external_id: str, metadata: dict) -> Optional[FetchedSample]:
    """Cook Political Report rating, sourced via 270toWin.

    cookpolitical.com is Cloudflare-blocked to direct HTTP scraping;
    270toWin mirrors Cook's per-district ratings with same-day accuracy.
    `external_id` should be the 270toWin URL, not the cookpolitical.com URL.
    """
    return _270towin_fetch(external_id, metadata, "cook")


def sabato_fetch(external_id: str, metadata: dict) -> Optional[FetchedSample]:
    """Sabato's Crystal Ball rating, sourced via 270toWin.

    centerforpolitics.org is Cloudflare-blocked to direct HTTP scraping;
    270toWin mirrors Sabato's per-district ratings with same-day accuracy.
    `external_id` should be the 270toWin URL, not the centerforpolitics.org URL.
    """
    return _270towin_fetch(external_id, metadata, "sabato")


# ─────────────────────────────────────────────────────────────────────────────
# Public fetcher: Inside Elections
# ─────────────────────────────────────────────────────────────────────────────

def inside_elections_fetch(external_id: str, metadata: dict) -> Optional[FetchedSample]:
    """Scrape Inside Elections' /ratings/house page for the configured district.

    `external_id` is the IE ratings URL.
    `metadata` carries `district_label` (e.g. "PA-08"). The page lists
    districts as separate state/number columns ("PA" / "8"), so we split
    the label before searching.

    Returns a FetchedSample with rating_label + band + favors. Raises
    CloudflareBlockedError if IE ever flips on Cloudflare's IUAM.
    """
    url = external_id
    district = (metadata.get("district_label") or "").upper().strip()
    m = re.match(r"^([A-Z]{2})-?(\d+)$", district)
    if not m:
        log.warning("inside_elections_fetch: cannot parse district %r", district)
        return None
    state = m.group(1)
    dist_num = str(int(m.group(2)))  # "08" → "8" to match IE's column format

    html = _get_html(url)
    soup = BeautifulSoup(html, "html.parser")
    rating = _parse_inside_elections_district(soup, state, dist_num)
    if rating is None:
        log.warning(
            "inside_elections_fetch: no row for %s-%s on %s", state, dist_num, url,
        )
        return None

    normalized = _normalize_rating_label(rating)
    band = RATING_BANDS.get(normalized, (None, None))
    favors = _favors_from_label(normalized)

    return FetchedSample(
        source_type="rating",
        rating_label=normalized,
        rating_min_pct=band[0],
        rating_max_pct=band[1],
        favors=favors,
        raw_response={"district": district, "rating_text": rating},
    )


def _parse_inside_elections_district(
    soup: BeautifulSoup, state: str, district: str,
) -> Optional[str]:
    """Find the rating tier for (state, district) on IE's /ratings/house page.

    Structure (validated 2026-05-29): the page groups districts by tier.
    Each tier opens with `<h3 class="rating lean-republican">Lean Republican</h3>`
    followed by a `<table class="ratings ...">` with one row per district:

        <tr>
          <td class="state">PA</td>
          <td class="district">8</td>
          <td class="party R"><span>R</span></td>
          <td class="notes"></td>
          <td class="incumbent">Bresnahan</td>
          <td class="shift "></td>
        </tr>

    Walk the document in order, remembering the latest seen h3.rating,
    and return that text when we encounter the matching state+district.
    """
    current_rating: Optional[str] = None
    for el in soup.find_all(["h3", "tr"]):
        if el.name == "h3":
            classes = el.get("class") or []
            if "rating" in classes:
                current_rating = el.get_text(strip=True)
            continue
        # el is a <tr>; check if it's a district row
        td_state = el.find("td", class_="state")
        td_district = el.find("td", class_="district")
        if td_state is None or td_district is None:
            continue
        row_state = td_state.get_text(strip=True).upper()
        row_district = td_district.get_text(strip=True)
        if row_state == state and row_district == district:
            return current_rating
    return None


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

    # Cloudflare's challenge page is served with a 403 or 503 and has the
    # title "Just a moment..." with a `cf_chl_opt` JS payload inline.
    #
    # IMPORTANT: ordinary CF-protected pages (most of the web) inject a
    # passive `/cdn-cgi/challenge-platform/scripts/jsd/main.js` tag for
    # bot-analytics. That tag is NOT a challenge — its presence alone
    # cannot mean "blocked", or every CF-protected 200 OK page falsely
    # registers as blocked. So we look for the *challenge-only* markers
    # (page title + inline challenge JS), not the generic infrastructure
    # marker.
    body = resp.text or ""
    challenge_markers = ("Just a moment...", "cf_chl_opt")
    is_challenge_status = resp.status_code in (403, 503)
    is_challenge_body = any(m in body[:8000] for m in challenge_markers)
    if is_challenge_status and is_challenge_body:
        raise CloudflareBlockedError(
            f"upstream is behind Cloudflare bot challenge ({resp.status_code}). "
            "Manual entry only until a headless-browser fetcher is added."
        )
    # Bare 403/503 without the challenge body — propagate as a normal HTTP error.
    resp.raise_for_status()
    return body


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
