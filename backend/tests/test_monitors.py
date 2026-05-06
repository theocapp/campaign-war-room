import json
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import CampaignConfig, Opponent, SourceItem, SourceMonitor


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


def test_monitor_generation_from_campaign_profile(db):
    from app.services.source_discovery import generate_monitors_for_campaign
    campaign = _campaign(db)
    monitors = generate_monitors_for_campaign(campaign, [])
    names = [m["name"] for m in monitors]
    assert any("Maria Alvarez news search" in n for n in names)
    assert any("District 7 election" in n for n in names)


def test_opponent_monitor_generation(db):
    from app.services.source_discovery import generate_monitors_for_campaign
    campaign = _campaign(db)
    opp = Opponent(name="Roy Harmon")
    monitors = generate_monitors_for_campaign(campaign, [opp])
    assert any(m["category"] == "opponent" and "Roy Harmon" in m["name"] for m in monitors)
    assert any("Maria Alvarez vs Roy Harmon" == m["name"] for m in monitors)


def test_issue_monitor_generation_from_priorities(db):
    from app.services.source_discovery import generate_monitors_for_campaign
    campaign = _campaign(db)
    monitors = generate_monitors_for_campaign(campaign, [])
    assert any(m["category"] == "issue" and "Housing" in m["name"] for m in monitors)
    assert any(m["category"] == "local_government" and "Economic" in m["name"] for m in monitors)


def test_monitor_generation_differs_for_local_primary_race(db):
    from app.services.source_discovery import generate_monitors_for_campaign
    federal = _campaign(db, race_level="federal", election_type="general")
    federal_monitors = generate_monitors_for_campaign(federal, [])
    local = _campaign(
        db,
        candidate_name="Alex Rivera",
        office="Assembly",
        district="Queens Assembly District 37",
        district_number="AD 37",
        location="Queens",
        race_level="state",
        election_type="primary",
        sparse_race_mode=True,
        neighborhood_keywords=json.dumps(["Sunnyside", "Woodside"]),
    )
    local_monitors = generate_monitors_for_campaign(local, [])
    assert len(local_monitors) > len(federal_monitors)
    assert any("endorsement" in m["category"] or m["category"] == "endorsement_or_election_board" for m in local_monitors)
    assert any("Instagram" in m["name"] for m in local_monitors)


def test_small_race_queries_include_geography_and_district_context(db):
    from app.services.source_discovery import generate_monitors_for_campaign
    campaign = _campaign(
        db,
        candidate_name="Alex Rivera",
        office="Assembly",
        district="Queens Assembly District 37",
        district_number="AD 37",
        location="Queens",
        race_level="state",
        election_type="primary",
        sparse_race_mode=True,
        neighborhood_keywords=json.dumps(["Sunnyside", "Woodside"]),
    )
    monitors = generate_monitors_for_campaign(campaign, [Opponent(name="Jordan Lee")])
    queries = [m["query"] for m in monitors if m["monitor_type"] == "search_query"]
    assert any('"Alex Rivera" "Assembly"' in q for q in queries)
    assert any('"AD 37" "Assembly" primary' in q for q in queries)
    assert any('"Sunnyside" "Alex Rivera"' in q for q in queries)
    assert any('"Jordan Lee" "Assembly"' in q for q in queries)


def test_no_duplicate_monitors(db):
    from app.routes.monitors import generate_monitors
    from app.schemas import GenerateMonitorsRequest
    _campaign(db)
    first = generate_monitors(GenerateMonitorsRequest(apply=True), db=db)
    second = generate_monitors(GenerateMonitorsRequest(apply=True), db=db)
    assert first.created_count > 0
    assert second.created_count == 0
    assert second.skipped_duplicates == len(second.suggestions)


