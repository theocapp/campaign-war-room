import json
import csv
from datetime import datetime
from pathlib import Path
from typing import Iterable
from zipfile import ZipFile

from fastapi import HTTPException
from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.models import CampaignConfig, Opponent, RaceCandidate, RaceDirectory


FEC_CANDIDATE_MASTER_URL = "https://www.fec.gov/files/bulk-downloads/2026/cn26.zip"
FEC_CANDIDATE_MASTER_PATH = Path(__file__).resolve().parents[1] / "data" / "fec_candidate_master_2026.psv"
FEC_ELECTION_YEAR = "2026"
FEC_CANDIDATE_FIELDS = [
    "CAND_ID",
    "CAND_NAME",
    "CAND_PTY_AFFILIATION",
    "CAND_ELECTION_YR",
    "CAND_OFFICE_ST",
    "CAND_OFFICE",
    "CAND_OFFICE_DISTRICT",
    "CAND_ICI",
    "CAND_STATUS",
    "CAND_PCC",
    "CAND_ST1",
    "CAND_ST2",
    "CAND_CITY",
    "CAND_ST",
    "CAND_ZIP",
]
LEGACY_PLACEHOLDER_RACE_KEYS = {
    "2026-pa-us-house-08-general",
    "2026-nc-us-senate-general",
    "2026-ca-assembly-47-general",
}
PARTY_LABELS = {
    "DEM": "Democrat",
    "DFL": "Democratic-Farmer-Labor",
    "REP": "Republican",
    "IND": "Independent",
    "LIB": "Libertarian",
    "GRE": "Green",
    "GRN": "Green",
    "CON": "Constitution",
    "NPA": "No party affiliation",
    "NON": "Nonpartisan",
    "UNK": "Unknown",
}
OFFICE_LABELS = {
    "H": "U.S. Representative",
    "S": "U.S. Senator",
    "P": "President of the United States",
}
OFFICE_KEY_PARTS = {
    "H": "house",
    "S": "senate",
    "P": "president",
}
ICI_LABELS = {
    "I": "Incumbent",
    "C": "Challenger",
    "O": "Open seat",
}
STATUS_LABELS = {
    "C": "Statutory candidate",
    "F": "Statutory candidate for future election",
    "N": "Not yet a statutory candidate",
    "P": "Statutory candidate in prior cycle",
}


def seed_race_directory(db: Session) -> None:
    """Seed the federal race directory from a bundled FEC candidate-master snapshot."""
    remove_legacy_placeholder_races(db)
    import_fec_candidate_master_file(db, FEC_CANDIDATE_MASTER_PATH)


def remove_legacy_placeholder_races(db: Session) -> int:
    """Delete the earlier fake/manual race examples without touching user-created races."""
    races = (
        db.query(RaceDirectory)
        .filter(RaceDirectory.race_key.in_(LEGACY_PLACEHOLDER_RACE_KEYS))
        .all()
    )
    for race in races:
        db.delete(race)
    return len(races)


def import_fec_candidate_master_file(db: Session, path: str | Path) -> int:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        return import_fec_candidate_master_rows(db, _read_candidate_master_rows(handle))


def import_fec_candidate_master_zip(db: Session, path: str | Path) -> int:
    with ZipFile(path) as archive:
        with archive.open("cn.txt") as raw:
            text_lines = (line.decode("latin-1") for line in raw)
            return import_fec_candidate_master_rows(db, _read_candidate_master_rows(text_lines))


def import_fec_candidate_master_rows(db: Session, rows: Iterable[dict[str, str]]) -> int:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        if not _is_supported_fec_candidate(row):
            continue
        key = _fec_race_key(row)
        grouped.setdefault(key, []).append(row)

    imported_races = 0
    for race_key, race_rows in grouped.items():
        race_data = _fec_race_data(race_key, race_rows[0])
        race = db.query(RaceDirectory).filter_by(race_key=race_key).first()
        if race:
            for field, value in race_data.items():
                setattr(race, field, value)
            race.updated_at = datetime.utcnow()
        else:
            race = RaceDirectory(**race_data)
            db.add(race)
            db.flush()
            imported_races += 1

        for row in sorted(race_rows, key=lambda item: (item.get("CAND_NAME") or "", item.get("CAND_ID") or "")):
            _upsert_fec_candidate(db, race, row)
    return imported_races


