from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import CampaignConfig, Opponent, SourceItem, NarrativeFrame
from app.schemas import SetupStatusOut, SetupChecklistItem

router = APIRouter()


@router.get("/setup/status", response_model=SetupStatusOut)
def get_setup_status(db: Session = Depends(get_db)):
    campaign = db.query(CampaignConfig).first()
    opponent_count = db.query(Opponent).count()
    source_count = db.query(SourceItem).count()
    frame_count = db.query(NarrativeFrame).filter(NarrativeFrame.active == True).count()  # noqa: E712

    profile_complete = bool(
        campaign
        and campaign.candidate_name
        and campaign.office
        and campaign.campaign_message
        and campaign.election_date
    )

    items = [
        SetupChecklistItem(
            id="campaign_profile",
            label="Campaign profile completed",
            complete=profile_complete,
            helper_text="Add your candidate name, office, district, and campaign message.",
            action_path="/campaign",
        ),
        SetupChecklistItem(
            id="opponent_added",
            label="At least one opponent added",
            complete=opponent_count > 0,
            helper_text="Add your opponent(s) so attacks and claims can be tracked automatically.",
            action_path="/opponents",
        ),
        SetupChecklistItem(
            id="source_added",
            label="At least one source added",
            complete=source_count > 0,
            helper_text="Add a news source, paste text, fetch a URL, or configure an RSS feed.",
            action_path="/sources",
        ),
        SetupChecklistItem(
            id="narrative_frame_added",
            label="At least one narrative frame defined",
            complete=frame_count > 0,
            helper_text="Define the narrative frames your campaign cares about — your message and the opponent's attacks.",
            action_path="/narratives",
        ),
    ]

    return SetupStatusOut(
        complete=all(item.complete for item in items),
        items=items,
    )
