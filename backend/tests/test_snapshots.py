from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import CampaignConfig, Issue, IssueMention, Opponent, OpponentActivity, SourceItem


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def _campaign(db):
    db.add(CampaignConfig(
        candidate_name="Alex Rivera",
        office="New York State Assembly",
        district="Queens Assembly District 30",
        location="Queens",
        sparse_race_mode=True,
    ))
    db.add(Opponent(name="Jordan Lee"))
    db.commit()


def _source(db, title="Housing forum", cluster="source-1", evidence=75, credibility=75):
    source = SourceItem(
        title=title,
        raw_text="Alex Rivera discussed rent stabilization with Queens tenants.",
        summary="Alex Rivera discussed rent stabilization with Queens tenants.",
        source_name="Queens Civic Forum",
        source_type="campaign_note",
        published_at=datetime.utcnow(),
        race_relevance_score=75,
        race_relevance_label="high",
        actionability_score=70,
        actionability_label="review",
        content_category="campaign",
        geo_relevance="district",
        candidate_mentioned=True,
        district_mentioned=True,
        evidence_score=evidence,
        credibility_score=credibility,
        archived_as_irrelevant=False,
        story_cluster_id=cluster,
    )
    db.add(source)
    db.commit()
    return source


def test_source_snapshot_appears_in_source_payload(db):
    from app.routes.sources import get_source

    _campaign(db)
    source = _source(db)

    detail = get_source(source.id, db=db)
    assert detail.snapshot is not None
    assert "Alex Rivera" in detail.snapshot.actors_summary
    assert detail.snapshot.action_signal == "review"
    assert detail.snapshot.evidence_summary in {"moderate", "strong"}


# issue-snapshot tests removed: depended on app.routes.issues which was
# dropped during the narrative-frames pivot.


def test_source_snapshot_includes_geography_and_opponent_actor_context(db):
    from app.routes.sources import get_source

    _campaign(db)
    source = _source(db, title="Jordan Lee attacks transit plan")
    source.opponent_mentioned = True
    opp = db.query(Opponent).first()
    db.add(OpponentActivity(opponent_id=opp.id, source_item_id=source.id, attack="Alex Rivera failed commuters"))
    db.commit()

    detail = get_source(source.id, db=db)
    assert "District-level" in detail.snapshot.geography_summary
    assert "Jordan Lee" in detail.snapshot.actors_summary
    assert detail.snapshot.key_claim_or_quote == "Alex Rivera failed commuters"
