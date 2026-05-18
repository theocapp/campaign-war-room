"""The single write boundary for cluster-native frame matches and opponent
activities.

Every code path that previously wrote `NarrativeFrameMention` or
`OpponentActivity` should call these helpers instead (Phase D will remove the
legacy writes; Phase A calls these *in addition to* the legacy writes for
parity).

The UNIQUE constraints on `frame_cluster_matches(frame_id, story_cluster_id)`
and `cluster_opponent_activities(opponent_id, story_cluster_id, fingerprint)`
are the correctness backbone. These helpers use SQLite's
`INSERT … ON CONFLICT … DO UPDATE` so concurrent or repeated calls never
violate the constraint and historical first-seen timestamps are preserved.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def upsert_frame_match(
    db: Session,
    *,
    frame_id: int,
    cluster_id: str,
    confidence: int = 75,
    source_type: str = "cluster_runtime",
    matched_by: str = "llm",
    representative_snapshot_ts: Optional[datetime] = None,
) -> None:
    """Insert a FrameClusterMatch row, or update confidence/last_seen on an
    existing one. Idempotent.

    `confidence` is monotonic: an UPSERT only raises it, never lowers it.
    `first_seen_at` is preserved on conflict; `last_seen_at` is bumped.
    """
    snapshot = (representative_snapshot_ts or datetime.utcnow()).isoformat()
    now_iso = _now_iso()
    db.execute(
        text(
            """
            INSERT INTO frame_cluster_matches
              (frame_id, story_cluster_id, confidence, matched_by, source_type,
               representative_snapshot_ts, first_seen_at, last_seen_at)
            VALUES
              (:frame_id, :cluster_id, :confidence, :matched_by, :source_type,
               :snapshot, :now, :now)
            ON CONFLICT(frame_id, story_cluster_id) DO UPDATE SET
              confidence = MAX(excluded.confidence, frame_cluster_matches.confidence),
              last_seen_at = excluded.last_seen_at,
              source_type = excluded.source_type,
              representative_snapshot_ts = excluded.representative_snapshot_ts
            """
        ),
        {
            "frame_id": frame_id,
            "cluster_id": cluster_id,
            "confidence": confidence,
            "matched_by": matched_by,
            "source_type": source_type,
            "snapshot": snapshot,
            "now": now_iso,
        },
    )


def _opponent_fingerprint(claim: Optional[str], attack: Optional[str], promise: Optional[str]) -> str:
    """Stable hash of normalized quote text — dedup key within (opponent, cluster).

    Uses the first non-empty of claim/attack/promise. Matches the spirit of
    opponent_analysis._activity_fingerprint without taking a hard dependency.
    """
    text_val = (attack or claim or promise or "").strip().lower()
    text_val = " ".join(text_val.split())  # collapse whitespace
    return hashlib.blake2b(text_val.encode("utf-8"), digest_size=16).hexdigest()


def upsert_opponent_activity(
    db: Session,
    *,
    opponent_id: int,
    cluster_id: str,
    claim: Optional[str] = None,
    attack: Optional[str] = None,
    promise: Optional[str] = None,
    fingerprint: Optional[str] = None,
    source_type: str = "cluster_runtime",
) -> None:
    """Insert a ClusterOpponentActivity row keyed by (opponent, cluster,
    fingerprint). If a row with that key already exists, bump last_seen_at and
    leave the original quote text untouched."""
    fp = fingerprint or _opponent_fingerprint(claim, attack, promise)
    if not fp:
        return
    now_iso = _now_iso()
    db.execute(
        text(
            """
            INSERT INTO cluster_opponent_activities
              (opponent_id, story_cluster_id, claim, attack, promise,
               fingerprint, source_type, first_seen_at, last_seen_at)
            VALUES
              (:opponent_id, :cluster_id, :claim, :attack, :promise,
               :fp, :source_type, :now, :now)
            ON CONFLICT(opponent_id, story_cluster_id, fingerprint) DO UPDATE SET
              last_seen_at = excluded.last_seen_at,
              source_type = excluded.source_type
            """
        ),
        {
            "opponent_id": opponent_id,
            "cluster_id": cluster_id,
            "claim": claim,
            "attack": attack,
            "promise": promise,
            "fp": fp,
            "source_type": source_type,
            "now": now_iso,
        },
    )
