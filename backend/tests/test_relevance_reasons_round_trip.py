"""Regression test for the JSON-array invariant on
``source_items.relevance_reasons``.

The column is documented as a JSON array stored as text
(``models.py``: ``relevance_reasons = Column(Text, nullable=True)  # JSON
array stored as text``). The dashboard reads it via
``_safe_json_list`` which calls ``json.loads`` and silently returns ``[]``
for anything that doesn't parse — so a plain prose string written here
is invisible-but-broken: the dashboard shows no reason at all.

In 2026-05-27/28 a bug in ``ingestion._create_and_analyze`` did exactly
that:

    reason = (analysis.get("reason") or "").strip()
    if reason:
        item.relevance_reasons = reason   # <-- plain string

It corrupted 187 rows before being noticed. This test exercises both
code paths in ``_create_and_analyze`` that write ``relevance_reasons``
and asserts every write round-trips through ``json.loads`` to a list.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import CampaignConfig, Opponent, SourceItem
from app.services import campaign_analysis, ingestion


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


@pytest.fixture
def cognetti_db(db):
    db.add(CampaignConfig(
        candidate_name="Paige Cognetti",
        race="PA-08 U.S. House",
        district="PA-08",
        location="Scranton, PA",
    ))
    db.add(Opponent(name="Rob Bresnahan", office="U.S. Representative"))
    db.commit()
    return db


def _assert_round_trips(raw: str | None) -> list:
    """Assert that the stored value is JSON that decodes to a list.
    Returns the decoded list so callers can make further assertions."""
    assert raw is not None and raw != "", (
        "relevance_reasons should be set, not None/empty"
    )
    # The dashboard's _safe_json_list parses with json.loads and silently
    # falls back to [] on any error — so we want a STRICT json.loads here
    # (no fallback) so the test fails loudly if the invariant breaks.
    decoded = json.loads(raw)
    assert isinstance(decoded, list), (
        f"relevance_reasons must decode to a list, got {type(decoded).__name__}: {decoded!r}"
    )
    return decoded


def test_llm_reason_path_writes_json_array(cognetti_db, monkeypatch):
    """When the LLM returns a `reason` string, ingestion must wrap it as a
    JSON array — not store the raw prose."""
    # Article must mention the candidate so it gets past the prefilter
    # (no_political_signal check at ingestion.py:556-581) and reaches the
    # `analyze_with_frames` call site that contains the bug we fixed.
    item = SourceItem(
        title="Paige Cognetti event in Scranton",
        raw_text="Paige Cognetti spoke at a community event in Scranton today.",
        source_name="local-paper",
        source_type="news",
        source_url="https://example.com/a",
    )

    # Stub analyze_with_frames to return a non-fallback result with a `reason`.
    # `relevant=False` keeps us out of the post-analysis cluster/perspective
    # branches (ingestion.py:638) — those touch more services than this test
    # cares about.
    def _stub_analyze_with_frames(db, src_item, frames=None):
        return {
            "_used_fallback": False,
            "relevant": False,
            "relevance_score": 25,
            "one_sentence": "Cognetti spoke in Scranton.",
            "framing": "neutral",
            "sentiment": "neutral",
            "needs_attention": False,
            "reason": "Mentions candidate but no policy substance.",
            "opponent_attacks": [],
            "frame_matches": [],
        }
    monkeypatch.setattr(
        campaign_analysis, "analyze_with_frames", _stub_analyze_with_frames
    )

    result = ingestion._create_and_analyze(cognetti_db, item)

    decoded = _assert_round_trips(result.relevance_reasons)
    assert decoded == ["Mentions candidate but no policy substance."], (
        f"Expected single-element list with the LLM reason; got {decoded!r}"
    )


def test_llm_empty_reason_does_not_overwrite(cognetti_db, monkeypatch):
    """If the LLM returns no `reason`, the assignment should be skipped
    (not overwrite whatever the prefilter / fallback already wrote)."""
    item = SourceItem(
        title="Paige Cognetti rally",
        raw_text="Paige Cognetti held a rally in Scranton today.",
        source_name="local-paper",
        source_type="news",
        source_url="https://example.com/b",
    )

    def _stub_analyze_with_frames(db, src_item, frames=None):
        return {
            "_used_fallback": False,
            "relevant": False,
            "relevance_score": 25,
            "one_sentence": "Cognetti rally.",
            "framing": "neutral",
            "sentiment": "neutral",
            "needs_attention": False,
            "reason": "   ",  # whitespace-only -> skipped after .strip()
            "opponent_attacks": [],
            "frame_matches": [],
        }
    monkeypatch.setattr(
        campaign_analysis, "analyze_with_frames", _stub_analyze_with_frames
    )

    result = ingestion._create_and_analyze(cognetti_db, item)

    # If something IS written, it must still round-trip — otherwise None
    # is fine (the dashboard handles null with `[]`).
    if result.relevance_reasons:
        _assert_round_trips(result.relevance_reasons)


def test_prefilter_drop_path_writes_json_array(cognetti_db, monkeypatch):
    """The prefilter early-return path (ingestion.py:573-576) is the
    OTHER place that writes relevance_reasons. Cover it too so a future
    refactor of either site can't break the invariant unnoticed."""
    # Article with zero political signal — no candidate/opponent/district
    # mention. Should be dropped by the prefilter before reaching the LLM.
    item = SourceItem(
        title="Local bakery wins regional award",
        raw_text="A small bakery in upstate New York won a regional award.",
        source_name="food-blog",
        source_type="news",
        source_url="https://example.com/c",
    )

    # Force the prefilter to drop by lowering the threshold; this article
    # will have race_relevance_score 0 and no candidate/opponent flags.
    monkeypatch.setenv("PREFILTER_THRESHOLD", "100")

    # Defensive: ensure analyze_with_frames is NOT called on the prefilter
    # drop path (it would mean the prefilter didn't drop, which would
    # invalidate this test's premise rather than the invariant).
    def _should_not_run(*args, **kwargs):
        raise AssertionError(
            "analyze_with_frames should not be called when prefilter drops"
        )
    monkeypatch.setattr(
        campaign_analysis, "analyze_with_frames", _should_not_run
    )

    result = ingestion._create_and_analyze(cognetti_db, item)

    decoded = _assert_round_trips(result.relevance_reasons)
    # The prefilter path appends a fixed marker; assert it's present so a
    # future refactor that drops the marker is caught.
    assert any("Prefilter" in s for s in decoded), (
        f"Prefilter path should include a 'Prefilter: ...' marker; got {decoded!r}"
    )