def _read_candidate_master_rows(lines: Iterable[str]) -> Iterable[dict[str, str]]:
    clean_lines = [line for line in lines if line.strip() and not line.startswith("#")]
    if not clean_lines:
        return []
    first_line = clean_lines[0]
    if first_line.startswith("CAND_ID|"):
        return csv.DictReader(clean_lines, delimiter="|")
    return csv.DictReader(clean_lines, fieldnames=FEC_CANDIDATE_FIELDS, delimiter="|")


def _is_supported_fec_candidate(row: dict[str, str]) -> bool:
    return (
        (row.get("CAND_ELECTION_YR") or "").strip() == FEC_ELECTION_YEAR
        and (row.get("CAND_OFFICE") or "").strip() in OFFICE_LABELS
        and (row.get("CAND_STATUS") or "").strip() in {"C", "F"}
        and bool((row.get("CAND_NAME") or "").strip())
    )


def _fec_race_key(row: dict[str, str]) -> str:
    office = OFFICE_KEY_PARTS.get((row.get("CAND_OFFICE") or "").strip(), "federal")
    state = ((row.get("CAND_OFFICE_ST") or "US").strip() or "US").lower()
    district = _normalize_district(row.get("CAND_OFFICE_DISTRICT"))
    if office == "house":
        return f"fec-{FEC_ELECTION_YEAR}-{office}-{state}-{district or '00'}"
    if office == "senate":
        return f"fec-{FEC_ELECTION_YEAR}-{office}-{state}"
    return f"fec-{FEC_ELECTION_YEAR}-{office}-us"


def _fec_race_data(race_key: str, row: dict[str, str]) -> dict:
    office_code = (row.get("CAND_OFFICE") or "").strip()
    state = ((row.get("CAND_OFFICE_ST") or "US").strip() or "US").upper()
    district = _normalize_district(row.get("CAND_OFFICE_DISTRICT"))
    office_name = OFFICE_LABELS[office_code]
    district_label = _district_label(office_code, state, district)
    return {
        "race_key": race_key,
        "race_name": _race_name(office_code, state, district_label),
        "race_level": "federal",
        "office_name": office_name,
        "state": state,
        "district_label": district_label,
        "district_number": _district_number(office_code, district),
        "election_type": "other",
        "election_date": None,
        "geography_summary": _geography_summary(office_code, state, district_label),
        "data_source": "fec",
        "is_active": True,
    }


def _upsert_fec_candidate(db: Session, race: RaceDirectory, row: dict[str, str]) -> RaceCandidate:
    candidate_id = (row.get("CAND_ID") or "").strip()
    candidate_url = f"https://www.fec.gov/data/candidate/{candidate_id}/" if candidate_id else None
    candidate = None
    if candidate_url:
        candidate = (
            db.query(RaceCandidate)
            .filter(RaceCandidate.race_id == race.id, RaceCandidate.campaign_url == candidate_url)
            .first()
        )
    if not candidate:
        candidate_name = (row.get("CAND_NAME") or "").strip()
        candidate = (
            db.query(RaceCandidate)
            .filter(RaceCandidate.race_id == race.id, RaceCandidate.candidate_name == candidate_name)
            .first()
        )
    values = {
        "candidate_name": (row.get("CAND_NAME") or "").strip(),
        "party": _party_label(row.get("CAND_PTY_AFFILIATION")),
        "is_incumbent": (row.get("CAND_ICI") or "").strip() == "I",
        "role": "candidate",
        "campaign_url": candidate_url,
        "notes": _fec_candidate_notes(row),
        "updated_at": datetime.utcnow(),
    }
    if candidate:
        for field, value in values.items():
            setattr(candidate, field, value)
    else:
        candidate = RaceCandidate(race_id=race.id, **values)
        db.add(candidate)
    return candidate


def _normalize_district(value: str | None) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    return raw.zfill(2) if raw.isdigit() else raw


