import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import CampaignConfig, Issue, IssueMention, Opponent, SourceItem


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
    c = CampaignConfig(
        candidate_name="Maria Alvarez",
        office="City Council",
        district="District 7",
        location="Riverton",
        key_priorities=json.dumps(["Housing", "Transit"]),
    )
    db.add(c)
    db.add(Opponent(name="Roy Harmon"))
    db.commit()
    return c


def test_candidate_mention_is_high_relevance(db):
    from app.services.ingestion import ingest_text
    _campaign(db)
    item = ingest_text(db, "Maria Alvarez releases housing plan", "The campaign announced details.", "Local", "news")
    assert item.race_relevance_label in {"high", "critical"}
    assert item.candidate_mentioned is True


def test_opponent_mention_is_high_relevance(db):
    from app.services.ingestion import ingest_text
    _campaign(db)
    item = ingest_text(db, "Roy Harmon claimed taxes will rise", "Harmon said the race is about taxes.", "Local", "news")
    assert item.race_relevance_label in {"high", "critical"}
    assert item.opponent_mentioned is True


def test_district_mention_is_high_relevance(db):
    from app.services.ingestion import ingest_text
    _campaign(db)
    item = ingest_text(db, "District 7 voters discuss transit", "Residents in District 7 want more bus service.", "Local", "news")
    assert item.race_relevance_label in {"high", "critical"}
    assert item.district_mentioned is True


def test_phillies_sports_story_is_irrelevant(db):
    from app.services.ingestion import ingest_text
    _campaign(db)
    item = ingest_text(db, "Phillies manager discusses playoffs", "The MLB season has another big game tonight.", "Sports", "news")
    assert item.content_category == "sports"
    assert item.archived_as_irrelevant is True
    assert item.actionability_label == "ignore"


def test_phillies_sports_story_does_not_enter_review_queue(db):
    from app.routes.review_queue import get_review_queue
    from app.services.ingestion import ingest_text
    _campaign(db)
    item = ingest_text(db, "Phillies playoff game", "The coach talked about the season.", "Sports", "news")
    results = get_review_queue(db=db)
    assert item.id not in [r.id for r in results]


def test_phillies_sports_story_does_not_create_dashboard_action(db):
    from app.routes.dashboard import get_dashboard
    from app.services.ingestion import ingest_text
    _campaign(db)
    item = ingest_text(db, "Phillies playoff game", "The coach talked about the season.", "Sports", "news")
    item.urgency = "high"
    item.credibility_note = "Urgent sports update."
    db.commit()
    dashboard = get_dashboard(db=db)
    assert all("Phillies" not in a.action for a in dashboard.suggested_actions)


def test_sports_story_with_candidate_mention_is_not_archived(db):
    from app.services.ingestion import ingest_text
    _campaign(db)
    item = ingest_text(
        db,
        "Maria Alvarez attends Phillies community game",
        "Maria Alvarez met District 7 voters before the Phillies game.",
        "Local",
        "news",
    )
    assert item.archived_as_irrelevant is False
    assert item.content_category == "campaign"


def test_broad_national_politics_without_local_connection_is_low_or_irrelevant(db):
    from app.services.ingestion import ingest_text
    _campaign(db)
    item = ingest_text(
        db,
        "National party leaders debate election strategy",
        "The presidential campaign and national polls dominated the story.",
        "National Wire",
        "news",
    )
    assert item.race_relevance_score <= 30
    assert item.race_relevance_label in {"low", "irrelevant"}


def test_priority_score_favors_race_relevance_over_urgency(db):
    from app.services.ingestion import ingest_text
    _campaign(db)
    sports = ingest_text(db, "URGENT Phillies playoff game", "Urgent MLB game and season update.", "Sports", "news")
    sports.urgency = "high"
    relevant = ingest_text(db, "Maria Alvarez housing plan", "Maria Alvarez discusses Housing.", "Local", "news")
    relevant.urgency = "low"
    db.commit()
    from app.services.ingestion import _compute_priority_score
    assert _compute_priority_score(db, relevant) > _compute_priority_score(db, sports)


def test_talking_points_warn_when_no_relevant_evidence_exists(db):
    from app.routes.talking_points import generate_talking_points
    from app.schemas import TalkingPointRequest
    _campaign(db)
    issue = Issue(name="Housing", urgency="low", trend="stable", mention_count=1, last_seen_at=datetime.utcnow())
    db.add(issue)
    db.commit()
    source = SourceItem(
        title="Generic housing trend",
        raw_text="Housing costs are discussed nationally.",
        source_name="Wire",
        source_type="news",
        published_at=datetime.utcnow(),
        race_relevance_score=20,
        race_relevance_label="low",
        actionability_label="ignore",
        content_category="irrelevant",
        archived_as_irrelevant=True,
    )
    db.add(source)
    db.commit()
    db.add(IssueMention(issue_id=issue.id, source_item_id=source.id))
    db.commit()
    response = generate_talking_points(body=TalkingPointRequest(issue_id=issue.id), db=db)
    assert "Evidence is too thin" in response.short_answer
    assert response.source_titles_used == []


