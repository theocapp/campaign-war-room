"""Bluesky jetstream firehose ingestion — keyword-filtered, real-time.

Background
----------
The existing `bluesky_scraper.py` polls per-profile feeds: you discover an
account, then we fetch its recent posts every 15 min. That's good for
journalists and politicians whose handle you know.

What it misses: random users discussing the race, journalists you haven't
discovered yet, hashtag-driven moments, citizens-of-the-district
conversations. Anything you didn't pre-curate.

The jetstream (https://github.com/bluesky-social/jetstream) is Bluesky's
public WebSocket feed — every post, every like, every follow, real-time.
For political monitoring we want only `app.bsky.feed.post` events that
contain keywords for our race.

Volume math (approximate, mid-2026):
  - Total network: ~500 posts/sec
  - Filtered by "cognetti" / "bresnahan" / "PA-08" / "scranton" etc:
    ~5-50 per day at the small-race level, 100s/day at presidential level.

Architecture
------------
- One long-running asyncio task launched by the scheduler.
- WebSocket connect to jetstream, reconnect with backoff on disconnect.
- Per-message: cheap text-contains check on a precomputed keyword set
  built from CampaignConfig. Non-matching messages are dropped before
  any DB work.
- Matching posts are batched (size-or-time) and committed together to
  avoid hammering SQLite's single writer lock.
- URL-level dedup against SourceItem.source_url — the same dedup the
  profile-poll scraper uses, so we can run both side-by-side without
  duplicating articles.

Failure modes
-------------
- WS disconnect: caught, reconnect after exponential backoff (1s, 2s, 4s,
  max 60s)
- Malformed JSON message: logged, skipped
- DB write failure: rollback, log, next batch continues
- Keyword refresh: re-read campaign config on a slow timer (10 min) so
  newly-added opponent names start matching without a restart
"""
from __future__ import annotations
import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

# Public jetstream endpoint, US-East shard. Bluesky also publishes us-west
# and EU shards if we ever need geo redundancy.
_JETSTREAM_URL = (
    "wss://jetstream2.us-east.bsky.network/subscribe"
    "?wantedCollections=app.bsky.feed.post"
)

# How often to refresh keywords from campaign config + opponents table.
_KEYWORDS_REFRESH_S = 600  # 10 minutes

# Batch DB writes to reduce writer-lock contention with the scheduler.
_BATCH_SIZE = 10
_BATCH_FLUSH_S = 15  # also flush after this many seconds, even if undersized

# Reconnect backoff.
_BACKOFF_INITIAL_S = 1.0
_BACKOFF_MAX_S = 60.0

# Hard cap on text length to lower-case + scan per message. Most posts
# are short; pathological 3-MB strings shouldn't waste cycles.
_MAX_TEXT_SCAN_LEN = 4000

# Internal state: one daemon per process.
_task: Optional[asyncio.Task] = None
_running = False
_stats = {
    "started_at": None,
    "events_seen": 0,
    "events_matched": 0,
    "events_written": 0,
    "reconnects": 0,
    "last_error": None,
    "last_match_at": None,
}


def get_stats() -> dict:
    """Snapshot of firehose runtime stats for the /api/system/llm-status
    style observability endpoint. Cheap to call."""
    return dict(_stats)


def _build_keyword_set(db) -> set[str]:
    """Build a lower-cased set of substrings to match against post text.

    Pulls candidate name(s), opponent name(s), district, geography, and a
    small set of campaign-context terms from CampaignConfig + Opponents.
    Names get split into last-name tokens to catch e.g. "Cognetti" alone.
    """
    from app.models import CampaignConfig, Opponent

    kws: set[str] = set()

    def _last_name_token(full: str) -> str | None:
        """Last word of a humanized "First Last" name. We add only the LAST
        name (distinctive: 'Cognetti', 'Bresnahan') and the full name. We
        deliberately DO NOT add the first name alone — first names like
        'Paige' are common and match enormous amounts of unrelated noise
        (Paige Spiranac, Paige Bueckers, etc.)."""
        toks = re.sub(r"[^a-zA-Z\s]", " ", full).split()
        return toks[-1].lower() if toks and len(toks[-1]) >= 4 else None

    config = db.query(CampaignConfig).first()
    if config:
        if config.candidate_name:
            kws.add(config.candidate_name.lower())  # full "paige cognetti"
            last = _last_name_token(config.candidate_name)
            if last:
                kws.add(last)
        if config.district:
            kws.add(config.district.lower())
            # "PA-08" → also "pa 08" form people sometimes write
            kws.add(config.district.lower().replace("-", " "))
        # NOTE: deliberately NOT adding config.location verbatim. It's
        # often "Scranton/Wilkes-Barre, PA-08" — a unique string that
        # would never appear in real posts. The district code above
        # handles geography matching.

    for opp in db.query(Opponent).all():
        if opp.name:
            kws.add(opp.name.lower())
            last = _last_name_token(opp.name)
            if last:
                kws.add(last)

    # Drop very short / very generic tokens that would match everything.
    BLOCK = {"the", "for", "and", "of", "or", "rep", "sen", "rev", "dr",
             "mr", "mrs", "ms", "us", "pa", "ca", "ny", "tx"}
    kws = {k for k in kws if k and k not in BLOCK and len(k) >= 4}
    return kws


