"""Tests for published_at handling in ingestion helpers."""
import time
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


# ── _parse_html_published_date ────────────────────────────────────────────────

class TestParseHtmlPublishedDate:
    def _fn(self):
        from app.services.ingestion import _parse_html_published_date
        return _parse_html_published_date

    def test_article_published_time_property(self):
        html = '<meta property="article:published_time" content="2024-03-15T10:30:00Z">'
        dt = self._fn()(html)
        assert dt is not None
        assert dt == datetime(2024, 3, 15, 10, 30, 0)
        assert dt.tzinfo is None  # stored as naive UTC

    def test_og_published_time_property(self):
        html = '<meta property="og:published_time" content="2024-06-01T08:00:00Z">'
        dt = self._fn()(html)
        assert dt is not None
        assert dt.year == 2024 and dt.month == 6

    def test_pubdate_name(self):
        html = '<meta name="pubdate" content="2024-05-20">'
        dt = self._fn()(html)
        assert dt is not None
        assert dt.year == 2024 and dt.month == 5 and dt.day == 20

    def test_itemprop_datepublished(self):
        html = '<meta itemprop="datePublished" content="2024-01-10T12:00:00Z">'
        dt = self._fn()(html)
        assert dt is not None
        assert dt.year == 2024 and dt.month == 1

    def test_timezone_offset_converted_to_utc(self):
        # +05:00 offset → 08:00 - 05:00 = 03:00 UTC
        html = '<meta property="article:published_time" content="2024-04-01T08:00:00+05:00">'
        dt = self._fn()(html)
        assert dt is not None
        assert dt.hour == 3
        assert dt.tzinfo is None

    def test_negative_timezone_offset(self):
        # -05:00 offset → 12:00 + 05:00 = 17:00 UTC
        html = '<meta property="article:published_time" content="2024-04-01T12:00:00-05:00">'
        dt = self._fn()(html)
        assert dt is not None
        assert dt.hour == 17

    def test_jsonld_date_published(self):
        html = (
            '<script type="application/ld+json">'
            '{"@type":"NewsArticle","datePublished":"2024-02-28T09:15:00Z"}'
            '</script>'
        )
        dt = self._fn()(html)
        assert dt is not None
        assert dt.year == 2024 and dt.month == 2 and dt.day == 28

    def test_time_element_datetime_attribute(self):
        html = '<time datetime="2024-07-04T00:00:00Z">July 4, 2024</time>'
        dt = self._fn()(html)
        assert dt is not None
        assert dt.year == 2024 and dt.month == 7 and dt.day == 4

    def test_time_element_date_only(self):
        html = '<time datetime="2024-09-15">September 15</time>'
        dt = self._fn()(html)
        assert dt is not None
        assert dt.year == 2024 and dt.month == 9 and dt.day == 15

    def test_no_date_metadata_returns_none(self):
        html = (
            '<html><head><title>Article</title></head>'
            '<body><p>Some content here.</p></body></html>'
        )
        dt = self._fn()(html)
        assert dt is None

    def test_meta_without_date_properties_returns_none(self):
        html = (
            '<meta name="description" content="An article about things">'
            '<meta property="og:title" content="My Article">'
        )
        dt = self._fn()(html)
        assert dt is None

    def test_priority_order_prefers_article_published_time(self):
        # article:published_time should win over a later og:published_time
        html = (
            '<meta property="article:published_time" content="2024-01-01T00:00:00Z">'
            '<meta property="og:published_time" content="2025-06-06T00:00:00Z">'
        )
        dt = self._fn()(html)
        assert dt is not None
        assert dt.year == 2024  # first match wins


# ── _rss_published_at ─────────────────────────────────────────────────────────

