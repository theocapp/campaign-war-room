import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import CampaignConfig, Issue, IssueMention, ManualCapture, Opponent, OpponentActivity, SourceItem


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def _campaign(db, sparse=False):
    db.add(CampaignConfig(
        candidate_name="Alex Rivera",
        office="New York State Assembly",
        district="Queens Assembly District 30",
        location="Queens",
        election_type="primary",
        sparse_race_mode=sparse,
        key_priorities=json.dumps(["Housing", "Transit", "Schools"]),
    ))
    db.add(Opponent(name="Jordan Lee"))
    db.commit()


def _issue(db, name, urgency="medium", trend="rising"):
    issue = Issue(name=name, urgency=urgency, trend=trend, mention_count=0, last_seen_at=datetime.utcnow())
    db.add(issue)
    db.commit()
    return issue


def _source(db, title, *, issue=None, cluster=None, action="review", archived=False, source_type="news", days=0):
    source = SourceItem(
        title=title,
        raw_text=f"{title} Queens campaign context",
        source_name="Local",
        source_type=source_type,
        published_at=datetime.utcnow() - timedelta(days=days),
        created_at=datetime.utcnow() - timedelta(days=days),
        urgency="high" if action == "respond" else "medium",
        race_relevance_score=80 if not archived else 10,
        race_relevance_label="critical" if not archived else "irrelevant",
        actionability_score=90 if action == "respond" else 65,
        actionability_label=action,
        content_category="campaign" if not archived else "irrelevant",
        archived_as_irrelevant=archived,
        priority_score=100 if action == "respond" else 70,
        evidence_score=75,
        credibility_score=75,
        geo_relevance="district",
        story_cluster_id=cluster,
    )
    db.add(source)
    db.commit()
    if not source.story_cluster_id:
        source.story_cluster_id = f"source-{source.id}"
        db.commit()
    if issue:
        db.add(IssueMention(issue_id=issue.id, source_item_id=source.id, link_strength=70, link_reasons='["Matched issue terms"]'))
        issue.mention_count = len({
            row[0] for row in db.query(SourceItem.story_cluster_id)
            .join(IssueMention, SourceItem.id == IssueMention.source_item_id)
            .filter(IssueMention.issue_id == issue.id)
            .all()
        })
        db.commit()
    return source


def test_dashboard_returns_curated_attention_cards(db):
    from app.routes.dashboard import get_dashboard

    _campaign(db)
    issue = _issue(db, "Housing & Affordability", urgency="high")
    source = _source(db, "Jordan Lee attacks housing plan", issue=issue, action="respond")
    opp = db.query(Opponent).first()
    db.add(OpponentActivity(opponent_id=opp.id, source_item_id=source.id, attack="Alex Rivera's housing plan is reckless", repeated_theme="housing"))
    db.commit()

    dashboard = get_dashboard(db=db)
    assert dashboard.race_header.candidate_name == "Alex Rivera"
    assert 1 <= len(dashboard.attention_now) <= 5
    assert any(card.action_label == "confirmed" for card in dashboard.attention_now)
    assert dashboard.review_snapshot.respond_now_count == 1
    assert dashboard.opponent_watch.latest_attack
    assert dashboard.opponent_watch.source_item_id == source.id
    assert dashboard.opponent_watch.source_title == source.title


def test_dashboard_priority_issues_use_distinct_developments(db):
    from app.routes.dashboard import get_dashboard

    _campaign(db)
    housing = _issue(db, "Housing & Affordability", urgency="high")
    _source(db, "Housing plan covered by outlet A", issue=housing, cluster="housing-1")
    _source(db, "Housing plan covered by outlet B", issue=housing, cluster="housing-1")
    _source(db, "Tenant forum raises rent issue", issue=housing, cluster="housing-2")

    dashboard = get_dashboard(db=db)
    issue = next(i for i in dashboard.priority_issues if i.name == "Housing & Affordability")
    assert issue.distinct_development_count == 2
    development = next(d for d in dashboard.recent_developments if d.cluster_id == "housing-1")
    assert development.source_count == 2


