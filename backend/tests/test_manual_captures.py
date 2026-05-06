import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import CampaignConfig, Issue, IssueMention, ManualCapture, Opponent
from app.schemas import ManualCaptureCreate, TalkingPointRequest


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def _campaign(db, sparse=True):
    db.add(CampaignConfig(
        candidate_name="Alex Rivera",
        office="New York State Assembly",
        district="Queens Assembly District 30",
        district_number="30",
        location="Queens",
        race_level="state",
        election_type="primary",
        sparse_race_mode=sparse,
        key_priorities=json.dumps(["Housing", "Transit"]),
    ))
    db.add(Opponent(name="Jordan Lee"))
    db.commit()


def _capture(db, **kwargs):
    from app.routes.manual_captures import create_manual_capture

    body = ManualCaptureCreate(**{
        "title": "Alex Rivera housing flyer",
        "raw_text": "Alex Rivera will fight for tenant protections and affordable housing in Queens Assembly District 30.",
        "source_name": "Campaign flyer",
        "source_type": "campaign_note",
        "capture_type": "flyer",
        "geography_tags": ["Queens", "District 30"],
        "issue_tags": ["Housing"],
        **kwargs,
    })
    return create_manual_capture(body=body, db=db)


def test_manual_capture_creates_source_item_through_pipeline(db):
    _campaign(db)
    result = _capture(db)

    assert result.capture.id is not None
    assert result.capture.source_item_id == result.source_item.id
    assert result.source_item.summary
    assert result.source_item.race_relevance_score >= 40
    assert result.source_item.evidence_score > 0
    assert db.query(ManualCapture).count() == 1
    assert any(i.name == "Housing & Affordability" for i in result.related_issues)


def test_relevant_manual_capture_enters_review_queue(db):
    from app.routes.review_queue import get_review_queue

    _campaign(db)
    result = _capture(db, title="Jordan Lee attacks Alex Rivera housing plan", raw_text="Jordan Lee says Alex Rivera failed Queens tenants on housing.")

    queue = get_review_queue(db=db)
    assert result.source_item.id in [item.id for item in queue]


def test_irrelevant_manual_capture_can_be_archived(db):
    _campaign(db)
    result = _capture(
        db,
        title="Restaurant newsletter recipe",
        raw_text="This newsletter shares a pasta recipe and restaurant specials.",
        source_name="Restaurant newsletter",
        source_type="news",
        capture_type="newsletter",
        geography_tags=[],
        issue_tags=[],
    )

    assert result.source_item.archived_as_irrelevant is True
    assert result.source_item.actionability_label == "ignore"


def test_sparse_race_mode_treats_manual_local_capture_as_relevant(db):
    _campaign(db, sparse=True)
    result = _capture(
        db,
        title="Queens civic forum notes",
        raw_text="Assembly candidates discussed bus service and housing at a Queens forum.",
        source_name="Civic association notes",
        source_type="public_record",
        capture_type="forum_notes",
        geography_tags=["Queens", "District 30"],
        issue_tags=["Transit"],
    )

    assert result.source_item.archived_as_irrelevant is False
    assert result.source_item.race_relevance_score >= 40


def test_dashboard_diagnostics_reflect_manual_source_dependence(db):
    from app.routes.dashboard import get_dashboard

    _campaign(db)
    _capture(db)

    dashboard = get_dashboard(db=db)
    assert dashboard.source_coverage.manual_source_dependence == "high"
    assert any("manual capture" in reason.lower() for reason in dashboard.source_coverage.reasons)


def test_talking_points_can_use_manual_captures_as_evidence(db):
    from app.routes.talking_points import generate_talking_points

    _campaign(db)
    first = _capture(db, title="Alex Rivera housing flyer")
    second = _capture(
        db,
        title="Queens tenants group endorsement",
        raw_text="A Queens tenants group endorsed Alex Rivera after his affordable housing pledge.",
        source_name="Tenants group",
        source_type="campaign_note",
        capture_type="endorsement",
        issue_tags=["Housing"],
        geography_tags=["Queens"],
    )

    issue = db.query(Issue).filter(Issue.name == "Housing & Affordability").first()
    assert issue is not None
    response = generate_talking_points(TalkingPointRequest(issue_id=issue.id), db=db)

    assert first.source_item.title in response.source_titles_used
    assert second.source_item.title in response.source_titles_used
    assert "Evidence is thin" not in response.short_answer
