import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import CampaignConfig, CandidateMessageLibrary, CandidateNarrative, Issue, IssueMention, OpponentActivity, GeneratedTalkingPoint, SourceItem
from app.schemas import TalkingPointRequest, TalkingPointResponse, GeneratedTalkingPointOut
from app.services import intelligence
from app.services.story_clustering import unique_by_cluster
from app.services.snapshots import build_source_summary

router = APIRouter()


def _thin_evidence_response(issue_name: str) -> TalkingPointResponse:
    warning = (
        "Evidence is too thin to generate campaign-ready talking points. "
        "Add local reporting, public records, canvassing notes, opponent statements, "
        "or campaign notes that are directly tied to this race."
    )
    return TalkingPointResponse(
        issue=issue_name,
        short_answer=warning,
        long_answer=warning,
        debate_answer=warning,
        social_post="",
        risk_warning="No race-relevant issue-linked sources were available.",
        evidence_notes="No linked source met the minimum race relevance score of 40.",
        source_titles_used=[],
        source_urls_used=[],
    )


def _thin_evidence_response_for_sources(issue_name: str, sources: list[SourceItem], campaign: CampaignConfig | None) -> TalkingPointResponse:
    source_count = len(unique_by_cluster(sources))
    geography = ", ".join(filter(None, [
        campaign.district if campaign else None,
        campaign.location if campaign else None,
        campaign.district_number if campaign else None,
    ]))
    warning = (
        f"Evidence is thin: only {source_count} distinct race-relevant source "
        f"{'cluster is' if source_count == 1 else 'clusters are'} linked to {issue_name}."
    )
    context = f" Race context: {campaign.office or campaign.race} in {geography}." if campaign and geography else ""
    return TalkingPointResponse(
        issue=issue_name,
        short_answer=f"{warning} Verify with another local source before using this as a campaign claim.",
        long_answer=(
            f"{warning}{context} Use this as a research lead, not a finished message. "
            "Add local reporting, public records, opponent/candidate statements, canvassing notes, "
            "or manual notes from forums, flyers, endorsement groups, and election-board pages."
        ),
        debate_answer="Do not use a debate-ready claim yet; the evidence base is too narrow.",
        social_post="",
        risk_warning="Evidence is plausible but not broad enough for confident public messaging.",
        evidence_notes="Duplicate or near-duplicate sources were collapsed before assessing evidence quality.",
        source_titles_used=[s.title for s in sources[:3]],
        source_urls_used=[s.source_url for s in sources[:3] if s.source_url],
    )


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
        "race_level": config.race_level,
        "election_type": config.election_type,
        "district_number": config.district_number,
    }


def _source_strength(source: SourceItem) -> int:
    return (
        int(source.race_relevance_score or 0) * 3
        + int(source.actionability_score or 0) * 2
        + int(source.credibility_score or 50)
        + int(source.evidence_score or 50)
        + (15 if source.opponent_mentioned else 0)
        + (10 if source.candidate_mentioned else 0)
    )


def _json_list(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return [str(x) for x in parsed if str(x).strip()]
    except Exception:
        return [p.strip() for p in value.split(",") if p.strip()]


def _message_guidance(db: Session, issue_name: str) -> CandidateNarrative | None:
    rows = (
        db.query(CandidateNarrative)
        .join(CandidateMessageLibrary, CandidateMessageLibrary.id == CandidateNarrative.library_id)
        .filter(CandidateNarrative.active == True)  # noqa: E712
        .order_by(CandidateNarrative.priority.desc(), CandidateNarrative.created_at.desc())
        .all()
    )
    issue_lower = issue_name.lower()
    for row in rows:
        if row.issue_name and row.issue_name.lower() in issue_lower:
            return row
    for row in rows:
        haystack = " ".join(filter(None, [row.short_label, row.canonical_text, row.issue_name])).lower()
        if issue_lower in haystack or any(part in haystack for part in issue_lower.split() if len(part) > 4):
            return row
    return None


def _apply_message_guidance(response: TalkingPointResponse, narrative: CandidateNarrative | None) -> TalkingPointResponse:
    if not narrative:
        return response
    preferred = _json_list(narrative.preferred_phrases)
    avoid = _json_list(narrative.avoid_phrases) + _json_list(narrative.red_lines)
    phrase = preferred[0] if preferred else narrative.canonical_text
    if phrase and phrase.lower() not in response.short_answer.lower():
        response.short_answer = f"{phrase}. {response.short_answer}"
    if phrase and phrase.lower() not in response.long_answer.lower():
        response.long_answer = f"Campaign frame: {phrase}. {response.long_answer}"
    for banned in avoid:
        if not banned:
            continue
        for field in ["short_answer", "long_answer", "debate_answer", "social_post"]:
            value = getattr(response, field)
            setattr(response, field, value.replace(banned, "[avoid phrase]"))
    note = f" Candidate message library frame used: {narrative.short_label}."
    response.evidence_notes = (response.evidence_notes or "") + note
    return response


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
    candidate_message = _message_guidance(db, issue_name)

    # Load strongest distinct source clusters for this issue.
    source_dicts: list[dict] = []
    selected_sources: list[SourceItem] = []
    if issue_obj:
        mentions = (
            db.query(IssueMention)
            .join(SourceItem, SourceItem.id == IssueMention.source_item_id)
            .filter(IssueMention.issue_id == issue_obj.id)
            .filter(SourceItem.race_relevance_score >= 40)
            .filter(SourceItem.archived_as_irrelevant == False)  # noqa: E712
            .filter(SourceItem.content_category != "irrelevant")
            .all()
        )
        candidates = [m.source_item for m in mentions if m.source_item]
        candidates.sort(key=_source_strength, reverse=True)
        selected_sources = unique_by_cluster(candidates)[:5]
        for s in selected_sources:
            source_dicts.append({
                "title": s.title,
                "summary": build_source_summary(s),
                "source_url": s.source_url,
                "urgency": s.urgency,
                "credibility_note": s.credibility_note,
                "race_relevance_score": s.race_relevance_score,
                "source_name": s.source_name,
                "source_type": s.source_type,
            })

        if not source_dicts:
            response = _thin_evidence_response(issue_name)
            db.add(GeneratedTalkingPoint(
                issue_id=issue_obj.id,
                custom_issue_text=None,
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
        if len(selected_sources) < 2:
            response = _thin_evidence_response_for_sources(issue_name, selected_sources, campaign)
            db.add(GeneratedTalkingPoint(
                issue_id=issue_obj.id,
                custom_issue_text=None,
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
    else:
        response = _thin_evidence_response(issue_name)
        db.add(GeneratedTalkingPoint(
            issue_id=None,
            custom_issue_text=body.custom_issue_text,
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

    # Load relevant opponent activities linked to this issue's sources
    opponent_activity_dicts: list[dict] = []
    if issue_obj and source_dicts:
        source_ids = [s.id for s in selected_sources]
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
    response = _apply_message_guidance(response, candidate_message)

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