def test_dashboard_does_not_surface_irrelevant_noise_actions(db):
    from app.routes.dashboard import get_dashboard

    _campaign(db)
    _source(db, "Phillies playoff recap", archived=True, action="ignore")
    dashboard = get_dashboard(db=db)
    assert all("Phillies" not in card.title for card in dashboard.attention_now)
    assert all("Phillies" not in item.title for item in dashboard.review_snapshot.top_items)
    assert all("Phillies" not in dev.title for dev in dashboard.recent_developments)


def test_sparse_race_manual_dependence_appears_in_readiness(db):
    from app.routes.dashboard import get_dashboard

    _campaign(db, sparse=True)
    issue = _issue(db, "Housing & Affordability")
    source = _source(db, "Manual tenant forum notes", issue=issue, source_type="campaign_note")
    db.add(ManualCapture(source_item_id=source.id, title=source.title, raw_text=source.raw_text, source_type=source.source_type, capture_type="forum_notes"))
    db.commit()

    dashboard = get_dashboard(db=db)
    assert dashboard.coverage_readiness.manual_source_dependence == "high"
    assert dashboard.coverage_readiness.sparse_race_note
    assert any("manual" in reason.lower() for reason in dashboard.coverage_readiness.reasons)


def test_dashboard_payload_is_focused(db):
    from app.routes.dashboard import get_dashboard

    _campaign(db)
    for idx in range(10):
        issue = _issue(db, f"Issue {idx}")
        _source(db, f"Source {idx}", issue=issue, cluster=f"cluster-{idx}")

    dashboard = get_dashboard(db=db)
    assert len(dashboard.attention_now) <= 5
    assert len(dashboard.priority_issues) <= 5
    assert len(dashboard.recent_developments) <= 5
    assert len(dashboard.review_snapshot.top_items) <= 3


def test_opponent_watch_includes_external_source_metadata(db):
    from app.routes.dashboard import get_dashboard

    _campaign(db)
    source = _source(db, "Opponent press release", action="respond")
    source.source_url = "https://example.com/opponent-attack"
    source.source_name = "Opponent Campaign"
    db.commit()
    opp = db.query(Opponent).first()
    db.add(OpponentActivity(opponent_id=opp.id, source_item_id=source.id, attack="Alex Rivera failed on transit"))
    db.commit()

    dashboard = get_dashboard(db=db)
    assert dashboard.opponent_watch.source_item_id == source.id
    assert dashboard.opponent_watch.source_title == "Opponent press release"
    assert dashboard.opponent_watch.source_name == "Opponent Campaign"
    assert dashboard.opponent_watch.source_url == "https://example.com/opponent-attack"
    assert dashboard.opponent_watch.source_created_at is not None


def test_opponent_watch_with_internal_source_only_has_source_id(db):
    from app.routes.dashboard import get_dashboard

    _campaign(db)
    source = _source(db, "Captured debate notes", action="respond")
    source.source_url = None
    db.commit()
    opp = db.query(Opponent).first()
    db.add(OpponentActivity(opponent_id=opp.id, source_item_id=source.id, attack="Debate attack on housing"))
    db.commit()

    dashboard = get_dashboard(db=db)
    assert dashboard.opponent_watch.source_item_id == source.id
    assert dashboard.opponent_watch.source_url is None
    assert dashboard.opponent_watch.source_title == "Captured debate notes"


def test_opponent_watch_no_source_degrades_gracefully(db):
    from app.routes.dashboard import get_dashboard

    _campaign(db)
    opp = db.query(Opponent).first()
    db.add(OpponentActivity(opponent_id=opp.id, source_item_id=None, attack="Unsourced attack"))
    db.commit()

    dashboard = get_dashboard(db=db)
    assert dashboard.opponent_watch.latest_attack == "Unsourced attack"
    assert dashboard.opponent_watch.source_item_id is None
    assert dashboard.opponent_watch.source_url is None
    assert dashboard.opponent_watch.source_title is None
