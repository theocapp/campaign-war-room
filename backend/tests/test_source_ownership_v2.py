"""Tests for source ownership classification and narrative direction fixes.

Covers:
  1. classify_source_owner() false-positive prevention — candidate/opponent name
     appearing inside a third-party news article must NOT imply ownership.
  2. Domain-based owned-media detection (e.g. bresnahan.house.gov).
  3. Opponent-owned press releases that praise the opponent → direction=neutral,
     not direction=against_candidate.
  4. Opponent-owned press release with explicit attack language → still classified
     as direction=against_candidate (regression guard).
"""
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import CampaignConfig, Opponent, SourceItem


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    session.add(CampaignConfig(
        candidate_name="Alex Rivera",
        office="Assembly",
        district="Queens Assembly District 30",
        location="Queens",
    ))
    session.add(Opponent(name="Jordan Lee"))
    session.commit()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def _news_source(db, title, raw_text="", source_name="Queens Daily",
                 source_url="https://queensdaily.com/article", source_type="news"):
    s = SourceItem(
        title=title,
        source_name=source_name,
        source_url=source_url,
        source_type=source_type,
        raw_text=raw_text,
        published_at=datetime.utcnow(),
        created_at=datetime.utcnow(),
        race_relevance_score=80,
        race_relevance_label="critical",
        actionability_score=75,
        actionability_label="monitor",
        content_category="campaign",
        archived_as_irrelevant=False,
        story_cluster_id="cluster-test",
        opponent_mentioned=True,
        candidate_mentioned=True,
    )
    db.add(s)
    db.commit()
    return s


# ── classify_source_owner: false-positive prevention ─────────────────────────

class TestClassifySourceOwnerFalsePositives:
    def _classify(self, db, source):
        from app.services.source_ownership import classify_source_owner
        return classify_source_owner(db, source)

    def test_candidate_name_in_news_article_not_candidate_owned(self, db):
        """A news article quoting the candidate should stay 'media' or 'unclear',
        not be misclassified as candidate_statement."""
        source = SourceItem(
            title="Alex Rivera says he'll fight for affordable housing",
            source_name="Queens Daily",
            source_url="https://queensdaily.com/rivera-housing",
            source_type="news",
            raw_text=(
                "In an interview, Alex Rivera said the city must do more for tenants. "
                "Rivera's campaign has focused on affordability. "
                "He announced a new plan for the district."
            ),
            published_at=datetime.utcnow(),
        )
        result = self._classify(db, source)
        assert result.source_owner_type not in {"candidate_statement"}, (
            f"News article covering the candidate was mislabeled as "
            f"'{result.source_owner_type}' — expected media/unclear"
        )

    def test_opponent_name_in_news_article_not_opponent_owned(self, db):
        """A news article reporting on the opponent should not become opponent_statement."""
        source = SourceItem(
            title="Jordan Lee campaigns for affordable housing in Queens",
            source_name="The Borough Press",
            source_url="https://boroughpress.com/lee-housing-campaign",
            source_type="news",
            raw_text=(
                "Jordan Lee held a rally for affordable housing. "
                "Lee's campaign said the opponent for assembly has failed tenants. "
                "The event drew dozens of supporters."
            ),
            published_at=datetime.utcnow(),
        )
        result = self._classify(db, source)
        assert result.source_owner_type not in {"opponent_statement"}, (
            f"Third-party news mentioning opponent was mislabeled as "
            f"'{result.source_owner_type}'"
        )

    def test_generic_word_for_in_article_does_not_trigger_ownership(self, db):
        """The word 'for' is ubiquitous — it alone must not cause ownership classification."""
        source = SourceItem(
            title="Community board votes for new housing policy",
            source_name="Local News Network",
            source_url="https://localnews.com/housing-vote",
            source_type="news",
            raw_text=(
                "The community board voted for a new housing policy. "
                "Alex Rivera praised the vote for affordable housing. "
                "Jordan Lee also spoke for tenant protections."
            ),
            published_at=datetime.utcnow(),
        )
        result = self._classify(db, source)
        # Should be media, unclear, or community — never campaign-owned
        assert result.source_owner_type not in {"candidate_statement", "opponent_statement"}, (
            f"Generic news with 'for' triggered false ownership: '{result.source_owner_type}'"
        )


