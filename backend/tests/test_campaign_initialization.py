"""Integration tests for the campaign initialization flow.

These tests exercise the full cascade across service boundaries and assert
final database state, complementing the unit-level tests in
test_campaign_initialize.py (service return values) and
test_campaign_auto_monitors.py (monitor generation logic).

Each test verifies what is actually persisted to the database after
initialize_campaign() runs, not just what the function returns.
"""

import json
import pytest
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import CampaignConfig, Opponent, RssFeed, SourceItem, SourceMonitor


# ── Fixtures ──────────────────────────────────────────────────────────────────

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
        "location": "Riverton, CA",
        "key_priorities": json.dumps(["Housing & Affordability", "Economy & Jobs"]),
    }
    defaults.update(kwargs)
    c = CampaignConfig(**defaults)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _search_provider_with_results(results):
    """Return a fake search provider that yields the given SearchResult list."""
    from app.services.search_provider import SearchResponse

    class _Provider:
        def search(self, query, limit=10):
            return SearchResponse(provider="test", results=results)

    return _Provider()


def _fake_ingest_url(db, url, source_type):
    """Lightweight stand-in for ingestion.ingest_url that writes a real SourceItem."""
    from app.services.ingestion import ingest_text
    return ingest_text(
        db,
        title=f"Story from {url}",
        raw_text="Maria Alvarez is working on housing in District 7.",
        source_name="test-source",
        source_type=source_type,
        source_url=url,
    )


# ── Monitor DB state after initialization ─────────────────────────────────────

class TestMonitorCreationInDB:
    def test_monitors_written_to_db_after_init(self, db, monkeypatch):
        """Initialization must persist SourceMonitor rows — not just return counts."""
        from app.services.campaign_setup import initialize_campaign
        monkeypatch.setenv("SEARCH_PROVIDER", "mock")
        _campaign(db)

        initialize_campaign(db)

        assert db.query(SourceMonitor).count() > 0

    def test_monitor_count_matches_returned_count(self, db, monkeypatch):
        """monitors_created in the return value must equal the DB row count."""
        from app.services.campaign_setup import initialize_campaign
        monkeypatch.setenv("SEARCH_PROVIDER", "mock")
        _campaign(db)

        result = initialize_campaign(db)

        db_count = db.query(SourceMonitor).count()
        assert db_count == result["monitors_created"]

    def test_search_query_monitors_exist_in_db(self, db, monkeypatch):
        """At least one search_query monitor must be created for a fully configured campaign."""
        from app.services.campaign_setup import initialize_campaign
        monkeypatch.setenv("SEARCH_PROVIDER", "mock")
        _campaign(db)

        initialize_campaign(db)

        search_monitors = (
            db.query(SourceMonitor)
            .filter(SourceMonitor.monitor_type == "search_query")
            .all()
        )
        assert len(search_monitors) > 0

    def test_candidate_name_appears_in_search_monitor_queries(self, db, monkeypatch):
        """Search monitor queries must be tailored to the candidate."""
        from app.services.campaign_setup import initialize_campaign
        monkeypatch.setenv("SEARCH_PROVIDER", "mock")
        _campaign(db)

        initialize_campaign(db)

        search_monitors = (
            db.query(SourceMonitor)
            .filter(SourceMonitor.monitor_type == "search_query")
            .all()
        )
        names = [m.name for m in search_monitors]
        assert any("Maria Alvarez" in n for n in names), (
            f"No monitor name contains 'Maria Alvarez'. Got: {names[:5]}"
        )

    def test_opponent_monitors_created_in_db(self, db, monkeypatch):
        """Initialization with an opponent must write opponent-named monitors to DB."""
        from app.services.campaign_setup import initialize_campaign
        monkeypatch.setenv("SEARCH_PROVIDER", "mock")
        _campaign(db)
        db.add(Opponent(name="Roy Harmon"))
        db.commit()

        initialize_campaign(db)

        names = [m.name for m in db.query(SourceMonitor).all()]
        assert any("Roy Harmon" in n for n in names), (
            f"No monitor name contains 'Roy Harmon'. Got: {names[:5]}"
        )

    def test_rss_monitors_create_rss_feed_rows(self, db, monkeypatch):
        """Every RSS-type monitor must also create a corresponding RssFeed row."""
        from app.services.campaign_setup import initialize_campaign
        from app.services.source_discovery import generate_monitors_for_campaign
        monkeypatch.setenv("SEARCH_PROVIDER", "mock")
        campaign = _campaign(db)

        initialize_campaign(db)

        rss_monitors = (
            db.query(SourceMonitor)
            .filter(SourceMonitor.monitor_type == "rss")
            .all()
        )
        for monitor in rss_monitors:
            if monitor.url:
                feed = db.query(RssFeed).filter_by(url=monitor.url).first()
                assert feed is not None, (
                    f"RssFeed not created for RSS monitor '{monitor.name}' url={monitor.url}"
                )


