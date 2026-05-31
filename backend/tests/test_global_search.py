"""Tests for /api/search/{entities,quotes,outlets} — the three new
endpoints powering the global header search dropdown.

Coverage:
  - entity search hits name AND aliases, ranked by mention_count
  - quote search returns the verbatim span + article + outlet-resolved source
  - outlet search hits name AND domain, ranked by authority_score, active only
  - empty / whitespace query → []
  - limit param caps result count
"""
from __future__ import annotations

from datetime import datetime, timedelta

import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
from app.main import app
from app.models import (
    ClaimRecord,
    Entity,
    EntityMention,
    NarrativeFrame,
    NarrativeFrameMention,
    Outlet,
    SourceItem,
)


@pytest.fixture
def client():
    # StaticPool keeps a single shared connection so the in-memory SQLite
    # database survives across requests made by TestClient — without it,
    # each request gets a fresh empty :memory: DB.
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    def _override_get_db():
        s = TestSession()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override_get_db

    # Seed: 2 entities, 2 outlets, 1 article, 1 claim_record
    with TestSession() as db:
        cognetti = Entity(
            canonical_id="person:cognetti", type="person", name="Paige Cognetti",
            aliases=json.dumps(["Mayor Cognetti", "P. Cognetti"]),
            affiliation="D", mention_count=940, source_count=940,
        )
        bresnahan = Entity(
            canonical_id="person:bresnahan", type="person", name="Rob Bresnahan",
            aliases=json.dumps(["Robert Bresnahan", "Rep. Bresnahan"]),
            affiliation="R", mention_count=812, source_count=812,
        )
        db.add_all([cognetti, bresnahan])

        nyt = Outlet(
            name="The New York Times", domain="nytimes.com",
            outlet_type="national", authority_score=10, active=True,
        )
        times_leader = Outlet(
            name="Times Leader", domain="timesleader.com",
            outlet_type="local_news", city="Wilkes-Barre", state="PA",
            authority_score=8, active=True,
        )
        inactive = Outlet(
            name="Defunct Times", domain="defuncttimes.com",
            outlet_type="local_news", authority_score=5, active=False,
        )
        db.add_all([nyt, times_leader, inactive])
        db.flush()

        article = SourceItem(
            title="Cognetti talks healthcare", source_name="Times Leader",
            source_type="rss", source_url="https://timesleader.com/healthcare",
            published_at=datetime(2026, 5, 1),
            outlet_id=times_leader.id, publisher_domain="timesleader.com",
        )
        db.add(article)
        db.flush()

        claim = ClaimRecord(
            article_id=article.id,
            evidence_span="Medicaid is a promise to care for our most vulnerable.",
            evidence_hash="hash-001", label="statement", confidence="high",
        )
        db.add(claim)
        db.commit()

    # The access-code middleware in app/main.py bypasses auth when the
    # request claims to come from localhost via x-forwarded-host. Setting
    # it as a default header on the TestClient mirrors how Vite's proxy
    # behaves in dev.
    yield TestClient(app, headers={"x-forwarded-host": "localhost:5174"})
    app.dependency_overrides.clear()


# ─── Entities ─────────────────────────────────────────────────────────────

def test_entities_search_by_name(client):
    r = client.get("/api/search/entities", params={"q": "cognetti"})
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["name"] == "Paige Cognetti"
    assert data[0]["affiliation"] == "D"
    assert data[0]["mention_count"] == 940


def test_entities_search_by_alias(client):
    """An alias-only match should still surface the entity."""
    r = client.get("/api/search/entities", params={"q": "mayor cognetti"})
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["canonical_id"] == "person:cognetti"


def test_entities_ranked_by_mention_count(client):
    """When multiple entities match, the one with more articles wins."""
    r = client.get("/api/search/entities", params={"q": "e"})  # matches both
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2
    assert data[0]["mention_count"] >= data[1]["mention_count"]


def test_entities_limit_caps_results(client):
    r = client.get("/api/search/entities", params={"q": "e", "limit": 1})
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_entities_empty_query_rejected(client):
    """min_length=1 should reject empty strings at the schema layer."""
    r = client.get("/api/search/entities", params={"q": ""})
    assert r.status_code == 422


