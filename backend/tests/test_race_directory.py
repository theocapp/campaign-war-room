import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import CampaignConfig, Opponent, RaceCandidate, RaceDirectory


FEC_TEST_ROWS = [
    {
        "CAND_ID": "H4AL02170",
        "CAND_NAME": "FIGURES, SHOMARI C.",
        "CAND_PTY_AFFILIATION": "DEM",
        "CAND_ELECTION_YR": "2026",
        "CAND_OFFICE_ST": "AL",
        "CAND_OFFICE": "H",
        "CAND_OFFICE_DISTRICT": "02",
        "CAND_ICI": "I",
        "CAND_STATUS": "C",
        "CAND_PCC": "C00856237",
    },
    {
        "CAND_ID": "H4AL02139",
        "CAND_NAME": "HARRIS, HAMPTON",
        "CAND_PTY_AFFILIATION": "REP",
        "CAND_ELECTION_YR": "2026",
        "CAND_OFFICE_ST": "AL",
        "CAND_OFFICE": "H",
        "CAND_OFFICE_DISTRICT": "02",
        "CAND_ICI": "C",
        "CAND_STATUS": "C",
        "CAND_PCC": "C00934398",
    },
    {
        "CAND_ID": "S2TX00106",
        "CAND_NAME": "CORNYN, JOHN SEN",
        "CAND_PTY_AFFILIATION": "REP",
        "CAND_ELECTION_YR": "2026",
        "CAND_OFFICE_ST": "TX",
        "CAND_OFFICE": "S",
        "CAND_OFFICE_DISTRICT": "00",
        "CAND_ICI": "I",
        "CAND_STATUS": "C",
        "CAND_PCC": "C00369033",
    },
]


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def _race(db):
    from app.services.race_directory import import_fec_candidate_master_rows

    import_fec_candidate_master_rows(db, FEC_TEST_ROWS)
    db.commit()
    return db.query(RaceDirectory).filter_by(race_key="fec-2026-house-al-02").first()


def test_seed_race_directory_imports_real_fec_federal_snapshot(db):
    from app.services.race_directory import seed_race_directory

    seed_race_directory(db)
    db.commit()

    races = db.query(RaceDirectory).all()
    assert len(races) > 400
    assert all(r.race_level == "federal" for r in races)
    assert all(r.data_source == "fec" for r in races)
    assert db.query(RaceCandidate).filter_by(candidate_name="Shomari C. Figures").first() is not None
    assert db.query(RaceCandidate).filter_by(candidate_name="Alex Rivera").first() is None


def test_seed_removes_legacy_placeholder_races(db):
    from app.services.race_directory import seed_race_directory

    legacy = RaceDirectory(
        race_key="2026-pa-us-house-08-general",
        race_name="PA-08 U.S. House General Election",
        race_level="federal",
        office_name="U.S. Representative",
        state="PA",
        election_type="general",
        data_source="manual_seed",
    )
    db.add(legacy)
    db.flush()
    db.add(RaceCandidate(race_id=legacy.id, candidate_name="Alex Rivera", party="Democrat"))
    db.commit()

    seed_race_directory(db)
    db.commit()

    assert db.query(RaceDirectory).filter_by(race_key="2026-pa-us-house-08-general").first() is None
    assert db.query(RaceCandidate).filter_by(candidate_name="Alex Rivera").first() is None


def test_search_finds_race_by_district_and_candidate(db):
    from app.services.race_directory import list_directory_races

    _race(db)

    assert [r.race_name for r in list_directory_races(db, q="AL-02")] == [
        "AL-02 U.S. House 2026 Candidate Filings"
    ]
    assert [r.race_name for r in list_directory_races(db, q="Shomari")] == [
        "AL-02 U.S. House 2026 Candidate Filings"
    ]


def test_race_routes_search_and_select(db):
    from app.routes.races import search_races, select_race
    from app.schemas import RaceSelectRequest

    race = _race(db)

    results = search_races(q="AL-02", db=db)
    assert len(results) == 1
    assert results[0].id == race.id

    result = select_race(
        race_id=race.id,
        body=RaceSelectRequest(candidate_name="Shomari C. Figures"),
        db=db,
    )
    assert result.race.id == race.id
    assert result.campaign.candidate_name == "Shomari C. Figures"
    assert result.opponents_created == 1


def test_select_race_updates_campaign_context_and_creates_opponent(db):
    from app.services.race_directory import select_directory_race

    race = _race(db)
    shomari = next(c for c in race.candidates if c.candidate_name == "Shomari C. Figures")
    _, campaign, selected, created, updated = select_directory_race(db, race.id, candidate_id=shomari.id)

    assert selected.candidate_name == "Shomari C. Figures"
    assert campaign.candidate_name == "Shomari C. Figures"
    assert campaign.party == "Democrat"
    assert campaign.race == "AL-02 U.S. House 2026 Candidate Filings"
    assert campaign.office == "U.S. Representative"
    assert campaign.district == "AL-02"
    assert campaign.location == "AL-02 federal candidate filings from the FEC Candidate Master file. This is not a certified ballot list."
    assert campaign.race_level == "federal"
    assert campaign.election_type == "general"
    assert campaign.district_number == "2"
    assert created == 1
    assert updated == 0

    opponent = db.query(Opponent).filter_by(name="Hampton Harris").first()
    assert opponent is not None
    assert opponent.party == "Republican"
    assert opponent.office == "U.S. Representative (AL-02)"
    assert "Loaded from race directory" in opponent.notes

    geography_terms = json.loads(campaign.geography_keywords)
    assert "AL" in geography_terms
    assert "AL-02" in geography_terms


def test_select_race_can_use_explicit_candidate_and_updates_other_candidates(db):
    from app.services.race_directory import select_directory_race

    race = _race(db)
    figures = next(c for c in race.candidates if c.candidate_name == "Shomari C. Figures")
    harris = next(c for c in race.candidates if c.candidate_name == "Hampton Harris")

    _, campaign, selected, created, updated = select_directory_race(
        db,
        race.id,
        candidate_id=harris.id,
    )

    assert selected.candidate_name == "Hampton Harris"
    assert campaign.candidate_name == "Hampton Harris"
    assert created == 1
    assert updated == 0
    assert db.query(Opponent).filter_by(name="Shomari C. Figures").first() is not None

    _, _, _, created_again, updated_again = select_directory_race(
        db,
        race.id,
        candidate_id=harris.id,
    )
    assert created_again == 0
    assert updated_again == 1
    assert db.query(Opponent).filter_by(name=figures.candidate_name).count() == 1


def test_custom_campaign_update_still_works(db):
    from app.routes.campaign import update_campaign
    from app.schemas import CampaignProfileIn

    db.add(CampaignConfig(candidate_name="Existing Candidate"))
    db.commit()

    result = update_campaign(
        body=CampaignProfileIn(
            candidate_name="Custom Local Candidate",
            party="Independent",
            race="River County School Board",
            office="School Board Member",
            district="District 2",
            location="River County",
            race_level="local",
            election_type="special",
            district_number="2",
            campaign_message="Keep schools focused on students.",
        ),
        db=db,
    )

    assert result.candidate_name == "Custom Local Candidate"
    assert result.race == "River County School Board"
    assert result.race_level == "local"
    assert db.query(Opponent).count() == 0
