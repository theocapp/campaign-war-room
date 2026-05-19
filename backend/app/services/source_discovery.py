"""Generate campaign-specific source monitor suggestions."""
import json
import re
from typing import Any
from urllib.parse import urlencode

from app.models import CampaignConfig, Opponent


# ── Local outlet catalog ──────────────────────────────────────────────────────
# Each entry: name, domain, rss_url, outlet_type, authority_score, state, city
# outlet_type: local_news | regional_news | broadcast | national | blog | social
# authority_score: 1–10 (10 = most authoritative; mirrors Outlet model)
#
# District entries: keyed by "ST-##" (e.g. "PA-08").  Added first, then
# state-level entries are appended; duplicates by domain are dropped.
# State entries: keyed by two-letter state code.
#
# RSS URL notes:
#   - WordPress sites typically expose /feed/ or /feed
#   - Nexstar TV stations (WNEP, WBRE/WYOU, etc.) use /feeds/syndication/rss/...
#   - MediaNews Group papers use /arcio/rss/ (Arc Publishing)
#   - Lee Enterprises papers often use /search/?f=rss or /feed/

_OUTLET_CATALOG: dict[str, dict[str, list[dict]]] = {

    # ── District-specific ─────────────────────────────────────────────────────
    "district": {

        # Pennsylvania 8th — Scranton / Wilkes-Barre / NEPA
        "PA-08": [
            {"name": "Times Leader",         "domain": "timesleader.com",     "rss_url": "https://www.timesleader.com/feed/",           "outlet_type": "local_news",    "authority_score": 8,  "state": "PA", "city": "Wilkes-Barre"},
            {"name": "The Times-Tribune",    "domain": "thetimes-tribune.com","rss_url": "https://thetimes-tribune.com/feed/",          "outlet_type": "local_news",    "authority_score": 9,  "state": "PA", "city": "Scranton"},
            {"name": "Citizens' Voice",      "domain": "citizensvoice.com",   "rss_url": "https://www.citizensvoice.com/feed/",         "outlet_type": "local_news",    "authority_score": 8,  "state": "PA", "city": "Wilkes-Barre"},
            {"name": "Standard-Speaker",     "domain": "standardspeaker.com", "rss_url": "https://standardspeaker.com/feed/",           "outlet_type": "local_news",    "authority_score": 6,  "state": "PA", "city": "Hazleton"},
            {"name": "Pocono Record",        "domain": "poconorecord.com",    "rss_url": "https://www.poconorecord.com/arcio/rss/",     "outlet_type": "local_news",    "authority_score": 6,  "state": "PA", "city": "Stroudsburg"},
            {"name": "Wayne Independent",    "domain": "wayneindependent.com","rss_url": "https://www.wayneindependent.com/feed/",      "outlet_type": "local_news",    "authority_score": 5,  "state": "PA", "city": "Honesdale"},
            {"name": "WNEP-TV",              "domain": "wnep.com",            "rss_url": "https://www.wnep.com/feeds/syndication/rss/news/local", "outlet_type": "broadcast", "authority_score": 9, "state": "PA", "city": "Scranton"},
            {"name": "PAHomepage (WBRE/WYOU)","domain": "pahomepage.com",     "rss_url": "https://www.pahomepage.com/feed/",            "outlet_type": "broadcast",     "authority_score": 7,  "state": "PA", "city": "Wilkes-Barre"},
            {"name": "River Reporter",       "domain": "riverreporter.com",   "rss_url": "https://www.riverreporter.com/feed/",         "outlet_type": "local_news",    "authority_score": 5,  "state": "PA", "city": "Narrowsburg"},
            {"name": "Votebeat Pennsylvania","domain": "votebeat.org",        "rss_url": "https://votebeat.org/pennsylvania/rss.xml",   "outlet_type": "regional_news", "authority_score": 7,  "state": "PA", "city": None},
        ],

        # Pennsylvania 7th — York / Lancaster
        "PA-07": [
            {"name": "York Daily Record",    "domain": "ydr.com",             "rss_url": "https://www.ydr.com/arcio/rss/",              "outlet_type": "local_news",    "authority_score": 7,  "state": "PA", "city": "York"},
            {"name": "LancasterOnline",      "domain": "lancasteronline.com", "rss_url": "https://lancasteronline.com/search/?f=rss",   "outlet_type": "local_news",    "authority_score": 7,  "state": "PA", "city": "Lancaster"},
            {"name": "York Dispatch",        "domain": "yorkdispatch.com",    "rss_url": "https://www.yorkdispatch.com/arcio/rss/",     "outlet_type": "local_news",    "authority_score": 6,  "state": "PA", "city": "York"},
        ],

        # Pennsylvania 17th — Pittsburgh suburbs / Allegheny
        "PA-17": [
            {"name": "Pittsburgh Post-Gazette","domain": "post-gazette.com",  "rss_url": "https://www.post-gazette.com/rss/rss-politics-state.xml", "outlet_type": "regional_news", "authority_score": 9, "state": "PA", "city": "Pittsburgh"},
            {"name": "Pittsburgh Tribune-Review","domain": "triblive.com",    "rss_url": "https://triblive.com/feed/",                  "outlet_type": "regional_news", "authority_score": 8,  "state": "PA", "city": "Pittsburgh"},
            {"name": "WPXI (Cox Media)",     "domain": "wpxi.com",            "rss_url": "https://www.wpxi.com/arcio/rss/category/news/local-news/", "outlet_type": "broadcast", "authority_score": 8, "state": "PA", "city": "Pittsburgh"},
        ],

        # Ohio 13th — Akron / Youngstown
        "OH-13": [
            {"name": "Akron Beacon Journal", "domain": "beaconjournal.com",   "rss_url": "https://www.beaconjournal.com/arcio/rss/",    "outlet_type": "local_news",    "authority_score": 8,  "state": "OH", "city": "Akron"},
            {"name": "Youngstown Vindicator","domain": "vindy.com",           "rss_url": "https://www.vindy.com/feeds/news.xml",         "outlet_type": "local_news",    "authority_score": 7,  "state": "OH", "city": "Youngstown"},
            {"name": "WKBN Youngstown",      "domain": "wkbn.com",            "rss_url": "https://www.wkbn.com/feed/",                  "outlet_type": "broadcast",     "authority_score": 7,  "state": "OH", "city": "Youngstown"},
        ],

        # Michigan 7th — Lansing / suburbs
        "MI-07": [
            {"name": "Lansing State Journal", "domain": "lansingstatejournal.com", "rss_url": "https://www.lansingstatejournal.com/arcio/rss/", "outlet_type": "local_news", "authority_score": 7, "state": "MI", "city": "Lansing"},
            {"name": "WLNS Lansing",         "domain": "wlns.com",            "rss_url": "https://www.wlns.com/feed/",                  "outlet_type": "broadcast",     "authority_score": 7,  "state": "MI", "city": "Lansing"},
        ],

        # Wisconsin 3rd — La Crosse / western WI
        "WI-03": [
            {"name": "La Crosse Tribune",    "domain": "lacrossetribune.com", "rss_url": "https://lacrossetribune.com/search/?f=rss",   "outlet_type": "local_news",    "authority_score": 7,  "state": "WI", "city": "La Crosse"},
            {"name": "WKBT La Crosse",       "domain": "wkbt.com",            "rss_url": "https://www.wkbt.com/feed/",                  "outlet_type": "broadcast",     "authority_score": 6,  "state": "WI", "city": "La Crosse"},
        ],

        # Arizona 6th — suburban Phoenix
        "AZ-06": [
            {"name": "AZFamily (3TV/CBS5)",  "domain": "azfamily.com",        "rss_url": "https://www.azfamily.com/arcio/rss/",         "outlet_type": "broadcast",     "authority_score": 8,  "state": "AZ", "city": "Phoenix"},
            {"name": "Arizona Republic",     "domain": "azcentral.com",       "rss_url": "https://www.azcentral.com/arcio/rss/",        "outlet_type": "regional_news", "authority_score": 9,  "state": "AZ", "city": "Phoenix"},
        ],

        # Georgia 6th — north Atlanta suburbs
        "GA-06": [
            {"name": "Atlanta Journal-Constitution","domain": "ajc.com",      "rss_url": "https://www.ajc.com/arcio/rss/",              "outlet_type": "regional_news", "authority_score": 9,  "state": "GA", "city": "Atlanta"},
            {"name": "WXIA (11Alive)",       "domain": "11alive.com",         "rss_url": "https://www.11alive.com/feeds/syndication/rss/news/local", "outlet_type": "broadcast", "authority_score": 8, "state": "GA", "city": "Atlanta"},
        ],

        # Nevada 3rd — suburban Las Vegas
        "NV-03": [
            {"name": "Las Vegas Review-Journal","domain": "reviewjournal.com","rss_url": "https://www.reviewjournal.com/feed/",          "outlet_type": "regional_news", "authority_score": 8,  "state": "NV", "city": "Las Vegas"},
            {"name": "Nevada Current",       "domain": "nevadacurrent.com",   "rss_url": "https://nevadacurrent.com/feed/",             "outlet_type": "regional_news", "authority_score": 7,  "state": "NV", "city": None},
        ],

        # North Carolina 13th — Raleigh suburbs
        "NC-13": [
            {"name": "News & Observer",      "domain": "newsobserver.com",    "rss_url": "https://www.newsobserver.com/arcio/rss/",     "outlet_type": "regional_news", "authority_score": 8,  "state": "NC", "city": "Raleigh"},
            {"name": "WRAL-TV",              "domain": "wral.com",            "rss_url": "https://www.wral.com/rss/",                   "outlet_type": "broadcast",     "authority_score": 8,  "state": "NC", "city": "Raleigh"},
        ],
    },

    # ── State-level (always added when district matches state) ────────────────
    "state": {
        "PA": [
            {"name": "Spotlight PA",         "domain": "spotlightpa.org",     "rss_url": "https://www.spotlightpa.org/news/feed.xml",   "outlet_type": "regional_news", "authority_score": 9,  "state": "PA", "city": None},
            {"name": "Pennsylvania Capital-Star","domain": "penncapital-star.com","rss_url": "https://penncapital-star.com/feed/",      "outlet_type": "regional_news", "authority_score": 8,  "state": "PA", "city": None},
            {"name": "PennLive",             "domain": "pennlive.com",        "rss_url": "https://www.pennlive.com/arc/outboundfeeds/rss/?outputType=xml", "outlet_type": "regional_news", "authority_score": 8, "state": "PA", "city": None},
            {"name": "WITF News",            "domain": "witf.org",            "rss_url": "https://www.witf.org/feed/",                  "outlet_type": "broadcast",     "authority_score": 7,  "state": "PA", "city": "Harrisburg"},
        ],
        "OH": [
            {"name": "Ohio Capital Journal",  "domain": "ohiocapitaljournal.com","rss_url": "https://ohiocapitaljournal.com/feed/",    "outlet_type": "regional_news", "authority_score": 8,  "state": "OH", "city": None},
            {"name": "Cleveland Plain Dealer","domain": "cleveland.com",       "rss_url": "https://www.cleveland.com/arc/outboundfeeds/rss/", "outlet_type": "regional_news", "authority_score": 8, "state": "OH", "city": "Cleveland"},
        ],
        "MI": [
            {"name": "Michigan Advance",      "domain": "michiganadvance.com", "rss_url": "https://michiganadvance.com/feed/",          "outlet_type": "regional_news", "authority_score": 7,  "state": "MI", "city": None},
            {"name": "Bridge Michigan",       "domain": "bridgemi.com",        "rss_url": "https://www.bridgemi.com/feed/",             "outlet_type": "regional_news", "authority_score": 8,  "state": "MI", "city": None},
        ],
        "WI": [
            {"name": "Wisconsin Examiner",    "domain": "wisconsinexaminer.com","rss_url": "https://wisconsinexaminer.com/feed/",       "outlet_type": "regional_news", "authority_score": 7,  "state": "WI", "city": None},
            {"name": "Milwaukee Journal Sentinel","domain": "jsonline.com",    "rss_url": "https://www.jsonline.com/arcio/rss/",        "outlet_type": "regional_news", "authority_score": 8,  "state": "WI", "city": "Milwaukee"},
        ],
        "AZ": [
            {"name": "Arizona Mirror",        "domain": "azmirror.com",        "rss_url": "https://azmirror.com/feed/",                 "outlet_type": "regional_news", "authority_score": 7,  "state": "AZ", "city": None},
            {"name": "12 News (KPNX)",        "domain": "12news.com",          "rss_url": "https://www.12news.com/feeds/syndication/rss/news/local", "outlet_type": "broadcast", "authority_score": 8, "state": "AZ", "city": "Phoenix"},
        ],
        "GA": [
            {"name": "Georgia Recorder",      "domain": "georgiarecorder.com", "rss_url": "https://georgiarecorder.com/feed/",          "outlet_type": "regional_news", "authority_score": 7,  "state": "GA", "city": None},
            {"name": "GPB News",              "domain": "gpb.org",             "rss_url": "https://www.gpb.org/news/feed",              "outlet_type": "broadcast",     "authority_score": 7,  "state": "GA", "city": None},
        ],
        "NV": [
            {"name": "Nevada Current",        "domain": "nevadacurrent.com",   "rss_url": "https://nevadacurrent.com/feed/",            "outlet_type": "regional_news", "authority_score": 7,  "state": "NV", "city": None},
            {"name": "KTNV Las Vegas",        "domain": "ktnv.com",            "rss_url": "https://www.ktnv.com/feed/",                 "outlet_type": "broadcast",     "authority_score": 7,  "state": "NV", "city": "Las Vegas"},
        ],
        "NC": [
            {"name": "NC Newsline",           "domain": "ncnewsline.com",      "rss_url": "https://ncnewsline.com/feed/",               "outlet_type": "regional_news", "authority_score": 7,  "state": "NC", "city": None},
            {"name": "WFAE Charlotte",        "domain": "wfae.org",            "rss_url": "https://www.wfae.org/feed",                  "outlet_type": "broadcast",     "authority_score": 7,  "state": "NC", "city": "Charlotte"},
        ],
        "FL": [
            {"name": "Florida Phoenix",       "domain": "floridaphoenix.com",  "rss_url": "https://floridaphoenix.com/feed/",           "outlet_type": "regional_news", "authority_score": 7,  "state": "FL", "city": None},
            {"name": "Tampa Bay Times",       "domain": "tampabay.com",        "rss_url": "https://www.tampabay.com/arcio/rss/",        "outlet_type": "regional_news", "authority_score": 9,  "state": "FL", "city": "Tampa"},
            {"name": "Orlando Sentinel",      "domain": "orlandosentinel.com", "rss_url": "https://www.orlandosentinel.com/arcio/rss/", "outlet_type": "regional_news", "authority_score": 8,  "state": "FL", "city": "Orlando"},
        ],
        "TX": [
            {"name": "Texas Tribune",         "domain": "texastribune.org",    "rss_url": "https://www.texastribune.org/feeds/news/rss.xml", "outlet_type": "regional_news", "authority_score": 9, "state": "TX", "city": None},
            {"name": "Houston Chronicle",     "domain": "houstonchronicle.com","rss_url": "https://www.houstonchronicle.com/arcio/rss/", "outlet_type": "regional_news", "authority_score": 9, "state": "TX", "city": "Houston"},
        ],
        "NY": [
            {"name": "New York Focus",        "domain": "nyfocus.org",         "rss_url": "https://nyfocus.org/feed/",                  "outlet_type": "regional_news", "authority_score": 7,  "state": "NY", "city": None},
            {"name": "City & State NY",       "domain": "cityandstateny.com",  "rss_url": "https://www.cityandstateny.com/feed/",       "outlet_type": "regional_news", "authority_score": 7,  "state": "NY", "city": "New York"},
        ],
        "CA": [
            {"name": "CalMatters",            "domain": "calmatters.org",      "rss_url": "https://calmatters.org/feed/",               "outlet_type": "regional_news", "authority_score": 8,  "state": "CA", "city": None},
            {"name": "LAist",                 "domain": "laist.com",           "rss_url": "https://laist.com/feeds/news.rss",           "outlet_type": "regional_news", "authority_score": 7,  "state": "CA", "city": "Los Angeles"},
        ],
        "MN": [
            {"name": "MinnPost",              "domain": "minnpost.com",        "rss_url": "https://www.minnpost.com/feed/",             "outlet_type": "regional_news", "authority_score": 7,  "state": "MN", "city": None},
            {"name": "Minnesota Reformer",    "domain": "minnesotareformer.com","rss_url": "https://minnesotareformer.com/feed/",       "outlet_type": "regional_news", "authority_score": 7,  "state": "MN", "city": None},
        ],
        "VA": [
            {"name": "Virginia Mercury",      "domain": "virginiamercury.com", "rss_url": "https://virginiamercury.com/feed/",          "outlet_type": "regional_news", "authority_score": 7,  "state": "VA", "city": None},
            {"name": "VPM News",              "domain": "vpm.org",             "rss_url": "https://vpm.org/feed/",                     "outlet_type": "broadcast",     "authority_score": 7,  "state": "VA", "city": "Richmond"},
        ],
        "CO": [
            {"name": "Colorado Sun",          "domain": "coloradosun.com",     "rss_url": "https://coloradosun.com/feed/",              "outlet_type": "regional_news", "authority_score": 8,  "state": "CO", "city": None},
            {"name": "Colorado Public Radio", "domain": "cpr.org",             "rss_url": "https://www.cpr.org/feed/",                  "outlet_type": "broadcast",     "authority_score": 8,  "state": "CO", "city": "Denver"},
        ],
    },
}


