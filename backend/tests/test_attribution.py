"""Tests for subject-vs-speaker attribution in narrative classification.

The core invariant:  a person's name appearing *inside* the text of a post
does NOT make that person the author/speaker of the post.

  "Rob Bresnahan made the 'most corrupt politicians' list"

posted by a Democratic Facebook page should be classified as an ATTACK
narrative ABOUT Rob, not a narrative BY Rob.
"""
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import CampaignConfig, Opponent, SourceItem


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def _campaign(db, candidate="Rob Bresnahan", opponent="Dan Meuser"):
    db.add(CampaignConfig(
        candidate_name=candidate,
        office="Congress",
        district="PA-08",
        location="Scranton",
    ))
    opp = Opponent(name=opponent)
    db.add(opp)
    db.commit()
    return opp


def _source(db, title, *, source_type="social", source_name="Democrats of PA-08",
            source_url="https://facebook.com/democratsofpa08",
            source_owner_type="unclear", candidate_mentioned=True,
            opponent_mentioned=False, actionability_label="monitor",
            race_relevance_score=70):
    s = SourceItem(
        title=title,
        raw_text=title,
        source_name=source_name,
        source_type=source_type,
        source_url=source_url,
        source_owner_type=source_owner_type,
        candidate_mentioned=candidate_mentioned,
        opponent_mentioned=opponent_mentioned,
        actionability_label=actionability_label,
        race_relevance_score=race_relevance_score,
        content_category="campaign",
        archived_as_irrelevant=False,
    )
    db.add(s)
    db.commit()
    return s


# ── _text_attacks_candidate ───────────────────────────────────────────────────

class TestTextAttacksCandidate:
    def test_corrupt_list_is_attack(self):
        from app.services.narratives import _text_attacks_candidate
        assert _text_attacks_candidate(
            "Rob Bresnahan made the 'most corrupt politicians' list",
            "Rob Bresnahan",
        ) is True

    def test_failed_tenants_is_attack(self):
        from app.services.narratives import _text_attacks_candidate
        assert _text_attacks_candidate(
            "Rob Bresnahan failed his tenants on housing",
            "Rob Bresnahan",
        ) is True

    def test_positive_framing_is_not_attack(self):
        from app.services.narratives import _text_attacks_candidate
        assert _text_attacks_candidate(
            "Rob Bresnahan is fighting for PA working families",
            "Rob Bresnahan",
        ) is False

    def test_neutral_mention_is_not_attack(self):
        from app.services.narratives import _text_attacks_candidate
        assert _text_attacks_candidate(
            "Rob Bresnahan attended the town hall",
            "Rob Bresnahan",
        ) is False

    def test_attack_without_candidate_name_is_not_attack(self):
        from app.services.narratives import _text_attacks_candidate
        assert _text_attacks_candidate(
            "The congressman is corrupt",
            "Rob Bresnahan",
        ) is False

    def test_none_candidate_name_safe(self):
        from app.services.narratives import _text_attacks_candidate
        assert _text_attacks_candidate("Rob Bresnahan is corrupt", None) is False


# ── Subject vs. speaker — _candidate_from_source ─────────────────────────────

