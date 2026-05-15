"""Phase 0 bug 3: duplicate Opponent rows from FEC import.

The FEC candidate-master uses "LAST, FIRST" formatting; users type
"First Last". Before the fix the dedup was a plain case-insensitive name
compare, so re-selecting a race after a manual opponent existed produced
duplicates like (`BRESNAHAN, ROB`, `Rob Bresnahan`). The fix dedupes by
FEC candidate ID extracted from the candidate's campaign_url, falling
back to a normalized name compare.
"""
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Opponent, RaceCandidate, RaceDirectory
from app.services.race_directory import (
    _fec_candidate_id_from_url,
    _normalize_candidate_name,
    _upsert_opponent,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def _race(db) -> RaceDirectory:
    race = RaceDirectory(
        race_key="fec-2026-house-pa-08",
        race_name="PA-08 U.S. House",
        race_level="federal",
        office_name="U.S. Representative",
        state="PA",
        district_label="PA-08",
        district_number="8",
        election_type="general",
        geography_summary="Pennsylvania 8th congressional district",
        data_source="fec",
        is_active=True,
    )
    db.add(race)
    db.commit()
    db.refresh(race)
    return race


def _candidate(race: RaceDirectory, name: str, fec_id: str | None = "H8PA08123") -> RaceCandidate:
    return RaceCandidate(
        race_id=race.id,
        candidate_name=name,
        party="Republican",
        is_incumbent=False,
        role="opponent",
        campaign_url=f"https://www.fec.gov/data/candidate/{fec_id}/" if fec_id else None,
        notes=f"FEC candidate ID {fec_id}." if fec_id else None,
    )


# ── helpers ────────────────────────────────────────────────────────────────────


def test_fec_id_extracted_from_url():
    assert _fec_candidate_id_from_url("https://www.fec.gov/data/candidate/H8PA08123/") == "H8PA08123"
    assert _fec_candidate_id_from_url("https://www.fec.gov/data/candidate/H8PA08123") == "H8PA08123"
    assert _fec_candidate_id_from_url(None) is None
    assert _fec_candidate_id_from_url("") is None


def test_normalize_candidate_name_handles_last_comma_first():
    assert _normalize_candidate_name("BRESNAHAN, ROB") == "rob bresnahan"
    assert _normalize_candidate_name("Bresnahan, Rob") == "rob bresnahan"
    assert _normalize_candidate_name("Rob Bresnahan") == "rob bresnahan"
    assert _normalize_candidate_name("  Rob   Bresnahan  ") == "rob bresnahan"


# ── _upsert_opponent dedup ─────────────────────────────────────────────────────


def test_first_import_creates_opponent(db):
    race = _race(db)
    cand = _candidate(race, "BRESNAHAN, ROB")
    db.add(cand)
    db.commit()
    db.refresh(cand)

    inserted = _upsert_opponent(db, race, cand)
    db.commit()

    assert inserted is True
    opponents = db.query(Opponent).all()
    assert len(opponents) == 1
    assert opponents[0].fec_candidate_id == "H8PA08123"


def test_reimport_same_fec_id_does_not_duplicate(db):
    race = _race(db)
    cand = _candidate(race, "BRESNAHAN, ROB")
    db.add(cand)
    db.commit()
    db.refresh(cand)

    _upsert_opponent(db, race, cand)
    db.commit()
    inserted_second = _upsert_opponent(db, race, cand)
    db.commit()

    assert inserted_second is False
    assert db.query(Opponent).count() == 1


def test_manually_created_opponent_matched_by_normalized_name(db):
    """A user typed 'Rob Bresnahan' before the race was loaded from FEC.
    The FEC import gives us 'BRESNAHAN, ROB' — same person, different format.
    No duplicate should be created; the existing row gets the FEC ID stamped on it.
    """
    race = _race(db)
    db.add(Opponent(name="Rob Bresnahan", created_at=datetime.utcnow()))
    db.commit()

    cand = _candidate(race, "BRESNAHAN, ROB", fec_id="H8PA08123")
    db.add(cand)
    db.commit()
    db.refresh(cand)

    inserted = _upsert_opponent(db, race, cand)
    db.commit()

    assert inserted is False
    opponents = db.query(Opponent).all()
    assert len(opponents) == 1
    assert opponents[0].name == "Rob Bresnahan"  # original name kept
    assert opponents[0].fec_candidate_id == "H8PA08123"  # stamped on


def test_different_fec_id_creates_new_row(db):
    race = _race(db)
    a = _candidate(race, "BRESNAHAN, ROB", fec_id="H8PA08123")
    b = _candidate(race, "SMITH, JANE", fec_id="H8PA08999")
    db.add_all([a, b])
    db.commit()
    db.refresh(a)
    db.refresh(b)

    _upsert_opponent(db, race, a)
    _upsert_opponent(db, race, b)
    db.commit()

    assert db.query(Opponent).count() == 2