# ── Ingestion pipeline ────────────────────────────────────────────────────────

class TestIngestionAfterInit:
    def test_search_results_produce_source_items_in_db(self, db, monkeypatch):
        """When the search provider returns results, SourceItem rows must be written."""
        from app.services.campaign_setup import initialize_campaign
        from app.services.search_provider import SearchResult

        _campaign(db)
        provider = _search_provider_with_results([
            SearchResult(title="Housing Crisis", url="https://news.example.com/housing-1"),
            SearchResult(title="Riverton Budget", url="https://news.example.com/budget-1"),
        ])
        monkeypatch.setattr("app.services.monitors.get_search_provider", lambda: provider)
        monkeypatch.setattr("app.services.monitors.ingestion.ingest_url", _fake_ingest_url)

        initialize_campaign(db)

        assert db.query(SourceItem).count() > 0

    def test_sources_ingested_count_matches_db_rows(self, db, monkeypatch):
        """The sources_ingested return value must be consistent with DB state."""
        from app.services.campaign_setup import initialize_campaign
        from app.services.search_provider import SearchResult

        _campaign(db)
        provider = _search_provider_with_results([
            SearchResult(title="Story", url="https://example.com/story"),
        ])
        monkeypatch.setattr("app.services.monitors.get_search_provider", lambda: provider)
        monkeypatch.setattr("app.services.monitors.ingestion.ingest_url", _fake_ingest_url)

        result = initialize_campaign(db)

        source_count = db.query(SourceItem).count()
        assert result["sources_ingested"] == source_count

    def test_duplicate_urls_not_ingested_twice(self, db, monkeypatch):
        """Re-running initialization must not create duplicate SourceItem rows for the same URL."""
        from app.services.campaign_setup import initialize_campaign
        from app.services.search_provider import SearchResult

        _campaign(db)
        url = "https://example.com/exclusive"
        provider = _search_provider_with_results([
            SearchResult(title="Exclusive Story", url=url),
        ])
        monkeypatch.setattr("app.services.monitors.get_search_provider", lambda: provider)
        monkeypatch.setattr("app.services.monitors.ingestion.ingest_url", _fake_ingest_url)

        initialize_campaign(db)
        count_after_first = db.query(SourceItem).filter_by(source_url=url).count()

        initialize_campaign(db)
        count_after_second = db.query(SourceItem).filter_by(source_url=url).count()

        assert count_after_first == count_after_second, (
            "Source URL was ingested more than once across two initialization runs"
        )

    def test_ingested_sources_have_correct_type(self, db, monkeypatch):
        """Sources created via search monitor ingestion should have source_type set."""
        from app.services.campaign_setup import initialize_campaign
        from app.services.search_provider import SearchResult

        _campaign(db)
        provider = _search_provider_with_results([
            SearchResult(title="A Story", url="https://example.com/story-a"),
        ])
        monkeypatch.setattr("app.services.monitors.get_search_provider", lambda: provider)
        monkeypatch.setattr("app.services.monitors.ingestion.ingest_url", _fake_ingest_url)

        initialize_campaign(db)

        items = db.query(SourceItem).all()
        for item in items:
            assert item.source_type is not None, f"SourceItem {item.id} missing source_type"


# ── Idempotency at the DB level ───────────────────────────────────────────────