def get_local_outlets(district: str | None, state_code: str | None,
                      db=None) -> list[dict]:
    """Return local outlet definitions for this district/state, deduped by domain.

    Sources (merged in priority order):
      1. Hardcoded district-specific catalog entries
      2. Hardcoded state-level catalog entries
      3. DB Outlet records tagged with this district or state via their `districts` field

    Pass a SQLAlchemy Session as `db` to include DB-managed outlets.  When db is
    None only the hardcoded catalog is used (safe at import time).
    """
    import json as _json

    seen_domains: set[str] = set()
    results: list[dict] = []

    dist_key = (district or "").upper().strip()
    state_key = (state_code or "").upper().strip()

    for outlet in _OUTLET_CATALOG["district"].get(dist_key, []):
        domain = outlet["domain"].lower()
        if domain not in seen_domains:
            seen_domains.add(domain)
            results.append(outlet)

    for outlet in _OUTLET_CATALOG["state"].get(state_key, []):
        domain = outlet["domain"].lower()
        if domain not in seen_domains:
            seen_domains.add(domain)
            results.append(outlet)

    # DB-managed outlets: any Outlet record whose `districts` JSON array contains
    # the current district code or state code.
    if db is not None:
        try:
            from app.models import Outlet as _Outlet
            db_outlets = db.query(_Outlet).filter(
                _Outlet.active == True,
                _Outlet.districts.isnot(None),
            ).all()
            for o in db_outlets:
                try:
                    tagged = _json.loads(o.districts or "[]")
                except Exception:
                    tagged = []
                if dist_key not in tagged and state_key not in tagged:
                    continue
                domain = (o.domain or "").lower()
                if not domain or domain in seen_domains:
                    continue
                seen_domains.add(domain)
                results.append({
                    "name": o.name,
                    "domain": domain,
                    "rss_url": o.rss_url or "",
                    "outlet_type": o.outlet_type or "local_news",
                    "authority_score": o.authority_score or 5,
                    "state": o.state,
                    "city": o.city,
                })
        except Exception:
            pass  # DB not available or model not loaded — fall back to catalog only

    return results


