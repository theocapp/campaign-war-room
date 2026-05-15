"""Tests for the single-LLM-call ingest pipeline.

Phase 0 — Option C wires opponent attack extraction into the same LLM call.
Phase 1 — Combined call also returns sentiment + frame_matches in one pass.
"""
import json
from datetime import datetime
from typing import Optional

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import (
    CampaignConfig,
    NarrativeFrame,
    NarrativeFrameMention,
    Opponent,
    OpponentActivity,
    SourceItem,
)
from app.services import campaign_analysis, llm_provider
from app.services.campaign_analysis import _validate_opponent_attacks, analyze, analyze_with_frames
from app.services.ingestion import _persist_opponent_attacks, _persist_frame_matches


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
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


class _StubProvider(llm_provider.BaseLLMProvider):
    """Minimal LLM stub that returns a pre-baked JSON string from complete()."""

    def __init__(self, response: str):
        self._response = response

    def complete(self, prompt: str) -> str:
        return self._response

    # Unused stubs (BaseLLMProvider is abstract).
    def summarize(self, text, max_words=80):
        return text

    def classify_urgency(self, text):
        return "low"

    def extract_issues(self, text):
        return []

    def detect_opponent_activity(self, text, opponent_name):
        return {}

    def generate_talking_points(self, *args, **kwargs):
        return {}

    def generate_risk_warning(self, text, credibility_note):
        return None

    def verify_opponent_subject(self, sentence, opponent_name, candidate_name):
        return "opponent"


# ── _validate_opponent_attacks ────────────────────────────────────────────────


def test_validate_attacks_drops_unknown_opponent():
    raw = [{"opponent_name": "Some Random Person", "type": "attack", "text": "X"}]
    assert _validate_opponent_attacks(raw, ["Rob Bresnahan"]) == []


def test_validate_attacks_drops_invalid_type():
    raw = [{"opponent_name": "Rob Bresnahan", "type": "rant", "text": "X"}]
    assert _validate_opponent_attacks(raw, ["Rob Bresnahan"]) == []


def test_validate_attacks_drops_empty_text():
    raw = [{"opponent_name": "Rob Bresnahan", "type": "attack", "text": "   "}]
    assert _validate_opponent_attacks(raw, ["Rob Bresnahan"]) == []


def test_validate_attacks_keeps_valid_entries():
    raw = [
        {"opponent_name": "Rob Bresnahan", "type": "attack", "text": "He said X"},
        {"opponent_name": "rob bresnahan", "type": "claim", "text": "He claimed Y"},
    ]
    out = _validate_opponent_attacks(raw, ["Rob Bresnahan"])
    assert len(out) == 2
    assert out[0]["type"] == "attack"
    assert out[1]["type"] == "claim"


def test_validate_attacks_handles_non_list_input():
    assert _validate_opponent_attacks(None, ["X"]) == []
    assert _validate_opponent_attacks({"not": "a list"}, ["X"]) == []
    assert _validate_opponent_attacks("attacks", ["X"]) == []


# ── analyze() happy path ──────────────────────────────────────────────────────


def test_analyze_returns_validated_attacks(cognetti_db, monkeypatch):
    item = SourceItem(
        title="Bresnahan slams Cognetti on healthcare",
        raw_text="Rob Bresnahan today attacked his opponent over recent votes.",
        source_name="Times-Tribune",
        source_type="news",
    )
    cognetti_db.add(item)
    cognetti_db.commit()

    stub = _StubProvider(json.dumps({
        "relevant": True,
        "relevance_score": 85,
        "one_sentence": "Bresnahan attacked Cognetti's healthcare record at a Scranton event.",
        "framing": "opponent_news",
        "needs_attention": False,
        "reason": "Direct opponent activity in the district.",
        "opponent_attacks": [
            {
                "opponent_name": "Rob Bresnahan",
                "type": "attack",
                "text": "Bresnahan called Cognetti's healthcare record reckless.",
            }
        ],
    }))
    monkeypatch.setattr(campaign_analysis, "get_provider", lambda: stub, raising=False)
    # The function imports get_provider locally inside analyze(), so patch the
    # actual module-level symbol.
    monkeypatch.setattr(llm_provider, "get_provider", lambda: stub)

    result = analyze(cognetti_db, item)

    assert result["_used_fallback"] is False
    assert result["relevance_score"] == 85
    assert result["relevant"] is True
    assert result["reason"] == "Direct opponent activity in the district."
    assert len(result["opponent_attacks"]) == 1
    assert result["opponent_attacks"][0]["type"] == "attack"


