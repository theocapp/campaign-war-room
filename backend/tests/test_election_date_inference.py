"""Tests for election date inference logic (campaign_setup service)."""

from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import CampaignConfig, RaceCandidate, RaceDirectory
from app.services.campaign_setup import (
    PRIMARY_CONFIG,
    _first_tue_after_first_mon,
    _nth_weekday,
    infer_election_date,
)


# ── Unit tests: date arithmetic helpers ──────────────────────────────────────

def test_first_tue_after_first_mon_november_2026():
    """Federal Election Day 2026 is November 3 (Nov 2 = first Monday, Nov 3 = Tue)."""
    d = _first_tue_after_first_mon(2026, 11)
    assert d.year == 2026
    assert d.month == 11
    assert d.day == 3


def test_first_tue_after_first_mon_november_2024():
    """2024 general election: November 5."""
    d = _first_tue_after_first_mon(2024, 11)
    assert d.year == 2024
    assert d.month == 11
    assert d.day == 5


def test_nth_weekday_pa_primary_2026():
    """PA primary: 3rd Tuesday in May 2026 = May 19."""
    import calendar
    d = _nth_weekday(2026, 5, 3, calendar.TUESDAY)
    assert d.year == 2026
    assert d.month == 5
    assert d.day == 19


def test_nth_weekday_first_occurrence():
    """1st Tuesday in March 2026 = March 3."""
    import calendar
    d = _nth_weekday(2026, 3, 1, calendar.TUESDAY)
    assert d.year == 2026
    assert d.month == 3
    assert d.day == 3


# ── Unit tests: infer_election_date public API ────────────────────────────────

class TestGeneralElectionInference:
    def test_returns_november_election_day(self):
        result = infer_election_date("general", 2026, "PA")
        assert result is not None
        assert result.month == 11
        assert result.day == 3
        assert result.year == 2026

    def test_state_does_not_affect_general_date(self):
        """General election date is federal — same for every state."""
        pa = infer_election_date("general", 2026, "PA")
        ca = infer_election_date("general", 2026, "CA")
        ny = infer_election_date("general", 2026, "NY")
        assert pa == ca == ny

    def test_general_2024(self):
        result = infer_election_date("general", 2024, "PA")
        assert result is not None
        assert result.day == 5
        assert result.month == 11
        assert result.year == 2024

    def test_returns_utc_midnight(self):
        """Election date is stored as midnight UTC (no tzinfo on naive datetime)."""
        result = infer_election_date("general", 2026, "PA")
        assert result.hour == 0
        assert result.minute == 0
        assert result.second == 0
        assert result.tzinfo is None  # naive UTC, consistent with other DB datetimes

    def test_case_insensitive_type(self):
        assert infer_election_date("General", 2026, "PA") is not None
        assert infer_election_date("GENERAL", 2026, "PA") is not None


class TestPrimaryElectionInference:
    def test_pa_primary_2026(self):
        """PA 3rd Tuesday in May 2026 = May 19."""
        result = infer_election_date("primary", 2026, "PA")
        assert result is not None
        assert result.month == 5
        assert result.day == 19
        assert result.year == 2026

    def test_tx_primary_2026(self):
        """TX first Tuesday after first Monday in March 2026 = March 3."""
        result = infer_election_date("primary", 2026, "TX")
        assert result is not None
        assert result.month == 3
        assert result.day == 3
        assert result.year == 2026

    def test_ca_primary_2026(self):
        """CA first Tuesday after first Monday in June 2026 = June 2."""
        result = infer_election_date("primary", 2026, "CA")
        assert result is not None
        assert result.month == 6
        assert result.day == 2
        assert result.year == 2026

    def test_primary_returns_utc_midnight(self):
        result = infer_election_date("primary", 2026, "PA")
        assert result.hour == 0
        assert result.minute == 0
        assert result.tzinfo is None

    def test_unknown_state_returns_none(self):
        result = infer_election_date("primary", 2026, "ZZ")
        assert result is None

    def test_missing_state_returns_none(self):
        result = infer_election_date("primary", 2026, None)
        assert result is None

    def test_unknown_year_returns_none(self):
        result = infer_election_date("primary", 2099, "PA")
        assert result is None

    def test_all_configured_states_resolve(self):
        """Every state in PRIMARY_CONFIG must produce a valid future-ish date."""
        for state in PRIMARY_CONFIG.get(2026, {}):
            result = infer_election_date("primary", 2026, state)
            assert result is not None, f"State {state} failed to resolve"
            assert isinstance(result, datetime)
            assert result.year == 2026


