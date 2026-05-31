import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas import CampaignInitializeResult, RaceDirectoryOut, RaceSelectRequest, RaceSelectResult
from app.services.access_codes import require_admin
from app.services.campaign_setup import initialize_campaign
from app.services.race_directory import (
    get_directory_race,
    list_directory_races,
    select_directory_race,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Selecting a race runs the LLM-heavy initialize_campaign chain — gate it.
# The read endpoints (list / search / detail) stay open.
_admin_only = [Depends(require_admin)]


@router.get("/races", response_model=list[RaceDirectoryOut])
def list_races(
    q: str | None = None,
    race_level: str | None = None,
    state: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    return list_directory_races(
        db,
        q=q,
        race_level=race_level,
        state=state,
        limit=limit,
    )


@router.get("/races/search", response_model=list[RaceDirectoryOut])
def search_races(q: str = "", limit: int = 25, db: Session = Depends(get_db)):
    return list_directory_races(db, q=q, limit=limit)


@router.get("/races/{race_id}", response_model=RaceDirectoryOut)
def get_race(race_id: int, db: Session = Depends(get_db)):
    return get_directory_race(db, race_id)


@router.post("/races/{race_id}/select", response_model=RaceSelectResult, dependencies=_admin_only)
def select_race(
    race_id: int,
    body: RaceSelectRequest | None = None,
    db: Session = Depends(get_db),
):
    body = body or RaceSelectRequest()
    race, campaign, selected, created, updated = select_directory_race(
        db,
        race_id,
        candidate_id=body.candidate_id,
        candidate_name=body.candidate_name,
    )

    init_result = None
    try:
        raw = initialize_campaign(db)
        init_result = CampaignInitializeResult(**raw)
    except Exception:
        logger.exception("initialize_campaign failed after selecting race %s", race_id)

    return RaceSelectResult(
        race=race,
        campaign=campaign,
        selected_candidate_name=selected.candidate_name,
        opponents_created=created,
        opponents_updated=updated,
        message=(
            f"Selected {race.race_name}. Campaign context was prefilled "
            f"for {selected.candidate_name}."
        ),
        init_result=init_result,
    )
