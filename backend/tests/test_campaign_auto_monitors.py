import json
import logging

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import CampaignConfig, SourceMonitor


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def _profile(**kwargs):
    from app.schemas import CampaignProfileIn
    defaults = dict(
        candidate_name="Maria Alvarez",
        office="City Council",
        district="District 7",
        location="Riverton",
        key_priorities=["Housing & Affordability", "Economy & Jobs"],
    )
    defaults.update(kwargs)
    return CampaignProfileIn(**defaults)


def test_update_campaign_auto_generates_monitors(db, monkeypatch):
    from app.routes.campaign import update_campaign
    monkeypatch.setenv("SEARCH_PROVIDER", "mock")

    update_campaign(_profile(), db=db)

    assert db.query(SourceMonitor).count() > 0


def test_update_campaign_monitors_are_idempotent(db, monkeypatch):
    from app.routes.campaign import update_campaign
    monkeypatch.setenv("SEARCH_PROVIDER", "mock")

    body = _profile()
    update_campaign(body, db=db)
    first_count = db.query(SourceMonitor).count()

    update_campaign(body, db=db)
    second_count = db.query(SourceMonitor).count()

    assert first_count > 0
    assert second_count == first_count


def test_auto_setup_monitors_returns_generated_count(db, monkeypatch):
    from app.services.monitors import auto_setup_monitors
    monkeypatch.setenv("SEARCH_PROVIDER", "mock")

    campaign = CampaignConfig(
        candidate_name="Maria Alvarez",
        office="City Council",
        district="District 7",
        location="Riverton",
        key_priorities=json.dumps(["Housing & Affordability"]),
    )
    db.add(campaign)
    db.commit()

    result = auto_setup_monitors(db)

    assert result["generated"] > 0
    assert result["skipped"] == 0
    assert result["search_monitors_ingested"] >= 0
    assert result["sources_ingested"] >= 0
    assert result["ingested"] == result["sources_ingested"]


def test_auto_setup_monitors_skips_duplicates_on_second_call(db, monkeypatch):
    from app.services.monitors import auto_setup_monitors
    monkeypatch.setenv("SEARCH_PROVIDER", "mock")

    campaign = CampaignConfig(
        candidate_name="Maria Alvarez",
        office="City Council",
        district="District 7",
        location="Riverton",
    )
    db.add(campaign)
    db.commit()

    first = auto_setup_monitors(db)
    second = auto_setup_monitors(db)

    assert first["generated"] > 0
    assert second["generated"] == 0
    assert second["skipped"] == first["generated"]


def test_auto_setup_monitors_ingests_new_search_monitors(db, monkeypatch):
    from app.services import ingestion
    from app.services.monitors import auto_setup_monitors
    from app.services.search_provider import SearchResponse, SearchResult

    campaign = CampaignConfig(
        candidate_name="Maria Alvarez",
        office="City Council",
        district="District 7",
        location="Riverton",
        key_priorities=json.dumps(["Housing & Affordability"]),
    )
    db.add(campaign)
    db.commit()

    class Provider:
        def search(self, query, limit=10):
            return SearchResponse(
                provider="test",
                results=[SearchResult(title="Candidate story", url="https://example.com/story1")],
            )

    def fake_ingest_url(db_arg, url, source_type):
        return ingestion.ingest_text(
            db_arg,
            "Maria Alvarez District 7 housing plan",
            "Maria Alvarez met voters in District 7 about housing.",
            "Example",
            source_type,
            source_url=url,
        )

    monkeypatch.setattr("app.services.monitors.get_search_provider", lambda: Provider())
    monkeypatch.setattr("app.services.monitors.ingestion.ingest_url", fake_ingest_url)

    result = auto_setup_monitors(db)
    assert result["generated"] > 0
    assert result["search_monitors_ingested"] > 0
    assert result["sources_ingested"] > 0
    assert result["ingested"] == result["sources_ingested"]


def test_auto_setup_monitors_no_campaign_returns_zeros(db):
    from app.services.monitors import auto_setup_monitors

    result = auto_setup_monitors(db)

    assert result == {
        "generated": 0,
        "skipped": 0,
        "search_monitors_ingested": 0,
        "sources_ingested": 0,
        "ingested": 0,
    }


def test_update_campaign_succeeds_even_if_monitor_setup_fails(db, monkeypatch):
    from app.routes.campaign import update_campaign

    def boom(db):
        raise RuntimeError("monitor setup exploded")

    monkeypatch.setattr("app.routes.campaign.auto_setup_monitors", boom)

    result = update_campaign(_profile(), db=db)
    assert result.candidate_name == "Maria Alvarez"


def test_update_campaign_logs_warning_if_monitor_setup_fails(db, monkeypatch, caplog):
    from app.routes.campaign import update_campaign

    def boom(db):
        raise RuntimeError("monitor setup exploded")

    monkeypatch.setattr("app.routes.campaign.auto_setup_monitors", boom)
    caplog.set_level(logging.WARNING, logger="app.routes.campaign")

    update_campaign(_profile(), db=db)

    assert "auto_setup_monitors failed during campaign update" in caplog.text


def test_update_campaign_marks_auto_inferred_election_date(db, monkeypatch):
    from app.routes.campaign import update_campaign
    monkeypatch.setenv("SEARCH_PROVIDER", "mock")

    result = update_campaign(_profile(location="Riverton, CA", election_type="general"), db=db)

    assert result.election_date is not None
    assert result.election_date_inferred is True


def test_update_campaign_marks_user_set_election_date_as_not_inferred(db, monkeypatch):
    from app.routes.campaign import update_campaign
    from datetime import datetime

    monkeypatch.setenv("SEARCH_PROVIDER", "mock")
    manual_date = datetime(2026, 10, 15)

    result = update_campaign(
        _profile(location="Riverton, CA", election_type="general", election_date=manual_date),
        db=db,
    )

    assert result.election_date == manual_date
    assert result.election_date_inferred is False
