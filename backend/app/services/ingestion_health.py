"""Per-source ingestion-quality monitoring.

Two detectors run daily and write rows to `ingestion_health_alerts`:

- `short_body`: a source whose trailing-24h avg `raw_text` length is
  significantly below its trailing-7d baseline. Catches the
  Google-News-style collapse where a feed switches from full body to
  title-only.
- `silent`: a source that historically posted regularly but has been
  silent in the last 24h.

Alerts are mutated in-place (one row per (source_name, kind)). When the
underlying metric recovers, `resolved_at` is populated. The dashboard
notifications bell renders unresolved rows.

Design intent: surface the failure mode that bit us 2026-05-26 — Google
News stopped delivering body excerpts and nobody noticed for three days
because each article still scored relevance from the title. Article
counts looked normal; only body length collapsed. Alerts have to
trigger on metric drift, not on a "no articles" signal alone.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import IngestionHealthAlert, SourceItem

logger = logging.getLogger(__name__)

# A source needs at least this many articles in each comparison window
# before we'll fire an alert — too few samples and the avg is noise.
MIN_SAMPLES_BASELINE = 10
MIN_SAMPLES_CURRENT = 5

# Short-body detector thresholds.
SHORT_BODY_DROP_RATIO = 0.5        # current must be ≤ 50% of baseline
SHORT_BODY_ABSOLUTE_FLOOR = 300    # AND current must be < 300 chars

# Recovery happens when current avg climbs back to ≥ 70% of baseline.
# Asymmetric vs the firing threshold so the alert doesn't flap when the
# metric hovers right at the boundary.
SHORT_BODY_RECOVERY_RATIO = 0.7

# Silent detector: a source that historically posted ≥ this much per day
# on average over the last 30 days, but had 0 items in the last 24h.
SILENT_MIN_DAILY_BASELINE = 1.0


@dataclass
class SourceMetrics:
    """Snapshot of one source's quality numbers at a given moment."""
    source_name: str
    current_avg_len: float
    baseline_avg_len: float
    sample_count_24h: int
    sample_count_7d: int


def _compute_source_metrics(db: Session, now: datetime) -> list[SourceMetrics]:
    """Per-source avg raw_text length for last-24h vs trailing-7d windows.

    Excludes archived-as-irrelevant items because their raw_text is
    irrelevant to the question "is this source delivering useful body
    content for the things we care about?". Also excludes items where
    raw_text is NULL (rare — pre-LLM ingest path).
    """
    cutoff_24h = now - timedelta(hours=24)
    # Baseline is 7d EXCLUDING the last 24h — comparing current to the
    # window that's still drifting with the current values would dampen
    # the signal we're trying to catch.
    cutoff_baseline_start = now - timedelta(days=7)
    cutoff_baseline_end = cutoff_24h

    # Use char_length on raw_text. Postgres has length() and char_length();
    # SQLite has length(). Both behave consistently for our purposes
    # (raw_text is utf-8 text, not bytes).
    char_length = func.length(SourceItem.raw_text)

    # Two queries, one per window. Cheaper than a single CASE-based query
    # because the indexes on created_at + source_name are independent.
    def _agg(start, end):
        return (
            db.query(
                SourceItem.source_name,
                func.avg(char_length).label("avg_len"),
                func.count(SourceItem.id).label("n"),
            )
            .filter(
                SourceItem.created_at >= start,
                SourceItem.created_at < end,
                SourceItem.source_name.isnot(None),
                SourceItem.raw_text.isnot(None),
                SourceItem.archived_as_irrelevant == False,  # noqa: E712
            )
            .group_by(SourceItem.source_name)
            .all()
        )

    current_rows = {r.source_name: (float(r.avg_len), int(r.n)) for r in _agg(cutoff_24h, now)}
    baseline_rows = {r.source_name: (float(r.avg_len), int(r.n)) for r in _agg(cutoff_baseline_start, cutoff_baseline_end)}

    out: list[SourceMetrics] = []
    for name in baseline_rows:
        c_avg, c_n = current_rows.get(name, (0.0, 0))
        b_avg, b_n = baseline_rows[name]
        out.append(SourceMetrics(
            source_name=name,
            current_avg_len=c_avg,
            baseline_avg_len=b_avg,
            sample_count_24h=c_n,
            sample_count_7d=b_n,
        ))
    return out