def test_analyze_falls_back_on_invalid_json(cognetti_db, monkeypatch):
    item = SourceItem(title="x", raw_text="x", source_name="x", source_type="news")
    cognetti_db.add(item)
    cognetti_db.commit()

    monkeypatch.setattr(llm_provider, "get_provider", lambda: _StubProvider("not json"))

    result = analyze(cognetti_db, item)
    assert result["_used_fallback"] is True
    assert result["opponent_attacks"] == []


# ── _persist_opponent_attacks ─────────────────────────────────────────────────


def test_persist_attacks_inserts_rows(cognetti_db):
    item = SourceItem(title="t", raw_text="t", source_name="x", source_type="news")
    cognetti_db.add(item)
    cognetti_db.commit()

    inserted = _persist_opponent_attacks(cognetti_db, item, [
        {"opponent_name": "Rob Bresnahan", "type": "attack", "text": "He attacked the bill."},
        {"opponent_name": "Rob Bresnahan", "type": "promise", "text": "He promised lower taxes."},
    ])
    cognetti_db.commit()

    assert inserted == 2
    activities = cognetti_db.query(OpponentActivity).filter_by(source_item_id=item.id).all()
    assert len(activities) == 2
    types_seen = {("attack" if a.attack else "promise" if a.promise else "claim") for a in activities}
    assert types_seen == {"attack", "promise"}


def test_persist_attacks_skips_unknown_opponent(cognetti_db):
    item = SourceItem(title="t", raw_text="t", source_name="x", source_type="news")
    cognetti_db.add(item)
    cognetti_db.commit()

    inserted = _persist_opponent_attacks(cognetti_db, item, [
        {"opponent_name": "Unknown Person", "type": "attack", "text": "X"},
    ])
    assert inserted == 0
    assert cognetti_db.query(OpponentActivity).count() == 0


def test_persist_attacks_dedups_against_existing_rows(cognetti_db):
    item = SourceItem(title="t", raw_text="t", source_name="x", source_type="news")
    cognetti_db.add(item)
    cognetti_db.commit()

    opponent = cognetti_db.query(Opponent).first()
    # Pre-existing identical activity on the same source.
    cognetti_db.add(OpponentActivity(
        opponent_id=opponent.id,
        source_item_id=item.id,
        attack="He attacked the bill.",
    ))
    cognetti_db.commit()

    inserted = _persist_opponent_attacks(cognetti_db, item, [
        {"opponent_name": "Rob Bresnahan", "type": "attack", "text": "He attacked the bill."},
        {"opponent_name": "Rob Bresnahan", "type": "claim", "text": "He claimed the data was wrong."},
    ])
    cognetti_db.commit()

    # First duplicate skipped, second (different fingerprint) inserted.
    assert inserted == 1
    assert cognetti_db.query(OpponentActivity).count() == 2


def test_persist_attacks_strips_html(cognetti_db):
    item = SourceItem(title="t", raw_text="t", source_name="x", source_type="news")
    cognetti_db.add(item)
    cognetti_db.commit()

    _persist_opponent_attacks(cognetti_db, item, [
        {
            "opponent_name": "Rob Bresnahan",
            "type": "attack",
            "text": "Bresnahan&#x2019;s <a href=\"https://x.com\">attack</a> on the union deal",
        }
    ])
    cognetti_db.commit()
    activity = cognetti_db.query(OpponentActivity).filter_by(source_item_id=item.id).first()
    assert activity is not None
    assert "&#x2019;" not in activity.attack
    assert "<a" not in activity.attack
    assert "’" in activity.attack


# ── Phase 1: combined call (sentiment + frame_matches) ────────────────────────


@pytest.fixture
def frames_db(cognetti_db):
    """cognetti_db extended with two active NarrativeFrames."""
    cognetti_db.add(NarrativeFrame(
        name="Healthcare record",
        description="Coverage of Cognetti’s healthcare positions",
        owner_type="candidate",
        active=True,
        source="manual",
    ))
    cognetti_db.add(NarrativeFrame(
        name="Bresnahan attacks",
        description="Attacks launched by Bresnahan against Cognetti",
        owner_type="opponent",
        active=True,
        source="manual",
    ))
    cognetti_db.commit()
    return cognetti_db


