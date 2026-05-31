"""Mastodon ingestion via public APIs — no credentials required.

Mastodon is a federated Twitter-like network on the ActivityPub protocol.
Many journalists, academics, and political-junkie types migrated there
after the Musk Twitter buyout. Each "instance" runs independently but
public timelines + hashtags + per-user feeds are openable via free
unauthenticated HTTP APIs.

Two ingestion modes:

  1. Hashtag polling. For each campaign-relevant hashtag, hit
     {instance}/api/v1/timelines/tag/{tag} on a small list of high-signal
     instances. Catches public discussion of e.g. #PA08, #PApolitics.

  2. Per-user polling (future). Mastodon also exposes account timelines
     at {instance}/api/v1/accounts/{id}/statuses for journalists we
     identify. Not in v1 — handle the broader signal first.

Volume: each instance + each tag returns up to ~40 posts/req. At ~5
instances × ~5 tags = 25 reqs per run, each returning 0-40 posts. After
dedup against existing source_items by URL, expect ~0-50 new posts/day
for a small race.

Configurable via env:
  MASTODON_INSTANCES — comma-separated host list (default below)
  MASTODON_EXTRA_TAGS — comma-separated extra hashtags
  MASTODON_ENABLED   — set to "false" to disable
"""
from __future__ import annotations
import logging
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# Default instance list. Picked for political-conversation density:
#  - mastodon.social: biggest general-purpose instance
#  - journa.host: journalists specifically
#  - mastodon.world: another large general instance, EU-leaning
#  - mas.to: another large general US-leaning instance
#  - newsie.social: news/journalism focused
_DEFAULT_INSTANCES = [
    "mastodon.social",
    "journa.host",
    "mastodon.world",
    "mas.to",
    "newsie.social",
]

# Per-instance / per-tag fetch limit. The API caps at 40 anyway.
_LIMIT = 40
_REQUEST_DELAY = 1.0  # courtesy delay between requests

_HEADERS = {
    "User-Agent": "CampaignWarRoom/1.0 (political monitoring)",
    "Accept": "application/json",
}


@dataclass
class MastodonIngestResult:
    instances_polled: int
    tags_polled: int
    posts_found: int
    added: int
    skipped: int
    errors: int


def _strip_html(s: str) -> str:
    """Mastodon stores statuses as HTML. Strip tags for our raw_text column.
    Not perfect — won't render emoji shortcodes — but good enough."""
    if not s:
        return ""
    # Convert common block-level tags to newlines so paragraph breaks
    # survive the strip.
    s = re.sub(r"</?(p|br|div|li)[^>]*>", "\n", s, flags=re.IGNORECASE)
    # Strip remaining tags.
    s = re.sub(r"<[^>]+>", "", s)
    # Decode the common HTML entities Mastodon uses.
    import html as _html
    return _html.unescape(s).strip()


def _campaign_hashtags(db) -> list[str]:
    """Derive a small set of hashtags to monitor from the campaign config.

    Heuristic — Mastodon doesn't have a discovery API for "what's the
    canonical tag for this race", so we generate plausible ones:
      - District code without the dash ("PA08")
      - Last names of candidate and opponents
      - State + politics combination ("PApolitics")
      - A small set of issue tags from the campaign priorities (if any)
    """
    from app.models import CampaignConfig, Opponent
    tags: list[str] = []

    config = db.query(CampaignConfig).first()
    if config:
        if config.district:
            # "PA-08" → "PA08". District code is race-specific so this is
            # the highest-signal tag.
            tags.append(config.district.replace("-", ""))
        # Deliberately NOT generating bare-state or state-politics tags.
        # Field test on PA-08: #PA produced 121 posts of pure noise
        # (Philadelphia bot reposts) and #PApolitics 42 posts of which 0
        # were race-relevant. The cost (LLM scoring per post) is real;
        # the signal is zero. Keep only race-specific tags.
        if config.candidate_name:
            # Last-name token
            tokens = re.sub(r"[^a-zA-Z\s]", " ", config.candidate_name).split()
            if tokens:
                last = max(tokens, key=len)
                if len(last) >= 4:
                    tags.append(last)

    for opp in db.query(Opponent).all():
        if not opp.name:
            continue
        tokens = re.sub(r"[^a-zA-Z\s]", " ", opp.name).split()
        if tokens:
            last = max(tokens, key=len)
            if len(last) >= 4 and last not in tags:
                tags.append(last)

    # Extra tags from env var (use sparingly — each tag × each instance
    # costs a request).
    extra = os.getenv("MASTODON_EXTRA_TAGS", "")
    for t in extra.split(","):
        t = t.strip().lstrip("#")
        if t and t not in tags:
            tags.append(t)

    # Mastodon hashtags are case-INSENSITIVE in the API but typically lowercased
    # in URLs. We pass through as-is; the server normalizes.
    return tags


def _fetch_tag_timeline(instance: str, tag: str, limit: int = _LIMIT) -> list[dict]:
    """One GET against an instance's hashtag timeline endpoint."""
    url = f"https://{instance}/api/v1/timelines/tag/{tag}"
    params = {"limit": str(limit)}
    try:
        resp = httpx.get(url, params=params, headers=_HEADERS, timeout=15, follow_redirects=True)
        if resp.status_code == 429:
            logger.warning("Mastodon: rate limited on %s — backing off 60s", instance)
            time.sleep(60)
            return []
        if resp.status_code != 200:
            logger.debug("Mastodon: %s/%s returned %d", instance, tag, resp.status_code)
            return []
        data = resp.json()
        return data if isinstance(data, list) else []
    except Exception as exc:
        logger.debug("Mastodon: request failed for %s/%s: %s", instance, tag, exc)
        return []