def _upsert_alert(
    db: Session,
    *,
    source_name: str,
    kind: str,
    now: datetime,
    metrics: SourceMetrics,
    is_firing: bool,
) -> None:
    """Insert or update the alert row for (source_name, kind).

    `is_firing`:
      True  → set detected_at if it's a new alert, clear resolved_at
              (a previously-resolved alert that re-fires is treated as
              "new occurrence"; detected_at is bumped so the
              notification timestamp is honest about when the current
              episode started).
      False → if a row exists and was firing, set resolved_at.
              No-op for non-existent or already-resolved rows so we
              don't write a row just to say "everything is fine."
    """
    row = (
        db.query(IngestionHealthAlert)
        .filter_by(source_name=source_name, kind=kind)
        .first()
    )
    if is_firing:
        if row is None:
            row = IngestionHealthAlert(
                source_name=source_name,
                kind=kind,
                detected_at=now,
                resolved_at=None,
                baseline_avg_len=metrics.baseline_avg_len,
                current_avg_len=metrics.current_avg_len,
                sample_count_24h=metrics.sample_count_24h,
                sample_count_7d=metrics.sample_count_7d,
                last_checked_at=now,
            )
            db.add(row)
        else:
            # If the alert had resolved and is now re-firing, treat it
            # as a new episode — bump detected_at, clear resolved_at.
            if row.resolved_at is not None:
                row.detected_at = now
                row.resolved_at = None
            row.baseline_avg_len = metrics.baseline_avg_len
            row.current_avg_len = metrics.current_avg_len
            row.sample_count_24h = metrics.sample_count_24h
            row.sample_count_7d = metrics.sample_count_7d
            row.last_checked_at = now
    else:
        if row is not None and row.resolved_at is None:
            row.resolved_at = now
            row.current_avg_len = metrics.current_avg_len
            row.sample_count_24h = metrics.sample_count_24h
            row.last_checked_at = now


def _detect_short_body(db: Session, now: datetime) -> dict:
    """Detect/resolve short-body alerts for every source with enough data."""
    metrics_by_source = _compute_source_metrics(db, now)
    fired = 0
    resolved = 0
    for m in metrics_by_source:
        if m.sample_count_7d < MIN_SAMPLES_BASELINE:
            # Not enough history to know what "normal" looks like —
            # ignore. New sources won't false-fire.
            continue
        if m.baseline_avg_len < SHORT_BODY_ABSOLUTE_FLOOR:
            # Source has always been short — not a regression.
            continue
        is_firing = (
            m.sample_count_24h >= MIN_SAMPLES_CURRENT
            and m.current_avg_len < SHORT_BODY_ABSOLUTE_FLOOR
            and m.current_avg_len < m.baseline_avg_len * SHORT_BODY_DROP_RATIO
        )
        # Recovery: only relevant if there's an existing firing row.
        # Compute is_recovered separately so a source that's flapping
        # between firing/healthy doesn't keep getting alerts cleared
        # on every borderline tick.
        existing = (
            db.query(IngestionHealthAlert)
            .filter_by(source_name=m.source_name, kind="short_body")
            .first()
        )
        is_recovered = (
            existing is not None
            and existing.resolved_at is None
            and m.sample_count_24h >= MIN_SAMPLES_CURRENT
            and m.current_avg_len >= m.baseline_avg_len * SHORT_BODY_RECOVERY_RATIO
        )
        if is_firing:
            _upsert_alert(db, source_name=m.source_name, kind="short_body",
                          now=now, metrics=m, is_firing=True)
            fired += 1
        elif is_recovered:
            _upsert_alert(db, source_name=m.source_name, kind="short_body",
                          now=now, metrics=m, is_firing=False)
            resolved += 1
    return {"fired": fired, "resolved": resolved}


