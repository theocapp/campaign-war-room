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
    UTC.

    DAY-ALIGNED ROLLING WINDOWS. "now" is pinned to the start of the
    current UTC day, not the literal moment of the call. This means:
      - Within a single day, the cutoff never moves — every consumer
        called between 00:00 and 23:59 UTC sees the SAME this_week and
        last_week boundaries. So `this_week` is monotonically non-
        decreasing within a day: new articles only ADD to it, never
        remove. An article published 6d, 22h ago at 10am stays in
        "this week" all day even though it's literally past 7 days
        old by 5pm. It crosses the boundary at the next midnight,
        not mid-afternoon.
      - At 00:00 UTC the window shifts forward by exactly one day,
        and one day's worth of articles cross from this_week into
        last_week (and one day's worth ages out of last_week
        entirely). That's predictable, not surprising.

    Previously this used the literal moment of the call, which
    produced confusing within-day decreases as articles slid out of
    the window mid-afternoon — see Sessions G/H in INTER_SESSION.md.

    FUTURE WORK (heavier): snapshot the (frame_id, this_week, last_week,
    total) tuples to a DB table at a fixed daily cron (e.g. 05:00 UTC).
    Briefing + frame cards + landscape sidebar all read the snapshot
    instead of computing on the fly. Absolutely stable counts for every
    consumer on a given day, regardless of when the data is fetched.
    Skipped for now — day-aligned window addresses the worst of the
    surprise without the storage + cron overhead.
    """
    if now is None:
        now = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
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
