"""Derive a canonical social *platform* for a SourceItem from its URL/name.

Why this exists
---------------
`source_items.source_type` does NOT track platform. The RSS ingestion path
(ingestion.py) stamps every feed-delivered item `source_type="news"` (or
"reference") regardless of the feed's configured type, so Twitter (via Nitter
RSS), YouTube, and Reddit-via-RSS all land inside "news". That made social
content look like <1% of the corpus when, classified by where the post
actually lives, it is several times that.

This module computes a separate, orthogonal `platform` tag from the item's
URL (primary signal) and source_name (fallback). It is intentionally
independent of `source_type` and of the muddled feed configs — several feeds
are mislabeled (e.g. a "Google News: Bresnahan" search feed configured as
`opponent_statement` actually returns general news). The URL is the ground
truth for *which platform a post is on*.

Returns None for plain news / web articles — the column is meaningful only as
"the social platform this item originated on, if any".

Vocabulary (item-level, deliberately coarser than the discovery service's
sub_platform vocab — we collapse reddit_subreddit/reddit_user -> "reddit"):
    twitter | bluesky | reddit | youtube | mastodon | facebook | instagram
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

# Canonical platform constants.
TWITTER = "twitter"
BLUESKY = "bluesky"
REDDIT = "reddit"
YOUTUBE = "youtube"
MASTODON = "mastodon"
FACEBOOK = "facebook"
INSTAGRAM = "instagram"

ALL_PLATFORMS = (
    TWITTER, BLUESKY, REDDIT, YOUTUBE, MASTODON, FACEBOOK, INSTAGRAM,
)

# Known Mastodon instance hosts seen in this corpus + common large instances.
# Mastodon is federated across thousands of hosts, so host-matching can never
# be exhaustive — the source_name "Mastodon" marker (set by mastodon_ingest)
# is the reliable fallback for our own ingestion. This list catches Mastodon
# posts that arrive with a real instance URL but no "Mastodon" name marker.
_MASTODON_HOSTS = frozenset({
    "mastodon.social", "mas.to", "mastodon.world", "mastodon.online",
    "journa.host", "c.im", "mstdn.social", "fosstodon.org", "infosec.exchange",
    "techhub.social", "newsie.social", "masto.ai", "universeodon.com",
    "hachyderm.io", "mstdn.party", "mastodon.green", "mvmt.social",
})


def _host(url: str | None) -> str:
    """Lower-cased hostname for a URL, with a leading 'www.' stripped. ''
    when unparseable. Tolerates bare hosts and odd inputs."""
    if not url:
        return ""
    try:
        netloc = urlparse(url.strip()).netloc.lower()
    except Exception:
        return ""
    if not netloc:
        # Maybe a bare host or path-only string slipped through.
        m = re.match(r"^[a-z0-9.-]+\.[a-z]{2,}", url.strip().lower())
        netloc = m.group(0) if m else ""
    netloc = netloc.split("@")[-1].split(":")[0]  # drop userinfo + port
    return netloc[4:] if netloc.startswith("www.") else netloc


def _host_matches(host: str, domain: str) -> bool:
    """True if host == domain or host is a subdomain of domain."""
    return host == domain or host.endswith("." + domain)


def derive_platform(source_url: str | None, source_name: str | None = None) -> str | None:
    """Return the canonical platform for an item, or None for news/web.

    Precedence: URL host (ground truth for where the post lives) first, then
    source_name markers. URL-first is what makes bridged Bluesky posts —
    which arrive via a Mastodon hashtag timeline but live at bsky.brid.gy /
    bsky.app — classify as `bluesky` rather than `mastodon`.
    """
    url_lower = (source_url or "").lower()
    host = _host(source_url)

    # Bridged Bluesky: brid.gy wraps the origin URL in the path, e.g.
    # https://fed.brid.gy/r/https://bsky.app/profile/<did>/post/<rkey>. The
    # embedded "bsky.app" is the ground-truth origin regardless of bridge
    # direction, so a substring check is more reliable than host-matching the
    # bridge. Checked first so these attribute to Bluesky, not Mastodon (their
    # source_name says "Mastodon … via mastodon.social" — the discovery
    # channel, not the platform).
    if "bsky.app" in url_lower:
        return BLUESKY

    # --- URL host: highest-confidence signals --------------------------------
    if host:
        if (
            _host_matches(host, "bsky.app")
            or _host_matches(host, "bsky.social")
            or host.endswith(".bsky.network")
        ):
            return BLUESKY
        # Twitter/X, including any Nitter mirror (nitter.net, nitter.poast.org,
        # twiiit.com, …). Nitter is how this project actually ingests X.
        if (
            _host_matches(host, "twitter.com")
            or _host_matches(host, "x.com")
            or "nitter" in host
            or host == "twiiit.com"
        ):
            return TWITTER
        if _host_matches(host, "reddit.com") or _host_matches(host, "redd.it"):
            return REDDIT
        if (
            _host_matches(host, "youtube.com")
            or _host_matches(host, "youtu.be")
            or _host_matches(host, "youtube-nocookie.com")
        ):
            return YOUTUBE
        if _host_matches(host, "facebook.com") or _host_matches(host, "fb.com") or _host_matches(host, "fb.watch"):
            return FACEBOOK
        if _host_matches(host, "instagram.com"):
            return INSTAGRAM
        if host in _MASTODON_HOSTS:
            return MASTODON

    # --- source_name markers: fallback when the URL is ambiguous/absent ------
    name = (source_name or "").lower()
    if name:
        # "X/Twitter profile", "X/Twitter (@handle)"
        if "x/twitter" in name or "twitter" in name:
            return TWITTER
        if "bluesky" in name:
            return BLUESKY
        if "reddit" in name:
            return REDDIT
        if "mastodon" in name:
            return MASTODON
        if "youtube" in name:
            return YOUTUBE
        if "instagram" in name:
            return INSTAGRAM
        if "facebook" in name:
            return FACEBOOK

    return None
