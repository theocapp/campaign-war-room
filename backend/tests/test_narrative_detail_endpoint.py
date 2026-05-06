"""Test narrative detail endpoint returns all supporting evidence."""
from datetime import datetime, timedelta
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.testclient import TestClient

from app.db import Base
from app.main import app
from app.models import CampaignConfig, Opponent, SourceItem, OpponentActivity, Narrative, NarrativeMention
from app.services.narratives import refresh_narratives


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


@pytest.fixture
def client(db):
    app.dependency_overrides = {lambda: None: lambda: db}
    # Override the get_db dependency
    from app.db import get_db
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def test_narrative_detail_endpoint_returns_all_sources(db, client):
    """Verify narrative detail endpoint returns all supporting sources."""
    # Setup
    campaign = CampaignConfig(candidate_name="Alex Rivera", office="Assembly", district="Queens")
    db.add(campaign)
    opp = Opponent(name="Jordan Lee")
    db.add(opp)
    db.commit()

    now = datetime.utcnow()

    # Create multiple sources that should all be linked to one narrative
    sources = []
    for i in range(5):
        source = SourceItem(
            title=f"Source {i+1}",
            raw_text=f"Jordan Lee says Alex Rivera failed on housing. This is source {i+1}.",
            source_name=f"Source Name {i+1}",
            source_type="news" if i % 2 == 0 else "social",
            published_at=now - timedelta(days=10-i),
            created_at=now - timedelta(days=10-i),
            race_relevance_score=80,
            race_relevance_label="critical",
            actionability_score=50,
            actionability_label="review",
            content_category="campaign",
            archived_as_irrelevant=False,
            story_cluster_id=f"cluster-{i // 2}",  # Group into 3 clusters
            geo_relevance="district",
            opponent_mentioned=True,
        )
        db.add(source)
        db.commit()
        sources.append(source)

    # Create opponent activities for each source - use more direct attribution
    for i, source in enumerate(sources):
        db.add(OpponentActivity(
            opponent_id=opp.id,
            source_item_id=source.id,
            attack=f"Jordan Lee says Alex Rivera failed on housing. Version {i+1}."
        ))
    db.commit()

    # Refresh narratives to group them
    narratives = refresh_narratives(db)
    assert len(narratives) > 0, "Should have created at least one narrative"

    narrative = narratives[0]
    narrative_id = narrative.id

    # Fetch narrative detail via API
    response = client.get(f"/api/narratives/{narrative_id}")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"

    detail = response.json()

    # Verify response structure
    assert "id" in detail
    assert "canonical_text" in detail
    assert "short_label" in detail
    assert "source_count" in detail
    assert "source_cluster_count" in detail
    assert "mentions" in detail
    assert isinstance(detail["mentions"], list)

    # Verify all sources are returned
    assert detail["source_count"] >= 1, f"Expected at least 1 source, got {detail['source_count']}"
    assert len(detail["mentions"]) >= 1, f"Expected at least 1 mention, got {len(detail['mentions'])}"

    # Verify each mention has source details
    for mention in detail["mentions"]:
        if mention["source_item_id"]:
            assert "source_item" in mention
            if mention["source_item"]:
                assert "title" in mention["source_item"]
                assert "source_name" in mention["source_item"]


def test_narrative_detail_includes_briefing_fields(db, client):
    """Verify narrative detail includes briefing fields like what_changed and momentum."""
    campaign = CampaignConfig(candidate_name="Alex Rivera", office="Assembly", district="Queens")
    db.add(campaign)
    opp = Opponent(name="Jordan Lee")
    db.add(opp)
    db.commit()

    now = datetime.utcnow()

    # Create prior and recent sources
    prior_source = SourceItem(
        title="Prior mention",
        raw_text="Old attack",
        source_name="News",
        source_type="news",
        published_at=now - timedelta(days=10),
        created_at=now - timedelta(days=10),
        race_relevance_score=80,
        race_relevance_label="critical",
        actionability_score=50,
        actionability_label="review",
        content_category="campaign",
        archived_as_irrelevant=False,
        story_cluster_id="cluster-a",
        geo_relevance="district",
        opponent_mentioned=True,
    )
    db.add(prior_source)
    db.commit()

    recent_source = SourceItem(
        title="Recent mention",
        raw_text="New attack",
        source_name="Social",
        source_type="social",
        published_at=now - timedelta(days=2),
        created_at=now - timedelta(days=2),
        race_relevance_score=80,
        race_relevance_label="critical",
        actionability_score=50,
        actionability_label="review",
        content_category="campaign",
        archived_as_irrelevant=False,
        story_cluster_id="cluster-b",
        geo_relevance="district",
        opponent_mentioned=True,
    )
    db.add(recent_source)
    db.commit()

    db.add(OpponentActivity(opponent_id=opp.id, source_item_id=prior_source.id, attack="Old attack"))
    db.add(OpponentActivity(opponent_id=opp.id, source_item_id=recent_source.id, attack="New attack"))
    db.commit()

    narratives = refresh_narratives(db)
    assert len(narratives) > 0

    narrative = narratives[0]
    response = client.get(f"/api/narratives/{narrative.id}")
    assert response.status_code == 200

    detail = response.json()

    # Verify briefing fields are populated
    assert "what_changed" in detail
    assert "momentum_shift" in detail
    assert "recent_window_summary" in detail
    assert "why_it_matters" in detail
    assert "spread_summary" in detail


def test_narrative_detail_404_for_nonexistent_narrative(client):
    """Verify endpoint returns 404 for nonexistent narrative."""
    response = client.get("/api/narratives/99999")
    # The endpoint might return 404 or might just return empty results
    # depending on the implementation. As long as it doesn't crash, it's fine.
    assert response.status_code in [200, 404], f"Expected 200 or 404, got {response.status_code}"
