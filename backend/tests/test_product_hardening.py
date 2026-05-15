import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import CampaignConfig, Issue, IssueMention, SourceItem


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def _campaign(db, **kwargs):
    defaults = {
        "candidate_name": "Alex Rivera",
        "office": "New York State Assembly",
        "district": "Queens Assembly District 30",
        "district_number": "30",
        "location": "Queens",
        "race_level": "state",
        "election_type": "primary",
        "sparse_race_mode": True,
        "key_priorities": json.dumps(["Housing", "Transit"]),
    }
    defaults.update(kwargs)
    db.add(CampaignConfig(**defaults))
    db.commit()


def test_near_duplicate_sources_share_cluster_and_do_not_inflate_issue_count(db):
    from app.services.ingestion import ingest_text

    _campaign(db)
    first = ingest_text(
        db,
        "Alex Rivera releases housing plan for Queens",
        "Alex Rivera discussed tenant protections and affordable housing in Queens.",
        "Queens Local",
        "news",
        source_url="https://queenslocal.test/rivera-housing-plan",
    )
    second = ingest_text(
        db,
        "Alex Rivera releases housing plan for Queens - Queens Local",
        "Alex Rivera discussed tenant protections and affordable housing in Queens.",
        "Queens Local",
        "news",
        source_url="https://queenslocal.test/rivera-housing-plan-copy",
    )

    issue = db.query(Issue).filter(Issue.name == "Housing & Affordability").one()
    assert first.story_cluster_id == second.story_cluster_id
    assert second.duplicate_of_source_id == first.id
    assert issue.mention_count == 1


def test_duplicate_sources_do_not_inflate_talking_point_evidence_quality(db):
    from app.routes.talking_points import generate_talking_points
    from app.schemas import TalkingPointRequest

    _campaign(db)
    issue = Issue(name="Housing & Affordability", urgency="high", trend="rising", mention_count=1, last_seen_at=datetime.utcnow())
    db.add(issue)
    db.commit()
    first = SourceItem(
        title="Alex Rivera releases housing plan for Queens",
        raw_text="Alex Rivera discussed housing in Queens.",
        source_name="Queens Local",
        source_type="news",
        published_at=datetime.utcnow(),
        race_relevance_score=80,
        race_relevance_label="critical",
        actionability_label="review",
        content_category="campaign",
        archived_as_irrelevant=False,
        story_cluster_id="source-1",
    )
    second = SourceItem(
        title="Alex Rivera releases housing plan for Queens - Queens Local",
        raw_text="Alex Rivera discussed housing in Queens.",
        source_name="Queens Local",
        source_type="news",
        published_at=datetime.utcnow(),
        race_relevance_score=75,
        race_relevance_label="high",
        actionability_label="review",
        content_category="campaign",
        archived_as_irrelevant=False,
        story_cluster_id="source-1",
        duplicate_of_source_id=1,
    )
    db.add_all([first, second])
    db.commit()
    db.add_all([
        IssueMention(issue_id=issue.id, source_item_id=first.id),
        IssueMention(issue_id=issue.id, source_item_id=second.id),
    ])
    db.commit()

    response = generate_talking_points(TalkingPointRequest(issue_id=issue.id), db=db)
    assert "Evidence is thin" in response.short_answer
    assert "Duplicate or near-duplicate sources were collapsed" in response.evidence_notes


def test_thin_talking_point_response_includes_geography_and_race_context(db):
    from app.routes.talking_points import generate_talking_points
    from app.schemas import TalkingPointRequest

    _campaign(db)
    issue = Issue(name="Transit", urgency="medium", trend="stable", mention_count=1, last_seen_at=datetime.utcnow())
    db.add(issue)
    db.commit()
    source = SourceItem(
        title="Queens forum discusses bus service",
        raw_text="Assembly candidates discussed bus service in Queens Assembly District 30.",
        source_name="Civic Forum",
        source_type="public_record",
        published_at=datetime.utcnow(),
        race_relevance_score=55,
        race_relevance_label="medium",
        actionability_label="monitor",
        content_category="campaign",
        archived_as_irrelevant=False,
        story_cluster_id="source-99",
    )
    db.add(source)
    db.commit()
    db.add(IssueMention(issue_id=issue.id, source_item_id=source.id))
    db.commit()

    response = generate_talking_points(TalkingPointRequest(issue_id=issue.id), db=db)
    assert "New York State Assembly" in response.long_answer
    assert "Queens" in response.long_answer


