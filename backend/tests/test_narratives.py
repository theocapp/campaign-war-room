import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import CampaignConfig, Narrative, Opponent, OpponentActivity, SourceItem


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
        office="Assembly",
        district="Queens District 30",
        location="Queens",
        key_priorities=json.dumps(["Housing", "Transit"]),
    ))
    opponent = Opponent(name="Jordan Lee")
    db.add(opponent)
    db.commit()
    return opponent


def _source(db, title, *, cluster, source_name="Local News", source_type="news", days=0):
    source = SourceItem(
        title=title,
        raw_text=title,
        source_name=source_name,
        source_type=source_type,
        published_at=datetime.utcnow() - timedelta(days=days),
        created_at=datetime.utcnow() - timedelta(days=days),
        race_relevance_score=85,
        race_relevance_label="critical",
        actionability_score=80,
        actionability_label="respond",
        content_category="campaign",
        archived_as_irrelevant=False,
        story_cluster_id=cluster,
        geo_relevance="district",
        opponent_mentioned=True,
    )
    db.add(source)
    db.commit()
    return source


def _activity(db, opponent, source, text):
    activity = OpponentActivity(
        opponent_id=opponent.id,
        source_item_id=source.id,
        attack=text,
        repeated_theme="housing",
    )
    db.add(activity)
    db.commit()
    return activity


def test_repeated_opponent_attack_canonicalizes_into_one_narrative(db):
    from app.services.narratives import refresh_narratives

    opponent = _campaign(db)
    s1 = _source(db, "Jordan Lee says Alex Rivera failed tenants", cluster="rent-1", source_name="Queens Daily")
    s2 = _source(db, "Jordan Lee claims Rivera failed Queens tenants", cluster="rent-2", source_name="Neighborhood Post")
    _activity(db, opponent, s1, "Jordan Lee says Alex Rivera failed tenants on housing.")
    _activity(db, opponent, s2, "Jordan Lee claims Rivera failed Queens tenants on housing.")

    narratives = refresh_narratives(db)

    assert len(narratives) == 1
    narrative = narratives[0]
    assert narrative.owner_type == "opponent"
    assert narrative.source_cluster_count == 2
    assert narrative.source_count == 2


def test_distinct_attacks_do_not_collapse(db):
    from app.services.narratives import refresh_narratives

    opponent = _campaign(db)
    s1 = _source(db, "Housing attack", cluster="rent-1")
    s2 = _source(db, "Budget attack", cluster="budget-1")
    _activity(db, opponent, s1, "Jordan Lee says Alex Rivera failed tenants on housing.")
    _activity(db, opponent, s2, "Jordan Lee says Alex Rivera is corrupt on the city budget.")

    narratives = refresh_narratives(db)

    assert len(narratives) == 2


def test_traction_score_rises_with_distinct_clusters_and_diversity(db):
    from app.services.narratives import refresh_narratives

    opponent = _campaign(db)
    s1 = _source(db, "Tenant attack A", cluster="rent-1", source_name="Queens Daily")
    s2 = _source(db, "Tenant attack B", cluster="rent-2", source_name="Local Newsletter")
    s3 = _source(db, "Tenant attack C", cluster="rent-3", source_name="Civic Forum")
    for source in [s1, s2, s3]:
        _activity(db, opponent, source, "Jordan Lee says Alex Rivera failed tenants on housing.")

    narrative = refresh_narratives(db)[0]

    assert narrative.traction_score >= 70
    assert narrative.status == "rising"
    assert narrative.evidence_strength == "strong"


def test_single_source_narrative_has_weak_confidence(db):
    from app.services.narratives import refresh_narratives

    opponent = _campaign(db)
    source = _source(db, "Single attack", cluster="rent-1")
    _activity(db, opponent, source, "Jordan Lee says Alex Rivera failed tenants on housing.")

    narrative = refresh_narratives(db)[0]

    assert narrative.source_cluster_count == 1
    assert narrative.evidence_strength == "weak"
    assert narrative.status == "emerging"
    assert "one distinct source cluster" in narrative.notes


def test_dashboard_surfaces_narratives_not_only_sources_and_issues(db):
    from app.routes.dashboard import get_dashboard

    opponent = _campaign(db)
    source = _source(db, "Jordan Lee attacks Rivera on tenants", cluster="rent-1")
    _activity(db, opponent, source, "Jordan Lee says Alex Rivera failed tenants on housing.")

    dashboard = get_dashboard(db=db)

    assert dashboard.narrative_briefing
    card = dashboard.narrative_briefing[0]
    assert card.owner_type == "opponent"
    assert card.source_item_id == source.id
    assert any(attention.card_type == "narrative" for attention in dashboard.attention_now)