def test_apply_false_does_not_write_to_db(db):
    from app.routes.monitors import generate_monitors
    from app.schemas import GenerateMonitorsRequest
    _campaign(db)
    result = generate_monitors(GenerateMonitorsRequest(apply=False), db=db)
    assert result.suggestions
    assert db.query(SourceMonitor).count() == 0


def test_apply_true_writes_monitors(db):
    from app.routes.monitors import generate_monitors
    from app.schemas import GenerateMonitorsRequest
    _campaign(db)
    result = generate_monitors(GenerateMonitorsRequest(apply=True), db=db)
    assert result.created_count > 0
    assert db.query(SourceMonitor).count() == result.created_count


def test_replace_existing_clears_and_replaces_safely(db):
    from app.routes.monitors import generate_monitors
    from app.schemas import GenerateMonitorsRequest
    _campaign(db)
    db.add(SourceMonitor(name="Old", monitor_type="manual"))
    db.commit()
    result = generate_monitors(GenerateMonitorsRequest(apply=True, replace_existing=True), db=db)
    assert result.created_count > 0
    assert db.query(SourceMonitor).filter_by(name="Old").first() is None


def test_custom_excluded_keywords_affect_race_relevance(db):
    from app.services.race_relevance import apply_relevance
    _campaign(db, excluded_keywords=json.dumps(["festival"]))
    item = SourceItem(
        title="Neighborhood festival lineup announced",
        raw_text="A music festival is planned this weekend.",
        source_name="Local",
        source_type="news",
        published_at=datetime.utcnow(),
    )
    apply_relevance(db, item)
    assert item.archived_as_irrelevant is True
    assert item.race_relevance_score == 0


def test_geography_keywords_affect_race_relevance(db):
    from app.services.race_relevance import apply_relevance
    _campaign(db, geography_keywords=json.dumps(["Lackawanna County"]))
    item = SourceItem(
        title="Healthcare access expands in Lackawanna County",
        raw_text="Residents discussed clinic access.",
        source_name="Local",
        source_type="news",
        published_at=datetime.utcnow(),
    )
    apply_relevance(db, item)
    assert item.geo_relevance == "local"
    assert item.race_relevance_score >= 15


def test_sports_story_still_archived_unless_race_connection_present(db):
    from app.services.race_relevance import apply_relevance
    _campaign(db)
    item = SourceItem(
        title="Phillies game preview",
        raw_text="The coach discussed the playoffs.",
        source_name="Sports",
        source_type="news",
        published_at=datetime.utcnow(),
    )
    apply_relevance(db, item)
    assert item.archived_as_irrelevant is True

    connected = SourceItem(
        title="Maria Alvarez attends Phillies community event",
        raw_text="Maria Alvarez spoke with District 7 voters at the game.",
        source_name="Local",
        source_type="news",
        published_at=datetime.utcnow(),
    )
    apply_relevance(db, connected)
    assert connected.archived_as_irrelevant is False


def test_mock_search_provider_fallback_behavior(monkeypatch):
    from app.services.search_provider import MockSearchProvider, get_search_provider
    monkeypatch.setenv("SEARCH_PROVIDER", "mock")
    provider = get_search_provider()
    response = provider.search('"Maria Alvarez"', limit=5)
    assert isinstance(provider, MockSearchProvider)
    assert response.provider == "mock"
    assert response.results == []
    assert "No live web search" in response.message


def test_tavily_provider_falls_back_when_key_missing(monkeypatch):
    from app.services.search_provider import MockSearchProvider, get_search_provider
    monkeypatch.setenv("SEARCH_PROVIDER", "tavily")
    monkeypatch.setenv("TAVILY_API_KEY", "")
    assert isinstance(get_search_provider(), MockSearchProvider)


def test_search_provider_loads_backend_and_project_env(monkeypatch):
    from app.services import search_provider
    loaded = []
    monkeypatch.setattr(search_provider, "load_dotenv", lambda path, override=False: loaded.append((path.name, override)))
    search_provider._load_search_env()
    assert (".env", False) in loaded
    assert len(loaded) == 2


