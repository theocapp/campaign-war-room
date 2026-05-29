"""Third-party account discovery — find external accounts/pages that
*talk about* a candidate's race, across multiple platforms.

This is the broader sibling of `social_handle_discovery.py`. That one
answers "what handles does this person have?". This one answers "who
else is posting about this race?" — county GOP committees, PACs, local
journalists, statewide subreddits, opposition orgs, news outlets'
social accounts, supporter pages, watchdog groups.

Approach is the same web-search engine — no platform APIs — so the
discovery half is unblocked even when ingestion (IG/FB) is paused.
Confirmed candidate/opponent handles are excluded so the candidate's
own accounts don't show up as "third party."

Platforms covered:
  - Instagram (third-party profiles)
  - Facebook (third-party pages)
  - Bluesky (handles)
  - Reddit subreddits AND users
  - YouTube channels

Twitter/X is skipped intentionally — the search-result URLs are noisy
(individual tweets dominate) and the platform has been progressively
locking down public access, so confidence on extracted "accounts" is
low. Bluesky is the better signal for this kind of discovery.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse, unquote

from app.services.search_provider import get_search_provider, SearchResult
from app.services.social_handle_discovery import (
    _instagram_handle_from_url,
    _facebook_page_from_url,
    _name_tokens,
)

logger = logging.getLogger(__name__)


# ── Platform-specific URL extractors ──────────────────────────────────────────

_BLUESKY_HANDLE_RE = re.compile(r"^[a-z0-9.-]+$", re.IGNORECASE)
_REDDIT_SUBREDDIT_RE = re.compile(r"^[A-Za-z0-9_]{2,21}$")
_REDDIT_USER_RE = re.compile(r"^[A-Za-z0-9_-]{2,20}$")
_YOUTUBE_HANDLE_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# Reddit URL paths that are NOT a subreddit/user identifier.
_REDDIT_NON_ENTITY_SEGMENTS = {
    "about", "wiki", "rules", "submit", "settings", "comments",
    "message", "search", "login", "signup", "help",
}


def _bluesky_handle_from_url(url: str) -> str | None:
    """Pull the handle out of a bsky.app URL like /profile/{handle}."""
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    host = parsed.netloc.lower().lstrip("www.")
    if "bsky.app" not in host:
        return None
    segments = [s for s in parsed.path.split("/") if s]
    if len(segments) < 2 or segments[0].lower() != "profile":
        return None
    handle = unquote(segments[1])
    if not _BLUESKY_HANDLE_RE.match(handle):
        return None
    return handle


def _reddit_subreddit_from_url(url: str) -> str | None:
    """Pull the subreddit name out of a reddit.com URL like /r/{name}."""
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    host = parsed.netloc.lower().lstrip("www.")
    if "reddit.com" not in host:
        return None
    segments = [s for s in parsed.path.split("/") if s]
    if len(segments) < 2 or segments[0].lower() != "r":
        return None
    name = unquote(segments[1])
    if name.lower() in _REDDIT_NON_ENTITY_SEGMENTS:
        return None
    if not _REDDIT_SUBREDDIT_RE.match(name):
        return None
    return name


def _reddit_user_from_url(url: str) -> str | None:
    """Pull the username out of a reddit.com URL like /u/{name} or /user/{name}."""
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    host = parsed.netloc.lower().lstrip("www.")
    if "reddit.com" not in host:
        return None
    segments = [s for s in parsed.path.split("/") if s]
    if len(segments) < 2 or segments[0].lower() not in ("u", "user"):
        return None
    name = unquote(segments[1])
    if name.lower() in _REDDIT_NON_ENTITY_SEGMENTS:
        return None
    if not _REDDIT_USER_RE.match(name):
        return None
    return name


def _youtube_channel_identifier_from_url(url: str) -> tuple[str, str] | None:
    """Pull a YouTube channel identifier from a URL.

    Returns (kind, value) where kind is one of:
      - "channel_id"  → value is the raw UCxxxx ID (RSS-ready)
      - "handle"      → value is the @handle form (needs lookup to get
                        channel_id before we can build RSS)
      - "name"        → value is the legacy /c/{name} or /user/{name}
                        form (also needs lookup)

    Returns None for URLs that point at a video, search, or non-channel
    page.
    """
    try:
        parsed = urlparse(url)
    except Exception:
        return None
    host = parsed.netloc.lower().lstrip("www.")
    if "youtube.com" not in host and "youtu.be" not in host:
        return None
    segments = [s for s in parsed.path.split("/") if s]
    if not segments:
        return None
    first = segments[0]
    if first.lower() == "channel" and len(segments) >= 2:
        cid = segments[1]
        if cid.startswith("UC") and len(cid) > 20:
            return ("channel_id", cid)
    if first.lower() == "c" and len(segments) >= 2:
        return ("name", unquote(segments[1]))
    if first.lower() == "user" and len(segments) >= 2:
        return ("name", unquote(segments[1]))
    if first.startswith("@") and len(first) > 1:
        handle = first[1:]
        if _YOUTUBE_HANDLE_RE.match(handle):
            return ("handle", handle)
    return None


# ── Role inference (location-aware keyword heuristic) ────────────────────────
#
# Roles are evaluated in priority order — first match wins. Two changes vs
# the first cut:
#   1. Order is intentional. "news" comes before "committee" because the
#      snippet "Washington Examiner's post about the committee" should
#      classify the account as news, not committee.
#   2. Some roles are "strong-only": they only fire when the keyword hits
#      in the IDENTIFIER (handle/slug) or DISPLAY NAME, not just any
#      snippet. This prevents quoted article titles from miscategorizing
#      accounts ("Paige Against the Machine" was making r/Pennsylvania
#      look like "opposition" in the first cut).

_ROLE_KEYWORDS: list[tuple[str, list[str]]] = [
    ("news",       ["news", "times", "tribune", "herald", "gazette",
                    "examiner", "chronicle", "review", "journal",
                    "wbre", "wnep", "wvia", "wyou", "pocono", "wilkes", "capital-star",
                    "spotlight pa", "the keystone", "roll call", "associated press",
                    " ap ", "reuters", "bloomberg", "politico", "axios"]),
    ("journalist", ["reporter", "journalist", "correspondent",
                    # match @{handle} when the snippet introduces a person as a reporter
                    "@(reporter|journalist|correspondent)"]),
    ("pac",        ["pac", "political action committee", "super pac"]),
    # DCCC and NRCC are the parties' congressional CAMPAIGN COMMITTEES, not
    # unions — keep them here. "committee" alone in a snippet is too loose,
    # so committee is strong-only.
    ("committee",  ["committee", "republican party", "democratic party",
                    " gop ", " dems ", "dnc", "rnc", "dccc", "nrcc",
                    "dscc", "nrsc"]),
    ("union",      ["union", "afl-cio", "uaw", "afscme", "seiu", "teamsters",
                    "uaw local", "iuoe", "ufcw"]),
    ("watchdog",   ["watchdog", "ethics", "anti-corruption", "accountability project"]),
    ("endorser",   ["endorses", "endorsement", "endorse "]),
    # Opposition is strong-only (identifier match required), so substring
    # collisions are vanishingly rare in practice — no real political
    # account is named "stoplight". Allowing bare "stop"/"defeat" lets
    # camelcase identifiers like "StopBresnahan" match.
    ("opposition", ["stop", "defeat", "noto", "against"]),
    ("activist",   ["activist", "advocacy", "movement", "grassroots"]),
]

# Roles that should only fire when the keyword hits in the identifier or the
# display name — NOT just any phrase in the snippet. Snippets contain
# article quotes, headlines, and free text, so loose words like "against"
# or "committee" trigger false positives there.
_STRONG_ONLY_ROLES = {"opposition", "activist", "endorser", "watchdog", "committee"}


def _infer_role(name: str | None, snippet: str | None, identifier: str) -> str:
    """Classify an account by role using a tiered keyword check.

    ONLY the identifier (handle / page slug / subreddit name) is treated
    as the authoritative source for strong-only roles. The "display name"
    we extract from a Tavily title is unreliable — it's often a quoted
    article headline ("Paige Against the Machine"), which used to make
    r/Pennsylvania look like an "opposition" account.

    Loose roles (news, journalist, pac, union) still fire on snippet or
    name matches — those keywords are reliable enough in free text.
    """
    identifier_text = (identifier or "").lower()
    name_text = (name or "").lower()
    snippet_text = (snippet or "").lower()
    loose_haystack = f"{name_text} {snippet_text}"

    for role, patterns in _ROLE_KEYWORDS:
        for pat in patterns:
            if pat.startswith("@(") and pat.endswith(")"):
                regex = pat[2:-1]
                if re.search(regex, identifier_text):
                    return role
                if role not in _STRONG_ONLY_ROLES and re.search(regex, loose_haystack):
                    return role
                continue
            if pat in identifier_text:
                return role
            if role not in _STRONG_ONLY_ROLES and pat in loose_haystack:
                return role
    return "unknown"


# ── Reddit-specific scoring helpers ──────────────────────────────────────────
#
# Reddit search returns lots of noise — mega-general subs (todayilearned,
# AskReddit) often surface because a single post happens to mention the
# candidate, and they're useless for tracking the race. Conversely,
# state/city/local-issue subs are extremely high-signal for political races.
# We boost geo-matching subs and penalize mega-subs at scoring time.

_REDDIT_MEGASUB_DOWNGRADES = {
    "todayilearned", "askreddit", "funny", "pics", "videos", "memes",
    "music", "movies", "gaming", "worldnews", "news",
    "samegrassbutgreener", "showerthoughts", "mildlyinteresting",
    "explainlikeimfive", "lifeprotips", "wholesomememes",
    "aww", "art", "books", "food", "personalfinance",
}


def _geo_tokens_for_reddit(location: str | None, district: str | None) -> set[str]:
    """Tokens that, if found in a subreddit name, signal it's a geography
    or race-related community (state, city, district). Cheap substring
    check downstream.
    """
    tokens: set[str] = set()
    for source in (location or "", district or ""):
        for tok in re.split(r"[^a-z0-9]+", source.lower()):
            if len(tok) >= 4:
                tokens.add(tok)
    return tokens


# ── Discovered account dataclass + scoring ───────────────────────────────────

def _platform_display_name(platform: str, identifier: str, title: str | None) -> str | None:
    """Build a clean chip label for an account.

    Per-platform rule:
      - Reddit subreddits / users: `r/{name}` / `u/{name}` — the slug IS
        the natural display label; the Tavily result title is a leaked
        article headline and would render as nonsense in the UI.
      - Bluesky: `@{handle}` — same reasoning.
      - YouTube: bare identifier (already either an @handle, c/name, or
        UCxxxxx ID).
      - Facebook / Instagram: prefer the Tavily title's first segment
        ("WBRE/WYOU 28/22 News") over the page slug ("2822news"), since
        outlet names there are typically descriptive. Falls back to the
        identifier when no title is available.
    """
    if platform == "reddit_subreddit":
        return f"r/{identifier}"
    if platform == "reddit_user":
        return f"u/{identifier}"
    if platform == "bluesky":
        return f"@{identifier}"
    if platform == "youtube":
        return identifier
    # facebook / instagram — title-derived when the title looks like an
    # actual page/outlet name. Tavily often returns the FIRST POST'S TEXT
    # as the result title for FB/IG profile pages (a post that begins
    # with a quote, an emoji, a long sentence, etc.). Those make terrible
    # chip labels, so we fall back to the bare identifier when the title
    # looks sentence-like rather than name-like.
    #
    # Real outlet names ("WBRE/WYOU 28/22 News", "Spotlight PA") are short
    # and free of sentence punctuation. Real post-content snippets contain
    # exclamations / questions / colons / ellipses. So the rule is: keep
    # the title only when it's ≤ 30 chars AND contains no sentence-style
    # punctuation anywhere (not just trailing).
    if title:
        first = title.split(" - ")[0].split(" | ")[0].strip().strip('"\'""''')
        if first and len(first) <= 30 and not any(c in first for c in "!?:…"):
            return first
    return identifier


@dataclass
class DiscoveredAccount:
    platform: str       # "instagram" | "facebook" | "bluesky" | "reddit_subreddit" | "reddit_user" | "youtube"
    identifier: str     # bare handle / page slug / subreddit name / @handle / UCxxxx
    display_name: str | None
    url: str            # canonical URL
    snippet: str | None
    score: float
    confidence: str     # "high" | "medium" | "low"
    inferred_role: str
    matched_queries: list[str] = field(default_factory=list)
    # Which anchors (candidate/opponent names) surfaced this account, in
    # whatever order they appeared. Lets the UI show "via Cognetti",
    # "via Bresnahan", or both pills next to a result so you can see at
    # a glance whose search returned each account — dedup otherwise hides
    # that signal once results from different anchors collapse into the
    # same bucket.
    matched_anchors: list[str] = field(default_factory=list)
    rss_url: str | None = None  # populated when we can build one without further lookup


def _confidence_label(score: float) -> str:
    if score >= 4.0:
        return "high"
    if score >= 2.0:
        return "medium"
    return "low"


def _score_account(
    appearances: int,
    cand_tokens: set[str],
    title: str | None,
    snippet: str | None,
    identifier: str,
    inferred_role: str,
    platform: str,
    geo_tokens: set[str],
) -> float:
    """Heuristic relevance score for a third-party account.

    Generic signal (all platforms):
      +2  appears in multiple search results (across queries)
      +2  inferred role is one we care about (committee/pac/news/etc.)
      +1  result title or snippet mentions the candidate's full name
      -1  identifier looks suspiciously short

    Reddit-specific additions:
      +2  subreddit name overlaps with geography tokens (state/city/district)
      -3  subreddit is on the mega-general-interest downgrade list — those
          accounts post about everything and aren't worth tracking even when
          one post happens to mention the candidate
    """
    score = 0.0
    if appearances >= 2:
        score += 2.0
    if inferred_role != "unknown":
        score += 2.0
    text = " ".join(filter(None, [title or "", snippet or ""])).lower()
    if cand_tokens and text:
        sig = [t for t in cand_tokens if len(t) >= 3]
        if sig and all(t in text for t in sig):
            score += 1.0
    if len(identifier) < 4:
        score -= 1.0

    if platform == "reddit_subreddit":
        sub_lower = identifier.lower()
        if sub_lower in _REDDIT_MEGASUB_DOWNGRADES:
            score -= 3.0
        elif geo_tokens and any(tok in sub_lower for tok in geo_tokens):
            score += 2.0

    return score


# ── RSS URL builders for ingestable platforms ────────────────────────────────

def _bluesky_rss(handle: str) -> str:
    """Bluesky profile RSS. Public, no auth needed."""
    return f"https://bsky.app/profile/{handle}/rss"


def _reddit_subreddit_rss(name: str) -> str:
    return f"https://www.reddit.com/r/{name}/.rss?limit=25"


def _reddit_user_rss(name: str) -> str:
    return f"https://www.reddit.com/user/{name}.rss"


def _youtube_channel_rss_from_id(channel_id: str) -> str:
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


# ── Discovery orchestration ──────────────────────────────────────────────────

# Cap on how many candidate+opponent anchors we'll use in a single discovery
# run. Prevents query-budget runaway on primary-election races with 10+
# candidates while still covering the typical general-election case
# (candidate + 1-3 opponents) without artificial truncation. Tavily's
# free-tier credit per call is the actual constraint; tune this if we move
# to a paid tier with higher headroom.
_MAX_ANCHORS_PER_DISCOVERY = 5


def _build_queries(
    candidate_name: str,
    opponent_names: list[str],
    location: str | None,
    district: str | None,  # unused — kept for back-compat; see note below
) -> list[tuple[str, str]]:
    """Yield (platform_filter_host, query_string) pairs.

    Builds ONE anchor per real actor (candidate + every opponent up to
    `_MAX_ANCHORS_PER_DISCOVERY` total). The district was previously
    appended as an extra anchor, but a bare district like "PA-08" tagged
    onto `site:facebook.com` returns far too much noise (it matches any
    post anywhere on Facebook that uses the district label, including
    ones that have nothing to do with this race). We drop it as an anchor
    and instead append `location` to every query so geography still
    narrows results.

    Returns one (platform, query) tuple per (host × anchor). Caller
    de-dups results by (sub_platform, identifier) so an account that
    surfaces from multiple anchors gets collapsed correctly, with the
    `matched_queries` field preserving which anchor(s) surfaced it.
    """
    anchors: list[str] = []
    if candidate_name and candidate_name.strip():
        anchors.append(f'"{candidate_name.strip()}"')
    # Guard against None opponent_names — internal callers always pass a
    # list, but the function is exported and external callers may not.
    for opp in (opponent_names or []):
        if opp and opp.strip():
            anchors.append(f'"{opp.strip()}"')
        if len(anchors) >= _MAX_ANCHORS_PER_DISCOVERY:
            break

    if not anchors:
        return []

    loc = (location or "").strip()
    hosts = [
        ("instagram",        "instagram.com"),
        ("facebook",         "facebook.com"),
        ("bluesky",          "bsky.app"),
        ("reddit",           "reddit.com"),
        ("youtube",          "youtube.com"),
    ]

    out: list[tuple[str, str]] = []
    for platform, host in hosts:
        for anchor in anchors:
            q = f"{anchor} site:{host}"
            if loc:
                q = f"{q} {loc}"
            out.append((platform, q))
    return out


def _extract_account_from_url(
    platform: str, url: str,
) -> tuple[str, str, str | None, str | None] | None:
    """Per-platform: turn a URL into (platform_sub, identifier, rss_url_or_None, canonical_url_or_None).

    `platform_sub` distinguishes reddit_subreddit vs reddit_user; for
    everything else it equals the input platform. `rss_url` is set when
    we can build one without a second lookup.
    """
    if platform == "instagram":
        h = _instagram_handle_from_url(url)
        if not h:
            return None
        return ("instagram", h, None, f"https://www.instagram.com/{h}")
    if platform == "facebook":
        h = _facebook_page_from_url(url)
        if not h:
            return None
        return ("facebook", h, None, f"https://www.facebook.com/{h}")
    if platform == "bluesky":
        h = _bluesky_handle_from_url(url)
        if not h:
            return None
        return ("bluesky", h, _bluesky_rss(h), f"https://bsky.app/profile/{h}")
    if platform == "reddit":
        sub = _reddit_subreddit_from_url(url)
        if sub:
            return ("reddit_subreddit", sub, _reddit_subreddit_rss(sub),
                    f"https://www.reddit.com/r/{sub}")
        user = _reddit_user_from_url(url)
        if user:
            return ("reddit_user", user, _reddit_user_rss(user),
                    f"https://www.reddit.com/user/{user}")
        return None
    if platform == "youtube":
        ident = _youtube_channel_identifier_from_url(url)
        if not ident:
            return None
        kind, value = ident
        if kind == "channel_id":
            return ("youtube", value, _youtube_channel_rss_from_id(value),
                    f"https://www.youtube.com/channel/{value}")
        # @handle or /c/{name} — keep as discovered, but no RSS yet
        prefix = "@" if kind == "handle" else "c/"
        return ("youtube", f"{prefix}{value}", None,
                f"https://www.youtube.com/{prefix}{value}")
    return None


def discover_third_party_accounts(
    candidate_name: str,
    opponent_names: list[str] | None = None,
    location: str | None = None,
    district: str | None = None,
    exclude: dict[str, set[str]] | None = None,
    limit_per_platform: int = 8,
) -> dict[str, list[DiscoveredAccount]]:
    """Return ranked third-party accounts/pages per platform.

    `exclude` is a dict mapping platform name to a set of identifiers we
    should NOT surface — typically the candidate's and opponents'
    already-confirmed handles, so we don't double-report them as
    "third-party."

    The platform keys in the result are:
      instagram, facebook, bluesky, reddit_subreddit, reddit_user, youtube
    """
    opponent_names = opponent_names or []
    exclude = exclude or {}
    provider = get_search_provider()
    cand_tokens = _name_tokens(candidate_name)
    geo_tokens = _geo_tokens_for_reddit(location, district)

    # Map full query string → simple anchor label (just the bare name) so
    # we can show "via Cognetti" / "via Bresnahan" pills in the UI without
    # parsing query strings on the frontend. Anchor order in the list is
    # candidate first, then opponents.
    anchor_for_query: dict[str, str] = {}
    anchor_names: list[str] = []
    if candidate_name and candidate_name.strip():
        anchor_names.append(candidate_name.strip())
    for opp in opponent_names:
        if opp and opp.strip() and len(anchor_names) < _MAX_ANCHORS_PER_DISCOVERY:
            anchor_names.append(opp.strip())

    # Bucket by (platform_sub, identifier) so we can aggregate appearances
    # across queries.
    accounts: dict[tuple[str, str], dict] = {}

    queries = _build_queries(candidate_name, opponent_names, location, district)
    for platform, query in queries:
        # Map this query back to its anchor by checking which name appears
        # as a quoted literal at the start. Cheaper than threading the anchor
        # through _build_queries' return tuple.
        anchor_label = None
        for name in anchor_names:
            if f'"{name}"' in query:
                anchor_label = name
                break
        if anchor_label and query not in anchor_for_query:
            anchor_for_query[query] = anchor_label

        response = provider.search(query, limit=12)
        if response.message:
            logger.info("third-party discovery %s: %s", platform, response.message)
        for r in response.results:
            extracted = _extract_account_from_url(platform, r.url)
            if not extracted:
                continue
            sub_platform, identifier, rss_url, canonical = extracted
            if identifier in exclude.get(sub_platform, set()):
                continue
            key = (sub_platform, identifier)
            bucket = accounts.setdefault(key, {
                "results": [], "rss_url": rss_url, "canonical": canonical,
                "queries": set(), "anchors": set(),
            })
            bucket["results"].append(r)
            bucket["queries"].add(query)
            if anchor_label:
                bucket["anchors"].add(anchor_label)

    # Convert to ranked DiscoveredAccount list per platform.
    by_platform: dict[str, list[DiscoveredAccount]] = {
        "instagram": [], "facebook": [], "bluesky": [],
        "reddit_subreddit": [], "reddit_user": [], "youtube": [],
    }
    for (sub_platform, identifier), bucket in accounts.items():
        best_result: SearchResult = bucket["results"][0]
        snippet = best_result.snippet or best_result.title
        display_name = _platform_display_name(sub_platform, identifier, best_result.title)
        role = _infer_role(display_name, snippet, identifier)
        score = _score_account(
            appearances=len(bucket["results"]),
            cand_tokens=cand_tokens,
            title=best_result.title,
            snippet=snippet,
            identifier=identifier,
            inferred_role=role,
            platform=sub_platform,
            geo_tokens=geo_tokens,
        )
        # Sort matched_anchors so candidate-first ordering is preserved.
        matched_anchors_sorted = [a for a in anchor_names if a in bucket["anchors"]]
        acct = DiscoveredAccount(
            platform=sub_platform,
            identifier=identifier,
            display_name=display_name,
            url=bucket["canonical"] or best_result.url,
            snippet=snippet,
            score=score,
            confidence=_confidence_label(score),
            inferred_role=role,
            matched_queries=sorted(bucket["queries"]),
            matched_anchors=matched_anchors_sorted,
            rss_url=bucket["rss_url"],
        )
        by_platform.setdefault(sub_platform, []).append(acct)

    for platform in by_platform:
        by_platform[platform].sort(key=lambda a: a.score, reverse=True)
        by_platform[platform] = by_platform[platform][:limit_per_platform]
    return by_platform
