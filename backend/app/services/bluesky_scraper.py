"""Bluesky profile monitoring via the public AT Protocol API.

Unlike Twitter/X, Bluesky has a stable public API that requires no API key
for read operations. We poll each `bluesky_profile` monitor's author feed
every 15 minutes and ingest new posts as SourceItems with source_type="social",
deduplicated by their canonical bsky.app URL.

Handles look like `someone.bsky.social` (the default for new accounts) or any
custom domain the account owner controls. The `query` field on the monitor row
stores the bare handle, mirroring how twitter_scraper stores the Twitter handle.

Auto-discovery: `lookup_bluesky_handles(name, role_hint)` uses the same LLM
fallback path as Twitter discovery, then verifies every proposed handle against
the live API before returning it — no hallucinated handles get persisted.
"""
from __future__ import annotations

import json as _json
import logging
import re
from datetime import datetime
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

PUBLIC_API = "https://public.api.bsky.app"
USER_AGENT = "CampaignWarRoom/1.0 (political monitoring)"

# Number of posts to fetch per poll. Bluesky's getAuthorFeed default is 50 but
# 30 is plenty for a 15-minute cadence and keeps responses small.
DEFAULT_LIMIT = 30

# Handle pattern: domain-like, optional dot-separated segments.
_HANDLE_RE = re.compile(r"^[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)+$")


def extract_bluesky_handle(value: str | None) -> str | None:
    """Parse a Bluesky handle out of a URL, @handle, or bare handle.

    Accepted forms:
      • someone.bsky.social
      • @someone.bsky.social
      • https://bsky.app/profile/someone.bsky.social
      • https://bsky.app/profile/jane.example.com  (custom domain handle)

    Returns the bare handle, or None if unparseable.
    """
    if not value:
        return None
    value = value.strip().lstrip("@")
    m = re.search(r"bsky\.app/profile/([A-Za-z0-9._-]+)", value)
    if m:
        candidate = m.group(1)
    else:
        candidate = value
    if _HANDLE_RE.fullmatch(candidate):
        return candidate
    return None


def verify_bluesky_handle(handle: str) -> bool:
    """Confirm a handle exists by fetching its profile. Returns True on 200."""
    try:
        r = httpx.get(
            f"{PUBLIC_API}/xrpc/app.bsky.actor.getProfile",
            params={"actor": handle},
            headers={"User-Agent": USER_AGENT},
            timeout=8,
        )
        return r.status_code == 200
    except Exception:
        return False


def fetch_recent_posts(handle: str, limit: int = DEFAULT_LIMIT) -> list[dict]:
    """Fetch the latest posts for a handle. Returns the raw `feed` array or []."""
    try:
        r = httpx.get(
            f"{PUBLIC_API}/xrpc/app.bsky.feed.getAuthorFeed",
            params={"actor": handle, "limit": str(limit)},
            headers={"User-Agent": USER_AGENT},
            timeout=15,
        )
        r.raise_for_status()
        return r.json().get("feed") or []
    except Exception as exc:
        logger.warning("bluesky: fetch failed for @%s: %s", handle, exc)
        return []


def _post_envelope_to_dict(envelope: dict, handle: str) -> Optional[dict]:
    """Convert a feed item to fields ready for SourceItem creation, or None
    if it's not ingestable (no text, reposts, etc.)."""
    post = envelope.get("post") or {}
    record = post.get("record") or {}
    text = (record.get("text") or "").strip()
    if not text:
        return None

    # Skip pure reposts/quotes that don't add new author text.
    if envelope.get("reason"):
        return None

    # Canonical URL: bsky.app/profile/{handle}/post/{rkey from at-uri}
    uri = post.get("uri", "")  # at://did:.../app.bsky.feed.post/{rkey}
    rkey = uri.split("/")[-1] if uri else None
    if not rkey:
        return None
    source_url = f"https://bsky.app/profile/{handle}/post/{rkey}"

    # Parse createdAt (Bluesky uses ISO 8601 with trailing Z).
    published_at: Optional[datetime] = None
    created_at_str = record.get("createdAt") or ""
    try:
        published_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
        if published_at.tzinfo is not None:
            published_at = published_at.replace(tzinfo=None)
    except Exception:
        pass

    # Title = first line / first ~120 chars.
    first_line = text.split("\n", 1)[0]
    title = first_line[:120].strip()
    if len(text) > 120:
        title = title.rstrip() + "…"
    if not title:
        title = f"Bluesky post by @{handle}"

    author = post.get("author") or {}
    display = author.get("displayName") or handle

    return {
        "title": title,
        "raw_text": text,
        "source_url": source_url,
        "source_name": f"{display} (Bluesky)",
        "source_type": "social",
        "published_at": published_at,
    }


