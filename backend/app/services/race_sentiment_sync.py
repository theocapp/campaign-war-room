"""Generic sync layer for race sentiment sources.

Every connector (Polymarket, Kalshi, Cook, Sabato, …) implements a small
fetcher function that returns a `FetchedSample`. This module handles the
shared work: writing a snapshot row, updating the current value on the
`race_sentiment` row, computing the 7-day delta, and recording sync
success/failure.

This keeps connector files small — they only have to know how to read
their source. Persistence + delta math + error recording all live here.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.models import RaceSentiment, RaceSentimentSnapshot

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Connector contract
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FetchedSample:
    """Result of one connector fetch. Connectors return this; the sync
    layer persists it. Every field is optional so a connector can return
    a partial reading (e.g. a rating with no implied band).
    """
    source_type: str                          # 'market' | 'rating'
    # Market shape
    candidate_pct: Optional[float] = None
    opponent_pct: Optional[float] = None
    # Rating shape
    rating_label: Optional[str] = None
    rating_min_pct: Optional[float] = None
    rating_max_pct: Optional[float] = None
    favors: Optional[str] = None              # 'candidate' | 'opponent' | 'tossup'
    # Common
    source_as_of: Optional[datetime] = None   # when the source said this value was sampled
    raw_response: Optional[dict] = field(default=None, repr=False)


# ─────────────────────────────────────────────────────────────────────────────
# Persist a sample
# ─────────────────────────────────────────────────────────────────────────────

def record_sample(
    db: Session, source: str, sample: FetchedSample,
    *, also_update_current: bool = True,
    captured_at: Optional[datetime] = None,
) -> RaceSentimentSnapshot | None:
    """Write a snapshot row AND (optionally) update the current-value row.

    `also_update_current=False` is for history backfills — when we're
    writing dozens of past daily snapshots, only the LATEST one should
    overwrite the visible current value.

    `captured_at` defaults to now; pass an explicit datetime when
    backfilling historical snapshots.

    Returns the persisted snapshot row, or None if the dedup constraint
    prevented insertion (same source + same captured_at second).
    """
    row = db.query(RaceSentiment).filter(RaceSentiment.source == source).first()
    if row is None:
        log.warning("record_sample: unknown source %r — skipping", source)
        return None

    ts = captured_at or datetime.utcnow()

    # ── Suspect flag: coherence check at write time ──
    # Two sides of the same binary contract should sum to ~100% (±spread).
    # A gross deviation means we read desynchronized / stale prices.
    suspect = False
    suspect_reason: Optional[str] = None
    if sample.candidate_pct is not None and sample.opponent_pct is not None:
        total = float(sample.candidate_pct) + float(sample.opponent_pct)
        if total < 80.0 or total > 120.0:
            suspect = True
            suspect_reason = f"incoherent: cand+opp={total:.1f}%"

    snap = RaceSentimentSnapshot(
        source=source,
        source_type=sample.source_type,
        candidate_pct=sample.candidate_pct,
        opponent_pct=sample.opponent_pct,
        rating_label=sample.rating_label,
        rating_min_pct=sample.rating_min_pct,
        rating_max_pct=sample.rating_max_pct,
        favors=sample.favors,
        captured_at=ts,
        source_as_of=sample.source_as_of,
        raw_response=json.dumps(sample.raw_response) if sample.raw_response else None,
        suspect=suspect,
        suspect_reason=suspect_reason,
    )
    try:
        db.add(snap)
        db.flush()
    except Exception as e:
        # UNIQUE (source, captured_at) violation — fine, just bail out cleanly.
        db.rollback()
        log.info("record_sample: dedup skip for %s @ %s (%s)", source, ts, e)
        return None

    # ── Suspect flag: temporal isolation (retroactive) ──
    # Now that we have a NEXT-relative-to-prev snapshot, retroactively check
    # whether the PREVIOUS snapshot was an isolated spike that snapped back.
    # A glitch row stays in the DB; charts filter suspect rows out.
    if sample.candidate_pct is not None and sample.source_type == "market":
        _flag_previous_if_isolated(db, source, snap)

    if also_update_current:
        if sample.candidate_pct is not None:
            row.candidate_pct = sample.candidate_pct
        if sample.opponent_pct is not None:
            row.opponent_pct = sample.opponent_pct
        if sample.rating_label is not None:
            row.rating_label = sample.rating_label
        if sample.rating_min_pct is not None:
            row.rating_min_pct = sample.rating_min_pct
        if sample.rating_max_pct is not None:
            row.rating_max_pct = sample.rating_max_pct
        if sample.favors is not None:
            row.favors = sample.favors
        if sample.source_as_of is not None:
            row.as_of = sample.source_as_of
        # 7-day delta on candidate_pct, computed from snapshots.
        if sample.source_type == "market" and sample.candidate_pct is not None:
            row.delta_7d = _compute_7d_delta(db, source, sample.candidate_pct, ts)
        row.last_synced_at = ts
        row.last_sync_error = None

    db.commit()
    return snap


def record_sync_error(db: Session, source: str, exc: Exception) -> None:
    """Mark a sync attempt as failed without overwriting last good value."""
    row = db.query(RaceSentiment).filter(RaceSentiment.source == source).first()
    if row is None:
        return
    # Keep the message short — full traceback would bloat the DB and the UI.
    row.last_sync_error = f"{type(exc).__name__}: {exc}"[:500]
    db.commit()


# Tunables for the temporal-isolation suspect check. Kept module-level so
# they can be tweaked from one spot. Must match the values used in the
# migration that backfills historical rows.
_SUSPECT_ISOLATION_PT = 15.0          # threshold for "wildly different"
_SUSPECT_NEIGHBOR_MAX_DAYS = 7        # don't compare snapshots > 7 days apart


def _flag_previous_if_isolated(
    db: Session, source: str, new_snap: RaceSentimentSnapshot,
) -> None:
    """Retroactive suspect check.

    Called right after a new snapshot is written. If the snapshot that
    came just BEFORE this one looks like an isolated spike (its value is
    way out of family with both ITS predecessor AND this new snapshot),
    flag it as suspect.

    This is how the Kalshi 2026-05-26 22:56 row (cand=9.5 sandwiched
    between 59 and 62) gets caught — at write time of the 23:08 row,
    we look back and see the 22:56 row is an isolated outlier.
    """
    if new_snap.candidate_pct is None:
        return
    recent = (
        db.query(RaceSentimentSnapshot)
        .filter(RaceSentimentSnapshot.source == source)
        .filter(RaceSentimentSnapshot.id != new_snap.id)
        .filter(RaceSentimentSnapshot.candidate_pct.isnot(None))
        .filter(RaceSentimentSnapshot.captured_at < new_snap.captured_at)
        .order_by(RaceSentimentSnapshot.captured_at.desc())
        .limit(2).all()
    )
    if len(recent) < 2:
        return
    prev = recent[0]         # immediately before new_snap
    prev_prev = recent[1]    # one before that
    if prev.suspect:
        # Already flagged; don't clobber an existing reason.
        return
    if (new_snap.captured_at - prev.captured_at).days > _SUSPECT_NEIGHBOR_MAX_DAYS:
        return
    if (prev.captured_at - prev_prev.captured_at).days > _SUSPECT_NEIGHBOR_MAX_DAYS:
        return
    delta_in = float(prev.candidate_pct) - float(prev_prev.candidate_pct)
    delta_out = float(new_snap.candidate_pct) - float(prev.candidate_pct)
    # Opposite signs + both larger than threshold = isolated spike/dip
    if (delta_in > _SUSPECT_ISOLATION_PT and delta_out < -_SUSPECT_ISOLATION_PT) or \
       (delta_in < -_SUSPECT_ISOLATION_PT and delta_out > _SUSPECT_ISOLATION_PT):
        prev.suspect = True
        prev.suspect_reason = (
            f"isolated outlier: {prev_prev.candidate_pct}→{prev.candidate_pct}→"
            f"{new_snap.candidate_pct} ({delta_in:+.1f}, then {delta_out:+.1f})"
        )
        log.info(
            "race_sentiment: flagged snapshot %d (%s @ %s) as isolated outlier",
            prev.id, source, prev.captured_at,
        )
        # The caller will commit; the parent transaction batches both
        # the new insert and this retroactive update.


def _compute_7d_delta(
    db: Session, source: str, current_pct: float, now: datetime,
) -> Optional[float]:
    """Compute change in candidate_pct vs ~7 days ago.

    Looks up the snapshot whose captured_at is closest to (now - 7 days),
    within a +/- 2 day window. Returns None if no comparable snapshot
    exists yet (typical for the first week after a connector goes live).
    """
    target = now - timedelta(days=7)
    window_lo = target - timedelta(days=2)
    window_hi = target + timedelta(days=2)
    snap = (
        db.query(RaceSentimentSnapshot)
        .filter(
            RaceSentimentSnapshot.source == source,
            RaceSentimentSnapshot.candidate_pct.isnot(None),
            RaceSentimentSnapshot.captured_at >= window_lo,
            RaceSentimentSnapshot.captured_at <= window_hi,
        )
        .order_by(RaceSentimentSnapshot.captured_at.asc())
        .first()
    )
    if snap is None or snap.candidate_pct is None:
        return None
    return round(current_pct - snap.candidate_pct, 2)


# ─────────────────────────────────────────────────────────────────────────────
# Sync orchestration
# ─────────────────────────────────────────────────────────────────────────────

_RATING_SOURCE_URLS = {
    "cook":             "https://www.cookpolitical.com/ratings/house-race-ratings",
    "sabato":           "https://centerforpolitics.org/crystalball/",
    "inside_elections": "https://insideelections.com/ratings/house",
    "ddhq":             "https://decisiondeskhq.com/",
}


def _rating_autoconfigure(db: Session, source: str, row) -> None:
    """Fallback auto-configure for rating sources (Cook, Sabato, IE, DDHQ).

    Normally handled at startup by _seed_race_sentiment_sources(); this runs
    in sync_one() as a safety net when a race is reconfigured between restarts.
    No network call required — the URL is fixed per source.
    """
    import json
    from app.models import CampaignConfig

    url = _RATING_SOURCE_URLS.get(source)
    if not url:
        return
    config = db.query(CampaignConfig).first()
    raw = ((config.district if config else None) or "").strip().upper()
    if not raw or "-" not in raw:
        return
    state, _, district_num = raw.partition("-")
    if not state or not district_num:
        return
    row.external_id = url
    row.source_url = url
    row.external_metadata = json.dumps({
        "district_label": raw,
        "state": state,
        "district_number": district_num.lstrip("0") or "0",
    })
    db.commit()
    log.info("%s_autoconfigure: configured for %s", source, raw)


def _polymarket_autodiscover(db: Session, row) -> None:
    """Discover and save Polymarket event slug + market IDs for the configured race.

    Reads state + district from CampaignConfig, calls polymarket_discover(),
    and writes the result to the row. No-ops if config is missing or discovery
    returns nothing.
    """
    import json
    from app.models import CampaignConfig
    from app.services.prediction_market_monitor import polymarket_discover

    config = db.query(CampaignConfig).first()
    raw = ((config.district if config else None) or "").strip().upper()
    if not raw or "-" not in raw:
        return
    state, _, district = raw.partition("-")
    if not state or not district:
        return

    log.info("polymarket_autodiscover: searching for %s-%s ...", state, district)
    found = polymarket_discover(state, district)
    if not found:
        log.info("polymarket_autodiscover: no market found for %s-%s", state, district)
        return

    slug = found["event_slug"]
    row.external_id = slug
    row.external_metadata = json.dumps(found)
    row.source_url = f"https://polymarket.com/event/{slug}"
    db.commit()
    log.info("polymarket_autodiscover: configured slug=%s", slug)


def _kalshi_autodiscover(db: Session, row) -> None:
    """Attempt to discover and save Kalshi tickers for the configured race.

    Reads state + district from CampaignConfig, calls kalshi_discover(),
    and writes the result to the row in-place (caller must commit).
    No-ops silently if config is missing or discovery returns nothing.
    """
    import json
    from app.models import CampaignConfig
    from app.services.prediction_market_monitor import kalshi_discover

    config = db.query(CampaignConfig).first()
    if not config:
        return

    # CampaignConfig.district is e.g. "PA-08"; split into state + number.
    raw = (config.district or "").strip().upper()
    if not raw or "-" not in raw:
        return
    state, _, district = raw.partition("-")
    if not state or not district:
        return

    log.info("kalshi_autodiscover: searching for %s-%s ...", state, district)
    found = kalshi_discover(state, district)
    if not found:
        log.info("kalshi_autodiscover: no market found for %s-%s", state, district)
        return

    event_ticker = found["event_ticker"]
    row.external_id = event_ticker
    row.external_metadata = json.dumps(found)
    row.source_url = (
        f"https://kalshi.com/markets/{event_ticker.lower()}"
    )
    db.commit()
    log.info("kalshi_autodiscover: configured %s (D=%s R=%s)",
             event_ticker,
             found.get("candidate_market_ticker"),
             found.get("opponent_market_ticker"))


def sync_one(db: Session, source: str) -> bool:
    """Run one source's fetcher and persist the result.

    Returns True on success, False on any error (also logged + recorded
    on the row's last_sync_error column for the UI).

    For the "kalshi" source, if external_id is not yet set, auto-discovery
    runs first using the campaign's state + district from CampaignConfig.
    If discovery succeeds, the tickers are saved to the row before fetching.
    """
    row = db.query(RaceSentiment).filter(RaceSentiment.source == source).first()
    if row is None:
        log.debug("sync_one: %s row not found", source)
        return False

    if not row.external_id and source == "kalshi":
        _kalshi_autodiscover(db, row)
    if not row.external_id and source == "polymarket":
        _polymarket_autodiscover(db, row)
    if not row.external_id and source in _RATING_SOURCE_URLS:
        _rating_autoconfigure(db, source, row)

    if not row.external_id:
        log.debug("sync_one: %s has no external_id — manual-only", source)
        return False

    metadata = {}
    if row.external_metadata:
        try:
            metadata = json.loads(row.external_metadata)
        except Exception:
            metadata = {}

    try:
        fetcher = _get_fetcher(source)
        if fetcher is None:
            log.warning("sync_one: no fetcher registered for %s", source)
            return False
        sample = fetcher(row.external_id, metadata)
        if sample is None:
            return False
        record_sample(db, source, sample)
        return True
    except Exception as e:
        log.warning("sync_one: %s failed: %s", source, e)
        record_sync_error(db, source, e)
        return False


def sync_all(db: Session) -> dict:
    """Daily job. Loops every source with an external_id and syncs it."""
    rows = db.query(RaceSentiment).filter(RaceSentiment.external_id.isnot(None)).all()
    results = {"synced": [], "failed": []}
    for row in rows:
        ok = sync_one(db, row.source)
        (results["synced"] if ok else results["failed"]).append(row.source)
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Fetcher registry
# ─────────────────────────────────────────────────────────────────────────────
#
# Imported lazily because connectors may pull in heavy dependencies
# (httpx, BeautifulSoup, etc.) that we don't want at sync-module import time.

def _get_fetcher(source: str):
    if source in ("polymarket",):
        from app.services.prediction_market_monitor import polymarket_fetch
        return polymarket_fetch
    if source in ("kalshi",):
        from app.services.prediction_market_monitor import kalshi_fetch
        return kalshi_fetch
    if source in ("cook",):
        from app.services.race_ratings_monitor import cook_fetch
        return cook_fetch
    # sabato, inside_elections, ddhq: fetchers not yet implemented (Cloudflare-blocked).
    # Rows are auto-configured at startup so they're ready when scrapers land.
    return None
