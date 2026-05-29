"""Auto-discover URLs for manual monitor placeholders and convert them to
webpage monitors.

Phase 2 scope: candidate / opponent campaign websites.
Phase 3 scope: + state/county election boards, city council & county
commission meeting-agenda pages.

Flow per monitor:
  1. Build a search query and a per-kind affinity scorer from the monitor
     name + campaign context.
  2. Call get_search_provider() → top N results.
  3. Filter results through a domain blocklist (wikipedia, social platforms,
     ballotpedia, FEC, news databases, archive.org).
  4. Rank surviving candidates by the per-kind affinity scorer so the most
     plausible URLs appear at the top of the prompt to the LLM judge.
  5. Ask get_judge_provider() to pick the most likely correct URL from the
     surviving candidates (or "none" if no good match).
  6. HTTP 200 + HTML content-type check on the chosen URL.
  7. On success: flip monitor_type='manual' → 'webpage', set monitor.url,
     stamp last_checked_at.  On failure: just stamp last_checked_at so the
     same monitor doesn't get retried within RETRY_COOLDOWN_HOURS.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Optional
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.models import CampaignConfig, Opponent, SourceMonitor

logger = logging.getLogger(__name__)


# How long to wait before retrying a monitor whose last discovery attempt
# failed.
RETRY_COOLDOWN_HOURS = 24

# Search results to fetch before domain filtering.
SEARCH_LIMIT = 10

# Maximum post-filter candidates passed to the LLM judge.
LLM_CANDIDATE_LIMIT = 5

# Hosts (and parent domains) that are never the target of any of the
# monitor types we discover. Conservative — only encyclopedias, FEC/
# databases, social platforms, and archive.org. News domains are not
# blocked because the LLM judge can reject them per-kind.
_DOMAIN_BLOCKLIST = {
    # Encyclopedias / databases
    "wikipedia.org", "en.wikipedia.org",
    "ballotpedia.org",
    "votesmart.org",
    "fec.gov",
    "opensecrets.org",
    "followthemoney.org",
    # Social platforms (handled by twitter_profile / bluesky_profile types)
    "facebook.com", "m.facebook.com",
    "twitter.com", "x.com", "mobile.twitter.com",
    "instagram.com",
    "threads.net",
    "linkedin.com",
    "tiktok.com",
    "youtube.com", "m.youtube.com",
    "reddit.com",
    "bsky.app",
    # Archives
    "archive.org", "web.archive.org",
}


# US state code → full name. Used to expand district codes like "PA-08"
# into queries like "Pennsylvania state board of elections".
_STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}


# ── Low-level helpers ────────────────────────────────────────────────────────

def _host_of(url: str) -> str:
    """Return the host portion of a URL, stripped of www., lowercase."""
    try:
        host = urlparse(url).netloc.lower()
        return host.removeprefix("www.")
    except Exception:
        return ""


def _is_blocked_host(host: str) -> bool:
    """True if host or any parent suffix is in the blocklist."""
    if not host:
        return True
    parts = host.split(".")
    for i in range(len(parts)):
        candidate = ".".join(parts[i:])
        if candidate in _DOMAIN_BLOCKLIST:
            return True
    return False


def _last_name(person: str) -> str:
    """Crude last-name extraction. 'Paige Cognetti' → 'cognetti'."""
    tokens = [t for t in re.split(r"\s+", person.strip()) if t]
    return tokens[-1].lower() if tokens else ""


def _http_check(url: str, timeout: int = 8) -> bool:
    """Return True if the URL points to a real webpage.

    Liveness probe, not a strict content validator. The downstream crawler
    (trafilatura with sitemap fallback + retry logic) does the real work; we
    just need to filter out 404s, broken DNS, and non-HTML responses.

    Accepted:
      • 2xx with HTML or HTML-ish content-type
      • 403 / 429 / 503 with HTML content-type — common on government and
        municipal sites with bot protection (Cloudflare, DataDome). The
        page IS real, our simple GET is just being shaped. Trafilatura's
        fetch path handles these.

    Rejected:
      • 404, 410, 5xx other than 503
      • Non-HTML content-types (PDFs, JSON, plain text without HTML body)
      • Connection failures
    """
    if not url:
        return False
    try:
        import requests as _req
        r = _req.get(
            url, timeout=timeout, allow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; CampaignBot/1.0)"},
        )
        status = r.status_code
        ct = (r.headers.get("content-type") or "").lower()
        is_html = (
            "html" in ct
            or "text/plain" in ct
            or "<html" in r.text[:200].lower()
        )
        # 2xx with HTML → accept outright
        if 200 <= status < 300 and is_html:
            return True
        # Bot-protected responses on real pages — accept if content-type
        # signals HTML.  These domains (cloudflare-protected .gov sites,
        # DataDome-fronted municipal sites) consistently serve HTML
        # challenge pages, which is still a signal the URL is real.
        if status in (403, 429, 503) and "html" in ct:
            logger.debug(
                "monitor_url_discovery: HTTP check accepting %s (status=%d, bot-protected but HTML)",
                url, status,
            )
            return True
        return False
    except Exception as exc:
        logger.debug("monitor_url_discovery: HTTP check failed for %s: %s", url, exc)
        return False


def _state_name(state_code: str) -> str:
    """'PA' → 'Pennsylvania'. Returns the input unchanged if not in the map."""
    return _STATE_NAMES.get((state_code or "").upper(), state_code or "")


def _primary_city(location: str) -> str:
    """Extract the primary city from a location string.

    Examples:
      'Scranton/Wilkes-Barre, PA-08' → 'Scranton'
      'Akron, OH-13'                 → 'Akron'
      'PA-08'                        → ''
    """
    if not location:
        return ""
    head = location.split(",")[0].strip()
    head = head.split("/")[0].strip()
    # If head is itself a district code (no real city), return empty.
    if re.match(r"^[A-Z]{2}[- ]\d+", head):
        return ""
    return head


# ── Affinity scoring (one per monitor kind) ──────────────────────────────────
#
# Affinity scorers take a `host` (string, www-stripped) and return an integer.
# Higher = more likely to be the correct URL for that monitor kind. Used to
# order candidates so the LLM judge sees the strongest options first.

def _affinity_campaign_website(host: str, person: str) -> int:
    score = 0
    last = _last_name(person)
    if last and last in host:
        score += 5
    flat = host.replace("-", "").replace("_", "")
    for hint in ("forcongress", "forsenate", "forgovernor", "forstate", "forhouse"):
        if hint in flat:
            score += 3
    if host.count(".") == 1:
        score += 1
    return score


def _affinity_state_election_board(host: str, state_code: str, state_name: str) -> int:
    score = 0
    if host.endswith(".gov") or ".gov." in host:
        score += 6
    if host.endswith(".us") or f".{state_code.lower()}.us" in host:
        score += 5
    # State codes/names embedded in hostname (e.g. "pa.gov", "elections.ohio.gov")
    sl = state_code.lower()
    if sl and (f".{sl}." in host or host.startswith(f"{sl}.") or host.endswith(f".{sl}")):
        score += 3
    if state_name and state_name.lower().replace(" ", "") in host.replace("-", "").replace("_", ""):
        score += 3
    for kw in ("vote", "election", "elections", "dos", "sos", "stateboard"):
        if kw in host:
            score += 2
    return score


def _affinity_county_election_board(host: str, city: str, state_code: str) -> int:
    score = 0
    if host.endswith(".gov") or ".gov." in host:
        score += 6
    sl = state_code.lower()
    if sl and (f".{sl}.us" in host or host.endswith(f".{sl}.us")):
        score += 4
    if host.endswith(".us"):
        score += 2
    if city and city.lower().replace(" ", "") in host.replace("-", "").replace("_", ""):
        score += 3
    for kw in ("election", "elections", "vote", "voter"):
        if kw in host:
            score += 2
    if "county" in host:
        score += 1
    return score


def _affinity_council_agenda(host: str, city: str, state_code: str) -> int:
    score = 0
    if host.endswith(".gov") or ".gov." in host:
        score += 5
    sl = state_code.lower()
    if sl and (f".{sl}.us" in host or host.endswith(f".{sl}.us")):
        score += 3
    if host.endswith(".us") or host.endswith(".org"):
        score += 1
    if city and city.lower().replace(" ", "") in host.replace("-", "").replace("_", ""):
        score += 3
    # Civic-meeting hosting platforms — strong signal these are agenda pages.
    for platform in ("granicus.com", "civicclerk.com", "civicplus.com",
                     "primegov.com", "legistar.com", "iqm2.com",
                     "boarddocs.com", "meetingbox.com"):
        if platform in host:
            score += 3
    if "council" in host:
        score += 1
    return score


def _affinity_commission_agenda(host: str, city: str, state_code: str) -> int:
    score = _affinity_council_agenda(host, city, state_code)
    if "commission" in host or "commissioners" in host:
        score += 1
    return score


# ── Candidate filtering (generic) ────────────────────────────────────────────

@dataclass
class _Candidate:
    url: str
    host: str
    title: str
    snippet: str
    affinity: int


def _filter_candidates(
    results,
    affinity_fn: Callable[[str], int],
    max_keep: int = LLM_CANDIDATE_LIMIT,
) -> list[_Candidate]:
    """Drop blocked hosts, dedupe by host, rank by `affinity_fn(host)`,
    cap to max_keep."""
    seen_hosts: set[str] = set()
    candidates: list[_Candidate] = []
    for r in results:
        url = (r.url or "").strip()
        if not url:
            continue
        host = _host_of(url)
        if not host or _is_blocked_host(host) or host in seen_hosts:
            continue
        seen_hosts.add(host)
        candidates.append(_Candidate(
            url=url,
            host=host,
            title=(r.title or "").strip(),
            snippet=(getattr(r, "snippet", "") or "").strip(),
            affinity=affinity_fn(host),
        ))
    candidates.sort(key=lambda c: c.affinity, reverse=True)
    return candidates[:max_keep]


# ── LLM picker (generic — caller supplies the prompt) ────────────────────────

def _llm_pick_url(
    framing: str,
    candidates: list[_Candidate],
) -> tuple[Optional[str], str]:
    """Ask the judge LLM to pick the correct URL from a list of candidates.

    `framing` is the leading natural-language description of what we're
    looking for (e.g. "Find the official campaign website for Paige
    Cognetti, candidate for US House PA-08"). This function appends the
    numbered candidate list and the answer-format instructions itself, so
    callers should NOT include candidate text in `framing`.

    Returns (chosen_url, reason). Reason is for logging.
    """
    if not candidates:
        return None, "no candidates after domain filter"

    from app.services.llm_provider import get_judge_provider, MockLLMProvider

    provider = get_judge_provider()
    if isinstance(provider, MockLLMProvider):
        top = candidates[0]
        return top.url, f"mock provider; picked top affinity ({top.host})"

    lines = []
    for i, c in enumerate(candidates, start=1):
        lines.append(
            f"{i}. {c.url}\n"
            f"   title: {c.title[:160]}\n"
            f"   snippet: {c.snippet[:240]}"
        )
    options = "\n".join(lines)

    prompt = (
        f"You are verifying URLs for a political-campaign monitoring system.\n\n"
        f"{framing}\n\n"
        f"Candidates:\n{options}\n\n"
        f"Reply with ONLY the number of the option that BEST matches what we "
        f"are looking for. If none of the options is a correct match, reply "
        f"with exactly: 0"
    )

    try:
        raw = (provider.complete(prompt) or "").strip()
    except Exception as exc:
        logger.warning("monitor_url_discovery: judge LLM failed: %s", exc)
        return None, f"judge LLM error: {exc}"

    m = re.search(r"\d+", raw)
    if not m:
        return None, f"unparseable judge response: {raw[:80]!r}"
    idx = int(m.group(0))
    if idx == 0:
        return None, f"judge rejected all candidates (reply: {raw[:40]!r})"
    if 1 <= idx <= len(candidates):
        picked = candidates[idx - 1]
        return picked.url, f"judge picked #{idx} ({picked.host})"
    return None, f"judge index out of range: {idx}"


# ── Discovery functions (one per monitor kind) ───────────────────────────────

def _get_search_provider_or_mock():
    """Return (provider, is_mock).  Centralized so each discovery function
    handles the mock case identically."""
    from app.services.search_provider import get_search_provider, MockSearchProvider
    provider = get_search_provider()
    return provider, isinstance(provider, MockSearchProvider)


def _do_search(query: str) -> tuple[list, Optional[str]]:
    """Run a search and return (results, error). On error returns ([], err)."""
    provider, is_mock = _get_search_provider_or_mock()
    if is_mock:
        return [], "search provider is mock"
    try:
        response = provider.search(query, limit=SEARCH_LIMIT)
        return list(response.results), None
    except Exception as exc:
        return [], f"search error: {exc}"


def discover_campaign_website(
    person_name: str,
    geo_hint: str = "",
    role_hint: str = "",
) -> tuple[Optional[str], str]:
    """Try to discover the official campaign website for a person."""
    query = f'"{person_name}" campaign website {geo_hint}'.strip()
    results, err = _do_search(query)
    if err:
        return None, err
    candidates = _filter_candidates(
        results, lambda h: _affinity_campaign_website(h, person_name),
    )
    if not candidates:
        return None, f"no candidates after filter (query: {query!r})"
    framing = (
        f"Person: {person_name}\n"
        f"Role: {role_hint or 'political candidate'}\n\n"
        f"We are looking for this person's OFFICIAL campaign website — a site "
        f"owned and controlled by the campaign, typically a custom domain "
        f"containing the candidate's name or a slogan like 'forcongress'. "
        f"News articles, Wikipedia, donor databases, and government pages "
        f"are NOT campaign websites."
    )
    url, judge_reason = _llm_pick_url(framing, candidates)
    if not url:
        return None, judge_reason
    if not _http_check(url):
        return None, f"http check failed for {url}"
    return url, f"discovered: {url} ({judge_reason})"


def discover_state_election_board(
    state_code: str,
) -> tuple[Optional[str], str]:
    """Try to discover the official state election-board / Department of
    State elections page for a state."""
    if not state_code:
        return None, "no state_code provided"
    state_name = _state_name(state_code)
    query = f"{state_name} state board of elections department of state vote"
    results, err = _do_search(query)
    if err:
        return None, err
    candidates = _filter_candidates(
        results, lambda h: _affinity_state_election_board(h, state_code, state_name),
    )
    if not candidates:
        return None, f"no candidates after filter (query: {query!r})"
    framing = (
        f"State: {state_name} ({state_code})\n\n"
        f"We are looking for the official STATE government page for "
        f"{state_name}'s elections — typically run by the Secretary of "
        f"State or Department of State. This page should host voter "
        f"information, election dates, ballot rules, and candidate lists "
        f"for the entire state. It must be a .gov or .{state_code.lower()}.us "
        f"government site (NOT a campaign site, news article, or partisan "
        f"organization)."
    )
    url, judge_reason = _llm_pick_url(framing, candidates)
    if not url:
        return None, judge_reason
    if not _http_check(url):
        return None, f"http check failed for {url}"
    return url, f"discovered: {url} ({judge_reason})"


def discover_county_election_board(
    city: str,
    state_code: str,
) -> tuple[Optional[str], str]:
    """Try to discover the official county election-board page using the
    primary city as a search anchor. The search engine usually resolves
    'Scranton county board of elections' → Lackawanna County (the county
    containing Scranton), so we don't need an explicit city→county map."""
    if not city:
        return None, "no city provided (need primary_city to anchor search)"
    state_name = _state_name(state_code)
    query = f"{city} {state_name} county board of elections office"
    results, err = _do_search(query)
    if err:
        return None, err
    candidates = _filter_candidates(
        results, lambda h: _affinity_county_election_board(h, city, state_code),
    )
    if not candidates:
        return None, f"no candidates after filter (query: {query!r})"
    framing = (
        f"Location: {city}, {state_name}\n\n"
        f"We are looking for the official COUNTY government elections "
        f"office page for the county containing {city}, {state_name}. This "
        f"should be a .gov or .{state_code.lower()}.us page run by the "
        f"county elections office or board — NOT a city page, not the "
        f"state page, and not a partisan or news source."
    )
    url, judge_reason = _llm_pick_url(framing, candidates)
    if not url:
        return None, judge_reason
    if not _http_check(url):
        return None, f"http check failed for {url}"
    return url, f"discovered: {url} ({judge_reason})"


def discover_city_council_agenda(
    city: str,
    state_code: str,
) -> tuple[Optional[str], str]:
    """Try to discover the city council meeting/agenda page."""
    if not city:
        return None, "no city provided"
    state_name = _state_name(state_code)
    query = f"{city} {state_name} city council meetings agenda"
    results, err = _do_search(query)
    if err:
        return None, err
    candidates = _filter_candidates(
        results, lambda h: _affinity_council_agenda(h, city, state_code),
    )
    if not candidates:
        return None, f"no candidates after filter (query: {query!r})"
    framing = (
        f"Location: {city}, {state_name}\n\n"
        f"We are looking for the official {city} CITY COUNCIL agendas or "
        f"meetings page. This should be a .gov / .us / civic-platform page "
        f"(granicus, civicclerk, civicplus, primegov, legistar) hosting "
        f"the council's upcoming meetings, agenda PDFs, or minutes. NOT a "
        f"county page, not a state page, and not a news article about the "
        f"council."
    )
    url, judge_reason = _llm_pick_url(framing, candidates)
    if not url:
        return None, judge_reason
    if not _http_check(url):
        return None, f"http check failed for {url}"
    return url, f"discovered: {url} ({judge_reason})"


def discover_county_commission_agenda(
    city: str,
    state_code: str,
) -> tuple[Optional[str], str]:
    """Try to discover the county commission/commissioners meeting page,
    anchored by the primary city in the district."""
    if not city:
        return None, "no city provided (need primary_city to anchor search)"
    state_name = _state_name(state_code)
    query = f"{city} {state_name} county commissioners meetings agenda"
    results, err = _do_search(query)
    if err:
        return None, err
    candidates = _filter_candidates(
        results, lambda h: _affinity_commission_agenda(h, city, state_code),
    )
    if not candidates:
        return None, f"no candidates after filter (query: {query!r})"
    framing = (
        f"Location: county containing {city}, {state_name}\n\n"
        f"We are looking for the official COUNTY COMMISSION or county "
        f"commissioners meetings/agendas page for the county containing "
        f"{city}, {state_name}. This should be a .gov / .us / civic-"
        f"platform page (granicus, civicclerk, civicplus, primegov, "
        f"legistar) hosting the commission's upcoming meetings, agenda "
        f"PDFs, or minutes. NOT a city council page, not a state page, "
        f"and not a news article."
    )
    url, judge_reason = _llm_pick_url(framing, candidates)
    if not url:
        return None, judge_reason
    if not _http_check(url):
        return None, f"http check failed for {url}"
    return url, f"discovered: {url} ({judge_reason})"


# ── Person-name parsing for website monitors ─────────────────────────────────

def _person_from_website_monitor(name: str) -> Optional[str]:
    """Extract the person's name from a 'X campaign website check' monitor."""
    suffix = " campaign website check"
    if not name.endswith(suffix):
        return None
    person = name[: -len(suffix)].strip()
    return person or None


def _role_hint_for_monitor(
    monitor: SourceMonitor,
    campaign: Optional[CampaignConfig],
    opponents: list[Opponent],
) -> str:
    """Build a role-hint string for the LLM judge."""
    parts: list[str] = []
    if monitor.category == "candidate" and campaign:
        if campaign.office:
            parts.append(f"candidate for {campaign.office}")
        if campaign.district:
            parts.append(campaign.district)
    elif monitor.category == "opponent":
        person = _person_from_website_monitor(monitor.name)
        opp = next((o for o in opponents if (o.name or "").lower() == (person or "").lower()), None)
        if opp:
            parts.append("opponent in race")
            if campaign and campaign.district:
                parts.append(campaign.district)
    return ", ".join(parts)


def _geo_hint(campaign: Optional[CampaignConfig]) -> str:
    if not campaign:
        return ""
    return (campaign.district or campaign.location or "").strip()


def _state_code_from_campaign(campaign: Optional[CampaignConfig]) -> str:
    """Extract a state code from the campaign's district or location.

    'PA-08' → 'PA'. 'Scranton/Wilkes-Barre, PA-08' → 'PA'.
    """
    if not campaign:
        return ""
    for field in (campaign.district, campaign.location):
        if not field:
            continue
        m = re.search(r"\b([A-Z]{2})(?:-\d+)?\b", field)
        if m:
            return m.group(1)
    return ""


# ── Monitor-kind dispatch ────────────────────────────────────────────────────

def _classify_manual_monitor(name: str) -> Optional[str]:
    """Return a tag for the kind of manual monitor based on its name, or
    None if the monitor isn't one of the kinds we can auto-discover.

    Tags:
      'campaign_website'         — '<Person> campaign website check'
      'state_election_board'     — 'State election board check'
      'county_election_board'    — 'County election board check'
      'city_council_agenda'      — 'City council agenda check'
      'county_commission_agenda' — 'County commission agenda check'
    """
    n = (name or "").strip()
    if n.endswith(" campaign website check"):
        return "campaign_website"
    nl = n.lower()
    if nl == "state election board check":
        return "state_election_board"
    if nl == "county election board check":
        return "county_election_board"
    if nl == "city council agenda check":
        return "city_council_agenda"
    if nl == "county commission agenda check":
        return "county_commission_agenda"
    return None


def _discover_for_monitor(
    monitor: SourceMonitor,
    campaign: Optional[CampaignConfig],
    opponents: list[Opponent],
) -> tuple[Optional[str], str, Optional[str]]:
    """Dispatch a single monitor to its discovery function.

    Returns (url, reason, kind). kind is None for unrecognized monitors
    (caller should leave them alone).
    """
    kind = _classify_manual_monitor(monitor.name)
    if kind is None:
        return None, "monitor name does not match any known discovery kind", None

    state_code = _state_code_from_campaign(campaign)
    city = _primary_city(campaign.location if campaign else "")

    if kind == "campaign_website":
        person = _person_from_website_monitor(monitor.name)
        if not person:
            return None, "could not parse person name from monitor name", kind
        role = _role_hint_for_monitor(monitor, campaign, opponents)
        url, reason = discover_campaign_website(person, _geo_hint(campaign), role)
        return url, reason, kind

    if kind == "state_election_board":
        url, reason = discover_state_election_board(state_code)
        return url, reason, kind

    if kind == "county_election_board":
        url, reason = discover_county_election_board(city, state_code)
        return url, reason, kind

    if kind == "city_council_agenda":
        url, reason = discover_city_council_agenda(city, state_code)
        return url, reason, kind

    if kind == "county_commission_agenda":
        url, reason = discover_county_commission_agenda(city, state_code)
        return url, reason, kind

    return None, f"unknown kind: {kind}", kind


# ── Top-level orchestrator ───────────────────────────────────────────────────

def convert_manuals_to_webpages(db: Session) -> dict:
    """Iterate active manual monitors of recognized kinds and try to
    auto-discover URLs, converting successful ones to webpage monitors.

    Recognized kinds:
      • '<Person> campaign website check'
      • 'State election board check'
      • 'County election board check'
      • 'City council agenda check'
      • 'County commission agenda check'

    Manual monitors that don't match any recognized kind are left alone.

    Honors RETRY_COOLDOWN_HOURS: monitors whose last attempt was within
    the cooldown window are skipped this run.

    Idempotent: webpage-typed monitors are not eligible.

    Returns a summary dict with counts and per-monitor outcomes.
    """
    campaign = db.query(CampaignConfig).first()
    opponents = db.query(Opponent).all()

    eligible = (
        db.query(SourceMonitor)
        .filter(
            SourceMonitor.monitor_type == "manual",
            SourceMonitor.active == True,  # noqa: E712
        )
        .all()
    )

    # Only keep monitors whose names match a recognized discovery kind.
    eligible = [m for m in eligible if _classify_manual_monitor(m.name) is not None]

    cooldown = datetime.utcnow() - timedelta(hours=RETRY_COOLDOWN_HOURS)

    converted: list[dict] = []
    skipped_cooldown: list[dict] = []
    failed: list[dict] = []

    for monitor in eligible:
        if monitor.last_checked_at and monitor.last_checked_at > cooldown:
            skipped_cooldown.append({
                "monitor_id": monitor.id,
                "name": monitor.name,
                "last_checked_at": monitor.last_checked_at.isoformat() if monitor.last_checked_at else None,
            })
            continue

        url, reason, kind = _discover_for_monitor(monitor, campaign, opponents)

        # Always stamp last_checked_at so we don't immediately retry.
        monitor.last_checked_at = datetime.utcnow()

        if url:
            monitor.url = url
            monitor.monitor_type = "webpage"
            monitor.updated_at = datetime.utcnow()
            converted.append({
                "monitor_id": monitor.id,
                "name": monitor.name,
                "kind": kind,
                "url": url,
                "reason": reason,
            })
            logger.info(
                "monitor_url_discovery: converted id=%d (%s, kind=%s) → %s",
                monitor.id, monitor.name, kind, url,
            )
        else:
            failed.append({
                "monitor_id": monitor.id,
                "name": monitor.name,
                "kind": kind,
                "reason": reason,
            })
            logger.info(
                "monitor_url_discovery: failed id=%d (%s, kind=%s) — %s",
                monitor.id, monitor.name, kind, reason,
            )

    db.commit()

    return {
        "eligible": len(eligible),
        "converted": len(converted),
        "failed": len(failed),
        "skipped_cooldown": len(skipped_cooldown),
        "details": {
            "converted": converted,
            "failed": failed,
            "skipped_cooldown": skipped_cooldown,
        },
    }


# Backward-compat alias — existing callers (admin route, monitors.py setup
# hook) import this name. The orchestrator is now broader in scope (all
# manual kinds, not just websites), so the name is slightly outdated, but
# keeping it avoids unnecessary churn in callers.
convert_website_manuals_to_webpages = convert_manuals_to_webpages
