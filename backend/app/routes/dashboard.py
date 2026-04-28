from collections import Counter
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session, joinedload

from app.db import get_db
from app.models import CampaignConfig, Issue, SourceItem, OpponentActivity, CanvassingNote, GeneratedTalkingPoint
from app.schemas import (
    DashboardOut, DashboardChange, DashboardChangesOut,
    IssueOut, SourceItemOut, OpponentActivityOut,
    SuggestedAction, RiskWarning,
)

router = APIRouter()


def _build_suggested_actions(
    campaign: CampaignConfig | None,
    top_issues: list[Issue],
    risk_sources: list[SourceItem],
    recent_attacks: list[OpponentActivity],
    canvassing_notes: list[CanvassingNote],
) -> list[SuggestedAction]:
    actions: list[SuggestedAction] = []

    # 1. Active opponent attacks need immediate response
    for act in recent_attacks[:2]:
        if act.attack:
            snippet = act.attack[:80] + ("…" if len(act.attack) > 80 else "")
            note = act.contradiction_note or "Prepare a factual rebuttal within 48 hours."
            actions.append(SuggestedAction(
                priority="urgent",
                action=f"Respond to attack: \"{snippet}\"",
                rationale=note[:200],
            ))

    # 2. High-urgency RISK sources
    for source in risk_sources[:1]:
        if source.credibility_note:
            actions.append(SuggestedAction(
                priority="urgent",
                action=f"Address: {source.title[:70]}",
                rationale=source.credibility_note[:200],
            ))

    # 3. Rising high-urgency issues → draft statement
    for issue in top_issues:
        if issue.urgency == "high" and issue.trend == "rising":
            actions.append(SuggestedAction(
                priority="high",
                action=f"Draft issue statement on {issue.name}",
                rationale=f"Rising issue with high urgency ({issue.mention_count} mentions). Get ahead of the narrative with a proactive statement.",
            ))
            break

    # 4. Rising medium issues → canvassing
    for issue in top_issues:
        if issue.urgency == "medium" and issue.trend == "rising":
            actions.append(SuggestedAction(
                priority="high",
                action=f"Schedule canvassing focus on {issue.name}",
                rationale=f"{issue.mention_count} mentions and trending up. Field presence in affected precincts signals responsiveness.",
            ))
            break

    # 5. Field follow-up based on canvassing data
    if canvassing_notes:
        recent_cutoff = datetime.utcnow() - timedelta(days=14)
        recent_notes = [n for n in canvassing_notes if n.date and n.date >= recent_cutoff]
        neg_notes = [n for n in recent_notes if n.sentiment == "negative" and n.issue]
        if neg_notes:
            issue_counts = Counter(n.issue for n in neg_notes)
            top_issue, count = issue_counts.most_common(1)[0]
            actions.append(SuggestedAction(
                priority="medium",
                action=f"Follow up on field feedback: {top_issue}",
                rationale=f"{count} recent negative canvassing contacts cited {top_issue}. Follow-through demonstrates responsiveness.",
            ))

    # 6. Campaign profile completeness
    if campaign and (not campaign.campaign_message or not campaign.election_date):
        actions.append(SuggestedAction(
            priority="medium",
            action="Complete your Campaign Setup",
            rationale="A complete campaign profile (message, election date, priorities) improves AI-generated talking points and contextualises all analysis.",
        ))
    elif not campaign:
        actions.append(SuggestedAction(
            priority="medium",
            action="Set up your Campaign Profile",
            rationale="Add your candidate name, office, district, and core message to personalise all AI outputs.",
        ))

    return actions[:6]


