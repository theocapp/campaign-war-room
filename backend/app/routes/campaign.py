import json
import logging
from datetime import datetime
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import CampaignConfig
from app.schemas import CampaignProfileOut, CampaignProfileIn, CampaignInitializeResult
from app.services.monitors import auto_setup_monitors
from app.services.campaign_setup import infer_election_date, initialize_campaign

router = APIRouter()
logger = logging.getLogger(__name__)


def _config_to_profile(config: CampaignConfig) -> CampaignProfileOut:
    profile = CampaignProfileOut.model_validate(config)
    if not config.election_date:
        return profile
    inferred = infer_election_date(
        config.election_type,
        config.election_date.year,
        _state_from_location(config.location),
    )
    inferred_flag = bool(inferred and inferred.date() == config.election_date.date())
    return profile.model_copy(update={"election_date_inferred": inferred_flag})


@router.post("/campaign/initialize", response_model=CampaignInitializeResult)
def campaign_initialize(db: Session = Depends(get_db)):
    """Run the full initialization sequence: monitors → ingestion → narrative refresh."""
    result = initialize_campaign(db)
    return CampaignInitializeResult(**result)


@router.get("/campaign", response_model=CampaignProfileOut)
def get_campaign(db: Session = Depends(get_db)):
    config = db.query(CampaignConfig).first()
    if not config:
        config = CampaignConfig(candidate_name="My Campaign")
        db.add(config)
        db.commit()
        db.refresh(config)
    return _config_to_profile(config)


def _election_year(election_date, location: str | None) -> int | None:
    """Best-effort election year from supplied date or current year."""
    if election_date:
        try:
            return election_date.year
        except AttributeError:
            pass
    return datetime.utcnow().year


def _state_from_location(location: str | None) -> str | None:
    """Extract a two-letter state code from a location string, e.g. 'Riverton, PA' → 'PA'."""
    if not location:
        return None
    for part in reversed(location.replace(",", " ").split()):
        if len(part) == 2 and part.isalpha():
            return part.upper()
    return None


@router.put("/campaign", response_model=CampaignProfileOut)
def update_campaign(body: CampaignProfileIn, db: Session = Depends(get_db)):
    config = db.query(CampaignConfig).first()
    if not config:
        config = CampaignConfig()
        db.add(config)

    config.candidate_name = body.candidate_name
    config.party = body.party
    config.race = body.race
    config.district = body.district
    config.office = body.office
    config.location = body.location
    config.race_level = body.race_level
    config.election_type = body.election_type
    config.district_number = body.district_number
    config.neighborhood_keywords = json.dumps(body.neighborhood_keywords) if body.neighborhood_keywords is not None else None
    config.sparse_race_mode = body.sparse_race_mode
    if body.election_date is not None:
        config.election_date = body.election_date
    elif not config.election_date:
        year = _election_year(body.election_date, body.location)
        config.election_date = infer_election_date(body.election_type, year, _state_from_location(body.location))
    config.campaign_message = body.campaign_message
    config.key_priorities = json.dumps(body.key_priorities) if body.key_priorities is not None else None
    config.relevance_keywords = json.dumps(body.relevance_keywords) if body.relevance_keywords is not None else None
    config.excluded_keywords = json.dumps(body.excluded_keywords) if body.excluded_keywords is not None else None
    config.geography_keywords = json.dumps(body.geography_keywords) if body.geography_keywords is not None else None
    config.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(config)

    try:
        auto_setup_monitors(db)
    except Exception as exc:
        logger.warning("auto_setup_monitors failed during campaign update: %s", exc, exc_info=True)

    return _config_to_profile(config)