def _status_to_fields(status: dict, instance: str, tag: str) -> Optional[dict]:
    """Convert a Mastodon status dict to SourceItem fields."""
    url = status.get("url") or status.get("uri")
    if not url:
        return None

    # Drop reblogs/boosts; they duplicate the original post URL anyway.
    if status.get("reblog"):
        return None

    content_html = status.get("content") or ""
    text = _strip_html(content_html)
    if not text:
        return None

    account = status.get("account") or {}
    display = account.get("display_name") or account.get("username") or "Mastodon user"
    acct = account.get("acct") or account.get("username") or ""

    # Parse createdAt (ISO 8601 with Z).
    created_str = status.get("created_at") or ""
    published_at: Optional[datetime] = None
    try:
        published_at = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
        if published_at.tzinfo is not None:
            published_at = published_at.replace(tzinfo=None)
    except Exception:
        pass

    first_line = text.split("\n", 1)[0]
    title = first_line[:120].strip()
    if len(text) > 120:
        title = title.rstrip() + "…"
    if not title:
        title = f"Mastodon post by {display}"
    from app.services.ingestion import clean_title as _clean_title
    title = _clean_title(title) or f"Mastodon post by {display}"

    return {
        "title": title,
        "raw_text": text,
        "source_url": url,
        "source_name": f"Mastodon #{tag} via {instance}",
        "source_type": "social",
        "source_author": acct or None,
        "published_at": published_at,
    }


def _campaign_keywords_for_filter(db) -> list[str]:
    """Lowercased substrings used to gate posts BEFORE we spend an LLM
    scoring call on them. A post that survives the hashtag fetch but
    doesn't mention any of these strings in its text is almost certainly
    not about this race. Mirrors the bluesky_firehose keyword set logic.

    Uses LAST-NAME + full-name only — first names are too common to be
    distinctive (would match Paige Spiranac, Page magazine, etc.).
    """
    from app.models import CampaignConfig, Opponent

    def _last_name_token(full: str) -> str | None:
        toks = re.sub(r"[^a-zA-Z\s]", " ", full).split()
        return toks[-1].lower() if toks and len(toks[-1]) >= 4 else None

    kws: list[str] = []
    config = db.query(CampaignConfig).first()
    if config:
        if config.candidate_name:
            kws.append(config.candidate_name.lower())
            last = _last_name_token(config.candidate_name)
            if last:
                kws.append(last)
        if config.district:
            kws.append(config.district.lower())
            kws.append(config.district.lower().replace("-", ""))
    for opp in db.query(Opponent).all():
        if opp.name:
            kws.append(opp.name.lower())
            last = _last_name_token(opp.name)
            if last:
                kws.append(last)
    BLOCK = {"the", "for", "and", "of", "or", "rep", "sen", "dr"}
    return [k for k in kws if k and k not in BLOCK]


def ingest_mastodon(db) -> MastodonIngestResult:
    """Poll a small set of Mastodon instances for campaign-relevant hashtags."""
    from app.models import SourceItem
    from app.services.ingestion import ingest_text

    if os.getenv("MASTODON_ENABLED", "true").lower() == "false":
        logger.info("Mastodon: ingest disabled via MASTODON_ENABLED=false")
        return MastodonIngestResult(0, 0, 0, 0, 0, 0)

    instances = [
        h.strip()
        for h in os.getenv("MASTODON_INSTANCES", ",".join(_DEFAULT_INSTANCES)).split(",")
        if h.strip()
    ]
    tags = _campaign_hashtags(db)
    if not tags:
        logger.info("Mastodon: no hashtags derivable from campaign config")
        return MastodonIngestResult(len(instances), 0, 0, 0, 0, 0)

    # Pre-LLM filter: only ingest posts whose TEXT mentions a race-specific
    # keyword. Field test showed even #PA08-tagged posts include unrelated
    # content (other PA-08 districts in other states, generic state news);
    # filtering by name/district before the LLM scoring call cuts wasted
    # token spend dramatically.
    race_keywords = _campaign_keywords_for_filter(db)

    posts_found = added = skipped = filtered_out = errors = 0

    for instance in instances:
        for tag in tags:
            statuses = _fetch_tag_timeline(instance, tag, _LIMIT)
            for s in statuses:
                fields = _status_to_fields(s, instance, tag)
                if not fields:
                    continue
                posts_found += 1
                # Pre-LLM keyword gate
                text_lower = (fields.get("raw_text") or "").lower()
                if race_keywords and not any(k in text_lower for k in race_keywords):
                    filtered_out += 1
                    continue
                url = fields["source_url"]
                if db.query(SourceItem).filter_by(source_url=url).first():
                    skipped += 1
                    continue
                try:
                    ingest_text(db, **fields)
                    added += 1
                except Exception as exc:
                    logger.debug("Mastodon: ingest_text failed for %s: %s", url, exc)
                    errors += 1
            time.sleep(_REQUEST_DELAY)

    logger.info(
        "Mastodon ingest: instances=%d tags=%d found=%d filtered_out=%d added=%d skipped=%d errors=%d",
        len(instances), len(tags), posts_found, filtered_out, added, skipped, errors,
    )
    return MastodonIngestResult(
        instances_polled=len(instances),
        tags_polled=len(tags),
        posts_found=posts_found,
        added=added,
        skipped=skipped,
        errors=errors,
    )