def test_analyze_with_frames_returns_sentiment(frames_db, monkeypatch):
    item = SourceItem(
        title="Bresnahan attacks Cognetti on healthcare",
        raw_text="Rob Bresnahan said Cognetti’s record is reckless.",
        source_name="Times-Tribune",
        source_type="news",
    )
    frames_db.add(item)
    frames_db.commit()

    frames = frames_db.query(NarrativeFrame).all()
    stub = _StubProvider(json.dumps({
        "relevant": True,
        "relevance_score": 80,
        "one_sentence": "Bresnahan attacked Cognetti on healthcare.",
        "framing": "hurts_candidate",
        "needs_attention": True,
        "reason": "Direct opponent attack.",
        "sentiment": "negative",
        "opponent_attacks": [],
        "frame_matches": [1, 2],
    }))
    monkeypatch.setattr(llm_provider, "get_provider", lambda: stub)

    result = analyze_with_frames(frames_db, item, frames=frames)

    assert result["_used_fallback"] is False
    assert result["sentiment"] == "negative"
    assert result["frame_matches"] == [1, 2]


def test_analyze_with_frames_coerces_bad_sentiment(frames_db, monkeypatch):
    item = SourceItem(title="x", raw_text="x", source_name="x", source_type="news")
    frames_db.add(item)
    frames_db.commit()

    stub = _StubProvider(json.dumps({
        "relevant": False,
        "relevance_score": 5,
        "one_sentence": None,
        "framing": "irrelevant",
        "needs_attention": False,
        "reason": "Not relevant.",
        "sentiment": "VERY_BAD_VALUE",
        "opponent_attacks": [],
        "frame_matches": [],
    }))
    monkeypatch.setattr(llm_provider, "get_provider", lambda: stub)

    result = analyze_with_frames(frames_db, item, frames=[])
    assert result["sentiment"] == "neutral"


def test_analyze_with_frames_ignores_out_of_range_indices(frames_db, monkeypatch):
    item = SourceItem(title="x", raw_text="x", source_name="x", source_type="news")
    frames_db.add(item)
    frames_db.commit()

    frames = frames_db.query(NarrativeFrame).all()  # 2 frames
    stub = _StubProvider(json.dumps({
        "relevant": True,
        "relevance_score": 60,
        "one_sentence": "Something happened.",
        "framing": "background",
        "needs_attention": False,
        "reason": "Relevant.",
        "sentiment": "neutral",
        "opponent_attacks": [],
        "frame_matches": [0, 1, 99],  # 0 and 99 are out of range; only 1 is valid
    }))
    monkeypatch.setattr(llm_provider, "get_provider", lambda: stub)

    result = analyze_with_frames(frames_db, item, frames=frames)
    assert result["frame_matches"] == [1]


def test_persist_frame_matches_creates_mentions(frames_db):
    item = SourceItem(title="t", raw_text="t", source_name="x", source_type="news")
    frames_db.add(item)
    frames_db.commit()

    frames = frames_db.query(NarrativeFrame).all()
    _persist_frame_matches(frames_db, item, frames, [1, 2])
    frames_db.commit()

    mentions = frames_db.query(NarrativeFrameMention).filter_by(source_item_id=item.id).all()
    assert len(mentions) == 2
    assert all(m.matched_by == "llm" for m in mentions)
    assert all(m.confidence == 75 for m in mentions)


def test_persist_frame_matches_deduplicates(frames_db):
    item = SourceItem(title="t", raw_text="t", source_name="x", source_type="news")
    frames_db.add(item)
    frames_db.commit()

    frames = frames_db.query(NarrativeFrame).all()
    _persist_frame_matches(frames_db, item, frames, [1])
    frames_db.commit()
    # Call again — should not create a duplicate
    _persist_frame_matches(frames_db, item, frames, [1])
    frames_db.commit()

    assert frames_db.query(NarrativeFrameMention).filter_by(source_item_id=item.id).count() == 1


def test_analyze_no_frames_returns_empty_frame_matches(cognetti_db, monkeypatch):
    item = SourceItem(title="x", raw_text="x", source_name="x", source_type="news")
    cognetti_db.add(item)
    cognetti_db.commit()

    stub = _StubProvider(json.dumps({
        "relevant": True,
        "relevance_score": 50,
        "one_sentence": "Something.",
        "framing": "background",
        "needs_attention": False,
        "reason": "Relevant.",
        "sentiment": "neutral",
        "opponent_attacks": [],
        "frame_matches": [],
    }))
    monkeypatch.setattr(llm_provider, "get_provider", lambda: stub)

    result = analyze_with_frames(cognetti_db, item, frames=None)
    assert result["frame_matches"] == []