def test_tavily_provider_selection(monkeypatch):
    from app.services.search_provider import TavilySearchProvider, get_search_provider
    monkeypatch.setenv("SEARCH_PROVIDER", "tavily")
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    assert isinstance(get_search_provider(), TavilySearchProvider)


def test_unsupported_provider_falls_back_to_mock(monkeypatch):
    from app.services.search_provider import MockSearchProvider, get_search_provider
    monkeypatch.setenv("SEARCH_PROVIDER", "unsupported")
    assert isinstance(get_search_provider(), MockSearchProvider)


def test_tavily_provider_response_normalization(monkeypatch):
    from app.services.search_provider import TavilySearchProvider

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {
                        "title": "Maria Alvarez housing plan",
                        "url": "https://news.example/story",
                        "content": "District 7 housing story",
                        "published_date": "2026-04-20",
                    }
                ]
            }

    calls = []

    def fake_post(*args, **kwargs):
        calls.append(kwargs)
        return Response()

    monkeypatch.setattr("app.services.search_provider.httpx.post", fake_post)
    result = TavilySearchProvider("key").search('"Maria Alvarez"', limit=5)
    assert result.provider == "tavily"
    assert result.results[0].title == "Maria Alvarez housing plan"
    assert result.results[0].url == "https://news.example/story"
    assert result.results[0].source_name == "news.example"
    assert result.results[0].snippet == "District 7 housing story"
    assert result.results[0].published_at is not None
    assert calls[0]["headers"]["Authorization"] == "Bearer key"
    assert calls[0]["json"]["query"] == '"Maria Alvarez"'


def test_tavily_provider_failure_returns_message(monkeypatch):
    from app.services.search_provider import TavilySearchProvider
    monkeypatch.setattr("app.services.search_provider.httpx.post", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("rate limited")))
    result = TavilySearchProvider("key").search('"Maria Alvarez"', limit=5)
    assert result.provider == "tavily"
    assert result.results == []
    assert "rate limited" in result.message


def test_search_query_monitor_ingest_path(db, monkeypatch):
    from app.routes.monitors import ingest_monitor
    from app.services import ingestion
    from app.services.search_provider import SearchResponse, SearchResult
    _campaign(db)
    monitor = SourceMonitor(name="Candidate search", monitor_type="search_query", query='"Maria Alvarez"', source_type="news")
    db.add(monitor)
    db.commit()
    db.refresh(monitor)

    class Provider:
        def search(self, query, limit=10):
            return SearchResponse(
                provider="test",
                results=[SearchResult(title="Candidate story", url="https://example.com/candidate")],
            )

    def fake_ingest_url(db_arg, url, source_type):
        return ingestion.ingest_text(
            db_arg,
            "Maria Alvarez releases housing plan",
            "Maria Alvarez told District 7 voters about housing.",
            "Example",
            source_type,
            source_url=url,
        )

    monkeypatch.setattr("app.routes.monitors.get_search_provider", lambda: Provider())
    monkeypatch.setattr("app.routes.monitors.ingestion.ingest_url", fake_ingest_url)
    result = ingest_monitor(monitor.id, db=db)
    assert result.added_count == 1
    assert result.results[0].relevance_score >= 40


def test_search_query_monitor_dedups_by_url(db, monkeypatch):
    from app.routes.monitors import ingest_monitor
    from app.services.search_provider import SearchResponse, SearchResult
    _campaign(db)
    existing = SourceItem(
        title="Existing",
        raw_text="Maria Alvarez existing story",
        source_name="Example",
        source_type="news",
        source_url="https://example.com/dupe",
        published_at=datetime.utcnow(),
        race_relevance_score=60,
        race_relevance_label="high",
    )
    monitor = SourceMonitor(name="Candidate search", monitor_type="search_query", query='"Maria Alvarez"', source_type="news")
    db.add_all([existing, monitor])
    db.commit()
    db.refresh(monitor)

    class Provider:
        def search(self, query, limit=10):
            return SearchResponse(provider="test", results=[SearchResult(title="Dupe", url="https://example.com/dupe")])

    monkeypatch.setattr("app.routes.monitors.get_search_provider", lambda: Provider())
    result = ingest_monitor(monitor.id, db=db)
    assert result.added_count == 0
    assert result.skipped_count == 1
    assert result.results[0].status == "skipped"


