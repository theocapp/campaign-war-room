"""Single source of truth for frame mention counts and time windows.

Before this module existed, four endpoints computed "this week" differently:

  - /api/narrative-frames (Dashboard list): COUNT(FCM rows) WHERE FCM.first_seen_at >= 7d
  - /api/narrative-frames/{id}/detail:      COUNT(DISTINCT article id) via cluster, published >= 7d
  - /api/briefing/morning (Briefing):       COUNT(NFM rows) via article, published >= 7d
  - briefing_summary._narrative_pulse_block: COUNT(NFM rows) via article, published >= 7d

The first two read FCM via different lenses; the last two read the largely-stale
NarrativeFrameMention table. Same frame, four different "this week" numbers.

Canonical definition (Option C):

    "Mentions this week" = COUNT(DISTINCT story_cluster_id) where the frame
    matches the cluster AND any article in the cluster has published_at within
    the last 7 days.

Properties:
  - Wire-syndication-deduped: one syndicated story across 50 outlets = 1.
  - Reflects "fresh coverage" intuitively (not "when the matcher first ran").
  - Cluster-native — consistent with the post-Phase-D data model.
  - Doesn't depend on FCM.first_seen_at semantics (which are subtle).

All four endpoints above now compute this_week / last_week through this helper.
"""
from __future__ import annotations
from datetime import datetime, timedelta
from typing import Iterable, Optional

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.models import FrameClusterMatch, SourceItem


def week_window(now: Optional[datetime] = None) -> tuple[datetime, datetime, datetime]:
    """Return (cutoff_7d, cutoff_14d, now).

    "this week" = published_at >= cutoff_7d.
    "last week" = cutoff_14d <= published_at < cutoff_7d.
    UTC. Rolling 7-day windows, not calendar-week ISO boundaries.
    """
    now = now or datetime.utcnow()
    return now - timedelta(days=7), now - timedelta(days=14), now


def frame_pulse_counts(
    db: Session,
    frame_ids: Iterable[int],
    now: Optional[datetime] = None,
) -> dict[int, tuple[int, int, int]]:
    """For each frame_id, return (this_week, last_week, total) using the
    canonical Option-C definition.

    One GROUP BY query. Returns an empty tuple (0, 0, 0) for any frame_id with
    no matches so callers can index by id without KeyError.
    """
    frame_ids = list(frame_ids)
    if not frame_ids:
        return {}

    cutoff_7d, cutoff_14d, _ = week_window(now)

    # COUNT(DISTINCT CASE WHEN ... THEN cluster_id END) — counts distinct
    # cluster ids that satisfy the predicate. A cluster with articles in both
    # this-week and last-week is counted once in each bucket.
    rows = (
        db.query(
            FrameClusterMatch.frame_id,
            func.count(func.distinct(
                case((SourceItem.published_at >= cutoff_7d,
                      FrameClusterMatch.story_cluster_id))
            )).label("this_week"),
            func.count(func.distinct(
                case((
                    (SourceItem.published_at >= cutoff_14d) &
                    (SourceItem.published_at < cutoff_7d),
                    FrameClusterMatch.story_cluster_id,
                ))
            )).label("last_week"),
            func.count(func.distinct(FrameClusterMatch.story_cluster_id))
                .label("total"),
        )
        .select_from(FrameClusterMatch)
        .join(SourceItem,
              SourceItem.story_cluster_id == FrameClusterMatch.story_cluster_id)
        .filter(
            FrameClusterMatch.frame_id.in_(frame_ids),
            SourceItem.published_at.isnot(None),
        )
        .group_by(FrameClusterMatch.frame_id)
        .all()
    )

    out: dict[int, tuple[int, int, int]] = {
        fid: (int(tw or 0), int(lw or 0), int(tot or 0))
        for fid, tw, lw, tot in rows
    }
    for fid in frame_ids:
        out.setdefault(fid, (0, 0, 0))
    return out