def _gnews_url(query: str) -> str:
    """Build a Google News RSS search URL for the given query string."""
    params = urlencode({"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    return f"https://news.google.com/rss/search?{params}"


def _gnews_url_with_dates(query: str, after: str, before: str) -> str:
    """Google News RSS with date range operators. after/before format: 'YYYY-MM-DD'"""
    dated_query = f"{query} after:{after} before:{before}"
    params = urlencode({"q": dated_query, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    return f"https://news.google.com/rss/search?{params}"


# Maps US state abbreviations to the subreddit name for that state.
# Verified against actual Reddit subreddit names (case-sensitive matters for display,
# but Reddit URLs are case-insensitive).
_STATE_SUBREDDITS: dict[str, str] = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas",
    "CA": "california", "CO": "colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "florida", "GA": "georgia", "HI": "hawaii", "ID": "Idaho",
    "IL": "illinois", "IN": "indiana", "IA": "iowa", "KS": "kansas",
    "KY": "Kentucky", "LA": "louisiana", "ME": "Maine", "MD": "maryland",
    "MA": "massachusetts", "MI": "michigan", "MN": "minnesota", "MS": "mississippi",
    "MO": "missouri", "MT": "Montana", "NE": "Nebraska", "NV": "nevada",
    "NH": "newhampshire", "NJ": "newjersey", "NM": "newmexico", "NY": "newyork",
    "NC": "NorthCarolina", "ND": "northdakota", "OH": "ohio", "OK": "oklahoma",
    "OR": "oregon", "PA": "pennsylvania", "RI": "RhodeIsland", "SC": "southcarolina",
    "SD": "southdakota", "TN": "Tennessee", "TX": "texas", "UT": "utah",
    "VT": "vermont", "VA": "virginia", "WA": "washington", "WV": "WestVirginia",
    "WI": "wisconsin", "WY": "wyoming",
}


def _globenewswire_url(query: str) -> str:
    """GlobeNewswire keyword RSS — free, no auth required."""
    params = urlencode({"rss": "true", "filter": "keyword", "search": query})
    return f"https://www.globenewswire.com/RssFeed/search?{params}"


def _youtube_channel_rss(channel_id: str) -> str:
    """RSS feed for a YouTube channel given its UCxxxxxxxx channel ID."""
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


def _reddit_search_url(query: str, subreddit: str | None = None) -> str:
    """Build a Reddit RSS search URL. subreddit=None searches all of Reddit."""
    params = urlencode({"q": query, "sort": "new", "limit": "25", "t": "all"})
    if subreddit:
        return f"https://www.reddit.com/r/{subreddit}/search.rss?{params}&restrict_sr=1"
    return f"https://www.reddit.com/search.rss?{params}"


def _reddit_subreddit_url(subreddit: str) -> str:
    """RSS feed for all posts in a subreddit (no search filter)."""
    return f"https://www.reddit.com/r/{subreddit}/.rss?limit=25"


def _parse_state_code(district: str | None, location: str | None) -> str | None:
    """Extract a two-letter state code from district ('PA-08') or location ('Scranton, PA')."""
    if district:
        m = re.match(r'^([A-Z]{2})-?\d', district.upper())
        if m:
            return m.group(1)
    if location:
        m = re.search(r',\s*([A-Z]{2})\b', location.upper())
        if m:
            return m.group(1)
    return None


_STATE_NAMES: dict[str, str] = {
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
    "WI": "Wisconsin", "WY": "Wyoming",
}


def _candidate_last_name(full_name: str | None) -> str | None:
    """Return a titlecased last name suitable for a Reddit search query.

    Handles both FEC format ('COGNETTI, PAIGE' → 'Cognetti') and
    natural order ('Rob Bresnahan' → 'Bresnahan').
    """
    if not full_name:
        return None
    name = full_name.strip()
    if "," in name:
        # FEC last-first format
        last = name.split(",")[0].strip()
    else:
        # Natural first-last format — take the last token
        parts = name.split()
        last = parts[-1] if parts else name
    return last.title() if last else None


def _terms(value: str | list[str] | None) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    try:
        parsed = json.loads(value)
        return [str(v).strip() for v in parsed if str(v).strip()]
    except Exception:
        return [v.strip() for v in value.split(",") if v.strip()]


def _q(*parts: str | None) -> str:
    return " ".join(p for p in parts if p)


def _quoted(value: str | None) -> str | None:
    value = (value or "").strip()
    return f'"{value}"' if value else None


def _add(monitors: list[dict[str, Any]], seen: set[tuple], **data: Any) -> None:
    key = (
        data.get("monitor_type"),
        (data.get("query") or "").lower().strip(),
        (data.get("url") or "").lower().strip(),
        data.get("name", "").lower().strip(),
    )
    if key in seen:
        return
    seen.add(key)
    monitors.append({
        "source_type": "news",
        "active": True,
        "required_terms": [],
        "excluded_terms": [],
        **data,
    })


def generate_monitors_for_campaign(campaign_profile: CampaignConfig, opponents: list[Opponent], db=None) -> list[dict[str, Any]]:
    candidate = campaign_profile.candidate_name
    office = campaign_profile.office or campaign_profile.race
    district = campaign_profile.district
    location = campaign_profile.location
    district_number = campaign_profile.district_number
    election_type = (campaign_profile.election_type or "").lower()
    race_level = (campaign_profile.race_level or "").lower()
    neighborhoods = _terms(campaign_profile.neighborhood_keywords)
    priorities = _terms(campaign_profile.key_priorities)
    required = [x for x in [candidate, district, location, office] if x]
    excluded = _terms(campaign_profile.excluded_keywords)
    small_race = bool(campaign_profile.sparse_race_mode or election_type in {"primary", "special"} or race_level in {"city", "local", "state"})
    geo_terms = [x for x in [district, district_number, location, *neighborhoods] if x]
    state_code = _parse_state_code(district, location)
    state_sub = _STATE_SUBREDDITS.get(state_code or "") if state_code else None
    state_name = _STATE_NAMES.get(state_code or "") if state_code else None

    monitors: list[dict[str, Any]] = []
    seen: set[tuple] = set()

    # ── Local outlet feeds + targeted historical searches ────────────────────
    # For each outlet in the district catalog we add TWO monitors:
    #
    #   1. Direct RSS — the outlet's own feed, fetched every scheduler tick.
    #      Catches new articles within ~30 minutes of publication. Signal is low
    #      because the feed includes all content (sports, weather, etc.), but the
    #      relevance filter removes non-race content cheaply.
    #
    #   2. Targeted Google News search — "site:<domain>" anchored to the
    #      candidate name (and opponent if present). This surfaces historical
    #      coverage of THIS specific race going back months, not just the last
    #      50 articles the live feed exposes. Fully general: any district in the
    #      catalog gets this automatically.
    #
    # Both monitors are generated from the catalog, so switching to a different
    # district (OH-13, WI-03, etc.) produces the right outlets automatically.
    local_outlets = get_local_outlets(district, state_code, db=db)

    # Build the candidate/opponent part of the site-search query once.
    cand_last_for_search = _candidate_last_name(candidate)
    opp_last_names = [_candidate_last_name(o.name) for o in opponents if o.name]

    for outlet in local_outlets:
        # 1. Direct RSS feed
        _add(monitors, seen,
             name=f"RSS: {outlet['name']}",
             monitor_type="rss",
             url=outlet["rss_url"],
             category="local_news",
             source_type="news",
             relevance_hint=f"Direct RSS feed from {outlet['name']} — a local outlet serving the district.")

        # 2. Site-specific Google News search for this outlet + candidates.
        #    Surfaces historical coverage that predates when the direct feed was set up.
        if cand_last_for_search:
            domain = outlet["domain"]
            name_parts = [cand_last_for_search] + [n for n in opp_last_names if n]
            # Build: site:domain "Cognetti" OR "Bresnahan"
            name_query = " OR ".join(f'"{n}"' for n in name_parts[:3])
            site_query = f"site:{domain} ({name_query})" if len(name_parts) > 1 else f'site:{domain} "{cand_last_for_search}"'
            _add(monitors, seen,
                 name=f"{outlet['name']} — Google News Feed",
                 monitor_type="rss",
                 url=_gnews_url(site_query),
                 category="local_news",
                 source_type="news",
                 relevance_hint=f"Google News search for {outlet['name']} coverage of this specific race — surfaces historical articles beyond the live feed window.")
    # ── End local outlet feeds ────────────────────────────────────────────────

    # ── Google News RSS feeds (auto-ingested by the scheduler) ────────────────
    # These are created as monitor_type="rss" so _ensure_rss_feed in monitors.py
    # adds them to the rss_feeds table and the scheduler picks them up automatically.
    if candidate:
        _add(monitors, seen,
             name=f"Google News: {candidate}",
             monitor_type="rss",
             url=_gnews_url(candidate),
             category="candidate",
             relevance_hint="Automatically tracks all Google News coverage mentioning the candidate.")
        if office:
            _add(monitors, seen,
                 name=f"Google News: {candidate} {office}",
                 monitor_type="rss",
                 url=_gnews_url(f'"{candidate}" {office}'),
                 category="candidate",
                 relevance_hint="Google News feed filtered to candidate + office title.")

    for opponent in opponents:
        if not opponent.name:
            continue
        _add(monitors, seen,
             name=f"Google News: {opponent.name}",
             monitor_type="rss",
             url=_gnews_url(opponent.name),
             category="opponent",
             source_type="opponent_statement",
             relevance_hint="Automatically tracks all Google News coverage mentioning the opponent.")
        if candidate and opponent.name:
            _add(monitors, seen,
                 name=f"Google News: {candidate} vs {opponent.name}",
                 monitor_type="rss",
                 url=_gnews_url(f'"{candidate}" "{opponent.name}"'),
                 category="race",
                 relevance_hint="Google News feed for articles that mention both candidates together.")

    if district and office:
        _add(monitors, seen,
             name=f"Google News: {district} {office}",
             monitor_type="rss",
             url=_gnews_url(f'"{district}" {office}'),
             category="race",
             relevance_hint="Google News feed for the race district and office.")

    if location and office:
        _add(monitors, seen,
             name=f"Google News: {location} {office}",
             monitor_type="rss",
             url=_gnews_url(f'"{location}" {office} election'),
             category="race",
             relevance_hint="Google News feed for the race location and office.")
    # National outlet pickup — single Google News search catches the candidate's
    # last name appearing in major national outlets, flagging when a local story
    # escapes into the national press ecosystem.
    cand_last_for_national = _candidate_last_name(candidate) if candidate else None
    if cand_last_for_national:
        _add(monitors, seen,
             name=f"National pickup: {cand_last_for_national}",
             monitor_type="rss",
             url=_gnews_url(
                 f'"{cand_last_for_national}" '
                 f'(Politico OR "The Hill" OR Axios OR "AP News" OR NPR OR Reuters OR CNN OR NBC OR "Fox News")'
             ),
             category="national",
             relevance_hint="Detects when national outlets pick up the candidate's story — the key signal for narrative escaping local coverage.")
    # ── End Google News RSS feeds ─────────────────────────────────────────────

    # ── Reddit RSS feeds (no credentials required) ────────────────────────────
    cand_last = _candidate_last_name(candidate)

    if cand_last:
        # Global Reddit search for candidate
        _add(monitors, seen,
             name=f"Reddit: {cand_last}",
             monitor_type="rss",
             url=_reddit_search_url(f"{cand_last} {state_name or state_code or ''}".strip()),
             category="candidate",
             relevance_hint="Reddit-wide search for candidate mentions.")

        if state_sub:
            # State subreddit search for candidate
            _add(monitors, seen,
                 name=f"Reddit r/{state_sub}: {cand_last}",
                 monitor_type="rss",
                 url=_reddit_search_url(cand_last, subreddit=state_sub),
                 category="candidate",
                 relevance_hint=f"Candidate mentions within r/{state_sub}.")

    for opponent in opponents:
        opp_last = _candidate_last_name(opponent.name)
        if not opp_last:
            continue
        _add(monitors, seen,
             name=f"Reddit: {opp_last}",
             monitor_type="rss",
             url=_reddit_search_url(f"{opp_last} {state_name or state_code or ''}".strip()),
             category="opponent",
             source_type="opponent_statement",
             relevance_hint="Reddit-wide search for opponent mentions.")
        if state_sub:
            _add(monitors, seen,
                 name=f"Reddit r/{state_sub}: {opp_last}",
                 monitor_type="rss",
                 url=_reddit_search_url(opp_last, subreddit=state_sub),
                 category="opponent",
                 source_type="opponent_statement",
                 relevance_hint=f"Opponent mentions within r/{state_sub}.")

    # Local subreddit feeds from neighborhood_keywords (e.g. "Scranton", "nepa")
    # Only use keywords that are plausible subreddit names (single word, <20 chars).
    for kw in neighborhoods:
        slug = kw.strip().replace(" ", "").replace("-", "")
        if slug and len(slug) <= 20:
            _add(monitors, seen,
                 name=f"Reddit r/{slug} (local feed)",
                 monitor_type="rss",
                 url=_reddit_subreddit_url(slug),
                 category="race",
                 relevance_hint=f"All posts in r/{slug} — local community discussion.")
    # ── End Reddit RSS feeds ──────────────────────────────────────────────────

    if candidate:
        _add(monitors, seen, name=f"{candidate} news search", monitor_type="search_query",
             query=_q(_quoted(candidate), _quoted(district) or _quoted(location)),
             category="candidate", required_terms=[candidate], excluded_terms=excluded,
             relevance_hint="Find reporting that mentions the candidate in the campaign geography.")
        if small_race:
            if office:
                _add(monitors, seen, name=f"{candidate} {office} search", monitor_type="search_query",
                     query=_q(_quoted(candidate), _quoted(office)), category="candidate",
                     required_terms=[candidate, office], excluded_terms=excluded,
                     relevance_hint="Small-race query anchored to candidate and office.")
            for geo in geo_terms[:5]:
                _add(monitors, seen, name=f"{candidate} in {geo}", monitor_type="search_query",
                     query=_q(_quoted(candidate), _quoted(geo)), category="candidate",
                     required_terms=[candidate, geo], excluded_terms=excluded,
                     relevance_hint="Small-race query anchored to candidate and local geography.")
        _add(monitors, seen, name=f"{candidate} campaign website check", monitor_type="manual",
             category="candidate", required_terms=[candidate],
             relevance_hint="Add the candidate campaign website URL if known; do not assume an official URL.")
        _add(monitors, seen, name=f"{candidate} social check", monitor_type="manual",
             category="candidate", required_terms=[candidate],
             relevance_hint="Add official social profile URLs after verifying them manually.")
        if small_race:
            for label in ["Facebook", "Instagram", "X/Twitter", "Threads", "LinkedIn"]:
                _add(monitors, seen, name=f"{candidate} {label} check", monitor_type="manual",
                     category="candidate", required_terms=[candidate],
                     relevance_hint=f"Verify the candidate's official {label} page before adding a URL.")

    for opponent in opponents:
        if not opponent.name:
            continue
        _add(monitors, seen, name=f"{opponent.name} news search", monitor_type="search_query",
             query=_q(_quoted(opponent.name), _quoted(district) or _quoted(location)),
             category="opponent", source_type="opponent_statement",
             required_terms=[opponent.name], excluded_terms=excluded,
             relevance_hint="Track opponent mentions tied to the race geography.")
        if small_race and office:
            _add(monitors, seen, name=f"{opponent.name} {office} search", monitor_type="search_query",
                 query=_q(_quoted(opponent.name), _quoted(office)), category="opponent",
                 source_type="opponent_statement", required_terms=[opponent.name, office],
                 excluded_terms=excluded, relevance_hint="Small-race query anchored to opponent and office.")
            for geo in geo_terms[:5]:
                _add(monitors, seen, name=f"{opponent.name} in {geo}", monitor_type="search_query",
                     query=_q(_quoted(opponent.name), _quoted(geo)), category="opponent",
                     source_type="opponent_statement", required_terms=[opponent.name, geo],
                     excluded_terms=excluded, relevance_hint="Small-race query anchored to opponent and local geography.")
        _add(monitors, seen, name=f"{opponent.name} campaign website check", monitor_type="manual",
             category="opponent", source_type="opponent_statement", required_terms=[opponent.name],
             relevance_hint="Add the opponent campaign website URL only after verifying it.")
        _add(monitors, seen, name=f"{opponent.name} social check", monitor_type="manual",
             category="opponent", source_type="opponent_statement", required_terms=[opponent.name],
             relevance_hint="Add verified opponent social profile URLs manually.")
        if candidate:
            _add(monitors, seen, name=f"{candidate} vs {opponent.name}", monitor_type="search_query",
                 query=_q(_quoted(candidate), _quoted(opponent.name)),
                 category="race", required_terms=[candidate, opponent.name], excluded_terms=excluded,
                 relevance_hint="Find coverage that compares the candidate and opponent directly.")

    if district:
        _add(monitors, seen, name=f"{district} election search", monitor_type="search_query",
             query=_q(_quoted(district), "election"), category="race",
             required_terms=[district], excluded_terms=excluded,
             relevance_hint="Track election coverage using the district name.")
    if office and district:
        _add(monitors, seen, name=f"{office} {district} race search", monitor_type="search_query",
             query=_q(_quoted(district), office), category="race",
             required_terms=[district, office], excluded_terms=excluded,
             relevance_hint="Track coverage of the office and district.")
    if location and office:
        _add(monitors, seen, name=f"{location} {office} election search", monitor_type="search_query",
             query=_q(_quoted(location), office, "election"), category="race",
             required_terms=[location], excluded_terms=excluded,
             relevance_hint="Track local election coverage.")
    if small_race:
        election_word = election_type or "primary"
        for geo in geo_terms[:6]:
            if office:
                _add(monitors, seen, name=f"{geo} {office} {election_word} search", monitor_type="search_query",
                     query=_q(_quoted(geo), _quoted(office), election_word),
                     category="race", required_terms=[geo, office], excluded_terms=excluded,
                     relevance_hint="Sparse-race query combining local geography, office, and election type.")
            if candidate:
                _add(monitors, seen, name=f"{geo} {candidate} search", monitor_type="search_query",
                     query=_q(_quoted(geo), _quoted(candidate)),
                     category="race", required_terms=[geo, candidate], excluded_terms=excluded,
                     relevance_hint="Sparse-race neighborhood query for candidate mentions.")

    for priority in priorities:
        if district:
            _add(monitors, seen, name=f"{priority} in {district}", monitor_type="search_query",
                 query=_q(_quoted(district), priority), category="issue",
                 required_terms=[priority, district], excluded_terms=excluded,
                 relevance_hint="Find district-specific coverage of a campaign priority.")
        if location:
            _add(monitors, seen, name=f"{priority} in {location}", monitor_type="search_query",
                 query=_q(_quoted(location), priority), category="issue",
                 required_terms=[priority, location], excluded_terms=excluded,
                 relevance_hint="Find local coverage of a campaign priority.")
        if candidate:
            _add(monitors, seen, name=f"{candidate} on {priority}", monitor_type="search_query",
                 query=_q(_quoted(candidate), priority), category="issue",
                 required_terms=[candidate, priority], excluded_terms=excluded,
                 relevance_hint="Find candidate statements or coverage tied to a priority.")
        for opponent in opponents[:3]:
            _add(monitors, seen, name=f"{opponent.name} on {priority}", monitor_type="search_query",
                 query=_q(_quoted(opponent.name), priority), category="issue",
                 source_type="opponent_statement", required_terms=[opponent.name, priority],
                 excluded_terms=excluded, relevance_hint="Find opponent claims tied to a campaign priority.")
        if small_race:
            for geo in geo_terms[:5]:
                _add(monitors, seen, name=f"{priority} in {geo}", monitor_type="search_query",
                     query=_q(_quoted(geo), priority), category="issue",
                     required_terms=[priority, geo], excluded_terms=excluded,
                     relevance_hint="Sparse-race issue query anchored to local geography.")

    # ── Press release wires ───────────────────────────────────────────────────
    # GlobeNewswire supports keyword RSS natively (free, no auth).
    # PR Newswire and Business Wire don't have keyword RSS, so we route them
    # through Google News site-specific search, which surfaces the same releases.
    wire_name = cand_last_for_search or candidate
    if wire_name:
        _add(monitors, seen,
             name=f"GlobeNewswire: {wire_name}",
             monitor_type="rss",
             url=_globenewswire_url(wire_name),
             category="press_release",
             source_type="news",
             relevance_hint="GlobeNewswire press releases mentioning the candidate. "
                            "Catches campaign announcements and opposition research before local outlets pick them up.")
        _add(monitors, seen,
             name=f"PR Newswire: {wire_name}",
             monitor_type="rss",
             url=_gnews_url(f'site:prnewswire.com "{wire_name}"'),
             category="press_release",
             source_type="news",
             relevance_hint="PR Newswire releases mentioning the candidate, surfaced via Google News.")
        _add(monitors, seen,
             name=f"Business Wire: {wire_name}",
             monitor_type="rss",
             url=_gnews_url(f'site:businesswire.com "{wire_name}"'),
             category="press_release",
             source_type="news",
             relevance_hint="Business Wire releases mentioning the candidate, surfaced via Google News.")

    for opponent in opponents:
        opp_wire = _candidate_last_name(opponent.name) or opponent.name
        if not opp_wire:
            continue
        _add(monitors, seen,
             name=f"GlobeNewswire: {opp_wire}",
             monitor_type="rss",
             url=_globenewswire_url(opp_wire),
             category="press_release",
             source_type="opponent_statement",
             relevance_hint=f"GlobeNewswire press releases from or about {opponent.name}.")
        _add(monitors, seen,
             name=f"PR Newswire: {opp_wire}",
             monitor_type="rss",
             url=_gnews_url(f'site:prnewswire.com "{opp_wire}"'),
             category="press_release",
             source_type="opponent_statement",
             relevance_hint=f"PR Newswire releases mentioning {opponent.name}.")
    # ── End press release wires ───────────────────────────────────────────────

    # ── YouTube ───────────────────────────────────────────────────────────────
    # Google News site search surfaces YouTube content that gets indexed as news.
    # Channel-based RSS (youtube.com/feeds/videos.xml?channel_id=UC...) is added
    # as monitor_type="youtube" once the user provides a channel URL — the monitor
    # runner resolves the channel ID and auto-converts it to a proper RSS feed.
    if wire_name:
        _add(monitors, seen,
             name=f"YouTube: {wire_name}",
             monitor_type="rss",
             url=_gnews_url(f'site:youtube.com "{wire_name}"'),
             category="social",
             source_type="social",
             relevance_hint="YouTube videos about the candidate surfaced via Google News — catches campaign ads, debate clips, and viral moments.")
        _add(monitors, seen,
             name=f"{candidate} YouTube channel",
             monitor_type="youtube",
             url=None,
             category="social",
             source_type="social",
             relevance_hint="Add the candidate's YouTube channel URL (e.g. youtube.com/@CandidateName) after verifying it. "
                            "The system will subscribe to the channel's video RSS feed automatically.")

    for opponent in opponents:
        if not opponent.name:
            continue
        opp_last = _candidate_last_name(opponent.name) or opponent.name
        _add(monitors, seen,
             name=f"YouTube: {opp_last}",
             monitor_type="rss",
             url=_gnews_url(f'site:youtube.com "{opp_last}"'),
             category="social",
             source_type="opponent_statement",
             relevance_hint=f"YouTube videos about {opponent.name} surfaced via Google News.")
        _add(monitors, seen,
             name=f"{opponent.name} YouTube channel",
             monitor_type="youtube",
             url=None,
             category="social",
             source_type="opponent_statement",
             relevance_hint=f"Add {opponent.name}'s verified YouTube channel URL. The system will subscribe to the video RSS feed automatically.")
    # ── End YouTube ───────────────────────────────────────────────────────────

    # ── Twitter/X profiles via Nitter RSS ─────────────────────────────────────
    # Twitter's public pages are JavaScript-rendered so trafilatura can't scrape
    # them. Nitter instances serve static HTML + RSS for public profiles. The
    # monitor runner probes the instance list and registers the first working
    # feed URL. If all instances are blocked the monitor retries daily.
    #
    # The `query` field holds the Twitter handle; the user fills it in after
    # verifying the official account. Format: @handle or bare handle.
    if candidate:
        _add(monitors, seen,
             name=f"{candidate} X/Twitter profile",
             monitor_type="twitter_profile",
             query=None,
             url=None,
             category="social",
             source_type="social",
             relevance_hint=f"Add the candidate's verified X/Twitter handle (e.g. @CognettForCongress) "
                            f"to monitor tweets. The system will find a working Nitter RSS feed automatically.")

    for opponent in opponents:
        if not opponent.name:
            continue
        _add(monitors, seen,
             name=f"{opponent.name} X/Twitter profile",
             monitor_type="twitter_profile",
             query=None,
             url=None,
             category="social",
             source_type="opponent_statement",
             relevance_hint=f"Add {opponent.name}'s verified X/Twitter handle to monitor their tweets and attacks. "
                            f"This is where opposition attacks typically land first.")

    # Local journalist Twitter accounts — high-signal for tip-offs and early framing.
    # Generated as a single manual monitor; user adds individual handles after identifying
    # which journalists actively cover the race.
    if location or district:
        geo_label = location or district
        _add(monitors, seen,
             name=f"{geo_label} journalists X/Twitter",
             monitor_type="twitter_profile",
             query=None,
             url=None,
             category="social",
             source_type="news",
             relevance_hint=f"Add Twitter handles for local journalists who cover {geo_label} politics "
                            f"(one monitor per journalist). Journalists often post breaking news on Twitter "
                            f"hours before it appears in print.")
    # ── End Twitter/X profiles ────────────────────────────────────────────────

    # ── FEC filing monitors ───────────────────────────────────────────────────
    # Independent expenditure 24/48-hr notices (schedule_e) are the most
    # time-sensitive FEC signal — a PAC spending against your candidate must
    # file within 24-48 hours, giving you advance warning before the ads air.
    #
    # Opponent fundraising (F3 quarterly reports) signal financial momentum.
    #
    # monitor_type="fec_filings" uses the `query` field to store the FEC
    # candidate ID (e.g. "H8PA08123").  The monitor runner calls api.fec.gov.
    for opponent in opponents:
        if not opponent.fec_candidate_id:
            continue
        _add(monitors, seen,
             name=f"FEC: {opponent.name} filings",
             monitor_type="fec_filings",
             query=opponent.fec_candidate_id,
             category="public_record",
             source_type="public_record",
             relevance_hint=f"Polls api.fec.gov for independent expenditure notices and fundraising reports tied to {opponent.name} (FEC ID: {opponent.fec_candidate_id}).")

    # District-level independent expenditure monitor — catches all PAC/dark money
    # spending in the race, not just spending tied to a specific candidate ID.
    if district:
        state_code_for_fec = state_code or ""
        dist_num = district_number or re.sub(r"[^0-9]", "", district or "")
        if state_code_for_fec and dist_num:
            _add(monitors, seen,
                 name=f"FEC: independent expenditures in {district}",
                 monitor_type="fec_ie_district",
                 query=f"{state_code_for_fec}:{dist_num}",
                 category="public_record",
                 source_type="public_record",
                 relevance_hint=f"Polls FEC schedule_e for all independent expenditure notices targeting {district} — catches dark money and PAC activity across the whole race.")
    # ── End FEC filing monitors ───────────────────────────────────────────────

    public_record_names = [
        "FEC candidate or committee check",
        "State election board check",
        "County election board check",
        "Campaign finance filing check",
        "Ballot access and deadline check",
    ]
    for name in public_record_names:
        _add(monitors, seen, name=name, monitor_type="manual", source_type="public_record",
             category="public_record", required_terms=required,
             relevance_hint="Configure the verified public-record page for this race; no official URL is assumed.")

    if small_race:
        for name in [
            "NYC Board of Elections candidate list check",
            "Local party endorsement page check",
            "Community newspaper candidate guide check",
            "Tenant union endorsement check",
            "Labor union endorsement check",
            "Good-government group questionnaire check",
            "Local civic association forum check",
        ]:
            _add(monitors, seen, name=name, monitor_type="manual", source_type="public_record",
                 category="endorsement_or_election_board", required_terms=required,
                 relevance_hint="Sparse races often surface through election boards, endorsement groups, questionnaires, and forums; add verified URLs manually.")

    for name in ["City council agenda check", "County commission agenda check"]:
        _add(monitors, seen, name=name, monitor_type="manual", source_type="public_record",
             category="local_government", required_terms=[x for x in [location, district] if x],
             relevance_hint="Add verified local agenda URLs for recurring manual review.")
    if any("school" in p.lower() or "education" in p.lower() for p in priorities):
        _add(monitors, seen, name="School board agenda check", monitor_type="manual",
             source_type="public_record", category="local_government",
             relevance_hint="Add the verified school board agenda page if education is a campaign priority.")
    if any("job" in p.lower() or "economy" in p.lower() or "economic" in p.lower() for p in priorities):
        _add(monitors, seen, name="Economic development and layoffs check", monitor_type="manual",
             source_type="public_record", category="local_government",
             relevance_hint="Track local employer layoffs, WARN notices, and economic development agendas.")

    return monitors
