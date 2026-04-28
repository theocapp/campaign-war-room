from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import CampaignConfig, Opponent, SourceItem, Issue, GeneratedTalkingPoint
from app.schemas import SetupStatusOut, SetupChecklistItem

router = APIRouter()


@router.get("/setup/status", response_model=SetupStatusOut)
def get_setup_status(db: Session = Depends(get_db)):
    campaign = db.query(CampaignConfig).first()
    opponent_count = db.query(Opponent).count()
    source_count = db.query(SourceItem).count()
    issue_count = db.query(Issue).count()
    tp_count = db.query(GeneratedTalkingPoint).count()

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
            id="issue_detected",
            label="At least one issue detected",
            complete=issue_count > 0,
            helper_text="Issues are detected automatically when sources are added.",
            action_path="/issues",
        ),
        SetupChecklistItem(
            id="talking_point_generated",
            label="At least one talking point generated",
            complete=tp_count > 0,
            helper_text="Generate your first talking point from the Talking Points page.",
            action_path="/talking",
        ),
    ]

    return SetupStatusOut(
        complete=all(item.complete for item in items),
        items=items,
    )
