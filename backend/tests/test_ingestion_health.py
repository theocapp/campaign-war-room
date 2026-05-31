"""Unit tests for the ingestion-quality detection job.

Uses an in-memory SQLite database with the SourceItem +
IngestionHealthAlert tables. Seeds synthetic raw_text length
distributions then asserts the detectors fire/resolve as expected.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import IngestionHealthAlert, SourceItem
from app.services import ingestion_health as ih


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    s = Session()
    try:
        yield s
    finally:
        s.close()


def _add_items(
    db,
    source_name: str,
    count: int,
    *,
    raw_text_len: int,
    base_time: datetime,
    span_hours: float,
    archived: bool = False,
    relevance: int = 75,
):
    """Seed `count` SourceItems with raw_text of the given length,
    spaced evenly across `span_hours` ending at base_time."""
    for i in range(count):
        # Spread items over (base_time - span_hours, base_time), exclusive
        # on both ends so an item at exactly base_time can't leak into the
        # next window's range check.
        offset_hours = ((i + 1) / (count + 1)) * span_hours
        ts = base_time - timedelta(hours=span_hours - offset_hours)
        db.add(SourceItem(
            title=f"{source_name} item {i}",
            raw_text="x" * raw_text_len,
            source_name=source_name,
            source_type="news",
            source_url=f"https://example.test/{source_name}/{i}",
            created_at=ts,
            archived_as_irrelevant=archived,
            race_relevance_score=relevance,
        ))
    db.commit()


# ── short_body detector ────────────────────────────────────────────────────

def test_short_body_fires_when_avg_drops_sharply(db):
    """The Google-News-collapse scenario: baseline 2000-char bodies,
    current 70-char stubs."""
    now = datetime(2026, 5, 30, 0, 0, 0)
    # Baseline window: 20 items @ 2000 chars between -7d and -24h.
    _add_items(db, "Citizens' Voice — Google News Feed",
               count=20, raw_text_len=2000,
               base_time=now - timedelta(hours=24), span_hours=(7*24 - 24))
    # Current window: 8 items @ 70 chars in the last 24h.
    _add_items(db, "Citizens' Voice — Google News Feed",
               count=8, raw_text_len=70,
               base_time=now - timedelta(minutes=30), span_hours=23)

    result = ih.run_health_check(db, now=now)
    assert result["short_body"]["fired"] == 1
    assert result["short_body"]["resolved"] == 0

    alert = (db.query(IngestionHealthAlert)
             .filter_by(kind="short_body", source_name="Citizens' Voice — Google News Feed")
             .one())
    assert alert.resolved_at is None
    assert alert.baseline_avg_len == pytest.approx(2000, abs=1)
    assert alert.current_avg_len == pytest.approx(70, abs=1)


def test_short_body_does_not_fire_when_baseline_is_already_short(db):
    """YouTube items have always been ~70 chars — that's by design, not a
    regression. Don't false-alarm on sources that are stably short."""
    now = datetime(2026, 5, 30, 0, 0, 0)
    _add_items(db, "YouTube: Cognetti",
               count=20, raw_text_len=70,
               base_time=now - timedelta(hours=24), span_hours=(7*24 - 24))
    _add_items(db, "YouTube: Cognetti",
               count=8, raw_text_len=65,
               base_time=now - timedelta(minutes=30), span_hours=23)

    result = ih.run_health_check(db, now=now)
    assert result["short_body"]["fired"] == 0


def test_short_body_does_not_fire_with_insufficient_baseline(db):
    """A brand-new source with only a handful of data points shouldn't
    create an alert until we have enough history to judge."""
    now = datetime(2026, 5, 30, 0, 0, 0)
    # Only 5 baseline items — below MIN_SAMPLES_BASELINE.
    _add_items(db, "New Source", count=5, raw_text_len=2000,
               base_time=now - timedelta(hours=24), span_hours=120)
    _add_items(db, "New Source", count=5, raw_text_len=70,
               base_time=now - timedelta(minutes=30), span_hours=23)

    result = ih.run_health_check(db, now=now)
    assert result["short_body"]["fired"] == 0


def test_short_body_does_not_fire_with_insufficient_current_samples(db):
    """A source with strong baseline but only 2 items in the last 24h —
    too few to know if it's a real regression vs noise."""
    now = datetime(2026, 5, 30, 0, 0, 0)
    _add_items(db, "Quiet Source", count=20, raw_text_len=2000,
               base_time=now - timedelta(hours=24), span_hours=(7*24 - 24))
    _add_items(db, "Quiet Source", count=2, raw_text_len=70,
               base_time=now - timedelta(minutes=30), span_hours=23)

    result = ih.run_health_check(db, now=now)
    assert result["short_body"]["fired"] == 0


def test_short_body_archived_items_excluded_from_metrics(db):
    """Archived-as-irrelevant items shouldn't drag the avg around."""
    now = datetime(2026, 5, 30, 0, 0, 0)
    _add_items(db, "Mixed Source", count=20, raw_text_len=2000,
               base_time=now - timedelta(hours=24), span_hours=(7*24 - 24))
    # Last 24h: 6 relevant items still at 2000 chars + 20 archived junk
    # at 50 chars. The detector should see the 2000-char avg, not a
    # weighted mix.
    _add_items(db, "Mixed Source", count=6, raw_text_len=2000,
               base_time=now - timedelta(minutes=30), span_hours=23)
    _add_items(db, "Mixed Source", count=20, raw_text_len=50,
               base_time=now - timedelta(minutes=30), span_hours=23,
               archived=True)

    result = ih.run_health_check(db, now=now)
    assert result["short_body"]["fired"] == 0


