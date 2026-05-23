"""
Frame × Trend cross-signal detector.

Joins three time series per frame:
  - Article volume (this week vs prior baseline) — already computed in narrative_frames.
  - Google Trends interest (this week vs prior baseline) — from GoogleTrendSnapshot.

Output: one of four signal types per frame, written to NarrativeFrame.momentum_signal:
  - "viral"            — both article volume AND search interest are well above baseline
  - "missing_coverage" — search is spiking but article volume is flat (voters are
                         interested, no narrative is reaching them)
  - "elite_only"       — articles spiking but search flat (journalist obsession,
                         not voter concern)
  - "stable"           — neither metric is spiking

Run nightly (or on-demand). Cheap: ~50 frames × small DB queries = a few seconds.

Term matching is keyword-based for the MVP — for each frame, finds tracked
trend terms whose words overlap the frame name. Embedding-based matching can
replace this later (small upgrade) but the keyword approach is good enough
when frame names are descriptive.
"""
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    GoogleTrendSnapshot,
    NarrativeFrame,
    SourceItem,
    FrameClusterMatch,
    StoryCluster,
)

logger = logging.getLogger(__name__)


# Thresholds — these ARE arbitrary, but they're documented and tunable.
# Velocity = (this_week_count) / max(baseline_weekly, 1).
# >= 2.0 means "doubled vs baseline" — a defensible bar for "spiking".
SPIKE_THRESHOLD = 2.0
# A frame is "active" (worth correlating) only if it has at least this many
# articles in the trailing 30 days — avoids analyzing dead frames.
MIN_ACTIVE_ARTICLES = 5
# Stop-words removed when extracting frame keywords for term matching.
_STOPWORDS = {
    "the", "a", "an", "of", "for", "to", "in", "on", "and", "or",
    "is", "are", "be", "by", "with", "from", "as", "at", "that",
    "this", "his", "her", "their", "its", "s",
}


def _frame_keywords(frame_name: str) -> set[str]:
    """Extract content words from a frame name for keyword overlap matching."""
    tokens = re.findall(r"[A-Za-z]+", (frame_name or "").lower())
    return {t for t in tokens if t not in _STOPWORDS and len(t) >= 3}


def _terms_matching_frame(
    frame_name: str, candidate_terms: list[str],
) -> list[str]:
    """Return trend terms whose words overlap the frame name's keywords."""
    fkw = _frame_keywords(frame_name)
    if not fkw:
        return []
    matched = []
    for term in candidate_terms:
        term_words = set(re.findall(r"[A-Za-z]+", term.lower())) - _STOPWORDS
        if fkw & term_words:
            matched.append(term)
    return matched


def _interest_velocity(
    db: Session, terms: list[str], geo: str = "US-PA",
) -> tuple[float, float, float]:
    """For the given trend terms, return (this_week_avg, prior_3_week_avg, velocity).

    'this_week_avg' = average interest score over last 7 days across all terms.
    'prior_3_week_avg' = average interest over days 8-28.
    'velocity' = this_week_avg / max(prior_3_week_avg, 1).
    """
    if not terms:
        return 0.0, 0.0, 0.0

    now = datetime.utcnow()
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=28)

    this_week = db.query(func.avg(GoogleTrendSnapshot.interest)).filter(
        GoogleTrendSnapshot.term.in_(terms),
        GoogleTrendSnapshot.geo == geo,
        GoogleTrendSnapshot.snapshot_date >= week_ago,
    ).scalar() or 0.0

    prior = db.query(func.avg(GoogleTrendSnapshot.interest)).filter(
        GoogleTrendSnapshot.term.in_(terms),
        GoogleTrendSnapshot.geo == geo,
        GoogleTrendSnapshot.snapshot_date >= month_ago,
        GoogleTrendSnapshot.snapshot_date < week_ago,
    ).scalar() or 0.0

    velocity = float(this_week) / max(float(prior), 1.0)
    return float(this_week), float(prior), velocity


