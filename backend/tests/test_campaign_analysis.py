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

# Note: tests for `_persist_opponent_attacks` and `_persist_frame_matches`
# were removed when those functions were consolidated into the
# cluster-native `_persist_cluster_native` helper. Cluster-native persistence
# is exercised end-to-end by the merge backfill regression tests.


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
    """v2 shape: stub returns an extracted_claims list with a personal_attack
    claim whose `quote` substring is present in the article body. The legacy
    `opponent_attacks` field is derived from extracted_claims by claim_type."""
    item = SourceItem(
        title="Bresnahan slams Cognetti on healthcare",
        raw_text=(
            "Rob Bresnahan today attacked his opponent over recent votes. "
            "He called her healthcare record reckless during a Scranton event."
        ),
        source_name="Times-Tribune",
        source_type="news",
    )
    cognetti_db.add(item)
    cognetti_db.commit()

    stub = _StubProvider(json.dumps({
        "verdict": "critical",
        "summary": "Bresnahan attacked Cognetti's healthcare record at a Scranton event.",
        "campaign_action": "respond",
        "sentiment": "favors_opponent",
        "source_credibility": "high",
        "needs_attention": True,
        "needs_attention_reason": "Direct opponent activity in the district.",
        "extracted_claims": [
            {
                "actor_name": "Rob Bresnahan",
                "actor_role": "opponent",
                "claim_type": "personal_attack",
                "quote": "called her healthcare record reckless during a Scranton event",
                "confidence": "high",
            }
        ],
    }))
    # `analyze` imports `get_ingestion_provider` locally from llm_provider —
    # patch the symbol on the llm_provider module so the local import
    # resolves to our stub.
    monkeypatch.setattr(llm_provider, "get_ingestion_provider", lambda: stub)

    result = analyze(cognetti_db, item)

    assert result["_used_fallback"] is False
    assert result["verdict"] == "critical"
    # critical base (75) + both candidate+opponent in title (+20 capped) +
    # both names in body (+8 capped) + high-conf claim (+3) + high source
    # credibility (+3) = 109 → capped to 100. See _compute_relevance_score.
    assert result["relevance_score"] == 100
    assert result["relevant"] is True
    assert result["needs_attention_reason"] == "Direct opponent activity in the district."
    # opponent_attacks is derived from extracted_claims (personal_attack → attack)
    assert len(result["opponent_attacks"]) == 1
    assert result["opponent_attacks"][0]["type"] == "attack"
    assert result["opponent_attacks"][0]["opponent_name"] == "Rob Bresnahan"


def test_analyze_falls_back_on_invalid_json(cognetti_db, monkeypatch):
    # Article must mention candidate/opponent to bypass the pre-LLM race-mention
    # gate; otherwise we never get to the JSON parse path being tested here.
    item = SourceItem(
        title="Cognetti event",
        raw_text="Paige Cognetti held a campaign event.",
        source_name="x", source_type="news",
    )
    cognetti_db.add(item)
    cognetti_db.commit()

    monkeypatch.setattr(llm_provider, "get_ingestion_provider", lambda: _StubProvider("not json"))

    result = analyze(cognetti_db, item)
    assert result["_used_fallback"] is True
    assert result["opponent_attacks"] == []


