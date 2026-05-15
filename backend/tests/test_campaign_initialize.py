"""Tests for POST /campaign/initialize endpoint and initialize_campaign service."""

import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import CampaignConfig, Opponent, SourceMonitor


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def _campaign(db, **kwargs):
    defaults = {
        "candidate_name": "Maria Alvarez",
        "office": "City Council",
        "district": "District 7",
        "location": "Riverton",
        "key_priorities": json.dumps(["Housing & Affordability", "Economy & Jobs"]),
    }
    defaults.update(kwargs)
    c = CampaignConfig(**defaults)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


# ── Service-level tests ───────────────────────────────────────────────────────

class TestInitializeCampaignService:
    def test_returns_four_steps(self, db, monkeypatch):
        from app.services.campaign_setup import initialize_campaign
        monkeypatch.setenv("SEARCH_PROVIDER", "mock")
        _campaign(db)

        result = initialize_campaign(db)

        assert len(result["steps"]) == 4
        assert [s["step"] for s in result["steps"]] == [1, 2, 3, 4]

    def test_all_steps_have_required_keys(self, db, monkeypatch):
        from app.services.campaign_setup import initialize_campaign
        monkeypatch.setenv("SEARCH_PROVIDER", "mock")
        _campaign(db)

        result = initialize_campaign(db)

        for step in result["steps"]:
            assert "step" in step
            assert "label" in step
            assert "status" in step
            assert "detail" in step
            assert step["status"] in ("ok", "skipped", "error")

    def test_monitors_created_on_first_run(self, db, monkeypatch):
        from app.services.campaign_setup import initialize_campaign
        monkeypatch.setenv("SEARCH_PROVIDER", "mock")
        _campaign(db)

        result = initialize_campaign(db)

        assert result["monitors_created"] > 0
        assert db.query(SourceMonitor).count() == result["monitors_created"]

    def test_idempotent_second_call_skips_duplicates(self, db, monkeypatch):
        from app.services.campaign_setup import initialize_campaign
        monkeypatch.setenv("SEARCH_PROVIDER", "mock")
        _campaign(db)

        first = initialize_campaign(db)
        second = initialize_campaign(db)

        assert first["monitors_created"] > 0
        assert second["monitors_created"] == 0
        assert second["monitors_skipped"] == first["monitors_created"]

    def test_returns_message(self, db, monkeypatch):
        from app.services.campaign_setup import initialize_campaign
        monkeypatch.setenv("SEARCH_PROVIDER", "mock")
        _campaign(db)

        result = initialize_campaign(db)

        assert isinstance(result["message"], str)
        assert len(result["message"]) > 0

    def test_returns_initialized_at_datetime(self, db, monkeypatch):
        from app.services.campaign_setup import initialize_campaign
        monkeypatch.setenv("SEARCH_PROVIDER", "mock")
        _campaign(db)

        result = initialize_campaign(db)

        assert isinstance(result["initialized_at"], datetime)

    def test_no_campaign_returns_error_step(self, db):
        from app.services.campaign_setup import initialize_campaign

        result = initialize_campaign(db)

        assert result["steps"][0]["status"] == "error"
        assert result["monitors_created"] == 0

    def test_narrative_refresh_runs(self, db, monkeypatch):
        from app.services.campaign_setup import initialize_campaign
        monkeypatch.setenv("SEARCH_PROVIDER", "mock")
        _campaign(db)

        result = initialize_campaign(db)

        step4 = result["steps"][3]
        assert step4["label"] == "Narrative refresh"
        assert step4["status"] in ("ok", "error")

    def test_step3_skipped_when_step2_errors(self, db, monkeypatch):
        from app.services.campaign_setup import initialize_campaign

        def boom(_db):
            raise RuntimeError("monitor setup failed")

        monkeypatch.setenv("SEARCH_PROVIDER", "mock")
        _campaign(db)
        monkeypatch.setattr("app.services.monitors.auto_setup_monitors", boom)

        result = initialize_campaign(db)
        step3 = result["steps"][2]
        assert step3["label"] == "Ingest coverage"
        assert step3["status"] == "skipped"

    def test_step3_skipped_when_no_search_monitor_ingestion_attempted(self, db, monkeypatch):
        from app.services.campaign_setup import initialize_campaign

        monkeypatch.setenv("SEARCH_PROVIDER", "mock")
        _campaign(db)
        monkeypatch.setattr("app.services.monitors.auto_setup_monitors", lambda db: {
            "generated": 0,
            "skipped": 5,
            "search_monitors_ingested": 0,
            "sources_ingested": 0,
            "ingested": 0,
        })

        result = initialize_campaign(db)
        step3 = result["steps"][2]
        assert step3["label"] == "Ingest coverage"
        assert step3["status"] == "skipped"

    def test_with_opponents_generates_opponent_monitors(self, db, monkeypatch):
        from app.services.campaign_setup import initialize_campaign
        monkeypatch.setenv("SEARCH_PROVIDER", "mock")
        _campaign(db)
        db.add(Opponent(name="Roy Harmon"))
        db.commit()

        result = initialize_campaign(db)

        names = [m.name for m in db.query(SourceMonitor).all()]
        assert any("Roy Harmon" in n for n in names)
        assert result["monitors_created"] > 0


# ── Endpoint-level tests ──────────────────────────────────────────────────────

class TestInitializeEndpoint:
    def test_endpoint_returns_200_with_campaign(self, db, monkeypatch):
        from app.routes.campaign import campaign_initialize
        monkeypatch.setenv("SEARCH_PROVIDER", "mock")
        _campaign(db)

        result = campaign_initialize(db=db)

        assert result.monitors_created >= 0
        assert len(result.steps) == 4

    def test_endpoint_step_statuses_are_valid(self, db, monkeypatch):
        from app.routes.campaign import campaign_initialize
        monkeypatch.setenv("SEARCH_PROVIDER", "mock")
        _campaign(db)

        result = campaign_initialize(db=db)

        for step in result.steps:
            assert step.status in ("ok", "skipped", "error")

    def test_endpoint_without_campaign_returns_error_step(self, db):
        from app.routes.campaign import campaign_initialize

        result = campaign_initialize(db=db)

        assert result.steps[0].status == "error"
        assert result.monitors_created == 0

    def test_endpoint_response_has_initialized_at(self, db, monkeypatch):
        from app.routes.campaign import campaign_initialize
        monkeypatch.setenv("SEARCH_PROVIDER", "mock")
        _campaign(db)

        result = campaign_initialize(db=db)

        assert isinstance(result.initialized_at, datetime)

    def test_endpoint_is_idempotent(self, db, monkeypatch):
        from app.routes.campaign import campaign_initialize
        monkeypatch.setenv("SEARCH_PROVIDER", "mock")
        _campaign(db)

        first = campaign_initialize(db=db)
        second = campaign_initialize(db=db)

        assert first.monitors_created > 0
        assert second.monitors_created == 0
        assert second.monitors_skipped == first.monitors_created

    def test_endpoint_ingestion_step_present(self, db, monkeypatch):
        from app.routes.campaign import campaign_initialize
        monkeypatch.setenv("SEARCH_PROVIDER", "mock")
        _campaign(db)

        result = campaign_initialize(db=db)

        labels = [s.label for s in result.steps]
        assert "Ingest coverage" in labels

    def test_endpoint_narrative_step_present(self, db, monkeypatch):
        from app.routes.campaign import campaign_initialize
        monkeypatch.setenv("SEARCH_PROVIDER", "mock")
        _campaign(db)

        result = campaign_initialize(db=db)

        labels = [s.label for s in result.steps]
        assert "Narrative refresh" in labels
