"""
Frame × Trend cross-signal detector.

Joins three press-side time series with one voter-side time series per frame:
  - Outlet velocity  — distinct outlets carrying any matching article (this
                       week vs prior 3-week baseline). Measures breadth of
                       editorial decisions to cover; robust to clustering
                       quality. A wire story across 5 outlets = 5 outlets.
  - Cluster velocity — distinct story clusters matching the frame. Measures
                       novelty / number of unique angles.
  - Article velocity — raw article count. Kept for context but NOT used in
                       spike classification (it conflates breadth and depth
                       in one number).
  - Trend velocity   — Google Trends interest (voter search). Orthogonal
                       to press signal — answers "do voters care?"

Output: one of six signal types per frame, written to NarrativeFrame.momentum_signal:

  - "viral"            — outlets spike AND voter search spike. Broad press
                         coverage × aligned voter interest. Highest urgency.
  - "amplified"        — outlets spike, voter search flat. Wire / press-release
                         pickup is broadcasting but voters aren't searching
                         yet. Distinct from viral: amplification without
                         demand. Often precedes a viral phase OR fizzles.
  - "elite_only"       — clusters spike but outlets flat. Few outlets writing
                         many angles — beat-reporter obsession, low voter reach.
  - "missing_coverage" — voter search spike, press flat. Unmet demand —
                         voters interested but the press isn't reaching them.
  - "stable"           — none of the above.
  - "no_trend_signal"  — no tracked trend terms overlap this frame's keywords,
                         so we have no orthogonal voter-side signal to
                         correlate against. Expand tracked terms in
                         google_trends.py to shrink this bucket.

Key design decision: outlet count is the primary press-spike signal, not
article count. Outlet count captures amplification (independent editorial
decisions to cover) and is immune to wire-fragmentation in the clustering
algorithm. Cluster velocity is the secondary signal that distinguishes
wire pickup (1 cluster × N outlets → outlets spike, clusters flat) from
beat-reporter obsession (N clusters × 1 outlet → outlets flat, clusters
spike).

Run nightly (or on-demand). Cheap: ~50 frames × one query each = a few seconds.

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


class _PressCounts:
    """Three orthogonal volume measures for a frame in a time window.

    outlets   — distinct outlet_ids carrying any article matching the frame.
                Measures BREADTH of editorial decisions to cover — robust to
                clustering quality. A wire story across 5 outlets = 5 outlets
                even if clustering fragments it into multiple clusters.

    clusters  — distinct story_cluster_ids matching the frame. Measures
                NOVELTY — how many unique stories/angles exist. Wire pickup
                of one release = 1 cluster (when clustering works correctly).

    articles  — raw article count. Measures total EXPOSURE volume — repeated
                coverage by the same outlet inflates this; wire pickup
                inflates this. Kept for context, not for spike classification.
    """
    __slots__ = ("outlets", "clusters", "articles")

    def __init__(self, outlets: int, clusters: int, articles: int):
        self.outlets = outlets
        self.clusters = clusters
        self.articles = articles


def _frame_press_counts(
    db: Session, frame_id: int, start: datetime, end: Optional[datetime] = None,
) -> _PressCounts:
    """Count outlets, clusters, and articles matching a frame in [start, end)."""
    q = (
        db.query(
            func.count(func.distinct(SourceItem.outlet_id)),
            func.count(func.distinct(FrameClusterMatch.story_cluster_id)),
            func.count(func.distinct(SourceItem.id)),
        )
        .select_from(FrameClusterMatch)
        .join(SourceItem,
              SourceItem.story_cluster_id == FrameClusterMatch.story_cluster_id)
        .filter(
            FrameClusterMatch.frame_id == frame_id,
            SourceItem.published_at.isnot(None),
            SourceItem.published_at >= start,
        )
    )
    if end is not None:
        q = q.filter(SourceItem.published_at < end)
    row = q.one()
    return _PressCounts(int(row[0] or 0), int(row[1] or 0), int(row[2] or 0))


def _velocity(this_week: int, prior_3w: int) -> float:
    """Ratio of this-week count to a normalized weekly baseline from the
    prior 3 weeks. 1.0 floor on the denominator avoids divide-by-zero and
    keeps tiny baselines from producing absurd ratios."""
    return this_week / max(prior_3w / 3.0, 1.0)


def _classify(outlet_v: float, cluster_v: float, trend_v: float) -> str:
    """Categorize a frame into one of five signal types.

    Decision order (most specific first):
      - outlets spike + trend spike → "viral" (broad press × voter attention)
      - outlets spike (no trend)    → "amplified" (wire/PR pickup, voters quiet)
      - clusters spike (no outlets) → "elite_only" (narrow press, many angles)
      - trend spike (no press)      → "missing_coverage" (unmet voter demand)
      - otherwise                   → "stable"

    Outlet velocity is the dominant press signal because it captures
    AMPLIFICATION (how many independent outlets chose to carry the story).
    Cluster velocity is the secondary press signal — it distinguishes wire
    pickup (one cluster × many outlets) from beat-reporter obsession
    (many clusters × few outlets).
    """
    outlets_spike = outlet_v >= SPIKE_THRESHOLD
    clusters_spike = cluster_v >= SPIKE_THRESHOLD
    trend_spike = trend_v >= SPIKE_THRESHOLD

    if outlets_spike and trend_spike:
        return "viral"
    if outlets_spike:
        return "amplified"
    if clusters_spike:
        return "elite_only"
    if trend_spike:
        return "missing_coverage"
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

    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=28)

    for f in frames:
        # Match frame to trend terms by keyword overlap.
        matched_terms = _terms_matching_frame(f.name, all_terms)

        # Press counts for this frame — outlets, clusters, articles for both
        # the trailing 7 days and the 21 days before that.
        tw = _frame_press_counts(db, f.id, start=week_ago)
        prior = _frame_press_counts(db, f.id, start=month_ago, end=week_ago)

        if tw.articles + prior.articles < MIN_ACTIVE_ARTICLES:
            # Frame has too little volume to draw conclusions from.
            continue

        outlet_v = _velocity(tw.outlets, prior.outlets)
        cluster_v = _velocity(tw.clusters, prior.clusters)
        article_v = _velocity(tw.articles, prior.articles)

        # Bug fix (2026-05-24): if no trend term matches this frame's keywords,
        # we have NO orthogonal signal to correlate against. Previously this
        # branch fell back to `all_terms` (typically just "Bresnahan" + "Cognetti")
        # which are constantly searched — so `trend_v` reliably exceeded the
        # spike threshold for every frame, and combined with any article
        # spike, ~all frames classified as "viral". 13 of 15 active frames
        # had momentum_signal="viral" before this fix, hiding any real signal.
        #
        # The right thing is to admit we can't classify and record that
        # explicitly so the user can see the gap. To unlock real classification
        # for more frames, expand the tracked trend terms in google_trends.py
        # (frame keywords, issue names, etc.) — once any frame matches at
        # least one term, this branch stops firing for it.
        if not matched_terms:
            signal = "no_trend_signal"
            data = {
                "outlet_velocity": round(outlet_v, 2),
                "cluster_velocity": round(cluster_v, 2),
                "article_velocity": round(article_v, 2),
                "this_week_outlets": tw.outlets,
                "this_week_clusters": tw.clusters,
                "this_week_articles": tw.articles,
                "prior_3w_outlets": prior.outlets,
                "prior_3w_clusters": prior.clusters,
                "prior_3w_articles": prior.articles,
                "reason": "no trend terms matched this frame's keywords",
                "frame_keywords": sorted(_frame_keywords(f.name)),
                "available_trend_terms": all_terms,
            }
            f.momentum_signal = signal
            f.momentum_signal_at = now
            f.momentum_data = json.dumps(data)
            by_signal[signal] = by_signal.get(signal, 0) + 1
            analyzed += 1
            continue

        # Trend velocity for matched terms (specific to this frame).
        trend_tw, trend_prior, trend_v = _interest_velocity(db, matched_terms)

        signal = _classify(outlet_v, cluster_v, trend_v)
        data = {
            "outlet_velocity": round(outlet_v, 2),
            "cluster_velocity": round(cluster_v, 2),
            "article_velocity": round(article_v, 2),
            "trend_velocity": round(trend_v, 2),
            "this_week_outlets": tw.outlets,
            "this_week_clusters": tw.clusters,
            "this_week_articles": tw.articles,
            "prior_3w_outlets": prior.outlets,
            "prior_3w_clusters": prior.clusters,
            "prior_3w_articles": prior.articles,
            "this_week_trend_avg": round(trend_tw, 1),
            "prior_3w_trend_avg": round(trend_prior, 1),
            "matched_terms": matched_terms,
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