@router.get("/dashboard/changes", response_model=DashboardChangesOut)
def get_dashboard_changes(hours: int = 24, db: Session = Depends(get_db)):
    since = datetime.utcnow() - timedelta(hours=hours)
    changes: list[DashboardChange] = []

    new_sources = (
        db.query(SourceItem)
        .filter(SourceItem.created_at >= since)
        .order_by(SourceItem.created_at.desc())
        .limit(20)
        .all()
    )
    for s in new_sources:
        changes.append(DashboardChange(
            type="new_source",
            title=s.title,
            detail=s.source_name,
            urgency=s.urgency,
            created_at=s.created_at,
        ))

    new_attacks = (
        db.query(OpponentActivity)
        .options(joinedload(OpponentActivity.source_item))
        .filter(
            OpponentActivity.created_at >= since,
            OpponentActivity.attack.isnot(None),
        )
        .order_by(OpponentActivity.created_at.desc())
        .limit(10)
        .all()
    )
    for a in new_attacks:
        changes.append(DashboardChange(
            type="new_attack",
            title=f"Opponent attack: {a.attack[:80]}{'…' if len(a.attack) > 80 else ''}",
            detail=a.source_item.title if a.source_item else None,
            urgency="high",
            created_at=a.created_at,
        ))

    changes.sort(key=lambda c: c.created_at, reverse=True)

    return DashboardChangesOut(
        since_hours=hours,
        changes=changes[:30],
        new_source_count=len(new_sources),
        new_attack_count=len(new_attacks),
    )


@router.get("/dashboard", response_model=DashboardOut)
def get_dashboard(db: Session = Depends(get_db)):
    config = db.query(CampaignConfig).first()
    candidate_name = config.candidate_name if config else "Candidate"
    race = config.race or (config.office + (f", {config.district}" if config.district else "") if config and config.office else "Unknown Race") if config else "Unknown Race"

    top_issues = (
        db.query(Issue)
        .order_by(Issue.mention_count.desc())
        .limit(5)
        .all()
    )

    recent_sources = (
        db.query(SourceItem)
        .order_by(SourceItem.published_at.desc())
        .limit(6)
        .all()
    )

    opponent_activity = (
        db.query(OpponentActivity)
        .options(joinedload(OpponentActivity.source_item))
        .order_by(OpponentActivity.created_at.desc())
        .limit(4)
        .all()
    )

    # Risk warnings: high-urgency sources with credibility notes
    risk_sources = (
        db.query(SourceItem)
        .filter(SourceItem.credibility_note.isnot(None))
        .filter(SourceItem.urgency == "high")
        .all()
    )
    risk_warnings = [
        RiskWarning(
            source_id=s.id,
            source_title=s.title,
            warning=s.credibility_note,
            urgency=s.urgency,
        )
        for s in risk_sources
        if s.credibility_note
    ]

    # Canvassing summary
    all_notes = db.query(CanvassingNote).all()
    canvassing_count = len(all_notes)
    canvassing_summary: str | None = None
    if canvassing_count > 0:
        issue_counts = Counter(n.issue for n in all_notes if n.issue)
        top_two = [i for i, _ in issue_counts.most_common(2)]
        neg = sum(1 for n in all_notes if n.sentiment == "negative")
        pct = round(neg / canvassing_count * 100)
        precincts = len(set(n.precinct for n in all_notes))
        canvassing_summary = (
            f"{canvassing_count} voters canvassed across {precincts} precincts. "
            f"Top concerns: {', '.join(top_two)}. "
            f"{pct}% expressed negative sentiment about current district leadership."
        )

    # Recent opponent attacks for action generation
    recent_attacks = (
        db.query(OpponentActivity)
        .filter(OpponentActivity.attack.isnot(None))
        .order_by(OpponentActivity.created_at.desc())
        .limit(3)
        .all()
    )

    suggested_actions = _build_suggested_actions(
        campaign=config,
        top_issues=top_issues,
        risk_sources=risk_sources,
        recent_attacks=recent_attacks,
        canvassing_notes=all_notes,
    )

    review_queue_count = (
        db.query(SourceItem)
        .filter(SourceItem.reviewed == False)  # noqa: E712
        .filter(SourceItem.dismissed == False)  # noqa: E712
        .count()
    )

    return DashboardOut(
        candidate_name=candidate_name,
        race=race,
        top_issues=[IssueOut.model_validate(i) for i in top_issues],
        recent_sources=[SourceItemOut.model_validate(s) for s in recent_sources],
        opponent_activity=[OpponentActivityOut.model_validate(a) for a in opponent_activity],
        suggested_actions=suggested_actions,
        risk_warnings=risk_warnings,
        canvassing_summary=canvassing_summary,
        review_queue_count=review_queue_count,
        last_updated=datetime.utcnow(),
    )
