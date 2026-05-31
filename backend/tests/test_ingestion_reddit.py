"""Fixture-based tests for the Reddit ingester.

All HTTP calls are mocked — tests never hit Reddit. The ingester was
rewritten from PRAW to direct `httpx.get(...)` against reddit.com's
public `search.json` endpoint, so these tests mock at the HTTP layer.
"""
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import CampaignConfig, Opponent, SourceItem
from app.services.ingestion_reddit import (
    _search_terms,
    _district_derived_subs,
    ingest_reddit,
)


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    session.add(CampaignConfig(candidate_name="Paige Cognetti", district="PA-08"))
    session.add(Opponent(name="Rob Bresnahan"))
    session.commit()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def _fake_post(
    title="Test post",
    selftext="",
    permalink="/r/pennsylvania/comments/abc/test/",
    created_utc=1715000000.0,
    author="user1",
) -> dict:
    """Build a dict matching the shape Reddit's search.json returns inside
    `data.children[i].data` — what `_ingest_submission` consumes."""
    return {
        "title": title,
        "selftext": selftext,
        "permalink": permalink,
        "created_utc": created_utc,
        "author": author,
    }


def _wrap_children(posts: list[dict]) -> dict:
    """Wrap a list of post dicts in Reddit's search.json envelope so
    `_search_subreddit` / `_search_site_wide` see the expected shape."""
    return {"data": {"children": [{"kind": "t3", "data": p} for p in posts]}}


