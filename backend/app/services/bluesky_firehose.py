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
import os
import re
import time
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

# Watchdog: the websockets client won't raise on a "wedged" socket — one
# that stays connected but stops delivering frames. The network carries
# ~500 posts/sec, so a multi-minute silence WHILE WE BELIEVE WE'RE CONNECTED
# is a wedge, not a quiet news day. _WEDGE_STALL_S is the silence that trips
# a restart. It is bounded on both sides by existing constants:
#   below the 5-min watchdog poll interval (scheduler) → one flat poll trips it
#   above _BACKOFF_MAX_S (60s)                          → a normal reconnect's
#                                                          quiet gap can't
#                                                          false-positive
_WEDGE_STALL_S = 240.0
# Grace between cancel and re-spawn in restart(); lets the cancelled task
# unwind on the loop before the replacement is created (see restart()).
_RESTART_GRACE_S = 2.0

# Internal state: one daemon per process.
_task: Optional[asyncio.Task] = None
_running = False
# Monotonic timestamp of the last WS frame observed (set on connect and per
# event). None until the first connect. get_health() compares it against
# now to detect a wedged socket; kept out of _stats because a raw monotonic
# float is meaningless to the JSON observability consumers of get_stats().
_last_event_monotonic: Optional[float] = None
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


def get_health(now: Optional[float] = None) -> dict:
    """Liveness assessment for the watchdog. Returns {state, ...}.

    `state` is one of:
      "disabled" — never started (firehose turned off via env)
      "dead"     — the asyncio task is gone/finished but should be running
      "wedged"   — connected but no frames for > _WEDGE_STALL_S (silent socket)
      "ok"       — task alive and recently delivering (or freshly connected)

    The "wedged" check keys off _last_event_monotonic, which the loop stamps
    on connect and on every frame — so a socket that connects then goes
    silent is caught (silence measured from connect), while a task that is
    still legitimately reconnecting through backoff is left alone.

    `now` is injectable for testing; defaults to time.monotonic() so the
    function has no dependency on a running event loop (it may be called from
    a sync observability endpoint).
    """
    if now is None:
        now = time.monotonic()

    task_alive = _task is not None and not _task.done()
    last = _last_event_monotonic
    silent_for = (now - last) if last is not None else None

    if not task_alive:
        # started_at is set the moment _firehose_loop runs; if it's still None
        # and we're not running, the firehose was simply never turned on.
        state = "disabled" if (_stats["started_at"] is None and not _running) else "dead"
    elif _running and silent_for is not None and silent_for > _WEDGE_STALL_S:
        state = "wedged"
    else:
        state = "ok"

    return {
        "state": state,
        "events_seen": _stats["events_seen"],
        "events_matched": _stats["events_matched"],
        "silent_for_s": round(silent_for, 1) if silent_for is not None else None,
        "reconnects": _stats["reconnects"],
        "last_error": _stats["last_error"],
    }


# Place-name fragments that look like city tokens but match everything.
# Applied on top of BLOCK when harvesting city tokens from config.location.
_GEO_STOPWORDS = {"pennsylvania", "penn", "county", "city", "town", "area",
                  "north", "south", "east", "west", "greater", "metro"}


def _load_keyword_overrides() -> tuple[set[str], set[str]]:
    """Optional env escape hatches, mirroring NITTER_INSTANCES for twitter.

    BLUESKY_EXTRA_KEYWORDS — comma-separated terms to ADD (a hashtag, a local
        issue, a newly-relevant name the auto-derived set misses).
    BLUESKY_BLOCK_KEYWORDS — comma-separated terms to REMOVE (e.g. a derived
        city token like "barre" that turns out to match unrelated noise).

    Both are lower-cased + trimmed. Lets the user tune recall vs. precision
    without a code change. Extras bypass the length/BLOCK guard (the user
    asked for them); blocks are applied last so a removal always wins.
    """
    def _parse(name: str) -> set[str]:
        raw = os.getenv(name, "").strip()
        return {t.strip().lower() for t in raw.split(",") if t.strip()} if raw else set()

    return _parse("BLUESKY_EXTRA_KEYWORDS"), _parse("BLUESKY_BLOCK_KEYWORDS")


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
            d = config.district.lower()
            kws.add(d)
            kws.add(d.replace("-", " "))   # "PA-08" → "pa 08"
            kws.add(d.replace("-", ""))    # "PA-08" → "pa08" (hashtag/handle form)
        # Harvest distinctive CITY tokens from config.location. We still do
        # NOT add the verbatim string ("Scranton/Wilkes-Barre, PA-08" never
        # appears in real posts), but the individual city names do — and they
        # materially broaden recall ("scranton" is this module's own canonical
        # keyword, see the docstring). Place-name fragments are noisier than
        # surnames, so these are the likeliest terms to need tuning; the
        # BLUESKY_BLOCK_KEYWORDS env hatch exists for exactly that.
        if getattr(config, "location", None):
            # Strip the district code so "PA-08" isn't re-split into junk.
            loc = re.sub(r"\bPA[- ]?\d{2}\b", " ", config.location, flags=re.IGNORECASE)
            for tok in re.split(r"[^A-Za-z]+", loc):
                t = tok.lower()
                if len(t) >= 4 and t not in _GEO_STOPWORDS:
                    kws.add(t)

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

    # User escape hatches: extras bypass the guard above; blocks win last.
    extra, blocked = _load_keyword_overrides()
    kws |= extra
    kws -= blocked
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
    from app.services.ingestion import clean_title as _clean_title
    title = _clean_title(title) or "Bluesky post"

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
    global _running, _last_event_monotonic
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
                # Start the wedge clock at connect: a socket that connects
                # then never delivers a frame is exactly the silent failure
                # the watchdog must catch (without this, _last_event_monotonic
                # would stay None and the wedge would read as "ok").
                _last_event_monotonic = time.monotonic()

                batch: list[dict] = []
                batch_started_at = asyncio.get_event_loop().time()

                async for raw in ws:
                    if not _running:
                        break
                    _stats["events_seen"] += 1
                    _last_event_monotonic = time.monotonic()

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


async def restart(reason: str = "") -> None:
    """Stop and re-spawn the firehose task. Async on purpose.

    The await between stop and start is load-bearing: stop_firehose() merely
    *schedules* a CancelledError on the old task; the sleep yields control so
    the loop actually delivers it (the old _firehose_loop catches it, sets
    _running=False, and returns) BEFORE we create the replacement. Skipping
    the yield would let the dying task's `_running = False` race the new
    task's `_running = True` and immediately stop the fresh loop.

    Called by the scheduler watchdog when get_health() reports dead/wedged.
    """
    global _last_event_monotonic
    logger.warning("bluesky_firehose: restart requested (%s)", reason or "manual")
    stop_firehose()
    _last_event_monotonic = None  # fresh wedge clock for the new connection
    await asyncio.sleep(_RESTART_GRACE_S)
    start_firehose()
