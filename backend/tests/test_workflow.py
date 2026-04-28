"""Tests for workflow features: setup checklist, RSS feeds, review queue,
issue-source linking, and talking point history."""
import json
import pytest
from datetime import datetime
from unittest.mock import patch, MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import (
    CampaignConfig, SourceItem, Issue, IssueMention, Opponent,
    RssFeed, GeneratedTalkingPoint, OpponentActivity,
)


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


def _source(db, title="Test Source", urgency="low", reviewed=False, dismissed=False, priority_score=0):
    s = SourceItem(
        title=title, raw_text="test content", source_name="test",
        source_type="news", urgency=urgency, published_at=datetime.utcnow(),
        reviewed=reviewed, dismissed=dismissed, priority_score=priority_score,
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


def _issue(db, name="Test Issue", urgency="low"):
    i = Issue(name=name, urgency=urgency, trend="stable", mention_count=1, last_seen_at=datetime.utcnow())
    db.add(i)
    db.commit()
    db.refresh(i)
    return i


def _campaign(db, **kwargs):
    defaults = {
        "candidate_name": "Test Candidate",
        "office": "Mayor",
        "district": "Downtown",
        "campaign_message": "A better tomorrow",
        "election_date": datetime(2026, 11, 3),
    }
    defaults.update(kwargs)
    c = CampaignConfig(**defaults)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


# ── Setup checklist ───────────────────────────────────────────────────────────

class TestSetupStatus:
    def test_empty_db_all_incomplete(self, db):
        from app.routes.setup import get_setup_status
        result = get_setup_status(db=db)
        assert not result.complete
        assert all(not item.complete for item in result.items)

    def test_profile_complete_when_all_fields_set(self, db):
        from app.routes.setup import get_setup_status
        _campaign(db)
        result = get_setup_status(db=db)
        profile_item = next(i for i in result.items if i.id == "campaign_profile")
        assert profile_item.complete

    def test_profile_incomplete_without_message(self, db):
        from app.routes.setup import get_setup_status
        _campaign(db, campaign_message=None)
        result = get_setup_status(db=db)
        profile_item = next(i for i in result.items if i.id == "campaign_profile")
        assert not profile_item.complete

    def test_opponent_item_complete_when_opponent_exists(self, db):
        from app.routes.setup import get_setup_status
        db.add(Opponent(name="Roy Harmon"))
        db.commit()
        result = get_setup_status(db=db)
        opp_item = next(i for i in result.items if i.id == "opponent_added")
        assert opp_item.complete

    def test_source_item_complete_when_source_exists(self, db):
        from app.routes.setup import get_setup_status
        _source(db)
        result = get_setup_status(db=db)
        src_item = next(i for i in result.items if i.id == "source_added")
        assert src_item.complete

    def test_issue_item_complete_when_issue_exists(self, db):
        from app.routes.setup import get_setup_status
        _issue(db)
        result = get_setup_status(db=db)
        issue_item = next(i for i in result.items if i.id == "issue_detected")
        assert issue_item.complete

    def test_talking_point_item_complete_when_history_exists(self, db):
        from app.routes.setup import get_setup_status
        db.add(GeneratedTalkingPoint(
            issue_name="Housing", tone="calm",
            short_answer="a", long_answer="b", debate_answer="c",
            social_post="d", evidence_notes="e",
        ))
        db.commit()
        result = get_setup_status(db=db)
        tp_item = next(i for i in result.items if i.id == "talking_point_generated")
        assert tp_item.complete

    def test_all_complete_when_everything_set(self, db):
        from app.routes.setup import get_setup_status
        _campaign(db)
        db.add(Opponent(name="Opp"))
        _source(db)
        _issue(db)
        db.add(GeneratedTalkingPoint(
            issue_name="X", tone="calm",
            short_answer="a", long_answer="b", debate_answer="c",
            social_post="d", evidence_notes="e",
        ))
        db.commit()
        result = get_setup_status(db=db)
        assert result.complete

    def test_each_item_has_action_path(self, db):
        from app.routes.setup import get_setup_status
        result = get_setup_status(db=db)
        for item in result.items:
            assert item.action_path.startswith("/"), f"{item.id} missing leading slash"


# ── RSS feed management ───────────────────────────────────────────────────────

class TestRssFeeds:
    def test_create_feed(self, db):
        feed = RssFeed(name="Local News", url="https://example.com/feed.rss", source_type="news")
        db.add(feed)
        db.commit()
        db.refresh(feed)
        assert feed.id is not None
        assert feed.active is True
        assert feed.last_fetched_at is None

    def test_url_unique_constraint(self, db):
        from sqlalchemy.exc import IntegrityError
        db.add(RssFeed(name="Feed A", url="https://example.com/rss"))
        db.commit()
        db.add(RssFeed(name="Feed B", url="https://example.com/rss"))
        with pytest.raises(IntegrityError):
            db.commit()

    def test_create_feed_endpoint_409_on_duplicate(self, db):
        from fastapi import HTTPException
        from app.routes.rss_feeds import create_feed
        from app.schemas import RssFeedCreate
        body = RssFeedCreate(name="Feed", url="https://dup.com/rss")
        create_feed(body=body, db=db)
        with pytest.raises(HTTPException) as exc:
            create_feed(body=body, db=db)
        assert exc.value.status_code == 409

    def test_update_feed_toggles_active(self, db):
        from app.routes.rss_feeds import update_feed
        from app.schemas import RssFeedUpdate
        feed = RssFeed(name="Feed", url="https://example.com/rss", active=True)
        db.add(feed)
        db.commit()
        db.refresh(feed)
        updated = update_feed(feed_id=feed.id, body=RssFeedUpdate(active=False), db=db)
        assert updated.active is False

    def test_delete_feed(self, db):
        from app.routes.rss_feeds import delete_feed
        feed = RssFeed(name="Feed", url="https://example.com/rss")
        db.add(feed)
        db.commit()
        db.refresh(feed)
        delete_feed(feed_id=feed.id, db=db)
        assert db.get(RssFeed, feed.id) is None

    def test_delete_feed_does_not_delete_sources(self, db):
        from app.routes.rss_feeds import delete_feed
        feed = RssFeed(name="Feed", url="https://example.com/rss")
        db.add(feed)
        s = _source(db)
        db.commit()
        db.refresh(feed)
        delete_feed(feed_id=feed.id, db=db)
        assert db.get(SourceItem, s.id) is not None

    def test_ingest_skips_duplicate_urls(self, db):
        from unittest.mock import patch, MagicMock
        from app.routes.rss_feeds import ingest_feed

        feed = RssFeed(name="Test Feed", url="https://example.com/feed.rss")
        db.add(feed)
        db.commit()
        db.refresh(feed)

        # Pre-seed a source with the same URL as what RSS would return
        existing = SourceItem(
            title="Existing", raw_text="x", source_name="test",
            source_type="news", urgency="low",
            source_url="https://example.com/article1",
            published_at=datetime.utcnow(),
        )
        db.add(existing)
        db.commit()

        mock_entry = MagicMock()
        mock_entry.get = lambda k, d=None: {
            "link": "https://example.com/article1",
            "title": "Article 1",
            "summary": "some content",
        }.get(k, d)
        mock_entry.published_parsed = None

        mock_feed = MagicMock()
        mock_feed.entries = [mock_entry]
        mock_feed.feed.get = lambda k, d=None: d

        with patch("app.services.ingestion.feedparser.parse", return_value=mock_feed):
            result = ingest_feed(feed_id=feed.id, db=db)

        assert result.skipped_count == 1
        assert result.added_count == 0

    def test_ingest_updates_last_fetched_at(self, db):
        from unittest.mock import patch, MagicMock
        from app.routes.rss_feeds import ingest_feed

        feed = RssFeed(name="Feed", url="https://example.com/feed.rss")
        db.add(feed)
        db.commit()
        db.refresh(feed)
        assert feed.last_fetched_at is None

        mock_feed = MagicMock()
        mock_feed.entries = []
        mock_feed.feed.get = lambda k, d=None: d

        with patch("app.services.ingestion.feedparser.parse", return_value=mock_feed):
            ingest_feed(feed_id=feed.id, db=db)

        db.refresh(feed)
        assert feed.last_fetched_at is not None


# ── Review queue ──────────────────────────────────────────────────────────────

class TestReviewQueue:
    def test_unreviewed_items_appear_in_queue(self, db):
        from app.routes.review_queue import get_review_queue
        _source(db, title="Unreviewed")
        results = get_review_queue(db=db)
        assert any(r.title == "Unreviewed" for r in results)

    def test_reviewed_items_excluded(self, db):
        from app.routes.review_queue import get_review_queue
        _source(db, title="Already Reviewed", reviewed=True)
        results = get_review_queue(db=db)
        assert not any(r.title == "Already Reviewed" for r in results)

    def test_dismissed_items_excluded(self, db):
        from app.routes.review_queue import get_review_queue
        _source(db, title="Dismissed", dismissed=True)
        results = get_review_queue(db=db)
        assert not any(r.title == "Dismissed" for r in results)

    def test_mark_reviewed(self, db):
        from app.routes.review_queue import mark_reviewed
        from app.schemas import ReviewAction
        s = _source(db)
        result = mark_reviewed(source_id=s.id, body=ReviewAction(review_note="Looks fine"), db=db)
        assert result.reviewed is True
        assert result.review_note == "Looks fine"

    def test_dismiss_item(self, db):
        from app.routes.review_queue import dismiss_item
        from app.schemas import ReviewAction
        s = _source(db)
        result = dismiss_item(source_id=s.id, body=ReviewAction(), db=db)
        assert result.dismissed is True

    def test_set_priority(self, db):
        from app.routes.review_queue import set_priority
        from app.schemas import PriorityUpdate
        s = _source(db)
        result = set_priority(source_id=s.id, body=PriorityUpdate(priority_score=50), db=db)
        assert result.priority_score == 50

    def test_queue_sorted_by_priority_desc(self, db):
        from app.routes.review_queue import get_review_queue
        _source(db, title="Low", priority_score=5)
        _source(db, title="High", priority_score=99)
        _source(db, title="Med", priority_score=42)
        results = get_review_queue(db=db)
        scores = [r.priority_score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_related_issue_names_populated(self, db):
        from app.routes.review_queue import get_review_queue
        s = _source(db)
        i = _issue(db, name="Housing")
        db.add(IssueMention(issue_id=i.id, source_item_id=s.id))
        db.commit()
        results = get_review_queue(db=db)
        item = next(r for r in results if r.id == s.id)
        assert "Housing" in item.related_issue_names

    def test_opponent_attack_count_populated(self, db):
        from app.routes.review_queue import get_review_queue
        s = _source(db)
        opp = Opponent(name="Opp")
        db.add(opp)
        db.flush()
        db.add(OpponentActivity(
            opponent_id=opp.id, source_item_id=s.id,
            attack="Attack text", created_at=datetime.utcnow(),
        ))
        db.commit()
        results = get_review_queue(db=db)
        item = next(r for r in results if r.id == s.id)
        assert item.opponent_attack_count == 1

    def test_404_on_unknown_source(self, db):
        from fastapi import HTTPException
        from app.routes.review_queue import mark_reviewed
        from app.schemas import ReviewAction
        with pytest.raises(HTTPException) as exc:
            mark_reviewed(source_id=99999, body=ReviewAction(), db=db)
        assert exc.value.status_code == 404


# ── Issue-source linking ──────────────────────────────────────────────────────

class TestIssueSourceLinking:
    def test_issue_detail_includes_sources(self, db):
        from app.routes.issues import get_issue
        i = _issue(db, name="Housing")
        s = _source(db, title="Rent article")
        db.add(IssueMention(issue_id=i.id, source_item_id=s.id))
        db.commit()
        result = get_issue(issue_id=i.id, db=db)
        assert any(src.title == "Rent article" for src in result.recent_sources)

    def test_issue_detail_excludes_unlinked_sources(self, db):
        from app.routes.issues import get_issue
        i = _issue(db, name="Housing")
        _source(db, title="Unlinked source")
        result = get_issue(issue_id=i.id, db=db)
        assert all(src.title != "Unlinked source" for src in result.recent_sources)

    def test_ingestion_links_issue_via_clustering(self, db):
        from app.services.ingestion import ingest_text
        # Create an issue with a known keyword
        housing = Issue(name="Housing & Affordability", urgency="low", mention_count=0,
                        trend="stable", last_seen_at=datetime.utcnow())
        db.add(housing)
        db.commit()
        item = ingest_text(
            db, title="Rent Crisis",
            raw_text="Housing costs are rising and rent affordability is a serious problem.",
            source_name="Test", source_type="news",
        )
        links = db.query(IssueMention).filter_by(source_item_id=item.id).all()
        assert len(links) > 0


# ── Talking point history ─────────────────────────────────────────────────────

class TestTalkingPointHistory:
    def test_generate_saves_to_history(self, db):
        from app.routes.talking_points import generate_talking_points
        from app.schemas import TalkingPointRequest
        issue = _issue(db, name="Housing & Affordability")
        db.add(CampaignConfig(candidate_name="Test", office="Mayor", district="D1"))
        db.commit()

        generate_talking_points(
            body=TalkingPointRequest(issue_id=issue.id, tone="calm", output_format="all"),
            db=db,
        )

        saved = db.query(GeneratedTalkingPoint).first()
        assert saved is not None
        assert saved.issue_name == "Housing & Affordability"
        assert saved.tone == "calm"

    def test_history_stores_tone(self, db):
        from app.routes.talking_points import generate_talking_points
        from app.schemas import TalkingPointRequest
        issue = _issue(db, name="Housing & Affordability")
        db.add(CampaignConfig(candidate_name="Test", office="Mayor", district="D1"))
        db.commit()

        generate_talking_points(
            body=TalkingPointRequest(issue_id=issue.id, tone="aggressive", output_format="all"),
            db=db,
        )

        saved = db.query(GeneratedTalkingPoint).first()
        assert saved.tone == "aggressive"

    def test_custom_issue_saved_to_history(self, db):
        from app.routes.talking_points import generate_talking_points
        from app.schemas import TalkingPointRequest
        db.add(CampaignConfig(candidate_name="Test", office="Mayor", district="D1"))
        db.commit()

        generate_talking_points(
            body=TalkingPointRequest(custom_issue_text="Water quality", tone="calm", output_format="all"),
            db=db,
        )

        saved = db.query(GeneratedTalkingPoint).first()
        assert saved.issue_name == "Water quality"
        assert saved.issue_id is None

    def test_source_lists_stored_as_json(self, db):
        from app.routes.talking_points import generate_talking_points
        from app.schemas import TalkingPointRequest
        db.add(CampaignConfig(candidate_name="Test", office="Mayor", district="D1"))
        db.commit()

        generate_talking_points(
            body=TalkingPointRequest(custom_issue_text="Schools", tone="calm", output_format="all"),
            db=db,
        )

        saved = db.query(GeneratedTalkingPoint).first()
        assert isinstance(saved.source_titles_used, str)
        parsed = json.loads(saved.source_titles_used)
        assert isinstance(parsed, list)

    def test_history_endpoint_returns_list(self, db):
        from app.routes.talking_points import get_history
        db.add(GeneratedTalkingPoint(
            issue_name="Housing", tone="calm",
            short_answer="a", long_answer="b", debate_answer="c",
            social_post="d", evidence_notes="e",
            source_titles_used="[]", source_urls_used="[]",
        ))
        db.commit()
        results = get_history(limit=20, db=db)
        assert len(results) == 1
        assert results[0].issue_name == "Housing"

    def test_history_sorted_newest_first(self, db):
        from app.routes.talking_points import get_history
        from datetime import timedelta
        old = GeneratedTalkingPoint(
            issue_name="Old", tone="calm",
            short_answer="a", long_answer="b", debate_answer="c",
            social_post="d", evidence_notes="e",
            source_titles_used="[]", source_urls_used="[]",
            created_at=datetime.utcnow() - timedelta(hours=2),
        )
        new = GeneratedTalkingPoint(
            issue_name="New", tone="calm",
            short_answer="a", long_answer="b", debate_answer="c",
            social_post="d", evidence_notes="e",
            source_titles_used="[]", source_urls_used="[]",
            created_at=datetime.utcnow(),
        )
        db.add_all([old, new])
        db.commit()
        results = get_history(limit=20, db=db)
        assert results[0].issue_name == "New"
        assert results[1].issue_name == "Old"

    def test_history_item_schema_parses_json_lists(self, db):
        from app.schemas import GeneratedTalkingPointOut
        gtp = GeneratedTalkingPoint(
            issue_name="Housing", tone="calm",
            short_answer="a", long_answer="b", debate_answer="c",
            social_post="d", evidence_notes="e",
            source_titles_used='["Source A", "Source B"]',
            source_urls_used='["https://a.com", null]',
        )
        db.add(gtp)
        db.commit()
        db.refresh(gtp)
        out = GeneratedTalkingPointOut.model_validate(gtp)
        assert out.source_titles_used == ["Source A", "Source B"]


# ── Priority scoring ──────────────────────────────────────────────────────────

class TestPriorityScoring:
    def test_high_urgency_gets_high_score(self, db):
        from app.services.ingestion import ingest_text
        item = ingest_text(
            db, title="URGENT: Housing crisis",
            raw_text="Housing affordability is an urgent high priority crisis.",
            source_name="test", source_type="news",
        )
        assert item.priority_score > 0

    def test_priority_score_set_on_ingest(self, db):
        from app.services.ingestion import ingest_text
        item = ingest_text(
            db, title="Low importance notice",
            raw_text="A brief administrative notice.",
            source_name="test", source_type="news",
        )
        assert item.priority_score is not None


# ── Bulk review/dismiss ───────────────────────────────────────────────────────

class TestBulkActions:
    def test_bulk_review(self, db):
        from app.routes.review_queue import bulk_review
        from app.schemas import BulkReviewAction
        s1 = _source(db, title="S1")
        s2 = _source(db, title="S2")
        s3 = _source(db, title="S3")
        result = bulk_review(body=BulkReviewAction(source_ids=[s1.id, s2.id]), db=db)
        assert result["updated"] == 2
        db.refresh(s1); db.refresh(s2); db.refresh(s3)
        assert s1.reviewed is True
        assert s2.reviewed is True
        assert s3.reviewed is False

    def test_bulk_dismiss(self, db):
        from app.routes.review_queue import bulk_dismiss
        from app.schemas import BulkReviewAction
        s1 = _source(db, title="D1")
        s2 = _source(db, title="D2")
        result = bulk_dismiss(body=BulkReviewAction(source_ids=[s1.id, s2.id]), db=db)
        assert result["updated"] == 2
        db.refresh(s1); db.refresh(s2)
        assert s1.dismissed is True
        assert s2.dismissed is True

    def test_bulk_review_with_note(self, db):
        from app.routes.review_queue import bulk_review
        from app.schemas import BulkReviewAction
        s = _source(db)
        bulk_review(body=BulkReviewAction(source_ids=[s.id], review_note="batch note"), db=db)
        db.refresh(s)
        assert s.review_note == "batch note"

    def test_bulk_review_ignores_missing_ids(self, db):
        from app.routes.review_queue import bulk_review
        from app.schemas import BulkReviewAction
        s = _source(db)
        result = bulk_review(body=BulkReviewAction(source_ids=[s.id, 99999]), db=db)
        assert result["updated"] == 1


# ── Evidence and credibility scoring ─────────────────────────────────────────

class TestScoringService:
    def test_evidence_score_increases_with_url(self, db):
        from app.services.scoring import compute_evidence_score
        s_no_url = SourceItem(title="T", source_type="news", raw_text="short")
        s_with_url = SourceItem(title="T", source_type="news", source_url="https://example.com", raw_text="short")
        assert compute_evidence_score(s_with_url) > compute_evidence_score(s_no_url)

    def test_evidence_score_numbers_boost(self, db):
        from app.services.scoring import compute_evidence_score
        s_no_nums = SourceItem(title="T", source_type="news", raw_text="Some text without data")
        s_with_nums = SourceItem(title="T", source_type="news", raw_text="Crime rate fell 12% in Q1 2025 according to report")
        assert compute_evidence_score(s_with_nums) > compute_evidence_score(s_no_nums)

    def test_credibility_score_public_record_high(self, db):
        from app.services.scoring import compute_credibility_score
        s_rec = SourceItem(title="T", source_type="public_record")
        s_soc = SourceItem(title="T", source_type="social")
        assert compute_credibility_score(s_rec) > compute_credibility_score(s_soc)

    def test_credibility_score_risk_words_deduct(self, db):
        from app.services.scoring import compute_credibility_score
        s_clean = SourceItem(title="Council passes housing measure", source_type="news")
        s_risky = SourceItem(title="Unverified rumor about alleged fabricated claim", source_type="news")
        assert compute_credibility_score(s_risky) < compute_credibility_score(s_clean)

    def test_scores_set_on_ingest(self, db):
        from app.services.ingestion import ingest_text
        item = ingest_text(
            db, title="Housing crisis update",
            raw_text="The housing market shows 15% rent increases in 2025.",
            source_name="Tribune", source_type="news",
            source_url="https://example.com/housing",
        )
        assert item.evidence_score is not None
        assert item.credibility_score is not None
        assert 0 <= item.evidence_score <= 100
        assert 0 <= item.credibility_score <= 100

    def test_scores_clamped_to_0_100(self, db):
        from app.services.scoring import compute_evidence_score, compute_credibility_score
        s = SourceItem(title="T", source_type="public_record",
                       source_url="https://x.com", source_name="City",
                       raw_text="In January 2025 the city recorded 1,234 housing units at $2,500/month")
        ev = compute_evidence_score(s)
        cr = compute_credibility_score(s)
        assert 0 <= ev <= 100
        assert 0 <= cr <= 100


# ── Source detail with related issues ────────────────────────────────────────

class TestSourceDetailRelatedIssues:
    def test_related_issues_populated(self, db):
        from app.routes.sources import get_source
        s = _source(db)
        i = _issue(db, name="Housing")
        db.add(IssueMention(issue_id=i.id, source_item_id=s.id))
        db.commit()
        detail = get_source(source_id=s.id, db=db)
        assert any(ri.name == "Housing" for ri in detail.related_issues)

    def test_related_issues_empty_when_none(self, db):
        from app.routes.sources import get_source
        s = _source(db)
        detail = get_source(source_id=s.id, db=db)
        assert detail.related_issues == []

    def test_related_issue_ids_in_review_queue(self, db):
        from app.routes.review_queue import get_review_queue
        s = _source(db)
        i = _issue(db, name="Safety")
        db.add(IssueMention(issue_id=i.id, source_item_id=s.id))
        db.commit()
        results = get_review_queue(db=db)
        item = next(r for r in results if r.id == s.id)
        assert i.id in item.related_issue_ids


# ── Dashboard changes ─────────────────────────────────────────────────────────

class TestDashboardChanges:
    def test_returns_new_sources(self, db):
        from app.routes.dashboard import get_dashboard_changes
        _source(db, title="Fresh source")
        result = get_dashboard_changes(hours=24, db=db)
        assert result.new_source_count >= 1
        assert any(c.title == "Fresh source" for c in result.changes)

    def test_empty_when_no_recent_items(self, db):
        from app.routes.dashboard import get_dashboard_changes
        from datetime import timedelta
        old = SourceItem(
            title="Old source", source_type="news",
            created_at=datetime.utcnow() - timedelta(hours=48),
            urgency="low",
        )
        db.add(old)
        db.commit()
        result = get_dashboard_changes(hours=24, db=db)
        assert not any(c.title == "Old source" for c in result.changes)

    def test_source_templates_endpoint(self, db):
        from app.routes.source_templates import get_source_templates
        templates = get_source_templates()
        assert len(templates) > 0
        assert all(t.id and t.name and t.category for t in templates)