def poll_bluesky_monitor(db, monitor) -> dict:
    """Fetch new posts for one bluesky_profile monitor and ingest them.

    Returns a result dict with handle/fetched/added/skipped counts.
    """
    from app.models import SourceItem

    handle = extract_bluesky_handle(monitor.query or monitor.url)
    if not handle:
        return {"error": f"no parseable handle on monitor {monitor.id}"}

    posts = fetch_recent_posts(handle)
    if not posts:
        return {"handle": handle, "fetched": 0, "added": 0, "skipped": 0}

    added = skipped = failed = 0
    for envelope in posts:
        fields = _post_envelope_to_dict(envelope, handle)
        if not fields:
            continue
        # URL-level dedup.
        if db.query(SourceItem.id).filter_by(source_url=fields["source_url"]).first():
            skipped += 1
            continue
        try:
            item = SourceItem(**fields)
            db.add(item)
            db.flush()
            # Cluster + outlet link — same downstream as RSS/GDELT ingestion.
            from app.services import story_clustering
            story_clustering.assign_story_cluster_v2(db, item)
            from app.services.outlet_linking import build_outlet_index, link_outlet_to_item
            link_outlet_to_item(item, build_outlet_index(db))
            db.commit()
            added += 1
        except Exception as exc:
            db.rollback()
            failed += 1
            logger.warning("bluesky: ingest failed for %s: %s", fields["source_url"], exc)

    return {
        "handle": handle, "fetched": len(posts),
        "added": added, "skipped": skipped, "failed": failed,
    }


def poll_all_bluesky_monitors(db) -> dict:
    """Poll every active bluesky_profile monitor. Called by the scheduler."""
    from app.models import SourceMonitor

    monitors = (
        db.query(SourceMonitor)
        .filter(
            SourceMonitor.monitor_type == "bluesky_profile",
            SourceMonitor.active == True,  # noqa: E712
        )
        .all()
    )
    if not monitors:
        return {"monitors": 0, "added": 0, "skipped": 0}

    total_added = total_skipped = total_failed = 0
    for m in monitors:
        try:
            r = poll_bluesky_monitor(db, m)
            total_added += r.get("added", 0)
            total_skipped += r.get("skipped", 0)
            total_failed += r.get("failed", 0)
        except Exception:
            logger.exception("bluesky: monitor %s failed", m.id)

    return {
        "monitors": len(monitors), "added": total_added,
        "skipped": total_skipped, "failed": total_failed,
    }


def lookup_bluesky_handles(name: str, role_hint: str = "") -> list[str]:
    """LLM-driven discovery of a person's Bluesky handle(s), with live verification.

    Returns a list of bare handles (e.g. "jane.bsky.social"). Every handle is
    verified against the Bluesky public API before being returned — no
    hallucinated handles slip through.
    """
    from app.services.llm_provider import get_provider, MockLLMProvider

    try:
        provider = get_provider()
        if isinstance(provider, MockLLMProvider):
            return []
        context = f" ({role_hint})" if role_hint else ""
        prompt = (
            f"Does {name}{context} have a Bluesky account? Bluesky handles look "
            f"like 'username.bsky.social' or a custom domain like 'jane.example.com'. "
            f"Reply with ONLY a JSON array of handles (no @ prefix). "
            f"If unknown or no Bluesky presence, reply with exactly: []"
        )
        raw = (provider.complete(prompt) or "").strip()
        if not raw or raw == "[]":
            return []

        try:
            parsed = _json.loads(raw)
            if not isinstance(parsed, list):
                return []
            candidates = [extract_bluesky_handle(h) for h in parsed if isinstance(h, str)]
        except Exception:
            return []

        verified = []
        for h in candidates:
            if h and verify_bluesky_handle(h):
                verified.append(h)
                logger.info("bluesky: verified handle %s for %r", h, name)
        return verified
    except Exception as exc:
        logger.warning("lookup_bluesky_handles: failed for %r: %s", name, exc)
        return []


def ensure_bluesky_monitor(db, *, name: str, handle: str,
                           source_type: str = "social",
                           category: str = "social") -> Optional[int]:
    """Idempotently create a bluesky_profile monitor for the given handle.
    Returns the monitor id, or None if a monitor for this handle already exists.
    """
    from app.models import SourceMonitor

    existing = (
        db.query(SourceMonitor)
        .filter(
            SourceMonitor.monitor_type == "bluesky_profile",
            SourceMonitor.query == handle,
        )
        .first()
    )
    if existing:
        return None

    monitor = SourceMonitor(
        name=f"{name} Bluesky (@{handle})",
        monitor_type="bluesky_profile",
        query=handle,
        category=category,
        source_type=source_type,
        active=True,
    )
    db.add(monitor)
    db.flush()
    return monitor.id