def _detect_silent(db: Session, now: datetime) -> dict:
    """Detect/resolve silent-source alerts.

    A source historically posting ≥1/day over the last 30 days but with
    0 items in the last 24h is firing. Recovery requires at least one
    new item in the last 24h.
    """
    cutoff_24h = now - timedelta(hours=24)
    cutoff_30d = now - timedelta(days=30)

    # Sources by historical activity over the 30-day baseline.
    baseline = (
        db.query(
            SourceItem.source_name,
            func.count(SourceItem.id).label("n"),
        )
        .filter(
            SourceItem.created_at >= cutoff_30d,
            SourceItem.created_at < cutoff_24h,
            SourceItem.source_name.isnot(None),
        )
        .group_by(SourceItem.source_name)
        .all()
    )
    # Daily rate = n / 29 days (the window excludes the last 24h so it's
    # 29d not 30d).
    silent_candidates = [
        r.source_name for r in baseline if (int(r.n) / 29.0) >= SILENT_MIN_DAILY_BASELINE
    ]
    if not silent_candidates:
        return {"fired": 0, "resolved": 0}

    # Items seen in the last 24h for each candidate.
    recent = dict(
        db.query(
            SourceItem.source_name,
            func.count(SourceItem.id),
        )
        .filter(
            SourceItem.created_at >= cutoff_24h,
            SourceItem.source_name.in_(silent_candidates),
        )
        .group_by(SourceItem.source_name)
        .all()
    )

    fired = 0
    resolved = 0
    for name in silent_candidates:
        last_24h_count = int(recent.get(name, 0))
        # The silent detector doesn't have a useful "current_avg_len"
        # to record; we leave that NULL. sample_count_24h doubles as
        # "items in the last 24h" for surface text.
        b_n_row = next((r for r in baseline if r.source_name == name), None)
        b_n = int(b_n_row.n) if b_n_row else 0
        metrics = SourceMetrics(
            source_name=name,
            current_avg_len=0.0,
            baseline_avg_len=0.0,
            sample_count_24h=last_24h_count,
            sample_count_7d=b_n,
        )
        is_firing = last_24h_count == 0
        existing = (
            db.query(IngestionHealthAlert)
            .filter_by(source_name=name, kind="silent")
            .first()
        )
        is_recovered = (
            existing is not None
            and existing.resolved_at is None
            and last_24h_count > 0
        )
        if is_firing:
            _upsert_alert(db, source_name=name, kind="silent",
                          now=now, metrics=metrics, is_firing=True)
            fired += 1
        elif is_recovered:
            _upsert_alert(db, source_name=name, kind="silent",
                          now=now, metrics=metrics, is_firing=False)
            resolved += 1
    return {"fired": fired, "resolved": resolved}


def run_health_check(db: Session, *, now: Optional[datetime] = None) -> dict:
    """Run both detectors and persist results.

    Returns a summary dict with counts. Safe to call multiple times per
    day — both detectors are idempotent.
    """
    now = now or datetime.utcnow()
    sb = _detect_short_body(db, now)
    sl = _detect_silent(db, now)
    db.commit()
    logger.info(
        "ingestion_health: short_body fired=%d resolved=%d, silent fired=%d resolved=%d",
        sb["fired"], sb["resolved"], sl["fired"], sl["resolved"],
    )
    return {"short_body": sb, "silent": sl, "checked_at": now.isoformat()}


def get_active_alerts(db: Session) -> list[dict]:
    """Return all unresolved alerts as plain dicts for the API surface."""
    rows = (
        db.query(IngestionHealthAlert)
        .filter(IngestionHealthAlert.resolved_at.is_(None))
        .order_by(IngestionHealthAlert.detected_at.desc())
        .all()
    )
    return [
        {
            "id": r.id,
            "source_name": r.source_name,
            "kind": r.kind,
            "detected_at": r.detected_at.isoformat() if r.detected_at else None,
            "baseline_avg_len": r.baseline_avg_len,
            "current_avg_len": r.current_avg_len,
            "sample_count_24h": r.sample_count_24h,
            "sample_count_7d": r.sample_count_7d,
            "last_checked_at": r.last_checked_at.isoformat() if r.last_checked_at else None,
        }
        for r in rows
    ]