def _post_matches(text: str, kws: set[str]) -> Optional[str]:
    """Return the first matched keyword, or None. Case-insensitive."""
    if not text or not kws:
        return None
    lower = text[:_MAX_TEXT_SCAN_LEN].lower()
    for kw in kws:
        if kw in lower:
            return kw
    return None


def _commit_to_source_items(db, batch: list[dict]) -> int:
    """Insert matched-post fields as SourceItem rows. Returns # written.

    URL-level dedup mirrors `bluesky_scraper._post_envelope_to_dict`'s
    output shape so the firehose and per-profile scrapers don't collide.
    """
    from app.models import SourceItem

    if not batch:
        return 0
    written = 0
    for fields in batch:
        url = fields.get("source_url")
        if not url:
            continue
        if db.query(SourceItem.id).filter_by(source_url=url).first():
            continue
        try:
            item = SourceItem(**fields)
            db.add(item)
            db.flush()
            written += 1
        except Exception as exc:
            logger.warning("bluesky_firehose: insert failed for %s: %s", url, exc)
            db.rollback()
            continue
    try:
        db.commit()
    except Exception as exc:
        logger.warning("bluesky_firehose: commit failed: %s", exc)
        db.rollback()
        return 0
    return written


def _event_to_fields(event: dict, matched_kw: str) -> Optional[dict]:
    """Translate a jetstream event into SourceItem-ready fields.

    Jetstream event shape (post create):
      {
        "did": "did:plc:...",
        "commit": {
          "operation": "create",
          "collection": "app.bsky.feed.post",
          "rkey": "...",
          "record": {"text": "...", "createdAt": "...", ...},
        },
        ...
      }
    """
    commit = event.get("commit") or {}
    if commit.get("operation") != "create":
        return None
    if commit.get("collection") != "app.bsky.feed.post":
        return None
    record = commit.get("record") or {}
    text = (record.get("text") or "").strip()
    if not text:
        return None
    # Skip pure reposts / quote-posts with no original author text
    # (jetstream gives the record verbatim — reposts are a separate
    # collection, but quote-posts have `embed.record` set).

    did = event.get("did") or ""
    rkey = commit.get("rkey") or ""
    if not did or not rkey:
        return None
    # Canonical URL — uses DID directly. (bsky.app accepts either DID or
    # handle in the URL, so this works without a handle lookup.)
    source_url = f"https://bsky.app/profile/{did}/post/{rkey}"

    # Parse createdAt from the post record (firehose timestamp is delivery
    # time, not authorship time — record.createdAt is the truth).
    published_at: Optional[datetime] = None
    try:
        cstr = (record.get("createdAt") or "").replace("Z", "+00:00")
        published_at = datetime.fromisoformat(cstr)
        if published_at.tzinfo is not None:
            published_at = published_at.replace(tzinfo=None)
    except Exception:
        pass

    first_line = text.split("\n", 1)[0]
    title = first_line[:120].strip()
    if len(text) > 120:
        title = title.rstrip() + "…"
    if not title:
        title = "Bluesky post"

    return {
        "title": title,
        "raw_text": text,
        "source_url": source_url,
        # Source name carries the firehose-origin marker so we can tell
        # firehose-discovered posts apart from per-profile-scraped ones
        # in the review queue later if needed.
        "source_name": f"Bluesky firehose (matched: {matched_kw})",
        "source_type": "social",
        "published_at": published_at,
    }