def _district_label(office_code: str, state: str, district: str | None) -> str | None:
    if office_code == "H":
        if not district or district == "00":
            return f"{state}-AL"
        return f"{state}-{district}"
    if office_code == "S":
        return "Statewide"
    return None


def _district_number(office_code: str, district: str | None) -> str | None:
    if office_code != "H" or not district or district == "00":
        return None
    return str(int(district)) if district.isdigit() else district


def _race_name(office_code: str, state: str, district_label: str | None) -> str:
    if office_code == "H":
        label = district_label or f"{state} House"
        return f"{label} U.S. House {FEC_ELECTION_YEAR} Candidate Filings"
    if office_code == "S":
        return f"{state} U.S. Senate {FEC_ELECTION_YEAR} Candidate Filings"
    return f"U.S. President {FEC_ELECTION_YEAR} Candidate Filings"


def _geography_summary(office_code: str, state: str, district_label: str | None) -> str:
    if office_code == "H":
        geography = district_label or state
        return (
            f"{geography} federal candidate filings from the FEC Candidate Master file. "
            "This is not a certified ballot list."
        )
    if office_code == "S":
        return (
            f"Statewide {state} U.S. Senate candidate filings from the FEC Candidate Master file. "
            "This is not a certified ballot list."
        )
    return "Presidential candidate filings from the FEC Candidate Master file. This is not a certified ballot list."


def _party_label(value: str | None) -> str | None:
    code = (value or "").strip().upper()
    if not code:
        return None
    return PARTY_LABELS.get(code, code)


def _fec_candidate_notes(row: dict[str, str]) -> str:
    parts = []
    if row.get("CAND_ID"):
        parts.append(f"FEC candidate ID {row['CAND_ID'].strip()}.")
    if row.get("CAND_STATUS"):
        parts.append(f"Status: {STATUS_LABELS.get(row['CAND_STATUS'].strip(), row['CAND_STATUS'].strip())}.")
    if row.get("CAND_ICI"):
        parts.append(f"FEC incumbent/challenger code: {ICI_LABELS.get(row['CAND_ICI'].strip(), row['CAND_ICI'].strip())}.")
    if row.get("CAND_PCC"):
        parts.append(f"Principal campaign committee {row['CAND_PCC'].strip()}.")
    return " ".join(parts) or None


def list_directory_races(
    db: Session,
    q: str | None = None,
    race_level: str | None = None,
    state: str | None = None,
    active_only: bool = True,
    limit: int = 50,
) -> list[RaceDirectory]:
    query = db.query(RaceDirectory).options(joinedload(RaceDirectory.candidates))
    if active_only:
        query = query.filter(RaceDirectory.is_active.is_(True))
    if race_level:
        query = query.filter(RaceDirectory.race_level == race_level)
    if state:
        query = query.filter(RaceDirectory.state == state.upper())
    if q:
        needle = f"%{q.strip()}%"
        query = query.filter(
            or_(
                RaceDirectory.race_name.ilike(needle),
                RaceDirectory.office_name.ilike(needle),
                RaceDirectory.state.ilike(needle),
                RaceDirectory.district_label.ilike(needle),
                RaceDirectory.geography_summary.ilike(needle),
                RaceDirectory.candidates.any(RaceCandidate.candidate_name.ilike(needle)),
            )
        )
    return (
        query.order_by(
            RaceDirectory.election_date.asc(),
            RaceDirectory.state.asc(),
            RaceDirectory.office_name.asc(),
            RaceDirectory.district_number.asc(),
        )
        .limit(min(max(limit, 1), 100))
        .all()
    )


def get_directory_race(db: Session, race_id: int) -> RaceDirectory:
    race = (
        db.query(RaceDirectory)
        .options(joinedload(RaceDirectory.candidates))
        .filter(RaceDirectory.id == race_id)
        .first()
    )
    if not race:
        raise HTTPException(status_code=404, detail="Race not found")
    return race