def test_search_query_monitor_soft_dedups_by_title_and_source(db, monkeypatch):
    from app.routes.monitors import ingest_monitor
    from app.services.search_provider import SearchResponse, SearchResult
    _campaign(db)
    existing = SourceItem(
        title="Maria Alvarez housing plan",
        raw_text="Existing",
        source_name="Example News",
        source_type="news",
        source_url="https://example.com/old",
        published_at=datetime.utcnow(),
        race_relevance_score=60,
        race_relevance_label="high",
    )
    monitor = SourceMonitor(name="Candidate search", monitor_type="search_query", query='"Maria Alvarez"', source_type="news")
    db.add_all([existing, monitor])
    db.commit()
    db.refresh(monitor)

    class Provider:
        def search(self, query, limit=10):
            return SearchResponse(provider="test", results=[
                SearchResult(title="Maria Alvarez housing plan", url="https://other.example/new", source_name="Example News")
            ])

    monkeypatch.setattr("app.routes.monitors.get_search_provider", lambda: Provider())
    result = ingest_monitor(monitor.id, db=db)
    assert result.added_count == 0
    assert result.skipped_count == 1
    assert result.results[0].reason == "Duplicate title and source"


def test_search_failed_fetch_continues_other_results(db, monkeypatch):
    from app.routes.monitors import ingest_monitor
    from app.services import ingestion
    from app.services.search_provider import SearchResponse, SearchResult
    _campaign(db)
    monitor = SourceMonitor(name="Candidate search", monitor_type="search_query", query='"Maria Alvarez"', source_type="news")
    db.add(monitor)
    db.commit()
    db.refresh(monitor)

    class Provider:
        def search(self, query, limit=10):
            return SearchResponse(provider="test", results=[
                SearchResult(title="Bad", url="https://example.com/bad"),
                SearchResult(title="Good", url="https://example.com/good"),
            ])

    def fake_ingest_url(db_arg, url, source_type):
        if url.endswith("/bad"):
            return None
        return ingestion.ingest_text(
            db_arg,
            "Maria Alvarez campaign update",
            "Maria Alvarez met District 7 voters.",
            "Example",
            source_type,
            source_url=url,
        )

    monkeypatch.setattr("app.routes.monitors.get_search_provider", lambda: Provider())
    monkeypatch.setattr("app.routes.monitors.ingestion.ingest_url", fake_ingest_url)
    result = ingest_monitor(monitor.id, db=db)
    assert result.failed_count == 1
    assert result.added_count == 1


def test_irrelevant_search_result_gets_archived(db, monkeypatch):
    from app.routes.monitors import ingest_monitor
    from app.services import ingestion
    from app.services.search_provider import SearchResponse, SearchResult
    _campaign(db)
    monitor = SourceMonitor(name="Sports search", monitor_type="search_query", query="Phillies", source_type="news")
    db.add(monitor)
    db.commit()
    db.refresh(monitor)

    class Provider:
        def search(self, query, limit=10):
            return SearchResponse(provider="test", results=[SearchResult(title="Sports", url="https://example.com/sports")])

    def fake_ingest_url(db_arg, url, source_type):
        return ingestion.ingest_text(
            db_arg,
            "Phillies manager previews playoffs",
            "The MLB coach discussed the game and season.",
            "Sports",
            source_type,
            source_url=url,
        )

    monkeypatch.setattr("app.routes.monitors.get_search_provider", lambda: Provider())
    monkeypatch.setattr("app.routes.monitors.ingestion.ingest_url", fake_ingest_url)
    result = ingest_monitor(monitor.id, db=db)
    source = db.get(SourceItem, result.results[0].source_id)
    assert source.archived_as_irrelevant is True
    assert result.results[0].archived_as_irrelevant is True


