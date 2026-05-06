from datetime import datetime

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


def test_nrcc_page_classifies_as_party_committee_statement(db):
    from app.services.source_ownership import classify_source_owner

    source = SourceItem(
        title="NRCC launches attack ad against Alex Rivera",
        source_name="National Republican Congressional Committee",
        source_url="https://nrcc.org/press/attack-ad",
        source_type="news",
        raw_text="The NRCC says Alex Rivera failed Queens families and must be stopped.",
        published_at=datetime.utcnow(),
    )

    result = classify_source_owner(db, source)

    assert result.source_owner_type == "party_committee_statement"
    assert result.source_owner_confidence == "high"


def test_party_committee_attack_stays_outside_opponent_owned_narratives(db):
    from app.services.narratives import refresh_narratives

    source = SourceItem(
        title="NRCC attacks Alex Rivera over housing",
        source_name="National Republican Congressional Committee",
        source_url="https://nrcc.org/press/attack",
        source_type="news",
        source_owner_type="party_committee_statement",
        source_owner_confidence="high",
        summary="NRCC attacks Alex Rivera over housing",
        raw_text="The NRCC says Alex Rivera failed Queens families and must be stopped.",
        published_at=datetime.utcnow(),
        created_at=datetime.utcnow(),
        race_relevance_score=80,
        race_relevance_label="high",
        actionability_score=85,
        actionability_label="respond",
        content_category="campaign",
        archived_as_irrelevant=False,
        story_cluster_id="nrcc-1",
        candidate_mentioned=True,
        opponent_mentioned=False,
    )
    db.add(source)
    db.commit()

    narrative = refresh_narratives(db, force=True)[0]

    assert narrative.owner_type == "party_committee"
    assert narrative.direction == "against_candidate"
    assert narrative.narrative_type == "possible_attack"
    assert narrative.attribution_type == "inferred_owned_source"


def test_outside_group_attack_is_not_labeled_as_opponent_owned(db):
    from app.services.narratives import refresh_narratives

    source = SourceItem(
        title="Outside group targets Alex Rivera",
        source_name="VoteVets",
        source_url="https://votevets.org/attack",
        source_type="news",
        source_owner_type="outside_group_statement",
        source_owner_confidence="high",
        summary="Outside group targets Alex Rivera",
        raw_text="VoteVets says Alex Rivera failed veterans and should be opposed.",
        published_at=datetime.utcnow(),
        created_at=datetime.utcnow(),
        race_relevance_score=80,
        race_relevance_label="high",
        actionability_score=85,
        actionability_label="respond",
        content_category="campaign",
        archived_as_irrelevant=False,
        story_cluster_id="og-1",
        candidate_mentioned=True,
    )
    db.add(source)
    db.commit()

    narrative = refresh_narratives(db, force=True)[0]

    assert narrative.owner_type == "outside_group"
    assert narrative.direction == "against_candidate"
    assert narrative.owner_confidence == "low"
    assert narrative.attribution_type == "inferred_owned_source"
