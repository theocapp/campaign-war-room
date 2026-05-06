import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import CampaignConfig, Issue, IssueMention, SourceItem
from app.schemas import CandidateMessageLibraryIn, CandidateNarrativeCreate, CandidateNarrativeUpdate, TalkingPointRequest


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
    campaign = CampaignConfig(
        candidate_name="Alex Rivera",
        office="Assembly",
        district="Queens District 30",
        location="Queens",
        campaign_message="Put affordability first.",
        key_priorities=json.dumps(["Housing & Affordability"]),
    )
    db.add(campaign)
    db.commit()
    return campaign


def _source(db, title, raw_text, *, source_type="campaign_note", source_name="Alex Rivera Campaign", cluster="c1"):
    source = SourceItem(
        title=title,
        raw_text=raw_text,
        summary=raw_text,
        source_name=source_name,
        source_type=source_type,
        published_at=datetime.utcnow(),
        created_at=datetime.utcnow(),
        race_relevance_score=85,
        race_relevance_label="critical",
        actionability_score=65,
        actionability_label="review",
        content_category="campaign",
        archived_as_irrelevant=False,
        story_cluster_id=cluster,
        candidate_mentioned=True,
        geo_relevance="district",
    )
    db.add(source)
    db.commit()
    return source


def _candidate_narrative(db, issue_name="Housing & Affordability"):
    from app.routes.message_library import create_candidate_narrative

    return create_candidate_narrative(CandidateNarrativeCreate(
        short_label="Affordability first",
        canonical_text="Alex Rivera will put affordability first for working families.",
        narrative_kind="issue_frame",
        issue_name=issue_name,
        preferred_phrases=["put affordability first"],
        must_mention_points=["working families"],
        priority=10,
        active=True,
    ), db=db)


def test_message_library_crud(db):
    from app.routes.message_library import (
        create_candidate_narrative,
        delete_candidate_narrative,
        get_message_library,
        list_candidate_narratives,
        update_candidate_narrative,
        update_message_library,
    )

    _campaign(db)
    library = get_message_library(db=db)
    assert library.core_message == "Put affordability first."

    updated = update_message_library(CandidateMessageLibraryIn(
        core_message="Safe streets and affordable neighborhoods.",
        short_bio_frame="A Queens organizer.",
        tone_guidance="Plainspoken and local.",
    ), db=db)
    assert updated.tone_guidance == "Plainspoken and local."

    narrative = create_candidate_narrative(CandidateNarrativeCreate(
        short_label="Safe affordable neighborhoods",
        canonical_text="Alex Rivera will make Queens safer and more affordable.",
        narrative_kind="issue_frame",
        issue_name="Public Safety",
        preferred_phrases=["safer and more affordable"],
    ), db=db)
    assert narrative.id is not None
    assert len(list_candidate_narratives(db=db)) == 1

    changed = update_candidate_narrative(narrative.id, CandidateNarrativeUpdate(priority=7, active=False), db=db)
    assert changed.priority == 7
    assert changed.active is False

    assert delete_candidate_narrative(narrative.id, db=db) == {"deleted": True}
    assert list_candidate_narratives(db=db) == []


def test_candidate_narrative_matching_from_owned_material(db):
    from app.services.narratives import refresh_narratives

    _campaign(db)
    candidate = _candidate_narrative(db)
    _source(
        db,
        "Campaign housing post",
        "Alex Rivera will put affordability first for working families in Queens.",
        source_type="campaign_note",
        source_name="Alex Rivera Campaign",
    )

    narratives = refresh_narratives(db)
    matched = next(n for n in narratives if n.owner_type == "candidate")

    assert matched.candidate_narrative_id == candidate.id
    assert matched.narrative_type == "policy_frame"
    assert matched.owner_confidence == "high"


def test_weak_candidate_message_match_does_not_create_false_narrative(db):
    from app.services.narratives import refresh_narratives

    _campaign(db)
    _candidate_narrative(db)
    _source(
        db,
        "Campaign generic post",
        "Alex Rivera met voters in Queens and talked about local concerns.",
        source_type="campaign_note",
        source_name="Alex Rivera Campaign",
    )

    narratives = refresh_narratives(db)

    assert not [n for n in narratives if n.owner_type == "candidate"]


def test_comparison_endpoint_distinguishes_owned_only_and_broader_spread(db):
    from app.routes.narratives import compare_narratives
    from app.services.narratives import refresh_narratives

    _campaign(db)
    _candidate_narrative(db)
    _source(
        db,
        "Campaign affordability post",
        "Alex Rivera will put affordability first for working families.",
        source_type="campaign_note",
        source_name="Alex Rivera Campaign",
        cluster="owned",
    )
    refresh_narratives(db)
    owned_only = compare_narratives(db=db)
    assert owned_only.candidate_owned_only
    assert not owned_only.candidate_broader_spread

    _source(
        db,
        "Local story quotes Rivera on affordability",
        "Alex Rivera said he will put affordability first for working families.",
        source_type="news",
        source_name="Queens Daily",
        cluster="news",
    )
    broader = compare_narratives(db=db)
    assert broader.candidate_broader_spread


def test_talking_points_use_preferred_candidate_framing(db):
    from app.routes.talking_points import generate_talking_points

    _campaign(db)
    _candidate_narrative(db)
    issue = Issue(name="Housing & Affordability", urgency="medium", trend="rising", mention_count=2, last_seen_at=datetime.utcnow())
    db.add(issue)
    db.commit()
    s1 = _source(db, "Housing source one", "Queens housing affordability pressure continues.", cluster="h1")
    s2 = _source(db, "Housing source two", "Another local housing affordability source.", source_name="Local News", source_type="news", cluster="h2")
    for source in [s1, s2]:
        db.add(IssueMention(issue_id=issue.id, source_item_id=source.id, link_strength=80))
    db.commit()

    response = generate_talking_points(TalkingPointRequest(issue_id=issue.id, tone="calm"), db=db)

    assert "put affordability first" in response.short_answer.lower()
    assert "Candidate message library frame used" in response.evidence_notes
