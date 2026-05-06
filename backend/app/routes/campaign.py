import json
from datetime import datetime
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import CampaignConfig
from app.schemas import CampaignProfileOut, CampaignProfileIn

router = APIRouter()


def _config_to_profile(config: CampaignConfig) -> CampaignProfileOut:
    return CampaignProfileOut.model_validate(config)


@router.get("/campaign", response_model=CampaignProfileOut)
def get_campaign(db: Session = Depends(get_db)):
    config = db.query(CampaignConfig).first()
    if not config:
        config = CampaignConfig(candidate_name="My Campaign")
        db.add(config)
        db.commit()
        db.refresh(config)
    return _config_to_profile(config)


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
    config.election_date = body.election_date
    config.campaign_message = body.campaign_message
    config.key_priorities = json.dumps(body.key_priorities) if body.key_priorities is not None else None
    config.relevance_keywords = json.dumps(body.relevance_keywords) if body.relevance_keywords is not None else None
    config.excluded_keywords = json.dumps(body.excluded_keywords) if body.excluded_keywords is not None else None
    config.geography_keywords = json.dumps(body.geography_keywords) if body.geography_keywords is not None else None
    config.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(config)
    return _config_to_profile(config)