def select_directory_race(
    db: Session,
    race_id: int,
    candidate_id: int | None = None,
    candidate_name: str | None = None,
) -> tuple[RaceDirectory, CampaignConfig, RaceCandidate, int, int]:
    race = get_directory_race(db, race_id)
    if not race.is_active:
        raise HTTPException(status_code=400, detail="Race is not active")

    selected = _pick_candidate(race, candidate_id, candidate_name, db.query(CampaignConfig).first())
    campaign = db.query(CampaignConfig).first()
    if not campaign:
        campaign = CampaignConfig(candidate_name=selected.candidate_name)
        db.add(campaign)

    campaign.candidate_name = selected.candidate_name
    campaign.party = selected.party
    campaign.race = race.race_name
    campaign.district = race.district_label
    campaign.office = race.office_name
    campaign.location = race.geography_summary or race.state
    campaign.race_level = race.race_level
    campaign.election_type = race.election_type
    campaign.district_number = race.district_number
    campaign.election_date = race.election_date
    campaign.geography_keywords = json.dumps(
        _merge_terms(
            campaign.geography_keywords,
            [race.state, race.district_label, race.district_number, race.geography_summary],
        )
    )
    campaign.relevance_keywords = json.dumps(
        _merge_terms(
            campaign.relevance_keywords,
            [race.race_name, race.office_name, race.district_label],
        )
    )
    campaign.updated_at = datetime.utcnow()

    created = 0
    updated = 0
    for candidate in race.candidates:
        if candidate.id == selected.id:
            continue
        was_created = _upsert_opponent(db, race, candidate)
        if was_created:
            created += 1
        else:
            updated += 1

    db.commit()
    db.refresh(campaign)
    db.refresh(race)
    return race, campaign, selected, created, updated


def _pick_candidate(
    race: RaceDirectory,
    candidate_id: int | None,
    candidate_name: str | None,
    campaign: CampaignConfig | None,
) -> RaceCandidate:
    candidates = list(race.candidates)
    if not candidates:
        raise HTTPException(status_code=422, detail="Race has no candidates to select")

    if candidate_id is not None:
        for candidate in candidates:
            if candidate.id == candidate_id:
                return candidate
        raise HTTPException(status_code=404, detail="Candidate not found in race")

    if candidate_name:
        candidate_name_key = candidate_name.strip().lower()
        for candidate in candidates:
            if candidate.candidate_name.lower() == candidate_name_key:
                return candidate
        raise HTTPException(status_code=404, detail="Candidate not found in race")

    for candidate in candidates:
        if candidate.role == "candidate":
            return candidate

    if campaign and campaign.candidate_name:
        current_name = campaign.candidate_name.strip().lower()
        for candidate in candidates:
            if candidate.candidate_name.lower() == current_name:
                return candidate

    return candidates[0]


def _upsert_opponent(db: Session, race: RaceDirectory, candidate: RaceCandidate) -> bool:
    existing = _find_opponent_by_name(db, candidate.candidate_name)
    notes = _opponent_notes(race, candidate)
    office = _opponent_office(race)
    if existing:
        existing.office = office
        existing.party = candidate.party or existing.party
        if not existing.notes:
            existing.notes = notes
        return False

    db.add(
        Opponent(
            name=candidate.candidate_name,
            office=office,
            party=candidate.party,
            notes=notes,
            created_at=datetime.utcnow(),
        )
    )
    return True


def _find_opponent_by_name(db: Session, name: str) -> Opponent | None:
    for opponent in db.query(Opponent).all():
        if opponent.name.strip().lower() == name.strip().lower():
            return opponent
    return None


def _opponent_office(race: RaceDirectory) -> str:
    return f"{race.office_name} ({race.district_label})" if race.district_label else race.office_name


def _opponent_notes(race: RaceDirectory, candidate: RaceCandidate) -> str:
    parts = [f"Loaded from race directory: {race.race_name}."]
    if candidate.is_incumbent:
        parts.append("Incumbent.")
    if candidate.notes:
        parts.append(candidate.notes)
    return " ".join(parts)


def _merge_terms(existing_raw: str | None, new_terms: list[str | None]) -> list[str]:
    merged: list[str] = []
    if existing_raw:
        try:
            existing = json.loads(existing_raw)
        except Exception:
            existing = []
        for term in existing:
            if isinstance(term, str) and term.strip() and term.strip() not in merged:
                merged.append(term.strip())
    for term in new_terms:
        if term and term.strip() and term.strip() not in merged:
            merged.append(term.strip())
    return merged