class TestRssPublishedAt:
    def _fn(self):
        from app.services.ingestion import _rss_published_at
        return _rss_published_at

    def test_utc_struct_time_converted_directly(self):
        # struct_time fields represent UTC values (feedparser guarantee).
        # 2024-03-15 14:30:00 UTC
        struct = time.strptime("2024-03-15 14:30:00", "%Y-%m-%d %H:%M:%S")
        dt = self._fn()(struct)
        assert dt == datetime(2024, 3, 15, 14, 30, 0)
        assert dt.tzinfo is None

    def test_midnight_utc(self):
        struct = time.strptime("2024-01-01 00:00:00", "%Y-%m-%d %H:%M:%S")
        dt = self._fn()(struct)
        assert dt == datetime(2024, 1, 1, 0, 0, 0)

    def test_none_input_returns_none(self):
        assert self._fn()(None) is None

    def test_false_input_returns_none(self):
        assert self._fn()(False) is None

    def test_result_is_naive(self):
        struct = time.strptime("2024-06-15 12:00:00", "%Y-%m-%d %H:%M:%S")
        dt = self._fn()(struct)
        assert dt is not None
        assert dt.tzinfo is None


# ── ingest_url: published_at integration ─────────────────────────────────────

def _make_mock_response(html: str) -> MagicMock:
    resp = MagicMock()
    resp.headers = {"content-type": "text/html"}
    resp.text = html
    resp.raise_for_status = MagicMock()
    return resp


_ARTICLE_BODY = (
    "The candidate announced a major policy proposal at a campaign event "
    "in the district on Tuesday. The election race has drawn national attention "
    "with both candidates making their case to voters ahead of the primary ballot. "
    "Local community members expressed support for the initiative during the town "
    "hall meeting. According to campaign officials, the plan would be implemented "
    "within the first six months of taking office if elected in the general election."
)

_HTML_NO_DATE = f"""
<html>
<head><title>Test Article No Date</title></head>
<body><article><p>{_ARTICLE_BODY}</p></article></body>
</html>
"""

_HTML_WITH_DATE = f"""
<html>
<head>
  <title>Test Article With Date</title>
  <meta property="article:published_time" content="2024-03-15T10:30:00Z">
</head>
<body><article><p>{_ARTICLE_BODY}</p></article></body>
</html>
"""


class TestIngestUrlPublishedAt:
    def _run_ingest(self, db, html: str, url: str = "https://example.com/article"):
        from app.services import ingestion

        mock_resp = _make_mock_response(html)
        with patch("app.services.ingestion.httpx.get", return_value=mock_resp):
            with patch("app.services.ingestion.intelligence.summarize_source", return_value="test summary"):
                with patch("app.services.ingestion.intelligence.classify_urgency", return_value="low"):
                    with patch("app.services.ingestion.narratives.refresh_narratives"):
                        return ingestion.ingest_url(db, url, "news")

    def test_no_date_in_html_leaves_published_at_none(self, db):
        """Key requirement: published_at must be NULL, not ingestion time, when no date is in HTML."""
        item = self._run_ingest(db, _HTML_NO_DATE)
        assert item is not None
        assert item.published_at is None, (
            f"Expected published_at to be None but got {item.published_at!r}. "
            "ingest_url() must not default to utcnow() when no publication date is found."
        )

    def test_date_in_html_sets_published_at(self, db):
        """When article:published_time is present, published_at is extracted correctly."""
        item = self._run_ingest(db, _HTML_WITH_DATE, url="https://example.com/article-dated")
        assert item is not None
        assert item.published_at is not None
        assert item.published_at.year == 2024
        assert item.published_at.month == 3
        assert item.published_at.day == 15
        assert item.published_at.hour == 10

    def test_created_at_is_always_set(self, db):
        """created_at must always reflect ingestion time regardless of published_at."""
        before = datetime.utcnow()
        item = self._run_ingest(db, _HTML_NO_DATE, url="https://example.com/article-created")
        after = datetime.utcnow()
        assert item is not None
        assert item.created_at is not None
        assert before <= item.created_at <= after

    def test_published_at_not_equal_to_ingestion_time(self, db):
        """Sanity check: even if we somehow get a date, it should not equal right-now."""
        item = self._run_ingest(db, _HTML_WITH_DATE, url="https://example.com/article-not-now")
        assert item is not None
        if item.published_at is not None:
            age_days = (datetime.utcnow() - item.published_at).days
            assert age_days > 30, (
                "Published date from HTML should be significantly in the past, "
                f"not recent ingestion time (age: {age_days} days)"
            )
