"""Unit tests for the confidence-weighted prediction-market blending.

The blender combines two binary-market "Yes" quotes (one per side of a
race) into a single estimate of each side's win probability. The key
behaviors:

  • Two liquid markets → both midpoints contribute, weighted by spread
  • One liquid + one dead market → blend collapses to the liquid side
  • Both dead → fall back to whatever midpoint we have
  • No quotes at all → None

See `_blend_p_x_wins` and `_confidence` in prediction_market_monitor.py.
"""
from __future__ import annotations

from app.services.prediction_market_monitor import (
    MarketQuote,
    _DEAD_MARKET_SPREAD_THRESHOLD_PCT,
    _blend_p_x_wins,
    _confidence,
)


# ─────────────────────────────────────────────────────────────────────────────
# _confidence
# ─────────────────────────────────────────────────────────────────────────────

def test_confidence_zero_spread_is_full():
    assert _confidence(0.0) == 1.0


def test_confidence_at_threshold_is_zero():
    assert _confidence(_DEAD_MARKET_SPREAD_THRESHOLD_PCT) == 0.0


def test_confidence_above_threshold_is_zero():
    assert _confidence(50.0) == 0.0
    assert _confidence(100.0) == 0.0


def test_confidence_decays_linearly_inside_threshold():
    half = _DEAD_MARKET_SPREAD_THRESHOLD_PCT / 2.0
    assert _confidence(half) == 0.5


def test_confidence_handles_unknown_spread():
    # Legacy quotes (no bid/ask) get full confidence rather than discarded.
    assert _confidence(None) == 1.0


# ─────────────────────────────────────────────────────────────────────────────
# _blend_p_x_wins
# ─────────────────────────────────────────────────────────────────────────────

def test_blend_returns_none_when_no_quotes():
    assert _blend_p_x_wins(None, None) is None


def test_blend_one_sided_uses_only_quote():
    # Only have the X-side quote — should return its midpoint.
    q = MarketQuote(midpoint_pct=62.0, spread_pct=2.0)
    assert _blend_p_x_wins(q, None) == 62.0


def test_blend_one_sided_from_other_inverts():
    # Only have the "other" side — should return 100 - other_midpoint.
    other = MarketQuote(midpoint_pct=40.0, spread_pct=2.0)
    assert _blend_p_x_wins(None, other) == 60.0


def test_blend_two_liquid_markets_averages_midpoints():
    # Symmetrical case: both midpoints suggest P(X wins) = 60.
    # Equal confidence → simple average.
    x = MarketQuote(midpoint_pct=60.0, spread_pct=2.0)
    other = MarketQuote(midpoint_pct=40.0, spread_pct=2.0)
    # From x: 60. From other: 100-40 = 60. Equal weights → 60.
    assert _blend_p_x_wins(x, other) == 60.0


def test_blend_disagreement_between_liquid_markets_lands_between():
    # X market says 62. Other says 49 (implying 51 for X). Equal-spread
    # weights → simple average of 62 and 51 = 56.5.
    x = MarketQuote(midpoint_pct=62.0, spread_pct=2.0)
    other = MarketQuote(midpoint_pct=49.0, spread_pct=2.0)
    result = _blend_p_x_wins(x, other)
    assert result is not None
    assert 56.0 <= result <= 57.0  # ~56.5


def test_blend_dead_other_market_collapses_to_x_side():
    # PA-08 Polymarket case: D market liquid (spread 2), R market dead
    # (spread 73). The R-implied estimate should get ~zero weight, so
    # the blended P(D wins) ≈ D market's midpoint of 62.
    x = MarketQuote(midpoint_pct=62.0, spread_pct=2.0)
    other_dead = MarketQuote(midpoint_pct=45.5, spread_pct=73.0)
    result = _blend_p_x_wins(x, other_dead)
    assert result is not None
    assert abs(result - 62.0) < 0.5


def test_blend_dead_x_market_uses_other_side_only():
    # Mirror of the above: if X is dead but other is liquid, the answer
    # should come from `1 - other_midpoint`.
    x_dead = MarketQuote(midpoint_pct=50.0, spread_pct=73.0)
    other = MarketQuote(midpoint_pct=40.0, spread_pct=2.0)
    # `1 - 40 = 60` is the only signal.
    result = _blend_p_x_wins(x_dead, other)
    assert result is not None
    assert abs(result - 60.0) < 0.5


def test_blend_both_dead_falls_back_to_x_midpoint():
    # When both confidences are zero, prefer the X-side midpoint rather
    # than returning None — degraded output is more useful than nothing.
    x_dead = MarketQuote(midpoint_pct=50.0, spread_pct=80.0)
    other_dead = MarketQuote(midpoint_pct=40.0, spread_pct=70.0)
    assert _blend_p_x_wins(x_dead, other_dead) == 50.0


def test_blend_unknown_spreads_treated_as_full_confidence():
    # Legacy code path: bid/ask weren't extracted, only midpoint. Both
    # quotes get full weight, so the result averages them.
    x = MarketQuote(midpoint_pct=60.0, spread_pct=None)
    other = MarketQuote(midpoint_pct=42.0, spread_pct=None)
    # From x: 60. From other: 58. Average = 59.
    result = _blend_p_x_wins(x, other)
    assert result is not None
    assert abs(result - 59.0) < 0.1


def test_blend_complement_symmetry_for_pa08_polymarket_today():
    # Same realistic inputs from PA-08 on 2026-05-29:
    #   D market: midpoint 62, spread 2  (liquid)
    #   R market: midpoint 45.5, spread 73 (dead)
    # P(D wins) ≈ 62.  P(R wins) ≈ 38.  Lead = 24.
    d = MarketQuote(midpoint_pct=62.0, spread_pct=2.0)
    r = MarketQuote(midpoint_pct=45.5, spread_pct=73.0)
    p_d = _blend_p_x_wins(d, r)
    p_r = _blend_p_x_wins(r, d)
    assert p_d is not None and p_r is not None
    assert abs(p_d - 62.0) < 0.5
    assert abs(p_r - 38.0) < 0.5
    assert abs((p_d - p_r) - 24.0) < 1.0