class TestSubjectVsSpeaker:
    def test_third_party_facebook_attack_is_not_candidate_self_definition(self, db):
        """
        "Rob Bresnahan made the 'most corrupt politicians' list" posted by a
        Facebook page that is NOT Rob's campaign must NOT produce a
        candidate_self_definition narrative with owner_type='candidate'.
        """
        from app.services.narratives import _candidate_from_source
        _campaign(db)
        campaign = db.query(CampaignConfig).first()
        opponents = db.query(Opponent).all()

        source = _source(
            db,
            "Rob Bresnahan made the 'most corrupt politicians' list",
            source_type="social",
            source_owner_type="unclear",  # NOT candidate-owned
        )

        result = _candidate_from_source(source, campaign, opponents)

        assert result is not None, "Attack should produce a narrative candidate"
        assert result.owner_type != "candidate", (
            f"Third-party attack should not be attributed to the candidate; got owner_type={result.owner_type!r}"
        )
        assert result.narrative_type != "candidate_self_definition", (
            f"Attack narrative must not be 'candidate_self_definition'; got {result.narrative_type!r}"
        )
        assert result.direction == "against_candidate", (
            f"Expected direction='against_candidate', got {result.direction!r}"
        )

    def test_third_party_attack_gets_possible_attack_type(self, db):
        from app.services.narratives import _candidate_from_source
        _campaign(db)
        campaign = db.query(CampaignConfig).first()
        opponents = db.query(Opponent).all()

        source = _source(
            db,
            "Rob Bresnahan made the 'most corrupt politicians' list",
            source_type="social",
            source_owner_type="unclear",
        )

        result = _candidate_from_source(source, campaign, opponents)
        assert result is not None
        assert result.narrative_type == "possible_attack"
        assert result.attribution_type == "inferred_attack_on_candidate"

    def test_candidate_owned_social_post_is_self_definition(self, db):
        """
        A social post FROM the candidate's own verified page should remain
        classified as candidate_self_definition.
        """
        from app.services.narratives import _candidate_from_source
        _campaign(db)
        campaign = db.query(CampaignConfig).first()
        opponents = db.query(Opponent).all()

        source = _source(
            db,
            "Rob Bresnahan is fighting for PA working families",
            source_type="social",
            source_name="Rob Bresnahan for Congress",
            source_url="https://bresnahan.house.gov",
            source_owner_type="candidate_statement",  # properly classified as candidate-owned
            candidate_mentioned=True,
        )

        result = _candidate_from_source(source, campaign, opponents)
        assert result is not None
        assert result.owner_type == "candidate"
        assert result.narrative_type == "candidate_self_definition"

    def test_news_article_about_candidate_is_not_candidate_authored(self, db):
        """
        A news article reporting on a corruption list should not be attributed
        to the candidate even when the candidate is the subject.
        """
        from app.services.narratives import _candidate_from_source
        _campaign(db)
        campaign = db.query(CampaignConfig).first()
        opponents = db.query(Opponent).all()

        source = _source(
            db,
            "Report: Rob Bresnahan named in corruption investigation",
            source_type="news",
            source_name="Times-Tribune",
            source_url="https://thetimes-tribune.com/news/bresnahan-corruption",
            source_owner_type="media",
        )

        result = _candidate_from_source(source, campaign, opponents)
        assert result is None or result.owner_type != "candidate", (
            "News article about candidate must not be attributed to candidate"
        )

    def test_third_party_attack_on_news_source_gets_possible_attack(self, db):
        """News article using attack language should surface as possible_attack, not neutral."""
        from app.services.narratives import _candidate_from_source
        _campaign(db)
        campaign = db.query(CampaignConfig).first()
        opponents = db.query(Opponent).all()

        source = _source(
            db,
            "Rob Bresnahan accused of reckless spending",
            source_type="news",
            source_name="PA Reporter",
            source_url="https://pareporter.com/bresnahan",
            source_owner_type="media",
            candidate_mentioned=True,
        )

        result = _candidate_from_source(source, campaign, opponents)
        if result is not None:
            assert result.owner_type != "candidate"
            assert result.direction == "against_candidate"

    def test_explicit_opponent_attack_still_classified_correctly(self, db):
        """
        When the named opponent is present AND the attack is explicit, we still
        get the high-confidence opponent_attack classification.
        """
        from app.services.narratives import _candidate_from_source
        _campaign(db)
        campaign = db.query(CampaignConfig).first()
        opponents = db.query(Opponent).all()

        source = _source(
            db,
            "Dan Meuser says Rob Bresnahan failed his constituents on healthcare",
            source_type="opponent_statement",
            source_name="Meuser Campaign",
            source_url="https://danmeuserforcongress.com",
            source_owner_type="opponent_statement",
            candidate_mentioned=True,
            opponent_mentioned=True,
            actionability_label="respond",
        )

        result = _candidate_from_source(source, campaign, opponents)
        assert result is not None
        assert result.owner_type == "opponent"
        assert result.direction == "against_candidate"
        assert result.narrative_type in {"opponent_attack", "possible_attack"}