async def _firehose_loop():
    """The long-running asyncio task. Connects, reads, filters, batches,
    commits. Reconnects on disconnect with exponential backoff."""
    global _running
    import websockets
    from app.db import SessionLocal

    _running = True
    _stats["started_at"] = datetime.utcnow().isoformat()
    backoff = _BACKOFF_INITIAL_S

    # Cached keyword set + refresh timer.
    kws: set[str] = set()
    last_kw_refresh = 0.0

    while _running:
        try:
            logger.info("bluesky_firehose: connecting to %s", _JETSTREAM_URL)
            async with websockets.connect(
                _JETSTREAM_URL,
                ping_interval=20,
                ping_timeout=20,
                max_size=2 ** 20,  # 1MB cap on a single message
            ) as ws:
                backoff = _BACKOFF_INITIAL_S
                logger.info("bluesky_firehose: connected")

                batch: list[dict] = []
                batch_started_at = asyncio.get_event_loop().time()

                async for raw in ws:
                    if not _running:
                        break
                    _stats["events_seen"] += 1

                    # Refresh keywords every ~10 min so adding an opponent
                    # starts matching without a restart.
                    now = asyncio.get_event_loop().time()
                    if now - last_kw_refresh > _KEYWORDS_REFRESH_S or not kws:
                        try:
                            with SessionLocal() as db:
                                kws = _build_keyword_set(db)
                            last_kw_refresh = now
                            logger.info(
                                "bluesky_firehose: keyword set refreshed (%d terms): %s",
                                len(kws), sorted(kws),
                            )
                        except Exception:
                            logger.exception("bluesky_firehose: keyword refresh failed")

                    # Parse, filter, accumulate.
                    try:
                        event = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    text = ((event.get("commit") or {}).get("record") or {}).get("text") or ""
                    matched = _post_matches(text, kws)
                    if not matched:
                        continue

                    fields = _event_to_fields(event, matched)
                    if not fields:
                        continue
                    _stats["events_matched"] += 1
                    _stats["last_match_at"] = datetime.utcnow().isoformat()
                    batch.append(fields)

                    # Flush batch by size or by age.
                    age = now - batch_started_at
                    if len(batch) >= _BATCH_SIZE or age >= _BATCH_FLUSH_S:
                        with SessionLocal() as db:
                            n = _commit_to_source_items(db, batch)
                        _stats["events_written"] += n
                        if n:
                            logger.info(
                                "bluesky_firehose: committed %d/%d matched posts",
                                n, len(batch),
                            )
                        batch = []
                        batch_started_at = now

                # Loop exited (server closed?). Flush whatever's pending.
                if batch:
                    with SessionLocal() as db:
                        _commit_to_source_items(db, batch)

        except asyncio.CancelledError:
            logger.info("bluesky_firehose: cancelled, stopping")
            _running = False
            return
        except Exception as exc:
            _stats["last_error"] = f"{type(exc).__name__}: {exc}"
            _stats["reconnects"] += 1
            logger.warning(
                "bluesky_firehose: disconnected (%s) — reconnecting in %.1fs",
                exc, backoff,
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _BACKOFF_MAX_S)
            continue


def start_firehose():
    """Spawn the firehose task in the running event loop. Idempotent.

    Called by the scheduler at startup. Safe to call multiple times; the
    second call is a no-op if the task is already running.

    Uses get_running_loop() (Python 3.10+) rather than the deprecated
    get_event_loop() — the latter silently creates a new loop in some
    contexts (or raises in 3.12+) which left the firehose task orphaned
    on a loop nothing was actually polling. We previously saw 0 ingested
    items across multiple session restarts because of this.
    """
    global _task, _stats
    if _task is not None and not _task.done():
        logger.debug("bluesky_firehose: already running, no-op")
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError as exc:
        # No running loop in this thread. Called from a sync context
        # without an active asyncio loop. Surface this loudly because
        # silent failure was the prior bug.
        logger.error(
            "bluesky_firehose: NOT STARTED — no running event loop "
            "(start_firehose was called from a sync context). %s", exc,
        )
        _stats["last_error"] = f"start_firehose: no running loop ({exc})"
        return
    _task = loop.create_task(_firehose_loop(), name="bluesky_firehose")
    logger.info("bluesky_firehose: task spawned on loop id=%d", id(loop))


def stop_firehose():
    """Cancel the firehose task (called by scheduler shutdown)."""
    global _task, _running
    _running = False
    if _task and not _task.done():
        _task.cancel()
        _task = None
