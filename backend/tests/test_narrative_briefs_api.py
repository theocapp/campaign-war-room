from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.routes.narratives import get_narrative_briefs


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def test_narrative_briefs_endpoint_returns_briefs(db):
    from .test_narratives import _campaign, _source, _activity

    opponent = _campaign(db)
    s1 = _source(db, "Jordan Lee attacks Rivera on tenants", cluster="rent-1")
    s2 = _source(db, "Repeat: Jordan Lee attacks Rivera on tenants again", cluster="rent-2", days=1)
    _activity(db, opponent, s1, "Jordan Lee says Alex Rivera failed tenants on housing.")
    _activity(db, opponent, s2, "Jordan Lee repeats accusation that Alex Rivera failed tenants on housing.")

    briefs = get_narrative_briefs(limit=5, db=db)
    assert isinstance(briefs, list)
    assert len(briefs) >= 1
    card = briefs[0]
    assert hasattr(card, 'what_changed')
    assert hasattr(card, 'action')
    assert hasattr(card, 'top_supporting_sources')
    assert isinstance(card.top_supporting_sources, list)
    assert card.what_changed is not None
    assert card.action in {"respond", "monitor", "ignore", "amplify"} or isinstance(card.action, str)
    # timeline/change-detection fields
    assert hasattr(card, 'change_summary')
    assert hasattr(card, 'new_messenger_types')
    assert hasattr(card, 'new_source_clusters_count')
    assert hasattr(card, 'escaped_owned_recently')
    assert hasattr(card, 'momentum_shift')


def test_narrative_what_changed_prefers_concrete_change_signals(db):
    from .test_narratives import _campaign, _source, _activity

    opponent = _campaign(db)
    old = _source(
        db,
        "Jordan Lee first attacks Rivera on tenants",
        cluster="rent-1",
        source_type="opponent_statement",
        days=10,
    )
    recent_news = _source(
        db,
        "Local outlet repeats tenant attack",
        cluster="rent-2",
        source_type="news",
        days=1,
    )
    recent_social = _source(
        db,
        "Community post repeats tenant attack",
        cluster="rent-3",
        source_type="social",
        days=1,
    )
    for source in [old, recent_news, recent_social]:
        _activity(db, opponent, source, "Jordan Lee says Alex Rivera failed tenants on housing.")

    card = get_narrative_briefs(limit=5, db=db)[0]

    assert "new source cluster" in card.what_changed
    assert "outside owned channels" in card.what_changed
    assert "New messenger" in card.what_changed
    assert "Traction has increased recently compared to prior baseline" not in card.what_changed