# ── classify_source_owner: domain-based detection ────────────────────────────

class TestDomainBasedOwnershipDetection:
    def _classify(self, db, source):
        from app.services.source_ownership import classify_source_owner
        return classify_source_owner(db, source)

    def test_gov_domain_with_opponent_name_is_opponent_owned(self, db):
        """bresnahan.house.gov → opponent_statement (government official domain)."""
        # Add a second opponent whose name matches the domain
        from app.models import Opponent as Opp
        db.add(Opp(name="Matt Bresnahan"))
        db.commit()

        source = SourceItem(
            title="Bresnahan Announces New Housing Initiative",
            source_name="Office of Rep. Matt Bresnahan",
            source_url="https://bresnahan.house.gov/press-releases/housing",
            source_type="public_record",
            raw_text="Rep. Bresnahan announced a new housing initiative today.",
            published_at=datetime.utcnow(),
        )
        result = self._classify(db, source)
        assert result.source_owner_type == "opponent_statement", (
            f"gov domain with opponent name should be opponent_statement, got '{result.source_owner_type}'"
        )
        assert result.source_owner_confidence in {"high", "medium"}

    def test_campaign_domain_with_candidate_name_is_candidate_owned(self, db):
        """alexriveracampaign.com → candidate_statement."""
        source = SourceItem(
            title="Rivera Campaign Launches Housing Platform",
            source_name="Alex Rivera for Assembly",
            source_url="https://alexriveracampaign.com/housing",
            source_type="news",
            raw_text="The Rivera campaign announced its housing platform today.",
            published_at=datetime.utcnow(),
        )
        result = self._classify(db, source)
        assert result.source_owner_type == "candidate_statement", (
            f"Campaign domain should be candidate_statement, got '{result.source_owner_type}'"
        )

    def test_news_domain_with_name_in_path_is_not_owned(self, db):
        """A news site with the candidate's name in the URL *path* (not domain) is not owned."""
        source = SourceItem(
            title="Alex Rivera Proposes Rent Control",
            source_name="Queens Tribune",
            source_url="https://queenstribune.com/news/alex-rivera-rent-control",
            source_type="news",
            raw_text="Councilmember Alex Rivera proposed rent control legislation.",
            published_at=datetime.utcnow(),
        )
        result = self._classify(db, source)
        assert result.source_owner_type not in {"candidate_statement"}, (
            f"Name in URL path only should not trigger ownership, got '{result.source_owner_type}'"
        )


# ── narratives: opponent press-release direction ──────────────────────────────