class TestSpecialElectionFallback:
    def test_special_returns_none(self):
        assert infer_election_date("special", 2026, "PA") is None

    def test_runoff_returns_none(self):
        assert infer_election_date("runoff", 2026, "PA") is None

    def test_other_returns_none(self):
        assert infer_election_date("other", 2026, "PA") is None

    def test_empty_type_returns_none(self):
        assert infer_election_date("", 2026, "PA") is None

    def test_none_type_returns_none(self):
        assert infer_election_date(None, 2026, "PA") is None

    def test_missing_year_returns_none(self):
        assert infer_election_date("general", None, "PA") is None


# ── Integration test: race selection prefills election date ───────────────────

@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def _add_race(db, state="PA", election_type="general", election_date=None):
    race = RaceDirectory(
        race_key=f"test-{state.lower()}-house-general",
        race_name=f"{state} House General",
        race_level="federal",
        office_name="U.S. Representative",
        state=state,
        district_label=f"{state}-08",
        district_number="8",
        election_type=election_type,
        election_date=election_date,
        data_source="test",
    )
    db.add(race)
    db.flush()
    candidate = RaceCandidate(
        race_id=race.id,
        candidate_name="Test Candidate",
        party="Democrat",
        role="candidate",
    )
    db.add(candidate)
    db.commit()
    db.refresh(race)
    return race


def test_select_race_infers_general_election_date(db):
    from app.services.race_directory import select_directory_race
    race = _add_race(db, state="PA", election_type="general")

    _, campaign, _, _, _ = select_directory_race(db, race.id)

    assert campaign.election_date is not None
    assert campaign.election_date.month == 11
    assert campaign.election_date.day == 3
    assert campaign.election_date.year == 2026


def test_select_race_explicit_date_takes_priority(db):
    from app.services.race_directory import select_directory_race
    explicit = datetime(2026, 3, 15)
    race = _add_race(db, state="PA", election_type="general", election_date=explicit)

    _, campaign, _, _, _ = select_directory_race(db, race.id)

    assert campaign.election_date == explicit


def test_select_race_special_leaves_date_none(db):
    from app.services.race_directory import select_directory_race
    race = _add_race(db, state="PA", election_type="special")

    _, campaign, _, _, _ = select_directory_race(db, race.id)

    assert campaign.election_date is None


# ── Integration test: update_campaign route infers date ──────────────────────

@pytest.fixture
def route_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def test_update_campaign_infers_date_when_not_provided(route_db, monkeypatch):
    from app.routes.campaign import update_campaign
    from app.schemas import CampaignProfileIn

    monkeypatch.setenv("SEARCH_PROVIDER", "mock")

    body = CampaignProfileIn(
        candidate_name="Maria Alvarez",
        office="City Council",
        election_type="general",
        location="Riverton, PA",
    )
    result = update_campaign(body, db=route_db)

    assert result.election_date is not None
    assert result.election_date.month == 11
    assert result.election_date.day == 3


def test_update_campaign_explicit_date_not_overwritten(route_db, monkeypatch):
    from app.routes.campaign import update_campaign
    from app.schemas import CampaignProfileIn

    monkeypatch.setenv("SEARCH_PROVIDER", "mock")
    explicit = datetime(2027, 5, 4)

    body = CampaignProfileIn(
        candidate_name="Maria Alvarez",
        office="City Council",
        election_type="general",
        location="Riverton, PA",
        election_date=explicit,
    )
    result = update_campaign(body, db=route_db)

    assert result.election_date == explicit