def _frame_article_velocity(
    db: Session, frame_id: int,
) -> tuple[int, int, float]:
    """Return (this_week_articles, prior_3_week_articles, velocity) for a frame."""
    now = datetime.utcnow()
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=28)

    # Article count via FrameClusterMatch → StoryCluster → SourceItem (published_at)
    q = (
        db.query(func.count(func.distinct(SourceItem.id)))
        .select_from(FrameClusterMatch)
        .join(StoryCluster, StoryCluster.id == FrameClusterMatch.story_cluster_id)
        .join(SourceItem, SourceItem.story_cluster_id == StoryCluster.id)
        .filter(FrameClusterMatch.frame_id == frame_id)
    )

    this_week = (
        q.filter(SourceItem.published_at >= week_ago).scalar() or 0
    )
    prior = (
        q.filter(
            SourceItem.published_at >= month_ago,
            SourceItem.published_at < week_ago,
        ).scalar() or 0
    )
    velocity = this_week / max(prior / 3.0, 1.0)  # prior is 3 weeks, normalize
    return int(this_week), int(prior), float(velocity)


def _classify(article_v: float, trend_v: float) -> str:
    """Categorize a frame into one of four signal types."""
    article_spike = article_v >= SPIKE_THRESHOLD
    trend_spike = trend_v >= SPIKE_THRESHOLD
    if article_spike and trend_spike:
        return "viral"
    if trend_spike and not article_spike:
        return "missing_coverage"
    if article_spike and not trend_spike:
        return "elite_only"
    return "stable"


def analyze_all_frames(db: Session) -> dict:
    """Compute the momentum signal for every active frame.

    Writes results to NarrativeFrame.momentum_signal / momentum_data and
    returns a summary dict for logging.
    """
    now = datetime.utcnow()

    # 1. Pull tracked trend terms (snapshots' unique terms).
    all_terms = [
        r[0] for r in db.query(GoogleTrendSnapshot.term).distinct().all()
    ]
    if not all_terms:
        logger.info("frame_momentum: no trend snapshots — skipping run")
        return {"frames_analyzed": 0, "reason": "no_trend_data"}

    frames = db.query(NarrativeFrame).filter(NarrativeFrame.active == True).all()

    by_signal: dict[str, int] = {}
    analyzed = 0

    for f in frames:
        # Match frame to trend terms by keyword overlap.
        matched_terms = _terms_matching_frame(f.name, all_terms)
        # If no specific match, fall back to campaign-wide trend signal —
        # we still want to know if any campaign-relevant search activity
        # correlates with this frame's article spike.
        terms_for_trend = matched_terms or all_terms

        # Article velocity for this frame.
        art_tw, art_prior, art_v = _frame_article_velocity(db, f.id)
        if art_tw + art_prior < MIN_ACTIVE_ARTICLES:
            # Frame has too little volume to draw conclusions from.
            continue

        # Trend velocity for matched terms.
        trend_tw, trend_prior, trend_v = _interest_velocity(db, terms_for_trend)

        signal = _classify(art_v, trend_v)
        data = {
            "article_velocity": round(art_v, 2),
            "trend_velocity": round(trend_v, 2),
            "this_week_articles": art_tw,
            "prior_3w_articles": art_prior,
            "this_week_trend_avg": round(trend_tw, 1),
            "prior_3w_trend_avg": round(trend_prior, 1),
            "matched_terms": matched_terms,
            "trend_terms_used": terms_for_trend if not matched_terms else matched_terms,
            "used_fallback_terms": not bool(matched_terms),
        }

        f.momentum_signal = signal
        f.momentum_signal_at = now
        f.momentum_data = json.dumps(data)
        by_signal[signal] = by_signal.get(signal, 0) + 1
        analyzed += 1

    db.commit()
    logger.info(
        "frame_momentum: analyzed=%d signals=%s", analyzed, by_signal,
    )
    return {"frames_analyzed": analyzed, "by_signal": by_signal}
