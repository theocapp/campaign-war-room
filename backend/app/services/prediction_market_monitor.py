"""Prediction-market connectors for race sentiment.

Implements both Polymarket and Kalshi (elections API), each with a live
current-value fetcher and a one-shot daily-bar history backfill. All
endpoints used here are public and unauthenticated.

Polymarket has a 2-tier API:

  • Gamma API (gamma-api.polymarket.com)        — markets/events metadata,
                                                   current prices
  • CLOB API  (clob.polymarket.com)             — historical price series

Kalshi exposes everything through one elections subdomain:

  • api.elections.kalshi.com/trade-api/v2       — events, markets, and
                                                   per-market candlesticks

The race-sentiment row stores each provider's identifiers in
external_metadata (Polymarket token IDs, Kalshi market tickers), so we
can pull candidate vs. opponent in a single round trip per provider.

Thin-market reminder: PA-08's total liquidity is ~$2K and volume <$1K.
The numbers move on tiny trades. The UI should treat market sentiment as
a media-elite proxy, not as ground truth — see the Race Sentiment card's
header tooltip for the user-facing version of this caveat.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import httpx

from app.services.race_sentiment_sync import FetchedSample

log = logging.getLogger(__name__)


GAMMA_BASE = "https://gamma-api.polymarket.com"
CLOB_BASE = "https://clob.polymarket.com"
KALSHI_ELECTIONS_BASE = "https://api.elections.kalshi.com/trade-api/v2"
REQUEST_TIMEOUT = 15.0


# ─────────────────────────────────────────────────────────────────────────────
# Cross-provider: confidence-weighted blend of the two binary markets that
# make up an election event (one "X wins" market per side).
#
# Why blend instead of just reading one side? In a perfectly liquid pair of
# markets, both midpoints carry signal — combining them (with appropriate
# weight) gives a lower-variance estimate than either alone. In an imperfect
# pair (one side dead), we want the dead side's noisy midpoint to
# automatically drop out of the result. Spread-based confidence weighting
# does both.
#
# The same logic is used by both the Polymarket and Kalshi fetchers, so it
# lives up here, above either provider.
# ─────────────────────────────────────────────────────────────────────────────

# Spreads wider than this many percentage points are treated as a dead
# market — the midpoint of a 20-cent-wide order book carries essentially
# no information about where the "true" probability sits. 20% is a generous
# threshold; in practice the markets we care about have spreads of 2–5%
# when liquid.
_DEAD_MARKET_SPREAD_THRESHOLD_PCT = 20.0


@dataclass(frozen=True)
class MarketQuote:
    """A snapshot of one binary market's "Yes" side.

    midpoint_pct: (best_bid + best_ask) / 2, in percentage points (0–100).
    spread_pct:   best_ask - best_bid, in percentage points (0–100). None
                  if we couldn't extract bid/ask (e.g. legacy data); the
                  blender treats that as full confidence.
    """
    midpoint_pct: float
    spread_pct: Optional[float]


def _confidence(spread_pct: Optional[float]) -> float:
    """Confidence weight for a market quote, derived from its bid-ask spread.

    Linearly decays from 1.0 (spread 0) to 0.0 (spread at the dead-market
    threshold). Above the threshold, confidence is zero — the midpoint of
    such a wide book has no information content.

    When spread is None (legacy / unknown), assume full confidence rather
    than discarding the quote.
    """
    if spread_pct is None:
        return 1.0
    if spread_pct >= _DEAD_MARKET_SPREAD_THRESHOLD_PCT:
        return 0.0
    return 1.0 - (spread_pct / _DEAD_MARKET_SPREAD_THRESHOLD_PCT)


def _blend_p_x_wins(
    x_yes_quote: Optional[MarketQuote],
    other_yes_quote: Optional[MarketQuote],
) -> Optional[float]:
    """Confidence-weighted estimate of P(X wins), in percentage points.

    Combines two readings:
      • x_yes_quote.midpoint_pct                   — direct from X's market
      • 100 - other_yes_quote.midpoint_pct         — implied by the other
                                                     side's "yes" price
                                                     (mutually-exclusive
                                                     outcomes)
    Each reading is weighted by its market's spread-derived confidence.
    Returns None when both quotes are absent. Falls back to the X-side
    midpoint when both quotes exist but both confidences are zero — that
    case is rare (both markets dead) and we'd rather show something than
    nothing.
    """
    estimates: list[tuple[float, float]] = []
    if x_yes_quote is not None:
        estimates.append((x_yes_quote.midpoint_pct, _confidence(x_yes_quote.spread_pct)))
    if other_yes_quote is not None:
        estimates.append(
            (100.0 - other_yes_quote.midpoint_pct, _confidence(other_yes_quote.spread_pct)),
        )

    if not estimates:
        return None

    total_weight = sum(w for _, w in estimates)
    if total_weight > 0:
        return round(sum(p * w for p, w in estimates) / total_weight, 2)

    # Both confidences are zero. Fall back to the direct X-side midpoint
    # (if we have it), since "the X market is dead" still tells us less
    # than "the other side's dead midpoint is somewhere in a huge range."
    if x_yes_quote is not None:
        return round(x_yes_quote.midpoint_pct, 2)
    if other_yes_quote is not None:
        return round(100.0 - other_yes_quote.midpoint_pct, 2)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Polymarket auto-discovery
# ─────────────────────────────────────────────────────────────────────────────

def polymarket_discover(state: str, district: str) -> Optional[dict]:
    """Auto-discover the Polymarket event slug and market IDs for a house race.

    Tries the canonical slug pattern first ({state}-{district:02d}-house-election-winner),
    then a bare-number variant, then falls back to a Gamma title scan.

    Returns a metadata dict ready to seed into the DB row (same shape as the
    existing PA-08 seed), or None if no market is found.
    """
    state_l = state.lower().strip()
    d_raw = str(district).lstrip("0") or "0"
    d_pad = d_raw.zfill(2)

    for slug in [
        f"{state_l}-{d_pad}-house-election-winner",
        f"{state_l}-{d_raw}-house-election-winner",
        f"{state_l}-{d_pad}-house-race",
        f"{state_l}-{d_raw}-house-race",
    ]:
        result = _polymarket_try_slug(slug)
        if result:
            log.info("polymarket_discover: found %s via slug pattern", slug)
            return result

    return _polymarket_scan_titles(state_l, d_raw, d_pad)


def _polymarket_try_slug(slug: str) -> Optional[dict]:
    """Fetch a Gamma event by exact slug. Returns metadata dict or None."""
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            resp = client.get(f"{GAMMA_BASE}/events", params={"slug": slug})
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return None
    events = data if isinstance(data, list) else data.get("events", [])
    if not events:
        return None
    return _polymarket_extract_metadata(events[0], slug)


def _polymarket_extract_metadata(event: dict, slug: str) -> Optional[dict]:
    """Pull market IDs and token IDs from a Gamma event dict."""
    markets = event.get("markets") or []
    event_id = str(event.get("id") or "")

    cand_mkt = None
    opp_mkt = None
    for m in markets:
        q = (m.get("question") or "").lower()
        if "democratic" in q or "democrat" in q:
            cand_mkt = m
        elif "republican" in q:
            opp_mkt = m

    if not cand_mkt and not opp_mkt:
        return None

    def _yes_token(m):
        tokens = m.get("clobTokenIds") or []
        return tokens[0] if tokens else ""

    return {
        "event_slug": slug,
        "event_id": event_id,
        "candidate_market_id": str(cand_mkt["id"]) if cand_mkt else "",
        "candidate_yes_token_id": _yes_token(cand_mkt) if cand_mkt else "",
        "opponent_market_id": str(opp_mkt["id"]) if opp_mkt else "",
        "opponent_yes_token_id": _yes_token(opp_mkt) if opp_mkt else "",
        "race_label": event.get("title") or slug,
    }


def _polymarket_scan_titles(state_l: str, d_raw: str, d_pad: str) -> Optional[dict]:
    """Scan active Gamma events looking for a title match. Reads up to 1 000 events."""
    needles = {f"{state_l.upper()}-{d_raw}", f"{state_l.upper()}-{d_pad}"}
    offset = 0
    limit = 100
    while offset < 1000:
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
                resp = client.get(
                    f"{GAMMA_BASE}/events",
                    params={"active": "true", "limit": limit, "offset": offset},
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            log.warning("polymarket_discover title-scan: %s", e)
            return None
        events = data if isinstance(data, list) else data.get("events", [])
        if not events:
            break
        for event in events:
            title = (event.get("title") or "").upper()
            if any(n in title for n in needles):
                slug = event.get("slug") or ""
                result = _polymarket_extract_metadata(event, slug)
                if result:
                    log.info("polymarket_discover: found %s via title scan", slug)
                    return result
        offset += len(events)
        if len(events) < limit:
            break
    log.info("polymarket_discover: no market found for %s-%s", state_l.upper(), d_raw)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Current-price fetch (daily)
# ─────────────────────────────────────────────────────────────────────────────

def polymarket_fetch(external_id: str, metadata: dict) -> Optional[FetchedSample]:
    """Pull the current candidate + opponent prices from Polymarket.

    `external_id` is the event slug (e.g. "pa-08-house-election-winner").
    `metadata` is the JSON we seeded:
      - candidate_market_id, candidate_yes_token_id
      - opponent_market_id,  opponent_yes_token_id

    For each side, the "Yes" outcome price IS that side's implied win
    probability. (The "No" price is the complement.) We read both.
    """
    slug = external_id
    url = f"{GAMMA_BASE}/events?slug={slug}"
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        log.warning("polymarket_fetch: GET %s failed: %s", url, e)
        raise

    # Gamma returns a list of events; we asked for one slug so we expect one entry.
    events = data if isinstance(data, list) else data.get("events") or []
    if not events:
        log.warning("polymarket_fetch: no event found for slug %s", slug)
        return None
    event = events[0]
    markets = event.get("markets") or []
    if not markets:
        return None

    cand_id = str(metadata.get("candidate_market_id") or "")
    opp_id = str(metadata.get("opponent_market_id") or "")

    cand_quote = _polymarket_yes_quote(markets, cand_id)
    opp_quote = _polymarket_yes_quote(markets, opp_id)

    # Both being None usually means the market IDs in metadata are wrong.
    if cand_quote is None and opp_quote is None:
        log.warning(
            "polymarket_fetch: neither candidate(%s) nor opponent(%s) market found in event %s",
            cand_id, opp_id, slug,
        )
        return None

    # Confidence-blend the two sides. When one market is dead (huge spread,
    # e.g. PA-08's Republican side as of 2026-05-29: bid 9¢ / ask 82¢), its
    # noisy midpoint gets ~zero weight and the result collapses to the
    # liquid side's view + its complement. When both are liquid, both
    # contribute.
    candidate_pct = _blend_p_x_wins(cand_quote, opp_quote)
    opponent_pct = _blend_p_x_wins(opp_quote, cand_quote)

    return FetchedSample(
        source_type="market",
        candidate_pct=candidate_pct,
        opponent_pct=opponent_pct,
        source_as_of=datetime.utcnow(),  # Gamma doesn't expose a per-quote ts; use fetch time
        raw_response={
            "event_slug": slug,
            "candidate_market_id": cand_id,
            "opponent_market_id": opp_id,
            "candidate_quote": cand_quote.__dict__ if cand_quote else None,
            "opponent_quote": opp_quote.__dict__ if opp_quote else None,
            "candidate_pct": candidate_pct,
            "opponent_pct": opponent_pct,
        },
    )


def _polymarket_yes_quote(markets: list, market_id: str) -> Optional[MarketQuote]:
    """Pull the Yes-side quote (midpoint + spread) for a Polymarket sub-market.

    Prefers bestBid/bestAsk (live order book) when present, since that
    gives us the spread for confidence-weighting downstream. Falls back to
    outcomePrices when bid/ask are missing (legacy / cached responses) —
    in that case the spread is unknown so we mark it None and the blender
    treats the quote at full confidence.
    """
    if not market_id:
        return None
    for m in markets:
        if str(m.get("id")) != market_id:
            continue
        # Preferred path: live bid/ask. Polymarket Gamma returns these as
        # numbers (e.g. 0.61, 0.63) when the market has any depth.
        bid = m.get("bestBid")
        ask = m.get("bestAsk")
        if bid is not None and ask is not None:
            try:
                bid_f, ask_f = float(bid), float(ask)
                midpoint = (bid_f + ask_f) / 2.0 * 100.0
                spread = (ask_f - bid_f) * 100.0
                return MarketQuote(
                    midpoint_pct=round(midpoint, 2),
                    spread_pct=round(spread, 2),
                )
            except (TypeError, ValueError):
                pass

        # Fallback: outcomePrices (the Gamma API's pre-computed yes/no
        # values, no spread info). Defensive parse since it comes back as
        # either a list or a JSON-string of a list.
        prices = m.get("outcomePrices")
        outcomes = m.get("outcomes") or []
        if not prices:
            return None
        if isinstance(prices, str):
            import json
            try:
                prices = json.loads(prices)
            except Exception:
                return None
        if isinstance(outcomes, str):
            import json
            try:
                outcomes = json.loads(outcomes)
            except Exception:
                outcomes = []
        yes_idx = 0
        for i, label in enumerate(outcomes):
            if str(label).lower() == "yes":
                yes_idx = i
                break
        try:
            yes_pct = round(float(prices[yes_idx]) * 100.0, 2)
            return MarketQuote(midpoint_pct=yes_pct, spread_pct=None)
        except Exception:
            return None
    return None


# ─────────────────────────────────────────────────────────────────────────────
# History backfill (one-shot, called when configuring a new connector)
# ─────────────────────────────────────────────────────────────────────────────

def polymarket_backfill_history(
    source: str, metadata: dict, *, days_back: int = 90,
) -> dict:
    """Pull historical daily prices and write past snapshots.

    For each of (candidate_yes_token_id, opponent_yes_token_id), call the
    CLOB /prices-history endpoint and merge the two series by timestamp.
    Writes one snapshot per day. Skips days that are already in the
    snapshots table (UNIQUE constraint handles the dedup at the DB level).

    Returns a small dict summary for the API caller / logs.
    """
    from app.db import SessionLocal
    from app.services.race_sentiment_sync import record_sample

    cand_token = str(metadata.get("candidate_yes_token_id") or "")
    opp_token = str(metadata.get("opponent_yes_token_id") or "")
    if not cand_token and not opp_token:
        return {"written": 0, "error": "no token ids in metadata"}

    cand_series = _clob_prices_history(cand_token, days_back) if cand_token else {}
    opp_series = _clob_prices_history(opp_token, days_back) if opp_token else {}

    # Merge by date — Polymarket's history points may not align by-second
    # across two tokens. Bucket each into ISO date strings so we get one
    # snapshot per day with both sides filled when available.
    by_date: dict[str, dict] = {}
    for ts, price in cand_series.items():
        d = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
        by_date.setdefault(d, {"ts": ts})["candidate_pct"] = round(price * 100.0, 2)
        # Keep the latest timestamp seen for this date so captured_at is reasonable.
        by_date[d]["ts"] = max(by_date[d]["ts"], ts)
    for ts, price in opp_series.items():
        d = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
        by_date.setdefault(d, {"ts": ts})["opponent_pct"] = round(price * 100.0, 2)
        by_date[d]["ts"] = max(by_date[d]["ts"], ts)

    written = 0
    skipped = 0
    with SessionLocal() as db:
        for d in sorted(by_date.keys()):
            bucket = by_date[d]
            captured = datetime.utcfromtimestamp(bucket["ts"])
            sample = FetchedSample(
                source_type="market",
                candidate_pct=bucket.get("candidate_pct"),
                opponent_pct=bucket.get("opponent_pct"),
                source_as_of=captured,
                raw_response={"backfill_date": d},
            )
            # `also_update_current=False` — only the very last point should
            # touch the current-value row; we update it once at the end.
            snap = record_sample(
                db, source, sample,
                also_update_current=False,
                captured_at=captured,
            )
            if snap is None:
                skipped += 1
            else:
                written += 1

        # Refresh the current-value row with whatever the latest live fetch shows.
        try:
            live = polymarket_fetch(metadata.get("event_slug") or "", metadata)
            if live:
                record_sample(db, source, live, also_update_current=True)
        except Exception as e:
            log.warning("polymarket_backfill_history: live refresh failed: %s", e)

    return {"written": written, "skipped_dedup": skipped}


# ─────────────────────────────────────────────────────────────────────────────
# Kalshi elections connector (public API — no auth required)
# ─────────────────────────────────────────────────────────────────────────────

def kalshi_discover(state: str, district: str) -> Optional[dict]:
    """Auto-discover the Kalshi event and sub-market tickers for a house race.

    Tries a set of known ticker patterns first (fast, 2–5 requests); falls
    back to scanning all events by title if every pattern misses.

    Args:
        state:    two-letter state code, e.g. "PA"
        district: district number as a string, e.g. "8" or "08" or "AL"

    Returns a metadata dict ready to seed into the DB row:
        event_ticker, candidate_market_ticker, opponent_market_ticker
    Or None if no matching market is found on Kalshi.
    """
    state = state.upper().strip()
    # Normalise: strip leading zeros for the "short" form, keep padded for the "long" form.
    d_raw = str(district).lstrip("0") or "0"        # "08" → "8",  "8" → "8"
    d_pad = d_raw.zfill(2) if d_raw != "AL" else "AL"  # "8" → "08", "AL" → "AL"

    # Kalshi uses two-digit year suffix for 2026 races.
    year = "26"

    patterns = [
        f"HOUSE{state}{d_raw}-{year}",            # HOUSEPA8-26   (PA-8 actual)
        f"HOUSE{state}{d_pad}-{year}",            # HOUSEPA08-26
        f"KXHOUSERACE-{state}{d_pad}-{year}",     # KXHOUSERACE-PA08-26 (most districts)
        f"KXHOUSERACE-{state}{d_raw}-{year}",     # KXHOUSERACE-PA8-26
        f"KXHOUSE-{state}{d_raw}-{year}",         # KXHOUSE-PA8-26
    ]

    for ticker in patterns:
        result = _kalshi_try_event(ticker)
        if result:
            log.info("kalshi_discover: found %s via pattern %s", ticker, ticker)
            return result

    # Pattern lookup failed — scan event titles.
    return _kalshi_scan_titles(state, d_raw, year)


def _kalshi_try_event(event_ticker: str) -> Optional[dict]:
    """Try one specific event ticker; return metadata dict or None."""
    url = f"{KALSHI_ELECTIONS_BASE}/events/{event_ticker}"
    try:
        resp = httpx.get(url, params={"with_nested_markets": "true"}, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
    except Exception:
        return None
    event = resp.json().get("event", {})
    return _metadata_from_event(event)


def _metadata_from_event(event: dict) -> Optional[dict]:
    """Extract the metadata dict from a Kalshi event dict (with nested markets)."""
    ticker = event.get("event_ticker", "")
    markets = event.get("markets") or []
    if not markets:
        return None
    d_mkt = next((m["ticker"] for m in markets if m.get("ticker", "").endswith("-D")), None)
    r_mkt = next((m["ticker"] for m in markets if m.get("ticker", "").endswith("-R")), None)
    if not d_mkt and not r_mkt:
        return None
    return {
        "event_ticker": ticker,
        "candidate_market_ticker": d_mkt or "",
        "opponent_market_ticker": r_mkt or "",
    }


def _kalshi_scan_titles(state: str, district: str, year: str) -> Optional[dict]:
    """Scan all Kalshi events looking for a title match for the given race.

    Checks titles for patterns like "PA-8", "PA-08", "Pennsylvania 8".
    Reads at most 2 000 events before giving up.
    """
    needles = {
        f"{state}-{district} ",
        f"{state}-{district.zfill(2)} ",
        f"{state}-{district}-",
        f"{state}{district}-",
    }
    url = f"{KALSHI_ELECTIONS_BASE}/events"
    cursor = ""
    scanned = 0
    while scanned < 2000:
        params: dict = {"limit": 200, "with_nested_markets": "true"}
        if cursor:
            params["cursor"] = cursor
        try:
            resp = httpx.get(url, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log.warning("kalshi_discover title-scan: %s", e)
            return None
        events = data.get("events") or []
        for event in events:
            title = (event.get("title") or "").upper()
            ticker = (event.get("event_ticker") or "").upper()
            if any(n.upper() in title or n.upper() in ticker for n in needles):
                result = _metadata_from_event(event)
                if result:
                    log.info("kalshi_discover: found %s via title scan", result["event_ticker"])
                    return result
        cursor = data.get("cursor", "")
        scanned += len(events)
        if not cursor or not events:
            break
    log.info("kalshi_discover: no market found for %s-%s after scanning %d events", state, district, scanned)
    return None


def kalshi_fetch(external_id: str, metadata: dict) -> Optional[FetchedSample]:
    """Pull current implied-win prices from the Kalshi elections API.

    `external_id` is the Kalshi event ticker (e.g. "KXHOUSERACE-PA08-26").
    `metadata` keys:
      - candidate_market_ticker: sub-market for the candidate (e.g. "KXHOUSERACE-PA08-26-D")
      - opponent_market_ticker:  sub-market for the opponent  (e.g. "KXHOUSERACE-PA08-26-R")

    The api.elections.kalshi.com endpoint is fully public — no API key or
    RSA-PSS signing required. Returns None if the event doesn't exist on
    Kalshi yet (404), which is the case for PA-08 until Kalshi lists it.
    """
    url = f"{KALSHI_ELECTIONS_BASE}/events/{external_id}"
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            resp = client.get(url, params={"with_nested_markets": "true"})
            if resp.status_code == 404:
                log.info("kalshi_fetch: event %s not yet listed on Kalshi", external_id)
                return None
            resp.raise_for_status()
            data = resp.json()
    except httpx.HTTPStatusError:
        raise
    except Exception as e:
        log.warning("kalshi_fetch: GET %s failed: %s", url, e)
        raise

    event = data.get("event") or {}
    markets = event.get("markets") or []
    if not markets:
        log.info("kalshi_fetch: event %s has no nested markets", external_id)
        return None

    cand_ticker = metadata.get("candidate_market_ticker", "")
    opp_ticker = metadata.get("opponent_market_ticker", "")

    cand_quote = _kalshi_yes_quote(markets, cand_ticker)
    opp_quote = _kalshi_yes_quote(markets, opp_ticker)

    if cand_quote is None and opp_quote is None:
        log.warning(
            "kalshi_fetch: neither candidate(%s) nor opponent(%s) found in event %s",
            cand_ticker, opp_ticker, external_id,
        )
        return None

    # Confidence-blend the two binary markets. See _blend_p_x_wins docstring.
    candidate_pct = _blend_p_x_wins(cand_quote, opp_quote)
    opponent_pct = _blend_p_x_wins(opp_quote, cand_quote)

    return FetchedSample(
        source_type="market",
        candidate_pct=candidate_pct,
        opponent_pct=opponent_pct,
        source_as_of=datetime.utcnow(),
        raw_response={
            "event_ticker": external_id,
            "candidate_market_ticker": cand_ticker,
            "opponent_market_ticker": opp_ticker,
            "candidate_quote": cand_quote.__dict__ if cand_quote else None,
            "opponent_quote": opp_quote.__dict__ if opp_quote else None,
            "candidate_pct": candidate_pct,
            "opponent_pct": opponent_pct,
        },
    )


def _kalshi_yes_quote(markets: list, ticker: str) -> Optional[MarketQuote]:
    """Pull the Yes-side quote (midpoint + spread) for a Kalshi sub-market.

    Prefers yes_bid_dollars/yes_ask_dollars (live order book) which gives
    us the spread for confidence-weighting. Falls back to last_price_dollars
    when bid/ask aren't populated — in that case the spread is unknown so
    we mark it None and the blender treats it as full confidence (the
    last-trade price is generally a meaningful signal when bid/ask are
    missing).
    """
    if not ticker:
        return None
    for m in markets:
        if m.get("ticker") != ticker:
            continue

        # Preferred path: live bid/ask gives us spread.
        bid = m.get("yes_bid_dollars")
        ask = m.get("yes_ask_dollars")
        if bid is not None and ask is not None:
            try:
                bid_f, ask_f = float(bid), float(ask)
                midpoint = (bid_f + ask_f) / 2.0 * 100.0
                spread = (ask_f - bid_f) * 100.0
                return MarketQuote(
                    midpoint_pct=round(midpoint, 2),
                    spread_pct=round(spread, 2),
                )
            except (TypeError, ValueError):
                pass

        # Fallback: last trade price (no spread info).
        last = m.get("last_price_dollars")
        if last:
            try:
                val = float(last)
                if val > 0:
                    return MarketQuote(
                        midpoint_pct=round(val * 100.0, 2),
                        spread_pct=None,
                    )
            except (TypeError, ValueError):
                pass
    return None


def kalshi_backfill_history(
    source: str, metadata: dict, *, days_back: int = 90,
) -> dict:
    """Pull historical daily prices from Kalshi and write past snapshots.

    Mirrors polymarket_backfill_history. For each of candidate/opponent
    sub-markets, fetches one OHLC candle per day from the elections
    candlesticks endpoint, takes the trade-price close (or yes_bid/ask
    midpoint when no trades occurred), and writes one snapshot per day.

    Idempotent: the UNIQUE (source, captured_at) constraint dedupes, so
    re-running just fills gaps. If Kalshi listed the market more recently
    than days_back days ago, the returned series naturally starts at the
    listing date.
    """
    from app.db import SessionLocal
    from app.services.race_sentiment_sync import record_sample

    cand_ticker = str(metadata.get("candidate_market_ticker") or "")
    opp_ticker = str(metadata.get("opponent_market_ticker") or "")
    if not cand_ticker and not opp_ticker:
        return {"written": 0, "error": "no market tickers in metadata"}

    # Kalshi's candlesticks endpoint is keyed by series + market. The series
    # ticker is the prefix before the first dash (e.g. "HOUSEPA8-26-D" → "HOUSEPA8").
    series_ticker = metadata.get("series_ticker")
    if not series_ticker:
        ref = cand_ticker or opp_ticker
        series_ticker = ref.split("-", 1)[0] if "-" in ref else ref
    if not series_ticker:
        return {"written": 0, "error": "could not derive series_ticker"}

    cand_series = _kalshi_candlesticks(series_ticker, cand_ticker, days_back) if cand_ticker else {}
    opp_series = _kalshi_candlesticks(series_ticker, opp_ticker, days_back) if opp_ticker else {}

    # Merge by date — same approach as polymarket_backfill_history.
    by_date: dict[str, dict] = {}
    for ts, pct in cand_series.items():
        d = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
        by_date.setdefault(d, {"ts": ts})["candidate_pct"] = round(pct, 2)
        by_date[d]["ts"] = max(by_date[d]["ts"], ts)
    for ts, pct in opp_series.items():
        d = datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
        by_date.setdefault(d, {"ts": ts})["opponent_pct"] = round(pct, 2)
        by_date[d]["ts"] = max(by_date[d]["ts"], ts)

    written = 0
    skipped = 0
    with SessionLocal() as db:
        for d in sorted(by_date.keys()):
            bucket = by_date[d]
            captured = datetime.utcfromtimestamp(bucket["ts"])
            sample = FetchedSample(
                source_type="market",
                candidate_pct=bucket.get("candidate_pct"),
                opponent_pct=bucket.get("opponent_pct"),
                source_as_of=captured,
                raw_response={"backfill_date": d},
            )
            snap = record_sample(
                db, source, sample,
                also_update_current=False,
                captured_at=captured,
            )
            if snap is None:
                skipped += 1
            else:
                written += 1

        # Refresh the current-value row with the latest live fetch.
        try:
            live = kalshi_fetch(metadata.get("event_ticker") or "", metadata)
            if live:
                record_sample(db, source, live, also_update_current=True)
        except Exception as e:
            log.warning("kalshi_backfill_history: live refresh failed: %s", e)

    return {"written": written, "skipped_dedup": skipped}


def _kalshi_candlesticks(
    series_ticker: str, market_ticker: str, days_back: int,
) -> dict[int, float]:
    """Fetch daily-close prices for one Kalshi market. Returns {unix_ts: percent_0_to_100}.

    Uses period_interval=1440 (one day per candle). Prefers price.close_dollars
    (last trade in the window); falls back to the midpoint of yes_bid/yes_ask
    close when no trades occurred that day.

    Kalshi quotes prices as dollar strings ("0.5100" = $0.51 = 51% implied),
    so we multiply by 100 to match the schema's 0–100 percentage convention.
    """
    end_ts = int(time.time())
    start_ts = end_ts - days_back * 86400
    url = f"{KALSHI_ELECTIONS_BASE}/series/{series_ticker}/markets/{market_ticker}/candlesticks"
    params = {
        "start_ts": start_ts,
        "end_ts": end_ts,
        "period_interval": 1440,  # one day per candle
    }
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            resp = client.get(url, params=params)
            if resp.status_code == 404:
                log.info("_kalshi_candlesticks: %s/%s not found (404)", series_ticker, market_ticker)
                return {}
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        log.warning("_kalshi_candlesticks: GET %s failed: %s", url, e)
        return {}

    out: dict[int, float] = {}
    for c in data.get("candlesticks") or []:
        ts = c.get("end_period_ts")
        if not ts:
            continue
        # Prefer last trade close.
        price_block = c.get("price") or {}
        close = price_block.get("close_dollars")
        if close is None:
            # Fall back to midpoint of bid/ask close.
            bid = (c.get("yes_bid") or {}).get("close_dollars")
            ask = (c.get("yes_ask") or {}).get("close_dollars")
            if bid is not None and ask is not None:
                try:
                    close = (float(bid) + float(ask)) / 2.0
                except (TypeError, ValueError):
                    close = None
        if close is None:
            continue
        try:
            out[int(ts)] = float(close) * 100.0
        except (TypeError, ValueError):
            continue
    return out


def _clob_prices_history(token_id: str, days_back: int) -> dict[int, float]:
    """Fetch the CLOB price history for one token. Returns {unix_ts: price}.

    Uses `interval=1d` for daily granularity. The CLOB API accepts:
      - market: clob token id
      - interval: 1h | 6h | 1d | 1w | 1m | max
      - fidelity: granularity hint (minutes)
      - startTs/endTs: unix seconds (alternative to interval)

    We default to interval-based fetch with a fidelity that matches days_back.
    """
    # Pick interval based on requested window.
    if days_back <= 1:
        interval = "1h"
    elif days_back <= 7:
        interval = "1w"
    elif days_back <= 31:
        interval = "1m"
    else:
        # The 'max' interval returns all history, which we then trim.
        interval = "max"

    url = f"{CLOB_BASE}/prices-history"
    params = {"market": token_id, "interval": interval, "fidelity": 60}
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        log.warning("_clob_prices_history: GET %s failed: %s", url, e)
        return {}

    history = data.get("history") or []
    cutoff_ts = time.time() - (days_back * 86400)
    out: dict[int, float] = {}
    for pt in history:
        ts = int(pt.get("t") or 0)
        price = pt.get("p")
        if ts < cutoff_ts:
            continue
        if price is None:
            continue
        try:
            out[ts] = float(price)
        except (TypeError, ValueError):
            continue
    return out
