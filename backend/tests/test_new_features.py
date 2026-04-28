"""Tests for Phase 1-4 features: campaign profile, provider fallback,
talking points with context, dashboard actions."""
import json
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import (
    CampaignConfig, Issue, IssueMention, SourceItem,
    Opponent, OpponentActivity, CanvassingNote,
)


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


def _source(db, title="Test Source", raw_text="housing", urgency="low", source_url=None):
    s = SourceItem(
        title=title, raw_text=raw_text, source_name="test",
        source_type="news", urgency=urgency, published_at=datetime.utcnow(),
        source_url=source_url,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _campaign(db, **kwargs):
    defaults = {"candidate_name": "Test Candidate", "office": "Mayor", "district": "Downtown"}
    defaults.update(kwargs)
    c = CampaignConfig(**defaults)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


# ── Campaign profile tests ────────────────────────────────────────────────────

class TestCampaignProfile:
    def test_get_campaign_returns_profile(self, db):
        c = _campaign(db, candidate_name="Jane Doe", party="Green", office="City Council")
        assert c.id is not None
        assert c.candidate_name == "Jane Doe"
        assert c.party == "Green"

    def test_key_priorities_stored_as_json(self, db):
        priorities = ["Housing", "Education", "Jobs"]
        c = _campaign(db, key_priorities=json.dumps(priorities))
        db.expire_all()
        loaded = db.get(CampaignConfig, c.id)
        parsed = json.loads(loaded.key_priorities)
        assert parsed == priorities

    def test_campaign_message_persists(self, db):
        msg = "Our community deserves better leadership."
        c = _campaign(db, campaign_message=msg)
        db.expire_all()
        loaded = db.get(CampaignConfig, c.id)
        assert loaded.campaign_message == msg

    def test_election_date_persists(self, db):
        date = datetime(2026, 11, 3)
        c = _campaign(db, election_date=date)
        db.expire_all()
        loaded = db.get(CampaignConfig, c.id)
        assert loaded.election_date == date

    def test_updated_at_set_on_create(self, db):
        c = _campaign(db)
        assert c.updated_at is not None


# ── Schema validator tests ────────────────────────────────────────────────────

class TestCampaignProfileSchema:
    def test_key_priorities_parsed_from_json_string(self):
        from app.schemas import CampaignProfileOut
        row = MagicMock()
        row.id = 1
        row.candidate_name = "Test"
        row.party = None
        row.race = None
        row.district = None
        row.office = None
        row.location = None
        row.election_date = None
        row.campaign_message = None
        row.key_priorities = '["Housing", "Education"]'
        row.created_at = datetime.utcnow()
        row.updated_at = datetime.utcnow()
        profile = CampaignProfileOut.model_validate(row)
        assert profile.key_priorities == ["Housing", "Education"]

    def test_key_priorities_none_when_missing(self):
        from app.schemas import CampaignProfileOut
        row = MagicMock()
        row.id = 1
        row.candidate_name = "Test"
        row.party = row.race = row.district = row.office = row.location = None
        row.election_date = row.campaign_message = row.key_priorities = None
        row.created_at = row.updated_at = datetime.utcnow()
        profile = CampaignProfileOut.model_validate(row)
        assert profile.key_priorities is None

    def test_talking_point_response_has_source_fields(self):
        from app.schemas import TalkingPointResponse
        r = TalkingPointResponse(
            issue="Housing",
            short_answer="test",
            long_answer="test",
            debate_answer="test",
            social_post="test",
            risk_warning=None,
            evidence_notes="test",
            source_titles_used=["Title A"],
            source_urls_used=["https://a.com"],
        )
        assert r.source_titles_used == ["Title A"]
        assert r.source_urls_used == ["https://a.com"]

    def test_talking_point_response_defaults_empty_lists(self):
        from app.schemas import TalkingPointResponse
        r = TalkingPointResponse(
            issue="X", short_answer="a", long_answer="b",
            debate_answer="c", social_post="d",
            risk_warning=None, evidence_notes="e",
        )
        assert r.source_titles_used == []
        assert r.source_urls_used == []


# ── Provider fallback tests ───────────────────────────────────────────────────

class TestProviderFallback:
    def test_mock_provider_returned_by_default(self):
        from app.services.llm_provider import MockLLMProvider, get_provider
        with patch.dict("os.environ", {"LLM_PROVIDER": "mock"}, clear=False):
            provider = get_provider()
        assert isinstance(provider, MockLLMProvider)

    def test_openai_falls_back_to_mock_without_key(self):
        from app.services.llm_provider import MockLLMProvider, get_provider
        env = {"LLM_PROVIDER": "openai", "OPENAI_API_KEY": ""}
        with patch.dict("os.environ", env, clear=False):
            provider = get_provider()
        assert isinstance(provider, MockLLMProvider)

    def test_anthropic_falls_back_to_mock_without_key(self):
        from app.services.llm_provider import MockLLMProvider, get_provider
        env = {"LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": ""}
        with patch.dict("os.environ", env, clear=False):
            provider = get_provider()
        assert isinstance(provider, MockLLMProvider)

    def test_unknown_provider_returns_mock(self):
        from app.services.llm_provider import MockLLMProvider, get_provider
        with patch.dict("os.environ", {"LLM_PROVIDER": "unknown_vendor"}, clear=False):
            provider = get_provider()
        assert isinstance(provider, MockLLMProvider)


# ── Talking point generation with context ─────────────────────────────────────

class TestTalkingPointsWithContext:
    def test_mock_returns_required_keys(self):
        from app.services.llm_provider import MockLLMProvider
        p = MockLLMProvider()
        result = p.generate_talking_points("Housing & Affordability", "calm")
        for key in ("short_answer", "long_answer", "debate_answer", "social_post",
                    "risk_warning", "evidence_notes", "source_titles_used", "source_urls_used"):
            assert key in result, f"Missing key: {key}"

    def test_mock_uses_passed_sources_for_titles(self):
        from app.services.llm_provider import MockLLMProvider
        p = MockLLMProvider()
        sources = [
            {"title": "Custom Source A", "summary": "test", "source_url": "https://a.com"},
            {"title": "Custom Source B", "summary": "test", "source_url": None},
        ]
        result = p.generate_talking_points(
            "Housing & Affordability", "calm", sources=sources
        )
        assert "Custom Source A" in result["source_titles_used"]
        assert "Custom Source B" in result["source_titles_used"]
        assert "https://a.com" in result["source_urls_used"]

    def test_mock_personalizes_with_campaign_profile(self):
        from app.services.llm_provider import MockLLMProvider
        p = MockLLMProvider()
        profile = {"candidate_name": "Maria Chen"}
        result = p.generate_talking_points(
            "Housing & Affordability", "calm", campaign_profile=profile
        )
        # Should not crash and should still return content
        assert result["short_answer"]

    def test_aggressive_tone_prefix(self):
        from app.services.llm_provider import MockLLMProvider
        p = MockLLMProvider()
        result = p.generate_talking_points("Housing & Affordability", "aggressive")
        assert result["short_answer"].startswith("My opponent has failed")

    def test_social_tone_uses_social_post(self):
        from app.services.llm_provider import MockLLMProvider
        p = MockLLMProvider()
        result = p.generate_talking_points("Housing & Affordability", "social")
        assert result["short_answer"] == result["social_post"]

    def test_custom_issue_returns_default_structure(self):
        from app.services.llm_provider import MockLLMProvider
        p = MockLLMProvider()
        result = p.generate_talking_points("Local Waterway Pollution", "calm")
        assert result["short_answer"]
        assert isinstance(result["source_titles_used"], list)

    def test_prompt_builder_includes_sources(self):
        from app.services.llm_provider import _build_tp_prompt
        prompt = _build_tp_prompt(
            issue="Housing",
            tone="calm",
            context="Rents are up",
            campaign_profile={"candidate_name": "Jane", "office": "Council"},
            sources=[{"title": "Rent Study", "summary": "Up 30%", "source_url": "https://x.com", "urgency": "high", "credibility_note": None}],
            opponent_activities=[{"attack": "Jane wants to raise taxes", "claim": None, "contradiction_note": "False"}],
        )
        assert "Rent Study" in prompt
        assert "Jane wants to raise taxes" in prompt
        assert "Jane" in prompt

    def test_json_parser_handles_markdown_fences(self):
        from app.services.llm_provider import _parse_json_response
        raw = '```json\n{"short_answer": "hello", "long_answer": "world"}\n```'
        result = _parse_json_response(raw)
        assert result["short_answer"] == "hello"

    def test_json_parser_handles_plain_json(self):
        from app.services.llm_provider import _parse_json_response
        raw = '{"short_answer": "hello"}'
        result = _parse_json_response(raw)
        assert result["short_answer"] == "hello"

    def test_json_parser_returns_empty_on_garbage(self):
        from app.services.llm_provider import _parse_json_response
        result = _parse_json_response("this is not json at all")
        assert result == {}


# ── Dashboard actions tests ───────────────────────────────────────────────────

class TestDashboardActions:
    def _issue(self, db, name="Test Issue", urgency="low", trend="stable", mention_count=5):
        i = Issue(name=name, urgency=urgency, trend=trend, mention_count=mention_count,
                  last_seen_at=datetime.utcnow())
        db.add(i)
        db.commit()
        db.refresh(i)
        return i

    def _attack(self, db, attack_text="Attack text"):
        opp = Opponent(name="Test Opp")
        db.add(opp)
        db.flush()
        act = OpponentActivity(
            opponent_id=opp.id,
            attack=attack_text,
            created_at=datetime.utcnow(),
        )
        db.add(act)
        db.commit()
        db.refresh(act)
        return act

    def test_urgent_action_for_attack(self, db):
        from app.routes.dashboard import _build_suggested_actions
        attack = self._attack(db)
        actions = _build_suggested_actions(
            campaign=None,
            top_issues=[],
            risk_sources=[],
            recent_attacks=[attack],
            canvassing_notes=[],
        )
        assert any(a.priority == "urgent" for a in actions)
        assert any("Respond to attack" in a.action for a in actions)

    def test_high_action_for_rising_high_urgency_issue(self, db):
        from app.routes.dashboard import _build_suggested_actions
        issue = self._issue(db, name="Housing", urgency="high", trend="rising")
        actions = _build_suggested_actions(
            campaign=None,
            top_issues=[issue],
            risk_sources=[],
            recent_attacks=[],
            canvassing_notes=[],
        )
        assert any(a.priority == "high" and "Housing" in a.action for a in actions)

    def test_campaign_setup_action_when_profile_incomplete(self, db):
        from app.routes.dashboard import _build_suggested_actions
        c = _campaign(db)  # no campaign_message, no election_date
        c.campaign_message = None
        c.election_date = None
        db.commit()
        actions = _build_suggested_actions(
            campaign=c,
            top_issues=[],
            risk_sources=[],
            recent_attacks=[],
            canvassing_notes=[],
        )
        assert any("Campaign Setup" in a.action or "Campaign Profile" in a.action for a in actions)

    def test_canvassing_action_from_recent_negative_notes(self, db):
        from app.routes.dashboard import _build_suggested_actions
        recent_date = datetime.utcnow() - timedelta(days=3)
        notes = [
            CanvassingNote(precinct="7A", issue="housing", sentiment="negative", date=recent_date),
            CanvassingNote(precinct="7A", issue="housing", sentiment="negative", date=recent_date),
            CanvassingNote(precinct="7A", issue="housing", sentiment="negative", date=recent_date),
        ]
        for n in notes:
            db.add(n)
        db.commit()
        actions = _build_suggested_actions(
            campaign=None,
            top_issues=[],
            risk_sources=[],
            recent_attacks=[],
            canvassing_notes=notes,
        )
        assert any("housing" in a.action.lower() for a in actions)

    def test_at_most_six_actions(self, db):
        from app.routes.dashboard import _build_suggested_actions
        issues = [self._issue(db, name=f"Issue {i}", urgency="high", trend="rising") for i in range(10)]
        attacks = [self._attack(db, f"Attack {i}") for i in range(5)]
        actions = _build_suggested_actions(
            campaign=None,
            top_issues=issues,
            risk_sources=[],
            recent_attacks=attacks,
            canvassing_notes=[],
        )
        assert len(actions) <= 6