def _http_response(json_payload: dict, status_code: int = 200):
    """Minimal stand-in for an httpx.Response object."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_payload
    return resp


# A V2-shape analyze_with_frames return value. Ingestion consumes the
# back-compat-derived legacy fields (relevance_score, relevant, framing,
# sentiment, opponent_attacks, reason), all of which the v2 path computes
# from the verdict enum + extracted_claims. We pre-bake those here.
_LEGACY_RELEVANT = {
    "verdict": "relevant",
    "summary": "Reddit post about Cognetti.",
    "campaign_action": "monitor",
    "sentiment_new": "neutral",
    "source_credibility": "medium",
    "extracted_claims": [],
    # Back-compat derived fields (what ingestion actually reads):
    "relevance_score": 65,
    "relevant": True,
    "one_sentence": "Reddit post about Cognetti.",
    "framing": "background",
    "reason": "",
    "sentiment": "neutral",
    "opponent_attacks": [],
    "frame_matches": [],
    "candidate_new_frames": [],
    "_used_fallback": False,
    "needs_attention": False,
    "needs_attention_reason": None,
}


# ── unit helpers ───────────────────────────────────────────────────────────────

def test_search_terms_includes_candidate_and_opponent(db):
    terms = _search_terms(db)
    assert "Paige Cognetti" in terms
    assert "Rob Bresnahan" in terms


def test_search_terms_adds_distinctive_surnames(db):
    # Broadening: people say "Cognetti"/"Bresnahan" far more than the full
    # name, and phrase search misses those. First names are NOT added.
    terms = _search_terms(db)
    assert "Cognetti" in terms
    assert "Bresnahan" in terms
    assert "Paige" not in terms
    assert "Rob" not in terms


def test_search_terms_extra_env_appends_and_dedupes(db, monkeypatch):
    monkeypatch.setenv("REDDIT_EXTRA_TERMS", "PA-08, Cognetti , #nepa")
    terms = _search_terms(db)
    assert "PA-08" in terms
    assert "#nepa" in terms
    # "Cognetti" is already derived as a surname — must not appear twice.
    assert terms.count("Cognetti") == 1


def test_district_subs_split_multi_city_location():
    # Bug fix: "Scranton/Wilkes-Barre, PA-08" must yield the two REAL subs
    # (r/Scranton, r/WilkesBarre), not the bogus concatenation that the old
    # single-token strip produced ("ScrantonWilkesBarre" — a sub that 404s).
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        session.add(CampaignConfig(
            candidate_name="Paige Cognetti",
            district="PA-08",
            location="Scranton/Wilkes-Barre, PA-08",
        ))
        session.commit()
        subs = _district_derived_subs(session)
        assert "Scranton" in subs
        assert "WilkesBarre" in subs
        assert "ScrantonWilkesBarre" not in subs
        assert "pennsylvania" in subs  # state code → full state sub
    finally:
        session.close()
        Base.metadata.drop_all(engine)


# ── ingest_reddit integration (HTTP mocked) ───────────────────────────────────


@patch("app.services.ingestion_reddit._probe_reddit_access", return_value=True)
@patch("app.services.ingestion_reddit._fetch_comments", return_value=[])
@patch("app.services.ingestion_reddit.httpx.get")
@patch("app.services.campaign_analysis.analyze_with_frames")
def test_ingest_reddit_adds_posts(mock_analyze, mock_httpx_get, mock_fetch_comments,
                                  mock_probe, db):
    mock_analyze.return_value = _LEGACY_RELEVANT
    post = _fake_post(
        title="Cognetti town hall in Scranton",
        selftext="She spoke about healthcare and infrastructure at today's event.",
        permalink="/r/pennsylvania/comments/xyz/cognetti_town_hall/",
    )
    # Every Reddit endpoint we hit returns this single post.
    mock_httpx_get.return_value = _http_response(_wrap_children([post]))

    import os
    with patch.dict(os.environ, {"REDDIT_SUBREDDITS": "pennsylvania",
                                  "REDDIT_COMMENTS_ENABLED": "false"}):
        result = ingest_reddit(db)

    assert result.added >= 1
    item = db.query(SourceItem).filter(SourceItem.source_type == "social").first()
    assert item is not None
    assert "Cognetti" in item.title
    assert item.source_url == "https://www.reddit.com/r/pennsylvania/comments/xyz/cognetti_town_hall/"


@patch("app.services.ingestion_reddit._probe_reddit_access", return_value=True)
@patch("app.services.ingestion_reddit._fetch_comments", return_value=[])
@patch("app.services.ingestion_reddit.httpx.get")
@patch("app.services.campaign_analysis.analyze_with_frames")
def test_ingest_reddit_deduplicates(mock_analyze, mock_httpx_get, mock_fetch_comments,
                                    mock_probe, db):
    mock_analyze.return_value = {**_LEGACY_RELEVANT, "relevant": False,
                                  "verdict": "loosely_related", "relevance_score": 25}
    post = _fake_post(permalink="/r/pennsylvania/comments/dup/post/")
    mock_httpx_get.return_value = _http_response(_wrap_children([post]))

    import os
    with patch.dict(os.environ, {"REDDIT_SUBREDDITS": "pennsylvania",
                                  "REDDIT_COMMENTS_ENABLED": "false"}):
        ingest_reddit(db)
        result = ingest_reddit(db)

    # Same post URL across two runs → second run skips it (source_url uniqueness)
    assert db.query(SourceItem).filter(SourceItem.source_type == "social").count() == 1
    assert result.skipped >= 1


@patch("app.services.ingestion_reddit._probe_reddit_access", return_value=False)
def test_ingest_reddit_blocked_access_returns_zeros(mock_probe, db):
    """When `_probe_reddit_access` reports Reddit is blocking unauthed
    requests (post-2024 anti-bot stance), the ingester short-circuits
    before doing any work. Replaces the v1 `_get_reddit returns None`
    case — credentials no longer pass through a PRAW factory."""
    result = ingest_reddit(db)
    assert result.subreddits_searched == 0
    assert result.added == 0


@patch("app.services.ingestion_reddit._probe_reddit_access", return_value=True)
def test_ingest_reddit_no_campaign_config_returns_zeros(mock_probe):
    """Without a campaign config there are no search terms — ingester
    short-circuits even when Reddit access is fine."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        result = ingest_reddit(session)
        assert result.added == 0
        assert result.subreddits_searched == 0
    finally:
        session.close()
        Base.metadata.drop_all(engine)