def test_relevant_search_result_enters_review_flow(db, monkeypatch):
    from app.routes.monitors import ingest_monitor
    from app.routes.review_queue import get_review_queue
    from app.services import ingestion
    from app.services.search_provider import SearchResponse, SearchResult
    _campaign(db)
    monitor = SourceMonitor(name="Candidate search", monitor_type="search_query", query='"Maria Alvarez"', source_type="news")
    db.add(monitor)
    db.commit()
    db.refresh(monitor)

    class Provider:
        def search(self, query, limit=10):
            return SearchResponse(provider="test", results=[SearchResult(title="Candidate", url="https://example.com/relevant")])

    def fake_ingest_url(db_arg, url, source_type):
        return ingestion.ingest_text(
            db_arg,
            "Maria Alvarez releases District 7 housing plan",
            "Maria Alvarez said housing is central to the campaign.",
            "Example",
            source_type,
            source_url=url,
        )

    monkeypatch.setattr("app.routes.monitors.get_search_provider", lambda: Provider())
    monkeypatch.setattr("app.routes.monitors.ingestion.ingest_url", fake_ingest_url)
    ingest_monitor(monitor.id, db=db)
    queue = get_review_queue(db=db)
    assert any(item.title == "Maria Alvarez releases District 7 housing plan" for item in queue)


def test_ingest_all_search_monitors_works(db, monkeypatch):
    from app.routes.monitors import ingest_all_search_monitors
    from app.services import ingestion
    from app.services.search_provider import SearchResponse, SearchResult
    _campaign(db)
    db.add_all([
        SourceMonitor(name="Search 1", monitor_type="search_query", query='"Maria Alvarez"', source_type="news"),
        SourceMonitor(name="Search 2", monitor_type="search_query", query='"District 7"', source_type="news"),
        SourceMonitor(name="Paused", monitor_type="search_query", query='"Paused"', source_type="news", active=False),
    ])
    db.commit()

    class Provider:
        def search(self, query, limit=10):
            slug = "one" if "Maria" in query else "two"
            return SearchResponse(provider="test", results=[SearchResult(title=slug, url=f"https://example.com/{slug}")])

    def fake_ingest_url(db_arg, url, source_type):
        return ingestion.ingest_text(
            db_arg,
            "Maria Alvarez District 7 update",
            "Maria Alvarez met District 7 voters.",
            "Example",
            source_type,
            source_url=url,
        )

    monkeypatch.setattr("app.routes.monitors.get_search_provider", lambda: Provider())
    monkeypatch.setattr("app.routes.monitors.ingestion.ingest_url", fake_ingest_url)
    result = ingest_all_search_monitors(db=db)
    assert result.monitor_count == 2
    assert result.added_count == 2


def test_failed_provider_request_handled_cleanly(db, monkeypatch):
    from app.routes.monitors import ingest_all_search_monitors
    _campaign(db)
    db.add_all([
        SourceMonitor(name="Broken", monitor_type="search_query", query='"Broken"', source_type="news"),
        SourceMonitor(name="Also Broken", monitor_type="search_query", query='"Also"', source_type="news"),
    ])
    db.commit()

    class Provider:
        name = "test"

        def search(self, query, limit=10):
            raise RuntimeError(f"provider failed for {query}")

    monkeypatch.setattr("app.routes.monitors.get_search_provider", lambda: Provider())
    result = ingest_all_search_monitors(db=db)
    assert result.monitor_count == 2
    assert result.failed_count == 2
    assert all("provider failed" in (r.message or "") for r in result.results)