def test_analyze_gate_skips_llm_when_no_race_mention(cognetti_db, monkeypatch):
    """Pre-LLM gate: articles without any candidate/opponent/district mention
    return irrelevant immediately, without invoking the LLM provider."""
    item = SourceItem(
        title="Mets drop series to Diamondbacks",
        raw_text="The Mets lost their third straight game to Arizona last night.",
        source_name="ESPN", source_type="news",
    )
    cognetti_db.add(item)
    cognetti_db.commit()

    # If the gate works, the provider is never called. Raise loudly if it is.
    def _boom():
        raise AssertionError("LLM provider should not be invoked by the gate")
    monkeypatch.setattr(llm_provider, "get_ingestion_provider", lambda: _boom() or None)

    # We need the provider getter to actually be called to be sure — but the
    # gate runs AFTER get_ingestion_provider() resolves. So instead, install
    # a stub that records whether complete() was called.
    calls: list[str] = []
    class _RecordingStub(_StubProvider):
        def complete(self, prompt: str) -> str:
            calls.append(prompt)
            return "{}"
    monkeypatch.setattr(
        llm_provider, "get_ingestion_provider", lambda: _RecordingStub("{}"),
    )

    result = analyze(cognetti_db, item)
    assert calls == [], "gate should prevent LLM call for off-topic articles"
    assert result["verdict"] == "irrelevant"
    assert result["relevance_score"] == 0
    assert result["relevant"] is False
    assert result["_gated_no_race_mention"] is True
    assert result["_used_fallback"] is False


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
    """v2 shape: sentiment is in `extracted_claims[].claim_type`-derived
    fields PLUS a top-level `sentiment` enum that maps to legacy values.
    frame_matches is derived from union of `matched_frames` (by name) on
    cleaned claims."""
    item = SourceItem(
        title="Bresnahan attacks Cognetti on healthcare",
        raw_text="Rob Bresnahan said Cognetti's record is reckless this morning.",
        source_name="Times-Tribune",
        source_type="news",
    )
    frames_db.add(item)
    frames_db.commit()

    frames = frames_db.query(NarrativeFrame).all()
    frame_names = [f.name for f in frames]
    stub = _StubProvider(json.dumps({
        "verdict": "relevant",
        "summary": "Bresnahan attacked Cognetti on healthcare.",
        "campaign_action": "respond",
        "sentiment": "favors_opponent",  # maps to legacy "negative"
        "source_credibility": "high",
        "needs_attention": True,
        "needs_attention_reason": "Direct opponent attack.",
        "extracted_claims": [
            {
                "actor_name": "Rob Bresnahan",
                "actor_role": "opponent",
                "claim_type": "personal_attack",
                "quote": "Cognetti's record is reckless this morning",
                "confidence": "high",
                "matched_frames": frame_names,  # match both fixture frames
            }
        ],
    }))
    monkeypatch.setattr(llm_provider, "get_ingestion_provider", lambda: stub)

    result = analyze_with_frames(frames_db, item, frames=frames)

    assert result["_used_fallback"] is False
    assert result["sentiment"] == "negative"  # derived from favors_opponent
    assert set(result["frame_matches"]) == {f.id for f in frames}


def test_analyze_with_frames_coerces_bad_sentiment(frames_db, monkeypatch):
    # Mentions candidate so the pre-LLM race-mention gate doesn't fire.
    item = SourceItem(
        title="Cognetti speech",
        raw_text="Paige Cognetti gave a speech.",
        source_name="x", source_type="news",
    )
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
    monkeypatch.setattr(llm_provider, "get_ingestion_provider", lambda: stub)

    result = analyze_with_frames(frames_db, item, frames=[])
    assert result["sentiment"] == "neutral"


def test_analyze_with_frames_ignores_unknown_frame_names(frames_db, monkeypatch):
    """v2 matches frames by NAME (not index). Unknown names returned by the
    LLM in `matched_frames` are dropped via _fuzzy_match_frame (returns None
    on no match). Replaces the v1 'out-of-range index' test — the v2 path
    can't have out-of-range indices because there are no indices."""
    item = SourceItem(
        title="Cognetti campaign event",
        raw_text="Paige Cognetti held a campaign event today in Scranton.",
        source_name="x",
        source_type="news",
    )
    frames_db.add(item)
    frames_db.commit()

    frames = frames_db.query(NarrativeFrame).all()  # 2 frames
    valid_frame_name = frames[0].name
    stub = _StubProvider(json.dumps({
        "verdict": "relevant",
        "summary": "Something happened.",
        "campaign_action": "monitor",
        "sentiment": "neutral",
        "source_credibility": "medium",
        "needs_attention": False,
        "needs_attention_reason": "",
        "extracted_claims": [
            {
                "actor_name": "Campaign",
                "actor_role": "candidate",
                "claim_type": "policy_position",
                "quote": "Paige Cognetti held a campaign event today in Scranton",
                "confidence": "medium",
                "matched_frames": [
                    valid_frame_name,
                    "Totally Made Up Frame Name That Does Not Exist",
                ],
            }
        ],
    }))
    monkeypatch.setattr(llm_provider, "get_ingestion_provider", lambda: stub)

    result = analyze_with_frames(frames_db, item, frames=frames)
    # Only the real frame survives — bogus name is dropped silently.
    assert result["frame_matches"] == [frames[0].id]


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
    monkeypatch.setattr(llm_provider, "get_ingestion_provider", lambda: stub)

    result = analyze_with_frames(cognetti_db, item, frames=None)
    assert result["frame_matches"] == []
