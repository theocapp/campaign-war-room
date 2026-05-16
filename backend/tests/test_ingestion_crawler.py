"""Fixture-based tests for the trafilatura crawler.

All network calls are mocked — tests never hit the live internet.
"""
import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import CampaignConfig, NarrativeFrame, Outlet, SourceItem
from app.services.ingestion_crawler import _domain, crawl_url


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    # Minimal campaign config so analyze_with_frames won't blow up
    session.add(CampaignConfig(candidate_name="Paige Cognetti", district="PA-08"))
    session.commit()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


_SAMPLE_HTML = """
<html>
<head>
  <title>Cognetti announces infrastructure plan</title>
  <meta name="author" content="Jane Reporter">
  <meta property="article:published_time" content="2026-05-14T10:00:00Z">
</head>
<body>
  <article>
    <h1>Cognetti announces infrastructure plan</h1>
    <p>Congressional candidate Paige Cognetti unveiled her infrastructure plan on Wednesday,
    pledging $50 million in federal investment for Scranton roads and bridges. The plan
    focuses on Northeast Pennsylvania's aging highway system and broadband expansion.</p>
    <p>Cognetti said the plan would create 2,000 union jobs in Lackawanna County.</p>
  </article>
</body>
</html>
"""

_FAKE_META = MagicMock()
_FAKE_META.title = "Cognetti announces infrastructure plan"
_FAKE_META.sitename = "Times-Tribune"
_FAKE_META.author = "Jane Reporter"
_FAKE_META.date = "2026-05-14"


# ── _domain helper ─────────────────────────────────────────────────────────────

def test_domain_strips_www():
    assert _domain("https://www.thetimes-tribune.com/news/article.html") == "thetimes-tribune.com"


def test_domain_bare():
    assert _domain("https://wnep.com/story/abc") == "wnep.com"


# ── crawl_url ─────────────────────────────────────────────────────────────────

_LONG_TEXT = (
    "Congressional candidate Paige Cognetti unveiled her infrastructure plan on Wednesday "
    "pledging fifty million dollars in federal investment for Scranton roads and bridges. "
    "The plan focuses on Northeast Pennsylvania aging highway system and broadband expansion "
    "into rural Wayne and Pike counties. Cognetti said the plan would create two thousand "
    "union jobs in Lackawanna County over the next five years and is fully funded through "
    "existing federal transportation grants that the district has not yet claimed."
)

@patch("app.services.ingestion_crawler.trafilatura.extract", return_value=_LONG_TEXT)
@patch("app.services.ingestion_crawler.trafilatura.extract_metadata", return_value=_FAKE_META)
@patch("app.services.ingestion_crawler.trafilatura.fetch_url", return_value=_SAMPLE_HTML)
@patch("app.services.campaign_analysis.analyze_with_frames")
def test_crawl_url_creates_source_item(mock_analyze, mock_fetch, mock_meta, mock_extract, db):
    mock_analyze.return_value = {
        "relevant": True,
        "relevance_score": 80,
        "one_sentence": "Cognetti unveils infrastructure plan.",
        "framing": "positive",
        "sentiment": "positive",
        "needs_attention": False,
        "reason": "Directly mentions candidate",
        "opponent_attacks": [],
        "frame_matches": [],
    }

    url = "https://thetimes-tribune.com/news/cognetti-infrastructure"
    result = crawl_url(db, url, source_type="news")

    assert result is True
    item = db.query(SourceItem).filter_by(source_url=url).first()
    assert item is not None
    assert "Cognetti" in item.title
    assert item.source_type == "news"
    assert item.source_name == "Times-Tribune"


@patch("app.services.ingestion_crawler.trafilatura.fetch_url", return_value=None)
def test_crawl_url_returns_false_on_empty_response(mock_fetch, db):
    result = crawl_url(db, "https://thetimes-tribune.com/dead-link", source_type="news")
    assert result is False
    assert db.query(SourceItem).count() == 0


@patch("app.services.ingestion_crawler.trafilatura.extract", return_value="Short.")
@patch("app.services.ingestion_crawler.trafilatura.extract_metadata", return_value=_FAKE_META)
@patch("app.services.ingestion_crawler.trafilatura.fetch_url", return_value=_SAMPLE_HTML)
def test_crawl_url_skips_thin_content(mock_fetch, mock_meta, mock_extract, db):
    result = crawl_url(db, "https://thetimes-tribune.com/thin", source_type="news")
    assert result is False


@patch("app.services.ingestion_crawler.trafilatura.extract", return_value=_LONG_TEXT)
@patch("app.services.ingestion_crawler.trafilatura.extract_metadata", return_value=_FAKE_META)
@patch("app.services.ingestion_crawler.trafilatura.fetch_url", return_value=_SAMPLE_HTML)
@patch("app.services.campaign_analysis.analyze_with_frames")
def test_crawl_url_deduplicates(mock_analyze, mock_fetch, mock_meta, mock_extract, db):
    mock_analyze.return_value = {
        "relevant": False, "relevance_score": 10, "one_sentence": ".", "framing": "neutral",
        "sentiment": "neutral", "needs_attention": False, "reason": "", "opponent_attacks": [], "frame_matches": [],
    }
    url = "https://thetimes-tribune.com/dup-article"
    crawl_url(db, url, source_type="news")
    result = crawl_url(db, url, source_type="news")
    assert result is False
    assert db.query(SourceItem).count() == 1


@patch("app.services.ingestion_crawler.trafilatura.extract", return_value=_LONG_TEXT)
@patch("app.services.ingestion_crawler.trafilatura.extract_metadata", return_value=_FAKE_META)
@patch("app.services.ingestion_crawler.trafilatura.fetch_url", return_value=_SAMPLE_HTML)
@patch("app.services.campaign_analysis.analyze_with_frames")
def test_crawl_url_sets_outlet_id(mock_analyze, mock_fetch, mock_meta, mock_extract, db):
    mock_analyze.return_value = {
        "relevant": True, "relevance_score": 75, "one_sentence": "Cognetti plan.", "framing": "positive",
        "sentiment": "positive", "needs_attention": False, "reason": "", "opponent_attacks": [], "frame_matches": [],
    }
    outlet = Outlet(name="Times-Tribune", domain="thetimes-tribune.com", outlet_type="local_news",
                    state="PA", city="Scranton", authority_score=9)
    db.add(outlet)
    db.commit()

    url = "https://thetimes-tribune.com/outlet-id-test"
    crawl_url(db, url, source_type="news", outlet_id=outlet.id)
    item = db.query(SourceItem).filter_by(source_url=url).first()
    assert item is not None
    assert item.outlet_id == outlet.id
