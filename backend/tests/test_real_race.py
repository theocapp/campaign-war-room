"""Tests for real-race features: reset workspace, source packs, reminders, CSV import."""
import csv
import io
import json
import pytest
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import (
    CampaignConfig, SourceItem, Issue, IssueMention,
    Opponent, OpponentActivity, CanvassingNote,
    RssFeed, GeneratedTalkingPoint,
    SourcePack, SourcePackItem, ManualSourceReminder,
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


def _campaign(db, name="Test Candidate"):
    c = CampaignConfig(candidate_name=name, office="Rep", district="D1",
                       created_at=datetime.utcnow(), updated_at=datetime.utcnow())
    db.add(c)
    db.commit()
    return c


def _source(db, title="Test Source"):
    s = SourceItem(title=title, source_type="news", urgency="low",
                   created_at=datetime.utcnow())
    db.add(s)
    db.commit()
    return s


def _pack(db, name="Test Pack"):
    p = SourcePack(name=name, race_level="federal", geography="us_house",
                   created_at=datetime.utcnow())
    db.add(p)
    db.flush()
    return p


def _csv_upload(rows: list[dict]) -> bytes:
    buf = io.StringIO()
    fieldnames = ["type", "name", "url", "category", "source_type",
                  "notes", "party", "office", "district", "location", "election_date"]
    w = csv.DictWriter(buf, fieldnames=fieldnames)
    w.writeheader()
    for row in rows:
        w.writerow({f: row.get(f, "") for f in fieldnames})
    return buf.getvalue().encode()


# ── Phase 1: Reset workspace ──────────────────────────────────────────────────

class TestResetWorkspace:
    def test_requires_confirmation_string(self, db):
        from fastapi import HTTPException
        from app.routes.admin import reset_workspace
        from app.schemas import ResetWorkspaceRequest
        with pytest.raises(HTTPException) as exc:
            reset_workspace(
                body=ResetWorkspaceRequest(
                    confirm="wrong string",
                    candidate_name="Jane", office="Rep",
                ),
                db=db,
            )
        assert exc.value.status_code == 400

    def test_wrong_case_rejected(self, db):
        from fastapi import HTTPException
        from app.routes.admin import reset_workspace
        from app.schemas import ResetWorkspaceRequest
        with pytest.raises(HTTPException):
            reset_workspace(
                body=ResetWorkspaceRequest(
                    confirm="reset workspace",  # lowercase
                    candidate_name="Jane", office="Rep",
                ),
                db=db,
            )

    def test_clears_sources(self, db):
        from app.routes.admin import reset_workspace
        from app.schemas import ResetWorkspaceRequest
        _campaign(db)
        _source(db, "Article 1")
        _source(db, "Article 2")
        result = reset_workspace(
            body=ResetWorkspaceRequest(
                confirm="RESET WORKSPACE",
                candidate_name="Jane Smith", office="U.S. Representative",
            ),
            db=db,
        )
        assert result.cleared_sources == 2
        assert db.query(SourceItem).count() == 0

    def test_clears_opponents(self, db):
        from app.routes.admin import reset_workspace
        from app.schemas import ResetWorkspaceRequest
        _campaign(db)
        db.add(Opponent(name="Opp A", created_at=datetime.utcnow()))
        db.commit()
        result = reset_workspace(
            body=ResetWorkspaceRequest(
                confirm="RESET WORKSPACE",
                candidate_name="Jane Smith", office="U.S. Representative",
            ),
            db=db,
        )
        assert result.cleared_opponents == 1
        assert db.query(Opponent).count() == 0

    def test_clears_issues(self, db):
        from app.routes.admin import reset_workspace
        from app.schemas import ResetWorkspaceRequest
        _campaign(db)
        db.add(Issue(name="Housing", urgency="low", mention_count=5,
                     trend="stable", last_seen_at=datetime.utcnow()))
        db.commit()
        result = reset_workspace(
            body=ResetWorkspaceRequest(
                confirm="RESET WORKSPACE",
                candidate_name="Jane Smith", office="U.S. Representative",
            ),
            db=db,
        )
        assert result.cleared_issues == 1
        assert db.query(Issue).count() == 0

    def test_clears_talking_points(self, db):
        from app.routes.admin import reset_workspace
        from app.schemas import ResetWorkspaceRequest
        _campaign(db)
        db.add(GeneratedTalkingPoint(
            issue_name="Housing", tone="calm",
            short_answer="a", long_answer="b", debate_answer="c",
            social_post="d", evidence_notes="e",
            source_titles_used="[]", source_urls_used="[]",
        ))
        db.commit()
        result = reset_workspace(
            body=ResetWorkspaceRequest(
                confirm="RESET WORKSPACE",
                candidate_name="Jane Smith", office="U.S. Representative",
            ),
            db=db,
        )
        assert result.cleared_talking_points == 1
        assert db.query(GeneratedTalkingPoint).count() == 0

    def test_creates_new_campaign_profile(self, db):
        from app.routes.admin import reset_workspace
        from app.schemas import ResetWorkspaceRequest
        _campaign(db, name="Old Candidate")
        reset_workspace(
            body=ResetWorkspaceRequest(
                confirm="RESET WORKSPACE",
                candidate_name="Jane Smith",
                office="U.S. Representative",
                district="PA-08",
                party="Democrat",
            ),
            db=db,
        )
        config = db.query(CampaignConfig).first()
        assert config is not None
        assert config.candidate_name == "Jane Smith"
        assert config.office == "U.S. Representative"
        assert config.district == "PA-08"
        assert config.party == "Democrat"

    def test_clears_feeds_by_default(self, db):
        from app.routes.admin import reset_workspace
        from app.schemas import ResetWorkspaceRequest
        _campaign(db)
        db.add(RssFeed(name="Feed 1", url="https://example.com/feed",
                       active=True, created_at=datetime.utcnow()))
        db.commit()
        result = reset_workspace(
            body=ResetWorkspaceRequest(
                confirm="RESET WORKSPACE",
                candidate_name="Jane Smith", office="U.S. Representative",
                preserve_feeds=False,
            ),
            db=db,
        )
        assert result.cleared_feeds == 1
        assert result.preserved_feeds == 0
        assert db.query(RssFeed).count() == 0

    def test_preserves_feeds_when_requested(self, db):
        from app.routes.admin import reset_workspace
        from app.schemas import ResetWorkspaceRequest
        _campaign(db)
        db.add(RssFeed(name="Feed 1", url="https://example.com/feed",
                       active=True, created_at=datetime.utcnow()))
        db.commit()
        result = reset_workspace(
            body=ResetWorkspaceRequest(
                confirm="RESET WORKSPACE",
                candidate_name="Jane Smith", office="U.S. Representative",
                preserve_feeds=True,
            ),
            db=db,
        )
        assert result.preserved_feeds == 1
        assert result.cleared_feeds == 0
        assert db.query(RssFeed).count() == 1

    def test_result_includes_candidate_name(self, db):
        from app.routes.admin import reset_workspace
        from app.schemas import ResetWorkspaceRequest
        _campaign(db)
        result = reset_workspace(
            body=ResetWorkspaceRequest(
                confirm="RESET WORKSPACE",
                candidate_name="Jane Smith", office="U.S. Representative",
            ),
            db=db,
        )
        assert result.candidate_name == "Jane Smith"


# ── Phase 2: Source packs ─────────────────────────────────────────────────────

class TestSourcePacks:
    def test_create_pack(self, db):
        from app.routes.source_packs import create_pack
        from app.schemas import SourcePackCreate
        result = create_pack(
            body=SourcePackCreate(
                name="Test Pack",
                description="A test pack",
                race_level="federal",
                geography="us_house",
                items=[
                    {"name": "Local Paper", "source_type": "news",
                     "url": "https://example.com/rss", "setup_note": "Check weekly"},
                ],
            ),
            db=db,
        )
        assert result.id is not None
        assert result.name == "Test Pack"
        assert len(result.items) == 1

    def test_list_packs(self, db):
        from app.routes.source_packs import list_packs, create_pack
        from app.schemas import SourcePackCreate
        create_pack(body=SourcePackCreate(name="Pack A", items=[]), db=db)
        create_pack(body=SourcePackCreate(name="Pack B", items=[]), db=db)
        results = list_packs(db=db)
        assert len(results) == 2

    def test_apply_creates_reminder_for_placeholder(self, db):
        from app.routes.source_packs import apply_pack
        p = _pack(db)
        db.add(SourcePackItem(
            source_pack_id=p.id, name="Opponent Site",
            source_type="opponent_statement",
            url=None, setup_note="[PLACEHOLDER] Add opponent URL", active=True,
        ))
        db.commit()
        result = apply_pack(pack_id=p.id, db=db)
        assert result.reminders_created == 1
        assert result.feeds_created == 0
        assert db.query(ManualSourceReminder).count() == 1

    def test_apply_creates_feed_for_rss_url(self, db):
        from app.routes.source_packs import apply_pack
        p = _pack(db)
        db.add(SourcePackItem(
            source_pack_id=p.id, name="News Feed",
            source_type="news",
            url="https://example.com/rss", active=True,
        ))
        db.commit()
        result = apply_pack(pack_id=p.id, db=db)
        assert result.feeds_created == 1
        assert result.reminders_created == 0
        assert db.query(RssFeed).count() == 1

    def test_apply_skips_duplicate_feed(self, db):
        from app.routes.source_packs import apply_pack
        p = _pack(db)
        db.add(SourcePackItem(
            source_pack_id=p.id, name="News Feed",
            source_type="news",
            url="https://example.com/rss", active=True,
        ))
        # Pre-existing feed with same URL
        db.add(RssFeed(name="Existing", url="https://example.com/rss",
                       active=True, created_at=datetime.utcnow()))
        db.commit()
        result = apply_pack(pack_id=p.id, db=db)
        assert result.feeds_created == 0
        assert result.skipped_duplicate_feeds == 1

    def test_apply_404_on_bad_pack(self, db):
        from fastapi import HTTPException
        from app.routes.source_packs import apply_pack
        with pytest.raises(HTTPException) as exc:
            apply_pack(pack_id=99999, db=db)
        assert exc.value.status_code == 404

    def test_placeholder_url_becomes_reminder_not_feed(self, db):
        from app.routes.source_packs import apply_pack
        p = _pack(db)
        db.add(SourcePackItem(
            source_pack_id=p.id, name="Google News Template",
            source_type="news",
            url="https://news.google.com/rss/search?q={candidate+name}",
            active=True,
        ))
        db.commit()
        result = apply_pack(pack_id=p.id, db=db)
        # URL has placeholder → becomes reminder, not RSS feed
        assert result.reminders_created == 1
        assert result.feeds_created == 0


# ── Phase 3: Manual source reminders ─────────────────────────────────────────

class TestSourceReminders:
    def test_create_reminder(self, db):
        from app.routes.source_reminders import create_reminder
        from app.schemas import ManualSourceReminderIn
        r = create_reminder(
            body=ManualSourceReminderIn(
                name="Check opponent FB", category="Opponent Monitoring",
                source_type="social", url="https://facebook.com/opponent",
                setup_note="Check for new attack ads",
            ),
            db=db,
        )
        assert r.id is not None
        assert r.name == "Check opponent FB"
        assert r.last_checked_at is None

    def test_list_reminders(self, db):
        from app.routes.source_reminders import create_reminder, list_reminders
        from app.schemas import ManualSourceReminderIn
        create_reminder(body=ManualSourceReminderIn(name="A", source_type="news"), db=db)
        create_reminder(body=ManualSourceReminderIn(name="B", source_type="social"), db=db)
        results = list_reminders(db=db)
        assert len(results) == 2

    def test_mark_checked_sets_timestamp(self, db):
        from app.routes.source_reminders import create_reminder, mark_checked
        from app.schemas import ManualSourceReminderIn
        r = create_reminder(body=ManualSourceReminderIn(name="FEC Page", source_type="public_record"), db=db)
        assert r.last_checked_at is None
        updated = mark_checked(reminder_id=r.id, db=db)
        assert updated.last_checked_at is not None

    def test_update_reminder(self, db):
        from app.routes.source_reminders import create_reminder, update_reminder
        from app.schemas import ManualSourceReminderIn, ManualSourceReminderUpdate
        r = create_reminder(body=ManualSourceReminderIn(name="Old Name", source_type="news"), db=db)
        updated = update_reminder(
            reminder_id=r.id,
            body=ManualSourceReminderUpdate(name="New Name", active=False),
            db=db,
        )
        assert updated.name == "New Name"
        assert updated.active is False

    def test_delete_reminder(self, db):
        from app.routes.source_reminders import create_reminder, delete_reminder
        from app.schemas import ManualSourceReminderIn
        r = create_reminder(body=ManualSourceReminderIn(name="Temp", source_type="news"), db=db)
        delete_reminder(reminder_id=r.id, db=db)
        assert db.query(ManualSourceReminder).count() == 0

    def test_404_on_missing_reminder(self, db):
        from fastapi import HTTPException
        from app.routes.source_reminders import mark_checked
        with pytest.raises(HTTPException) as exc:
            mark_checked(reminder_id=99999, db=db)
        assert exc.value.status_code == 404


# ── Phase 4: Race CSV import ──────────────────────────────────────────────────

class TestRaceImport:
    def _make_upload(self, rows):
        from unittest.mock import AsyncMock, MagicMock
        data = _csv_upload(rows)
        upload = MagicMock()
        upload.read = AsyncMock(return_value=data)
        return upload

    @pytest.mark.asyncio
    async def test_import_campaign_row(self, db):
        from app.routes.race_import import import_race_csv
        upload = self._make_upload([{
            "type": "campaign", "name": "Jane Smith",
            "office": "U.S. Representative", "district": "PA-08",
            "party": "Democrat", "location": "Scranton, PA",
        }])
        result = await import_race_csv(file=upload, db=db)
        assert result.campaign_updated is True
        config = db.query(CampaignConfig).first()
        assert config.candidate_name == "Jane Smith"
        assert config.district == "PA-08"

    @pytest.mark.asyncio
    async def test_import_opponent_row(self, db):
        from app.routes.race_import import import_race_csv
        upload = self._make_upload([{
            "type": "opponent", "name": "John Doe",
            "party": "Republican", "office": "U.S. Representative",
        }])
        result = await import_race_csv(file=upload, db=db)
        assert result.opponents_created == 1
        opp = db.query(Opponent).first()
        assert opp.name == "John Doe"
        assert opp.party == "Republican"

    @pytest.mark.asyncio
    async def test_import_feed_row(self, db):
        from app.routes.race_import import import_race_csv
        upload = self._make_upload([{
            "type": "rss_feed", "name": "Local Tribune",
            "url": "https://tribune.example.com/feed",
            "source_type": "news",
        }])
        result = await import_race_csv(file=upload, db=db)
        assert result.feeds_created == 1
        feed = db.query(RssFeed).first()
        assert feed.url == "https://tribune.example.com/feed"

    @pytest.mark.asyncio
    async def test_import_reminder_row(self, db):
        from app.routes.race_import import import_race_csv
        upload = self._make_upload([{
            "type": "reminder", "name": "Check FEC Page",
            "url": "https://fec.gov", "source_type": "public_record",
            "notes": "Check quarterly",
        }])
        result = await import_race_csv(file=upload, db=db)
        assert result.reminders_created == 1
        r = db.query(ManualSourceReminder).first()
        assert r.name == "Check FEC Page"

    @pytest.mark.asyncio
    async def test_skip_duplicate_opponent(self, db):
        from app.routes.race_import import import_race_csv
        db.add(Opponent(name="John Doe", created_at=datetime.utcnow()))
        db.commit()
        upload = self._make_upload([{
            "type": "opponent", "name": "John Doe", "party": "R",
        }])
        result = await import_race_csv(file=upload, db=db)
        assert result.opponents_created == 0
        assert result.skipped == 1

    @pytest.mark.asyncio
    async def test_skip_duplicate_feed(self, db):
        from app.routes.race_import import import_race_csv
        db.add(RssFeed(name="Existing", url="https://example.com/feed",
                       active=True, created_at=datetime.utcnow()))
        db.commit()
        upload = self._make_upload([{
            "type": "rss_feed", "name": "Dup Feed",
            "url": "https://example.com/feed",
        }])
        result = await import_race_csv(file=upload, db=db)
        assert result.feeds_created == 0
        assert result.skipped == 1

    @pytest.mark.asyncio
    async def test_unknown_type_goes_to_errors(self, db):
        from app.routes.race_import import import_race_csv
        upload = self._make_upload([{"type": "bogus_type", "name": "X"}])
        result = await import_race_csv(file=upload, db=db)
        assert len(result.errors) == 1
        assert "bogus_type" in result.errors[0]

    @pytest.mark.asyncio
    async def test_mixed_rows_all_processed(self, db):
        from app.routes.race_import import import_race_csv
        upload = self._make_upload([
            {"type": "campaign", "name": "Jane Smith", "office": "Rep"},
            {"type": "opponent", "name": "John Doe"},
            {"type": "rss_feed", "name": "Feed", "url": "https://example.com/rss"},
            {"type": "reminder", "name": "Check FEC"},
        ])
        result = await import_race_csv(file=upload, db=db)
        assert result.campaign_updated is True
        assert result.opponents_created == 1
        assert result.feeds_created == 1
        assert result.reminders_created == 1
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_feed_row_missing_url_gives_error(self, db):
        from app.routes.race_import import import_race_csv
        upload = self._make_upload([{"type": "rss_feed", "name": "No URL Feed"}])
        result = await import_race_csv(file=upload, db=db)
        assert len(result.errors) >= 1

    @pytest.mark.asyncio
    async def test_seed_pack_seeded_correctly(self, db):
        from app.seed import _seed_source_packs
        _seed_source_packs(db)
        db.commit()
        pack = db.query(SourcePack).filter_by(name="US House Race Starter Pack").first()
        assert pack is not None
        assert len(pack.items) > 5
        assert pack.race_level == "federal"
        assert pack.geography == "us_house"
