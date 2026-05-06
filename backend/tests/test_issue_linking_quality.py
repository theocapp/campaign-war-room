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


def _campaign(db):
    db.add(CampaignConfig(
        candidate_name="Alex Rivera",
        office="New York State Assembly",
        district="Queens Assembly District 30",
        location="Queens",
        sparse_race_mode=True,
    ))
    db.commit()


def _source(db, title: str, raw_text: str, source_id: int | None = None) -> SourceItem:
    item = SourceItem(
        id=source_id,
        title=title,
        raw_text=raw_text,
        source_name="Local",
        source_type="news",
        published_at=datetime.utcnow(),
        race_relevance_score=70,
        race_relevance_label="high",
        actionability_label="review",
        content_category="campaign",
        archived_as_irrelevant=False,
        story_cluster_id=f"source-{source_id}" if source_id else None,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    if not item.story_cluster_id:
        item.story_cluster_id = f"source-{item.id}"
        db.commit()
    return item


def test_broad_civic_language_does_not_link_source_to_every_issue(db):
    from app.services.issue_clustering import assign_issues_to_source

    _campaign(db)
    item = _source(
        db,
        "Candidate campaign update for District 30",
        "The candidate discussed the campaign, voters, business outreach, green spaces, safety, and budget priorities.",
    )
    issues = assign_issues_to_source(db, item)
    names = {issue.name for issue in issues}

    assert len(names) <= 2
    assert "Local Government" not in names
    assert "Environment" not in names
    assert "Economy & Jobs" not in names


def test_housing_source_links_to_housing_not_unrelated_issues(db):
    from app.services.issue_clustering import assign_issues_to_source

    _campaign(db)
    item = _source(
        db,
        "Tenant rent plan in Queens",
        "Alex Rivera released a housing plan focused on rent stabilization, tenant protections, and affordable apartments.",
    )
    issues = assign_issues_to_source(db, item)
    names = {issue.name for issue in issues}

    assert "Housing & Affordability" in names
    assert "Education & Schools" not in names
    assert "Public Safety" not in names


def test_local_government_source_links_without_polluting_other_issues(db):
    from app.services.issue_clustering import assign_issues_to_source

    _campaign(db)
    item = _source(
        db,
        "Board of Elections posts ballot access hearing",
        "The election board scheduled a public hearing on ballot access and filing deadlines for Queens candidates.",
    )
    issues = assign_issues_to_source(db, item)
    names = {issue.name for issue in issues}

    assert "Local Government" in names
    assert "Housing & Affordability" not in names
    assert "Healthcare" not in names


def test_issue_detail_returns_issue_specific_related_intelligence(db):
    from app.routes.issues import get_issue
    from app.services.issue_clustering import assign_issues_to_source

    _campaign(db)
    housing_source = _source(
        db,
        "Tenant rent plan in Queens",
        "The housing proposal includes rent stabilization and tenant protections.",
    )
    schools_source = _source(
        db,
        "School overcrowding forum",
        "Parents discussed school overcrowding, classroom size, teachers, and after-school programs.",
    )
    assign_issues_to_source(db, housing_source)
    assign_issues_to_source(db, schools_source)

    housing = db.query(Issue).filter(Issue.name == "Housing & Affordability").one()
    education = db.query(Issue).filter(Issue.name == "Education & Schools").one()

    housing_detail = get_issue(housing.id, db=db)
    education_detail = get_issue(education.id, db=db)

    assert [s.title for s in housing_detail.recent_sources] == ["Tenant rent plan in Queens"]
    assert [s.title for s in education_detail.recent_sources] == ["School overcrowding forum"]
    assert housing_detail.recent_sources[0].issue_link_strength is not None
    assert housing_detail.recent_sources[0].issue_link_reasons


def test_issue_detail_filters_weak_issue_links_from_frontend_payload(db):
    from app.routes.issues import get_issue

    issue = Issue(name="Environment", urgency="low", mention_count=1, trend="stable", last_seen_at=datetime.utcnow())
    db.add(issue)
    db.commit()
    weak = _source(db, "Generic campaign update", "The campaign mentioned green outreach.", source_id=501)
    strong = _source(db, "Clean water pollution plan", "The plan addresses clean water, pollution, and toxic contamination.", source_id=502)
    db.add_all([
        IssueMention(issue_id=issue.id, source_item_id=weak.id, link_strength=12),
        IssueMention(issue_id=issue.id, source_item_id=strong.id, link_strength=70, link_reasons='["Matched issue terms: clean water, pollution"]'),
    ])
    db.commit()

    detail = get_issue(issue.id, db=db)
    assert [s.title for s in detail.recent_sources] == ["Clean water pollution plan"]