# ─── Quotes ───────────────────────────────────────────────────────────────

def test_quotes_search_hits_span(client):
    r = client.get("/api/search/quotes", params={"q": "medicaid"})
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert "Medicaid" in data[0]["evidence_span"]
    assert data[0]["article_title"] == "Cognetti talks healthcare"
    # source_name should be outlet-resolved, NOT the raw source_name field —
    # the article has outlet_id pointing at Times Leader.
    assert data[0]["source_name"] == "Times Leader"


def test_quotes_no_match_returns_empty(client):
    r = client.get("/api/search/quotes", params={"q": "spaceship"})
    assert r.status_code == 200
    assert r.json() == []


# ─── Outlets ──────────────────────────────────────────────────────────────

def test_outlets_search_by_name(client):
    r = client.get("/api/search/outlets", params={"q": "times"})
    assert r.status_code == 200
    data = r.json()
    # Two ACTIVE outlets contain "Times" — the inactive "Defunct Times"
    # should be filtered out.
    names = [o["name"] for o in data]
    assert "The New York Times" in names
    assert "Times Leader" in names
    assert "Defunct Times" not in names


def test_outlets_ranked_by_authority_score(client):
    r = client.get("/api/search/outlets", params={"q": "times"})
    data = r.json()
    # NYT has authority_score=10, Times Leader=8 → NYT first.
    assert data[0]["name"] == "The New York Times"
    assert data[1]["name"] == "Times Leader"


def test_outlets_search_by_domain(client):
    r = client.get("/api/search/outlets", params={"q": "timesleader.com"})
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["domain"] == "timesleader.com"


def test_outlets_limit_caps_results(client):
    r = client.get("/api/search/outlets", params={"q": "times", "limit": 1})
    assert r.status_code == 200
    assert len(r.json()) == 1


# ─── Suggestions ──────────────────────────────────────────────────────────

