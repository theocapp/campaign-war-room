from datetime import datetime, timedelta
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.routes.dashboard import get_dashboard


@pytest.fixture
def db():
    from app.db import Base
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def test_dashboard_narrative_brief_has_brief_fields(db):
    # setup campaign and opponent using helpers from existing test file
    from .test_narratives import _campaign, _source, _activity

    opponent = _campaign(db)
    s1 = _source(db, "Jordan Lee attacks Rivera on tenants", cluster="rent-1")
    s2 = _source(db, "Repeat: Jordan Lee attacks Rivera on tenants again", cluster="rent-2", days=1)
    _activity(db, opponent, s1, "Jordan Lee says Alex Rivera failed tenants on housing.")
    _activity(db, opponent, s2, "Jordan Lee repeats accusation that Alex Rivera failed tenants on housing.")

    dashboard = get_dashboard(db=db)
    assert dashboard.narrative_briefing
    card = dashboard.narrative_briefing[0]
    # new narrative-brief fields should be present
    assert hasattr(card, 'what_changed')
    assert hasattr(card, 'action')
    assert hasattr(card, 'top_supporting_sources')
    assert isinstance(card.top_supporting_sources, list)
    # timeline/change-detection fields
    assert hasattr(card, 'change_summary')
    assert hasattr(card, 'new_messenger_types')
    assert hasattr(card, 'new_source_clusters_count')
    assert hasattr(card, 'escaped_owned_recently')
    assert hasattr(card, 'momentum_shift')
    # conservative expectations about content
    assert card.what_changed is not None
    assert card.action in {"respond", "monitor", "ignore", "amplify"} or isinstance(card.action, str)