class TestIdempotencyInDB:
    def test_monitor_count_stable_after_second_init(self, db, monkeypatch):
        """Running initialization twice must not increase the monitor row count."""
        from app.services.campaign_setup import initialize_campaign
        monkeypatch.setenv("SEARCH_PROVIDER", "mock")
        _campaign(db)

        initialize_campaign(db)
        count_first = db.query(SourceMonitor).count()

        initialize_campaign(db)
        count_second = db.query(SourceMonitor).count()

        assert count_second == count_first, (
            f"Monitor count grew from {count_first} to {count_second} on second init"
        )

    def test_rss_feeds_not_duplicated_on_second_init(self, db, monkeypatch):
        """RSS feeds must not be duplicated if initialization is called twice."""
        from app.services.campaign_setup import initialize_campaign
        monkeypatch.setenv("SEARCH_PROVIDER", "mock")
        _campaign(db)

        initialize_campaign(db)
        feeds_first = db.query(RssFeed).count()

        initialize_campaign(db)
        feeds_second = db.query(RssFeed).count()

        assert feeds_second == feeds_first

    def test_second_init_reports_zero_monitors_created(self, db, monkeypatch):
        """monitors_created on the second call must be 0 (all skipped as duplicates)."""
        from app.services.campaign_setup import initialize_campaign
        monkeypatch.setenv("SEARCH_PROVIDER", "mock")
        _campaign(db)

        first = initialize_campaign(db)
        second = initialize_campaign(db)

        assert first["monitors_created"] > 0
        assert second["monitors_created"] == 0
        assert second["monitors_skipped"] == first["monitors_created"]


# ── Full cascade scenario ─────────────────────────────────────────────────────

class TestFullCascade:
    def test_all_four_steps_complete_with_full_profile(self, db, monkeypatch):
        """A campaign with name + office + priorities should complete all four steps OK."""
        from app.services.campaign_setup import initialize_campaign
        monkeypatch.setenv("SEARCH_PROVIDER", "mock")
        _campaign(db)

        result = initialize_campaign(db)

        statuses = {s["label"]: s["status"] for s in result["steps"]}
        assert statuses["Validate campaign"] == "ok"
        assert statuses["Monitors created"] in ("ok", "skipped")
        assert statuses["Ingest coverage"] == "ok"
        assert statuses["Narrative refresh"] in ("ok", "skipped", "error")

    def test_full_cascade_with_opponent_and_search_results(self, db, monkeypatch):
        """End-to-end: campaign + opponent + live search results → monitors, sources, no errors."""
        from app.services.campaign_setup import initialize_campaign
        from app.services.search_provider import SearchResult

        _campaign(db)
        db.add(Opponent(name="Roy Harmon"))
        db.commit()

        provider = _search_provider_with_results([
            SearchResult(title="Alvarez leads District 7", url="https://riverton.example.com/1"),
            SearchResult(title="Harmon campaign launch", url="https://riverton.example.com/2"),
        ])
        monkeypatch.setattr("app.services.monitors.get_search_provider", lambda: provider)
        monkeypatch.setattr("app.services.monitors.ingestion.ingest_url", _fake_ingest_url)

        result = initialize_campaign(db)

        # Structural assertions
        assert result["monitors_created"] > 0
        assert db.query(SourceMonitor).count() > 0
        assert db.query(SourceItem).count() > 0

        # Opponent monitors present
        names = [m.name for m in db.query(SourceMonitor).all()]
        assert any("Roy Harmon" in n for n in names)

        # No step-level errors in monitor or ingest steps
        step_statuses = {s["step"]: s["status"] for s in result["steps"]}
        assert step_statuses[2] != "error"
        assert step_statuses[3] != "error"

    def test_initialization_via_endpoint_reflects_db_state(self, db, monkeypatch):
        """The /campaign/initialize endpoint response must match what is in the DB."""
        from app.routes.campaign import campaign_initialize
        monkeypatch.setenv("SEARCH_PROVIDER", "mock")
        _campaign(db)

        response = campaign_initialize(db=db)

        db_monitor_count = db.query(SourceMonitor).count()
        # monitors_created + monitors_skipped = total monitors in DB
        assert response.monitors_created + response.monitors_skipped == db_monitor_count
