"""Unit tests for backend services."""
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Issue, IssueMention, SourceItem, Opponent, OpponentActivity


# ── In-memory DB fixture ──────────────────────────────────────────────────────

@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def _source(db, title="Test", raw_text="", urgency="low"):
    s = SourceItem(
        title=title,
        raw_text=raw_text,
        source_name="test",
        source_type="news",
        published_at=datetime.utcnow(),
        urgency=urgency,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


# ── issue_clustering tests ────────────────────────────────────────────────────

class TestIssueClustering:
    def test_taxonomy_match(self):
        from app.services.issue_clustering import _match_taxonomy
        matches = _match_taxonomy("rents are up and housing is unaffordable")
        names = [m[0] for m in matches]
        assert "Housing & Affordability" in names

    def test_taxonomy_no_match(self):
        from app.services.issue_clustering import _match_taxonomy
        matches = _match_taxonomy("the weather was nice today")
        assert matches == []

    def test_urgency_bump(self):
        from app.services.issue_clustering import _match_taxonomy
        matches = _match_taxonomy("homeless people were evicted from downtown")
        housing = next(m for m in matches if m[0] == "Housing & Affordability")
        assert housing[1] is True  # has urgency bump

    def test_auto_create_issue(self, db):
        from app.services.issue_clustering import _get_or_create_issue
        issue = _get_or_create_issue(db, "Housing & Affordability")
        assert issue.id is not None
        assert issue.name == "Housing & Affordability"
        # Second call returns the same row
        issue2 = _get_or_create_issue(db, "Housing & Affordability")
        assert issue.id == issue2.id

    def test_assign_increments_mention_count(self, db):
        from app.services.issue_clustering import assign_issues_to_source
        s = _source(db, title="Rent Crisis", raw_text="Rent and housing are unaffordable")
        issues = assign_issues_to_source(db, s)
        housing = next(i for i in issues if i.name == "Housing & Affordability")
        assert housing.mention_count == 1

    def test_no_double_count_on_reingest(self, db):
        from app.services.issue_clustering import assign_issues_to_source
        s = _source(db, title="Rent Crisis", raw_text="Rent and housing are unaffordable")
        assign_issues_to_source(db, s)
        assign_issues_to_source(db, s)  # second call on same source
        db.expire_all()
        issue = db.query(Issue).filter_by(name="Housing & Affordability").first()
        assert issue.mention_count == 1  # not double-counted

    def test_trend_rising(self, db):
        from app.services.issue_clustering import assign_issues_to_source, _update_trend
        # Add 5 recent sources and 1 prior source
        for i in range(5):
            s = SourceItem(
                title=f"Rent story {i}", raw_text="rent housing",
                source_name="t", source_type="news",
                published_at=datetime.utcnow() - timedelta(days=1),
                urgency="low",
            )
            db.add(s)
        db.commit()
        # One source 10 days ago
        old = SourceItem(
            title="Old rent story", raw_text="rent housing",
            source_name="t", source_type="news",
            published_at=datetime.utcnow() - timedelta(days=10),
            urgency="low",
        )
        db.add(old)
        db.commit()

        db.refresh(old)
        issue = Issue(name="Housing & Affordability", urgency="low", mention_count=0, trend="stable")
        db.add(issue)
        db.flush()

        # Create mention links
        for s in db.query(SourceItem).all():
            db.add(IssueMention(issue_id=issue.id, source_item_id=s.id))
        db.commit()

        _update_trend(db, issue)
        assert issue.trend == "rising"


# ── opponent_analysis tests ───────────────────────────────────────────────────

class TestOpponentAnalysis:
    def test_classify_attack(self):
        from app.services.opponent_analysis import _classify_sentence
        result = _classify_sentence("Harmon falsely claimed crime fell.", "Harmon")
        assert result is not None
        assert result["is_attack"] is True

    def test_classify_claim(self):
        from app.services.opponent_analysis import _classify_sentence
        result = _classify_sentence("Harmon says the budget is balanced.", "Harmon")
        assert result is not None
        assert result["is_claim"] is True

    def test_classify_promise(self):
        from app.services.opponent_analysis import _classify_sentence
        result = _classify_sentence("Harmon promised to cut taxes next year.", "Harmon")
        assert result is not None
        assert result["is_promise"] is True

    def test_no_match_without_name(self):
        from app.services.opponent_analysis import _classify_sentence
        result = _classify_sentence("He falsely claimed crime fell.", "Harmon")
        assert result is None

    def test_extract_activities_deduplicates(self):
        from app.services.opponent_analysis import _extract_activities
        text = "Harmon falsely claimed the budget is balanced. Harmon falsely claimed the budget is balanced."
        activities = _extract_activities(text, "Harmon")
        assert len(activities) == 1

    def test_analyze_source_creates_activity(self, db):
        from app.services.opponent_analysis import analyze_source_for_opponents
        opp = Opponent(name="Harmon", office="Council", party="R")
        db.add(opp)
        db.commit()

        s = _source(db, title="Harmon falsely claimed crime is down.", raw_text="")
        activities = analyze_source_for_opponents(db, s)
        assert len(activities) >= 1
        assert activities[0].attack is not None

    def test_no_duplicate_activities(self, db):
        from app.services.opponent_analysis import analyze_source_for_opponents
        opp = Opponent(name="Harmon", office="Council", party="R")
        db.add(opp)
        db.commit()

        s = _source(db, title="Harmon falsely claimed crime is down.", raw_text="")
        analyze_source_for_opponents(db, s)
        analyze_source_for_opponents(db, s)  # second pass
        count = db.query(OpponentActivity).filter_by(source_item_id=s.id).count()
        assert count == 1  # not doubled


# ── ingestion tests ───────────────────────────────────────────────────────────

class TestIngestion:
    def test_ingest_text(self, db):
        from app.services.ingestion import ingest_text
        item = ingest_text(db, "Test Title", "Housing is expensive.", "TestSource", "news")
        assert item.id is not None
        assert item.title == "Test Title"
        assert item.summary is not None

    def test_ingest_text_with_url(self, db):
        from app.services.ingestion import ingest_text
        item = ingest_text(db, "Test", "text", "src", "news", source_url="https://example.com/1")
        assert item.source_url == "https://example.com/1"

    def test_ingest_url_dedup(self, db):
        from app.services.ingestion import ingest_url
        # Create existing item with the URL
        existing = SourceItem(
            title="Existing", raw_text="",
            source_name="x", source_type="news",
            source_url="https://example.com/dedup",
            published_at=datetime.utcnow(),
        )
        db.add(existing)
        db.commit()
        db.refresh(existing)

        result = ingest_url(db, "https://example.com/dedup", "news")
        assert result is not None
        assert result.id == existing.id

    def test_ingest_rss_returns_result(self, db):
        from app.services.ingestion import RSSIngestResult, ingest_rss
        import unittest.mock as mock
        import feedparser

        fake_feed = MagicMock()
        fake_feed.entries = []
        fake_feed.feed = MagicMock()
        fake_feed.feed.get = lambda k, d=None: "Test Feed"

        with mock.patch("feedparser.parse", return_value=fake_feed):
            result = ingest_rss(db, "http://fake.com/feed.xml")

        assert isinstance(result, RSSIngestResult)
        assert result.added == 0
        assert result.skipped == 0
        assert result.items == []


# ── HTML cleaning tests ───────────────────────────────────────────────────────

class TestHTMLCleaning:
    def test_strips_tags(self):
        from app.services.ingestion import _clean_html
        title, body = _clean_html("<html><head><title>Hello</title></head><body><p>World</p></body></html>")
        assert title == "Hello"
        assert "World" in body
        assert "<" not in body

    def test_removes_script_tags(self):
        from app.services.ingestion import _clean_html
        _, body = _clean_html("<p>Content</p><script>evil()</script><p>More</p>")
        assert "evil" not in body
        assert "Content" in body

    def test_decodes_entities(self):
        from app.services.ingestion import _clean_html
        _, body = _clean_html("<p>Price &amp; Value &lt;tag&gt;</p>")
        assert "&amp;" not in body
        assert "&" in body

    def test_decodes_hex_entities(self):
        from app.services.ingestion import _normalize_text
        result = _normalize_text('Rob &#x201c;test&#x201d;')
        assert result == 'Rob “test”', repr(result)

    def test_normalize_text_idempotent(self):
        from app.services.ingestion import _normalize_text
        clean = "Rob “test” already clean"
        assert _normalize_text(clean) == clean


# ── LLM provider / intelligence tests ────────────────────────────────────────

class TestMockLLMProvider:
    def test_summarize_short_text(self):
        from app.services.llm_provider import MockLLMProvider
        p = MockLLMProvider()
        result = p.summarize("Short text.", max_words=80)
        assert result == "Short text."

    def test_summarize_truncates_long_text(self):
        from app.services.llm_provider import MockLLMProvider
        p = MockLLMProvider()
        long_text = " ".join(["word"] * 200)
        result = p.summarize(long_text, max_words=10)
        assert result.endswith("...")
        assert len(result.split()) <= 11

    def test_classify_urgency_high(self):
        from app.services.llm_provider import MockLLMProvider
        p = MockLLMProvider()
        assert p.classify_urgency("this is a fabricat attack on our campaign") == "high"

    def test_classify_urgency_low(self):
        from app.services.llm_provider import MockLLMProvider
        p = MockLLMProvider()
        assert p.classify_urgency("weather is nice today") == "low"

    def test_generate_talking_points_known_issue(self):
        from app.services.llm_provider import MockLLMProvider
        p = MockLLMProvider()
        result = p.generate_talking_points("Housing & Affordability", "calm")
        assert "short_answer" in result
        assert "34%" in result["short_answer"]

    def test_generate_talking_points_aggressive_tone(self):
        from app.services.llm_provider import MockLLMProvider
        p = MockLLMProvider()
        result = p.generate_talking_points("Housing & Affordability", "aggressive")
        assert result["short_answer"].startswith("My opponent has failed")
