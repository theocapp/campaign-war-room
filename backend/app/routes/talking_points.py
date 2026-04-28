import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import CampaignConfig, Issue, IssueMention, OpponentActivity, GeneratedTalkingPoint
from app.schemas import TalkingPointRequest, TalkingPointResponse, GeneratedTalkingPointOut
from app.services import intelligence

router = APIRouter()


def _campaign_dict(config: CampaignConfig) -> dict:
    import json
    prios = config.key_priorities
    if isinstance(prios, str):
        try:
            prios = json.loads(prios)
        except Exception:
            prios = []
    return {
        "candidate_name": config.candidate_name,
        "party": config.party,
        "office": config.office or config.race,
        "district": config.district,
        "location": config.location,
        "campaign_message": config.campaign_message,
        "key_priorities": prios,
    }


@router.post("/talking-points", response_model=TalkingPointResponse)
def generate_talking_points(body: TalkingPointRequest, db: Session = Depends(get_db)):
    issue_obj: Issue | None = None

    if body.issue_id:
        issue_obj = db.get(Issue, body.issue_id)
        if not issue_obj:
            raise HTTPException(status_code=404, detail="Issue not found")
        issue_name = issue_obj.name
        context = issue_obj.summary or ""
    elif body.custom_issue_text:
        issue_name = body.custom_issue_text
        context = ""
    else:
        raise HTTPException(status_code=422, detail="Provide either issue_id or custom_issue_text")

    # Load campaign profile
    campaign = db.query(CampaignConfig).first()
    campaign_profile = _campaign_dict(campaign) if campaign else None

    # Load up to 5 linked sources for this issue
    source_dicts: list[dict] = []
    if issue_obj:
        mentions = (
            db.query(IssueMention)
            .filter_by(issue_id=issue_obj.id)
            .limit(6)
            .all()
        )
        for m in mentions:
            s = m.source_item
            if s:
                source_dicts.append({
                    "title": s.title,
                    "summary": s.summary or "",
                    "source_url": s.source_url,
                    "urgency": s.urgency,
                    "credibility_note": s.credibility_note,
                })

    # Load relevant opponent activities linked to this issue's sources
    opponent_activity_dicts: list[dict] = []
    if issue_obj and source_dicts:
        source_ids = [
            m.source_item_id
            for m in db.query(IssueMention).filter_by(issue_id=issue_obj.id).all()
            if m.source_item_id
        ]
        if source_ids:
            acts = (
                db.query(OpponentActivity)
                .filter(OpponentActivity.source_item_id.in_(source_ids))
                .filter(
                    (OpponentActivity.attack.isnot(None)) |
                    (OpponentActivity.claim.isnot(None))
                )
                .limit(4)
                .all()
            )
            for act in acts:
                opponent_activity_dicts.append({
                    "attack": act.attack,
                    "claim": act.claim,
                    "promise": act.promise,
                    "contradiction_note": act.contradiction_note,
                })

    result = intelligence.generate_talking_points(
        issue_name, body.tone, context,
        campaign_profile=campaign_profile,
        sources=source_dicts if source_dicts else None,
        opponent_activities=opponent_activity_dicts if opponent_activity_dicts else None,
    )

    response = TalkingPointResponse(
        issue=issue_name,
        short_answer=result.get("short_answer", ""),
        long_answer=result.get("long_answer", ""),
        debate_answer=result.get("debate_answer", ""),
        social_post=result.get("social_post", ""),
        risk_warning=result.get("risk_warning"),
        evidence_notes=result.get("evidence_notes", ""),
        source_titles_used=result.get("source_titles_used", []),
        source_urls_used=result.get("source_urls_used", []),
    )

    # Persist to history
    db.add(GeneratedTalkingPoint(
        issue_id=issue_obj.id if issue_obj else None,
        custom_issue_text=body.custom_issue_text if not issue_obj else None,
        issue_name=issue_name,
        tone=body.tone,
        short_answer=response.short_answer,
        long_answer=response.long_answer,
        debate_answer=response.debate_answer,
        social_post=response.social_post,
        risk_warning=response.risk_warning,
        evidence_notes=response.evidence_notes,
        source_titles_used=json.dumps(response.source_titles_used),
        source_urls_used=json.dumps(response.source_urls_used),
    ))
    db.commit()

    return response


@router.get("/talking-points/history", response_model=list[GeneratedTalkingPointOut])
def get_history(limit: int = 20, db: Session = Depends(get_db)):
    return (
        db.query(GeneratedTalkingPoint)
        .order_by(GeneratedTalkingPoint.created_at.desc())
        .limit(min(limit, 100))
        .all()
    )


@router.get("/talking-points/history/{tp_id}", response_model=GeneratedTalkingPointOut)
def get_history_item(tp_id: int, db: Session = Depends(get_db)):
    item = db.get(GeneratedTalkingPoint, tp_id)
    if not item:
        raise HTTPException(status_code=404, detail="Not found")
    return item