class TestOpponentPressReleaseDirection:
    def _narratives(self, db):
        from app.services.narratives import refresh_narratives
        return refresh_narratives(db, force=True)

    def test_opponent_press_release_praising_self_is_neutral_not_attack(self, db):
        """An opponent press release touting their own record must not become
        direction=against_candidate or narrative_type=opponent_attack."""
        source = SourceItem(
            title="Jordan Lee Announces Comprehensive Housing Plan",
            source_name="Jordan Lee Campaign",
            source_url="https://jordanleecampaign.com/press/housing-plan",
            source_type="opponent_statement",
            source_owner_type="opponent_statement",
            source_owner_confidence="high",
            raw_text=(
                "Jordan Lee today announced a comprehensive housing plan to build "
                "10,000 new units across Queens. Lee said the plan reflects his "
                "commitment to affordable housing for all residents."
            ),
            summary="Jordan Lee announces housing plan for Queens.",
            published_at=datetime.utcnow(),
            created_at=datetime.utcnow(),
            race_relevance_score=80,
            race_relevance_label="critical",
            actionability_score=60,
            actionability_label="monitor",
            content_category="campaign",
            archived_as_irrelevant=False,
            story_cluster_id="lee-housing-pr",
            opponent_mentioned=True,
            candidate_mentioned=False,
        )
        db.add(source)
        db.commit()

        narratives = self._narratives(db)
        opp_narratives = [n for n in narratives if n.owner_type == "opponent"]
        assert opp_narratives, "Expected at least one opponent-owned narrative"
        # None of the opponent narratives should label this as an attack
        attack_narratives = [
            n for n in opp_narratives
            if n.direction == "against_candidate" or n.narrative_type == "opponent_attack"
        ]
        assert not attack_narratives, (
            f"Self-promotional press release was mislabeled as attack: "
            f"{[(n.narrative_type, n.direction) for n in attack_narratives]}"
        )

    def test_opponent_press_release_explicit_attack_is_still_against_candidate(self, db):
        """An opponent press release that explicitly attacks the candidate by name
        must still be classified as direction=against_candidate."""
        source = SourceItem(
            title="Lee: Rivera Failed Queens Families on Housing",
            source_name="Jordan Lee Campaign",
            source_url="https://jordanleecampaign.com/press/attack",
            source_type="opponent_statement",
            source_owner_type="opponent_statement",
            source_owner_confidence="high",
            raw_text=(
                "Jordan Lee said Alex Rivera failed Queens families and has a "
                "dishonest record on housing. Lee accused Rivera of misleading "
                "tenants about rent control."
            ),
            summary="Lee says Rivera failed Queens families on housing.",
            published_at=datetime.utcnow(),
            created_at=datetime.utcnow(),
            race_relevance_score=90,
            race_relevance_label="critical",
            actionability_score=90,
            actionability_label="respond",
            content_category="campaign",
            archived_as_irrelevant=False,
            story_cluster_id="lee-attack-pr",
            opponent_mentioned=True,
            candidate_mentioned=True,
        )
        db.add(source)
        db.commit()

        narratives = self._narratives(db)
        attack_narratives = [
            n for n in narratives
            if n.direction == "against_candidate" or n.narrative_type in {"opponent_attack", "possible_attack"}
        ]
        assert attack_narratives, (
            "Explicit attack press release should produce an against_candidate narrative"
        )

    def test_news_article_quoting_candidate_is_not_candidate_owned_narrative(self, db):
        """A news article in which the candidate is quoted should not produce a
        candidate_self_definition narrative from the candidate_owned_source path."""
        source = SourceItem(
            title="Rivera says he'll fight for tenants",
            source_name="Queens Daily",
            source_url="https://queensdaily.com/rivera-tenants",
            source_type="news",
            source_owner_type="media",
            source_owner_confidence="medium",
            raw_text=(
                "Alex Rivera said he will fight for tenant protections. "
                "Rivera's campaign announced the pledge at a community event. "
                "Jordan Lee criticized the proposal as insufficient."
            ),
            summary="Rivera says he'll fight for tenants.",
            published_at=datetime.utcnow(),
            created_at=datetime.utcnow(),
            race_relevance_score=80,
            race_relevance_label="critical",
            actionability_score=65,
            actionability_label="monitor",
            content_category="campaign",
            archived_as_irrelevant=False,
            story_cluster_id="news-rivera-tenants",
            candidate_mentioned=True,
            opponent_mentioned=True,
        )
        db.add(source)
        db.commit()

        narratives = self._narratives(db)
        # Should not have a candidate-owned narrative from a news article
        candidate_owned = [
            n for n in narratives
            if n.owner_type == "candidate" and n.attribution_type == "candidate_owned_source"
        ]
        assert not candidate_owned, (
            f"News article was treated as candidate-owned source: "
            f"{[(n.narrative_type, n.attribution_type) for n in candidate_owned]}"
        )
