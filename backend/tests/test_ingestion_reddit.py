"""Fixture-based tests for the Reddit ingester.

All PRAW and network calls are mocked — tests never hit Reddit.
"""
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import CampaignConfig, Opponent, SourceItem
from app.services.ingestion_reddit import _post_text, _search_terms, ingest_reddit


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


def _fake_submission(title="Test post", selftext="", permalink="/r/pennsylvania/comments/abc/test/", created_utc=1715000000.0, author="user1"):
    sub = MagicMock()
    sub.title = title
    sub.selftext = selftext
    sub.permalink = permalink
    sub.created_utc = created_utc
    sub.author = MagicMock()
    sub.author.__str__ = lambda self: author
    return sub


# ── unit helpers ───────────────────────────────────────────────────────────────

def test_search_terms_includes_candidate_and_opponent(db):
    terms = _search_terms(db)
    assert "Paige Cognetti" in terms
    assert "Rob Bresnahan" in terms


def test_post_text_combines_title_and_selftext():
    sub = _fake_submission(title="Cognetti on healthcare", selftext="She said healthcare is a priority.")
    assert "Cognetti on healthcare" in _post_text(sub)
    assert "healthcare is a priority" in _post_text(sub)


def test_post_text_title_only_for_link_posts():
    sub = _fake_submission(title="Bresnahan endorsement", selftext="")
    assert _post_text(sub) == "Bresnahan endorsement"


def test_post_text_ignores_deleted_selftext():
    sub = _fake_submission(title="A title", selftext="[deleted]")
    assert _post_text(sub) == "A title"


# ── ingest_reddit integration ──────────────────────────────────────────────────

def _make_reddit_mock(submissions):
    """Build a mock praw.Reddit whose subreddit().search() yields submissions.

    Returns a fresh list on every call so the iterator is never exhausted across
    multiple ingest_reddit() calls in the same test.
    """
    reddit = MagicMock()
    subreddit = MagicMock()
    subreddit.search.side_effect = lambda *a, **kw: list(submissions)
    reddit.subreddit.return_value = subreddit
    return reddit


@patch("app.services.ingestion_reddit._get_reddit")
@patch("app.services.campaign_analysis.analyze_with_frames")
def test_ingest_reddit_adds_posts(mock_analyze, mock_get_reddit, db):
    mock_analyze.return_value = {
        "relevant": True, "relevance_score": 70, "one_sentence": "Reddit post about Cognetti.",
        "framing": "neutral", "sentiment": "neutral", "needs_attention": False,
        "reason": "", "opponent_attacks": [], "frame_matches": [],
    }
    sub = _fake_submission(
        title="Cognetti town hall in Scranton",
        selftext="She spoke about healthcare and infrastructure at today's event.",
        permalink="/r/pennsylvania/comments/xyz/cognetti_town_hall/",
    )
    mock_get_reddit.return_value = _make_reddit_mock([sub])

    import os
    with patch.dict(os.environ, {"REDDIT_SUBREDDITS": "pennsylvania"}):
        result = ingest_reddit(db)

    # Two search terms (Cognetti + Bresnahan) × 1 subreddit = 2 search calls
    # but same submission returned each time — second call deduped
    assert result.subreddits_searched == 1
    assert result.added >= 1
    item = db.query(SourceItem).filter(SourceItem.source_type == "social").first()
    assert item is not None
    assert item.source_name == "Reddit r/pennsylvania"
    assert "Cognetti" in item.title


@patch("app.services.ingestion_reddit._get_reddit")
@patch("app.services.campaign_analysis.analyze_with_frames")
def test_ingest_reddit_deduplicates(mock_analyze, mock_get_reddit, db):
    mock_analyze.return_value = {
        "relevant": False, "relevance_score": 20, "one_sentence": ".",
        "framing": "neutral", "sentiment": "neutral", "needs_attention": False,
        "reason": "", "opponent_attacks": [], "frame_matches": [],
    }
    sub = _fake_submission(permalink="/r/pennsylvania/comments/dup/post/")
    mock_get_reddit.return_value = _make_reddit_mock([sub])

    import os
    with patch.dict(os.environ, {"REDDIT_SUBREDDITS": "pennsylvania"}):
        ingest_reddit(db)
        result = ingest_reddit(db)

    assert db.query(SourceItem).filter(SourceItem.source_type == "social").count() == 1
    assert result.skipped >= 1


@patch("app.services.ingestion_reddit._get_reddit", return_value=None)
def test_ingest_reddit_no_credentials_returns_zeros(mock_get_reddit, db):
    result = ingest_reddit(db)
    assert result.subreddits_searched == 0
    assert result.added == 0


def test_ingest_reddit_no_campaign_config_returns_zeros():
    """Without a campaign config there are no search terms — ingester short-circuits."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    with patch("app.services.ingestion_reddit._get_reddit") as mock_get_reddit:
        mock_get_reddit.return_value = MagicMock()
        result = ingest_reddit(session)

    assert result.added == 0
    session.close()
    Base.metadata.drop_all(engine)