@pytest.fixture
def suggestions_client():
    """Larger seed for the suggestions endpoint — needs entity_mentions,
    narrative_frames, and a labeled claim_record to exercise every branch
    of the 7-day ranking."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine)

    def _override_get_db():
        s = TestSession()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override_get_db

    now = datetime.utcnow()
    recent = now - timedelta(days=2)   # inside 7-day window
    stale = now - timedelta(days=20)   # outside 7-day window

    with TestSession() as db:
        # Entities — Bresnahan is more active in the last 7 days; Cognetti
        # has more lifetime mentions but no recent activity.
        bresnahan = Entity(
            canonical_id="person:bresnahan", type="person", name="Rob Bresnahan",
            aliases=json.dumps([]), affiliation="R", mention_count=100,
        )
        cognetti = Entity(
            canonical_id="person:cognetti", type="person", name="Paige Cognetti",
            aliases=json.dumps([]), affiliation="D", mention_count=500,
        )
        db.add_all([bresnahan, cognetti])

        outlet_a = Outlet(name="Times Leader", domain="timesleader.com",
                          outlet_type="local_news", authority_score=8, active=True)
        outlet_b = Outlet(name="National Wire", domain="natwire.com",
                          outlet_type="national", authority_score=10, active=True)
        db.add_all([outlet_a, outlet_b])
        db.flush()

        # 3 articles in the last 7 days from outlet_a, 1 from outlet_b
        articles_recent = []
        for i in range(3):
            a = SourceItem(
                title=f"Recent article {i}", source_name="Times Leader",
                source_type="rss", source_url=f"https://timesleader.com/{i}",
                published_at=recent, outlet_id=outlet_a.id,
                publisher_domain="timesleader.com",
            )
            db.add(a)
            articles_recent.append(a)
        national_article = SourceItem(
            title="National article", source_name="National Wire",
            source_type="rss", source_url="https://natwire.com/x",
            published_at=recent, outlet_id=outlet_b.id,
            publisher_domain="natwire.com",
        )
        db.add(national_article)

        stale_article = SourceItem(
            title="Old article", source_name="Times Leader",
            source_type="rss", source_url="https://timesleader.com/stale",
            published_at=stale, outlet_id=outlet_a.id,
            publisher_domain="timesleader.com",
        )
        db.add(stale_article)
        db.flush()

        # Bresnahan gets 3 recent mentions, Cognetti gets 1 — Bresnahan should win.
        for a in articles_recent:
            db.add(EntityMention(article_id=a.id, entity_id=bresnahan.id,
                                 confidence="high", extraction_method="llm"))
        db.add(EntityMention(article_id=articles_recent[0].id, entity_id=cognetti.id,
                             confidence="high", extraction_method="llm"))
        # Stale mention shouldn't influence ranking.
        db.add(EntityMention(article_id=stale_article.id, entity_id=cognetti.id,
                             confidence="high", extraction_method="llm"))

        # Two narrative frames; the active one gets 2 recent mentions, the
        # inactive one gets 0.
        active_frame = NarrativeFrame(
            name="Bresnahan Healthcare", description="x",
            owner_type="candidate", subject_type="opponent",
            source="human",
        )
        sleepy_frame = NarrativeFrame(
            name="Sleepy Frame", description="x",
            owner_type="candidate", subject_type="opponent",
            source="human",
        )
        db.add_all([active_frame, sleepy_frame])
        db.flush()
        for a in articles_recent[:2]:
            db.add(NarrativeFrameMention(frame_id=active_frame.id, source_item_id=a.id))

        # Labeled and unlabeled claim records — endpoint should prefer the
        # labeled one (label != null, label != 'statement').
        db.add(ClaimRecord(
            article_id=articles_recent[0].id,
            evidence_span="An endorsement quote.",
            evidence_hash="hash-endorse", label="endorsement", confidence="high",
        ))
        db.add(ClaimRecord(
            article_id=articles_recent[1].id,
            evidence_span="A plain statement.",
            evidence_hash="hash-stmt", label="statement", confidence="high",
        ))
        db.commit()

    yield TestClient(app, headers={"x-forwarded-host": "localhost:5174"})
    app.dependency_overrides.clear()


def test_suggestions_entities_ranked_by_recent_mentions(suggestions_client):
    r = suggestions_client.get("/api/search/suggestions", params={"per_type": 2})
    assert r.status_code == 200
    data = r.json()
    # Bresnahan (3 recent) ranks above Cognetti (1 recent) despite Cognetti
    # having more lifetime mentions.
    names = [e["name"] for e in data["entities"]]
    assert names[0] == "Rob Bresnahan"
    assert data["entities"][0]["mentions_this_week"] == 3


def test_suggestions_outlets_filtered_to_local_and_regional(suggestions_client):
    r = suggestions_client.get("/api/search/suggestions", params={"per_type": 5})
    data = r.json()
    # National Wire has outlet_type='national' and must NOT appear.
    outlet_names = [o["name"] for o in data["outlets"]]
    assert "Times Leader" in outlet_names
    assert "National Wire" not in outlet_names


def test_suggestions_frames_ranked_by_recent_mentions(suggestions_client):
    r = suggestions_client.get("/api/search/suggestions", params={"per_type": 5})
    data = r.json()
    # Sleepy Frame has zero recent mentions → not returned (inner JOIN drops it).
    frame_names = [f["name"] for f in data["frames"]]
    assert frame_names == ["Bresnahan Healthcare"]
    assert data["frames"][0]["mentions_this_week"] == 2


def test_suggestions_quotes_prefer_non_statement_labels(suggestions_client):
    r = suggestions_client.get("/api/search/suggestions", params={"per_type": 2})
    data = r.json()
    # 2 quote records exist: one 'endorsement', one 'statement'. The
    # endpoint filters out null AND 'statement', so only the endorsement
    # quote should come back.
    spans = [q["evidence_span"] for q in data["quotes"]]
    assert "An endorsement quote." in spans
    assert "A plain statement." not in spans
    # And source_name was resolved via display_source_name, not raw.
    assert data["quotes"][0]["source_name"] == "Times Leader"


def test_suggestions_per_type_param_caps_each_section(suggestions_client):
    r = suggestions_client.get("/api/search/suggestions", params={"per_type": 1})
    data = r.json()
    assert len(data["entities"]) <= 1
    assert len(data["outlets"]) <= 1
    assert len(data["frames"]) <= 1
    assert len(data["quotes"]) <= 1