def test_narrative_briefing_endpoint_returns_summary(db):
    from app.routes.narratives import get_narrative_briefing

    opponent = _campaign(db)
    source = _source(db, "Jordan Lee attacks Rivera on tenants", cluster="rent-1")
    _activity(db, opponent, source, "Jordan Lee says Alex Rivera failed tenants on housing.")

    briefing = get_narrative_briefing(db=db)

    assert briefing.narratives
    assert "narrative" in briefing.summary.lower()


def test_negative_media_framing_without_opponent_attribution_is_not_opponent_attack(db):
    from app.services.narratives import refresh_narratives

    _campaign(db)
    source = SourceItem(
        title="Editorial says Alex Rivera failed tenants",
        raw_text="The article argues Alex Rivera failed tenants, but does not attribute that criticism to Jordan Lee.",
        source_name="Local Editorial Board",
        source_type="news",
        published_at=datetime.utcnow(),
        created_at=datetime.utcnow(),
        race_relevance_score=80,
        race_relevance_label="critical",
        actionability_score=80,
        actionability_label="respond",
        content_category="campaign",
        archived_as_irrelevant=False,
        story_cluster_id="editorial-1",
        candidate_mentioned=True,
        opponent_mentioned=False,
    )
    db.add(source)
    db.commit()

    narrative = refresh_narratives(db)[0]

    assert narrative.narrative_type != "opponent_attack"
    assert narrative.owner_confidence == "low"
    assert narrative.attribution_type == "unclear"


def test_direct_opponent_statement_becomes_high_confidence_opponent_attack(db):
    from app.services.narratives import refresh_narratives

    _campaign(db)
    source = _source(
        db,
        "Jordan Lee campaign statement attacks Alex Rivera on housing",
        cluster="attack-1",
        source_name="Jordan Lee Campaign",
        source_type="opponent_statement",
    )
    source.raw_text = "Jordan Lee says Alex Rivera failed tenants and cannot be trusted on housing."
    db.commit()

    narrative = refresh_narratives(db)[0]

    assert narrative.narrative_type == "opponent_attack"
    assert narrative.owner_type == "opponent"
    assert narrative.owner_confidence == "high"
    assert narrative.attribution_type == "opponent_owned_source"


def test_explicit_reported_attack_in_neutral_article_can_be_opponent_attack(db):
    from app.services.narratives import refresh_narratives

    _campaign(db)
    source = _source(
        db,
        "Neutral article reports Jordan Lee criticized Alex Rivera",
        cluster="reported-1",
        source_name="Queens Daily",
    )
    source.raw_text = "Jordan Lee criticized Alex Rivera over tenant protections at a Queens forum."
    db.commit()

    narrative = refresh_narratives(db)[0]

    assert narrative.narrative_type == "opponent_attack"
    assert narrative.attribution_type == "explicit_reported_attack"
    assert narrative.owner_confidence == "high"
    assert narrative.target_confidence == "high"


def test_weak_opponent_mention_does_not_overstate_ownership(db):
    from app.services.narratives import refresh_narratives

    _campaign(db)
    source = _source(
        db,
        "Race story mentions Jordan Lee and criticism of Alex Rivera",
        cluster="weak-1",
        source_name="Queens Daily",
    )
    source.raw_text = "The race includes Jordan Lee. Some voters say Alex Rivera failed tenants."
    db.commit()

    narrative = refresh_narratives(db)[0]

    assert narrative.narrative_type in {"possible_attack", "media_frame"}
    assert narrative.owner_type != "opponent"
    assert narrative.owner_confidence == "low"


def test_dashboard_does_not_overclaim_low_confidence_narrative(db):
    from app.routes.dashboard import get_dashboard

    _campaign(db)
    source = SourceItem(
        title="Editorial says Alex Rivera failed tenants",
        raw_text="The article argues Alex Rivera failed tenants, without attributing the criticism to Jordan Lee.",
        source_name="Local Editorial Board",
        source_type="news",
        published_at=datetime.utcnow(),
        created_at=datetime.utcnow(),
        race_relevance_score=80,
        race_relevance_label="critical",
        actionability_score=80,
        actionability_label="respond",
        content_category="campaign",
        archived_as_irrelevant=False,
        story_cluster_id="editorial-1",
        candidate_mentioned=True,
    )
    db.add(source)
    db.commit()

    dashboard = get_dashboard(db=db)
    card = dashboard.narrative_briefing[0]

    assert card.narrative_type != "opponent_attack"
    assert card.owner_confidence == "low"
    assert "not strong enough" in card.why_it_matters