def test_sparse_race_mode_changes_relevance_behavior(db):
    from app.services.race_relevance import apply_relevance
    _campaign(db)
    regular = SourceItem(
        title="Assembly primary forum in Sunnyside",
        raw_text="Candidates discussed transit at a neighborhood forum.",
        source_name="Local Civic Group",
        source_type="public_record",
        published_at=datetime.utcnow(),
    )
    apply_relevance(db, regular)
    assert regular.archived_as_irrelevant is True

    db.query(CampaignConfig).delete()
    db.commit()
    db.add(CampaignConfig(
        candidate_name="Alex Rivera",
        office="Assembly",
        district="Queens Assembly District 37",
        district_number="AD 37",
        location="Queens",
        race_level="state",
        election_type="primary",
        sparse_race_mode=True,
        neighborhood_keywords=json.dumps(["Sunnyside"]),
    ))
    db.commit()
    sparse = SourceItem(
        title="Assembly primary forum in Sunnyside",
        raw_text="Candidates discussed transit at a neighborhood forum.",
        source_name="Local Civic Group",
        source_type="public_record",
        published_at=datetime.utcnow(),
    )
    apply_relevance(db, sparse)
    assert sparse.race_relevance_score > regular.race_relevance_score
    assert sparse.archived_as_irrelevant is False


def test_existing_sports_item_becomes_archived_after_reanalysis(db):
    from app.services.reanalysis import ReanalysisOptions, reanalyze_sources
    _campaign(db)
    item = SourceItem(
        title="Phillies manager discusses playoffs",
        raw_text="The MLB season has another big game tonight.",
        source_name="Sports",
        source_type="news",
        published_at=datetime.utcnow(),
    )
    db.add(item)
    db.commit()
    result = reanalyze_sources(db, ReanalysisOptions(source_id=item.id))
    db.refresh(item)
    assert result["updated_count"] == 1
    assert item.archived_as_irrelevant is True
    assert item.content_category == "sports"


def test_existing_opponent_item_gets_high_relevance_after_reanalysis(db):
    from app.services.reanalysis import ReanalysisOptions, reanalyze_sources
    _campaign(db)
    item = SourceItem(
        title="Roy Harmon claimed Maria Alvarez failed voters",
        raw_text="Roy Harmon said Maria Alvarez is wrong on housing.",
        source_name="Local",
        source_type="news",
        published_at=datetime.utcnow(),
    )
    db.add(item)
    db.commit()
    reanalyze_sources(db, ReanalysisOptions(source_id=item.id))
    db.refresh(item)
    assert item.race_relevance_label in {"high", "critical"}
    assert item.actionability_label == "respond"
    assert item.archived_as_irrelevant is False


def test_reanalysis_dry_run_does_not_modify_db(db):
    from app.services.reanalysis import ReanalysisOptions, reanalyze_sources
    _campaign(db)
    item = SourceItem(
        title="Phillies playoff game",
        raw_text="The coach discussed the season.",
        source_name="Sports",
        source_type="news",
        published_at=datetime.utcnow(),
    )
    db.add(item)
    db.commit()
    result = reanalyze_sources(db, ReanalysisOptions(source_id=item.id, dry_run=True))
    db.refresh(item)
    assert result["dry_run"] is True
    assert result["results"][0]["changed"] is True
    assert item.archived_as_irrelevant is False
    assert item.race_relevance_score == 0


def test_reanalysis_source_id_limits_to_one_source(db):
    from app.services.reanalysis import ReanalysisOptions, reanalyze_sources
    _campaign(db)
    sports = SourceItem(
        title="Phillies playoff game",
        raw_text="The coach discussed the season.",
        source_name="Sports",
        source_type="news",
        published_at=datetime.utcnow(),
    )
    opponent = SourceItem(
        title="Roy Harmon claimed taxes will rise",
        raw_text="Roy Harmon said the campaign is about taxes.",
        source_name="Local",
        source_type="news",
        published_at=datetime.utcnow(),
    )
    db.add_all([sports, opponent])
    db.commit()
    result = reanalyze_sources(db, ReanalysisOptions(source_id=sports.id))
    db.refresh(sports)
    db.refresh(opponent)
    assert result["matched_count"] == 1
    assert sports.archived_as_irrelevant is True
    assert opponent.race_relevance_score == 0