def test_short_body_resolves_when_metric_recovers(db):
    """A previously-firing alert clears when avg climbs back."""
    now = datetime(2026, 5, 30, 0, 0, 0)

    # Plant a pre-existing firing alert.
    db.add(IngestionHealthAlert(
        source_name="Recovering Source",
        kind="short_body",
        detected_at=now - timedelta(days=2),
        resolved_at=None,
        baseline_avg_len=2000.0,
        current_avg_len=70.0,
        sample_count_24h=8,
        sample_count_7d=40,
        last_checked_at=now - timedelta(days=1),
    ))
    db.commit()

    # Healthy data now: baseline at 2000, current also back to 1800
    # (≥ 70% of baseline = recovery threshold).
    _add_items(db, "Recovering Source", count=20, raw_text_len=2000,
               base_time=now - timedelta(hours=24), span_hours=(7*24 - 24))
    _add_items(db, "Recovering Source", count=8, raw_text_len=1800,
               base_time=now - timedelta(minutes=30), span_hours=23)

    result = ih.run_health_check(db, now=now)
    assert result["short_body"]["resolved"] == 1
    row = (db.query(IngestionHealthAlert)
           .filter_by(source_name="Recovering Source", kind="short_body").one())
    assert row.resolved_at is not None


def test_short_body_re_fires_after_resolution(db):
    """An alert that resolved and then drops again is a new incident —
    detected_at should be updated to the new firing time."""
    now = datetime(2026, 5, 30, 0, 0, 0)
    old_resolved = now - timedelta(days=5)
    db.add(IngestionHealthAlert(
        source_name="Flapping Source",
        kind="short_body",
        detected_at=now - timedelta(days=10),
        resolved_at=old_resolved,
        baseline_avg_len=2000.0,
        current_avg_len=1900.0,
        sample_count_24h=8,
        sample_count_7d=40,
        last_checked_at=now - timedelta(days=4),
    ))
    db.commit()

    # Re-firing pattern.
    _add_items(db, "Flapping Source", count=20, raw_text_len=2000,
               base_time=now - timedelta(hours=24), span_hours=(7*24 - 24))
    _add_items(db, "Flapping Source", count=8, raw_text_len=80,
               base_time=now - timedelta(minutes=30), span_hours=23)

    ih.run_health_check(db, now=now)
    row = (db.query(IngestionHealthAlert)
           .filter_by(source_name="Flapping Source", kind="short_body").one())
    assert row.resolved_at is None
    # detected_at should be bumped to ~now since we treat re-fire as new
    assert row.detected_at == now


# ── silent detector ──────────────────────────────────────────────────────

def test_silent_fires_when_active_source_goes_quiet(db):
    """30+ items over 29 days but 0 in the last 24h."""
    now = datetime(2026, 5, 30, 0, 0, 0)
    _add_items(db, "Reliable Daily Source", count=60, raw_text_len=500,
               base_time=now - timedelta(hours=24), span_hours=(29*24))
    # Zero items in last 24h.
    result = ih.run_health_check(db, now=now)
    assert result["silent"]["fired"] == 1


def test_silent_does_not_fire_for_low_volume_sources(db):
    """A source that posts once a week shouldn't fire silent alerts."""
    now = datetime(2026, 5, 30, 0, 0, 0)
    # 4 items over 30 days = 0.13/day — below SILENT_MIN_DAILY_BASELINE.
    _add_items(db, "Occasional Source", count=4, raw_text_len=500,
               base_time=now - timedelta(hours=24), span_hours=(29*24))
    result = ih.run_health_check(db, now=now)
    assert result["silent"]["fired"] == 0


def test_silent_resolves_when_source_returns(db):
    """Previously-silent alert clears on first new item."""
    now = datetime(2026, 5, 30, 0, 0, 0)
    db.add(IngestionHealthAlert(
        source_name="Returning Source",
        kind="silent",
        detected_at=now - timedelta(hours=72),
        resolved_at=None,
        sample_count_24h=0,
        sample_count_7d=30,
        last_checked_at=now - timedelta(hours=24),
    ))
    db.commit()

    _add_items(db, "Returning Source", count=60, raw_text_len=500,
               base_time=now - timedelta(hours=24), span_hours=(29*24))
    # Item in last 24h is what triggers resolution.
    _add_items(db, "Returning Source", count=2, raw_text_len=500,
               base_time=now - timedelta(minutes=30), span_hours=20)

    result = ih.run_health_check(db, now=now)
    assert result["silent"]["resolved"] == 1
    row = (db.query(IngestionHealthAlert)
           .filter_by(source_name="Returning Source", kind="silent").one())
    assert row.resolved_at is not None


# ── get_active_alerts ──────────────────────────────────────────────────────

def test_get_active_alerts_returns_unresolved_only(db):
    now = datetime(2026, 5, 30, 0, 0, 0)
    db.add(IngestionHealthAlert(
        source_name="Source A", kind="short_body",
        detected_at=now - timedelta(hours=2), resolved_at=None,
        baseline_avg_len=2000.0, current_avg_len=80.0,
        sample_count_24h=6, sample_count_7d=40,
        last_checked_at=now,
    ))
    db.add(IngestionHealthAlert(
        source_name="Source B", kind="silent",
        detected_at=now - timedelta(days=2),
        resolved_at=now - timedelta(hours=1),
        sample_count_24h=0, sample_count_7d=30,
        last_checked_at=now,
    ))
    db.commit()

    out = ih.get_active_alerts(db)
    assert len(out) == 1
    assert out[0]["source_name"] == "Source A"
    assert out[0]["kind"] == "short_body"
