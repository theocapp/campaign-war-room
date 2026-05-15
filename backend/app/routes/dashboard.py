from collections import Counter
from datetime import datetime, timedelta
import json
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session, joinedload

from app.db import get_db
from app.models import CampaignConfig, Issue, IssueMention, SourceItem, Opponent, OpponentActivity, CanvassingNote, GeneratedTalkingPoint, SourceMonitor, ManualCapture
from app.schemas import (
    DashboardOut, DashboardChange, DashboardChangesOut,
    IssueOut, SourceItemOut, OpponentActivityOut,
    SuggestedAction, RiskWarning, SourceCoverageDiagnostic,
    DashboardAttentionCard, DashboardDevelopment, DashboardOpponentWatch,
    DashboardPriorityIssue, DashboardRaceHeader, DashboardReadiness,
    DashboardReviewQueueItem, DashboardReviewSnapshot, DashboardNarrativeCard,
    DashboardNarrativeComparison,
)
from app.routes.narratives import compare_narratives
from app.knowledge_graph.orm import KGClaim, KGNarrative, KGNarrativeClaim
from app.services.narrative_briefing import build_brief_cards
from app.services.story_clustering import unique_by_cluster
from app.services.snapshots import source_out

router = APIRouter()


def _issue_names(source: SourceItem) -> list[str]:
    return [m.issue.name for m in source.issue_mentions if m.issue]


def _cluster_key(source: SourceItem) -> str:
    return source.story_cluster_id or f"source-{source.id}"


def _source_sort_score(source: SourceItem) -> int:
    return (
        int(source.priority_score or 0)
        + int(source.race_relevance_score or 0)
        + int(source.actionability_score or 0)
        + (20 if source.actionability_label == "respond" else 0)
    )


def _active_relevant_sources(db: Session, limit: int = 150) -> list[SourceItem]:
    return (
        db.query(SourceItem)
        .options(joinedload(SourceItem.issue_mentions).joinedload(IssueMention.issue))
        .filter(SourceItem.archived_as_irrelevant == False)  # noqa: E712
        .filter(SourceItem.content_category != "irrelevant")
        .filter(SourceItem.race_relevance_score >= 40)
        .order_by(SourceItem.published_at.desc())
        .limit(limit)
        .all()
    )


def _cluster_counts(sources: list[SourceItem]) -> Counter:
    return Counter(_cluster_key(source) for source in sources)


def _campaign_connection(source: SourceItem | None) -> str:
    if not source:
        return "Campaign connection: linked opponent activity."
    reasons: list[str] = []
    if source.relevance_reasons:
        try:
            reasons = [str(r) for r in json.loads(source.relevance_reasons)]
        except Exception:
            reasons = [source.relevance_reasons]
    reason = reasons[0] if reasons else source.race_relevance_label
    return f"Campaign connection: {reason} (relevance {source.race_relevance_score}/100, action {source.actionability_label})."


def _build_suggested_actions(
    campaign: CampaignConfig | None,
    top_issues: list[Issue],
    risk_sources: list[SourceItem],
    recent_attacks: list[OpponentActivity],
    canvassing_notes: list[CanvassingNote],
    evidence_sources: list[SourceItem] | None = None,
    coverage: SourceCoverageDiagnostic | None = None,
) -> list[SuggestedAction]:
    actions: list[SuggestedAction] = []
    evidence_by_issue: dict[str, list[SourceItem]] = {}
    require_issue_evidence = evidence_sources is not None
    for source in unique_by_cluster(evidence_sources or []):
        for mention in source.issue_mentions:
            if mention.issue:
                evidence_by_issue.setdefault(mention.issue.name, []).append(source)

    # 1. Active opponent attacks become evidence signals, not instructions
    for act in recent_attacks[:2]:
        if act.attack and (not act.source_item or (act.source_item.race_relevance_score or 0) >= 50):
            snippet = act.attack[:80] + ("…" if len(act.attack) > 80 else "")
            note = act.contradiction_note or "Opponent-aligned attack appears in race-relevant evidence."
            connection = _campaign_connection(act.source_item)
            actions.append(SuggestedAction(
                priority="urgent",
                action=f"Opponent-aligned attack confirmed: \"{snippet}\"",
                rationale=f"{connection} {note}"[:240],
            ))

    # 2. High-urgency RISK sources
    for source in risk_sources[:1]:
        if source.credibility_note:
            actions.append(SuggestedAction(
                priority="urgent",
                action=f"High-urgency source signal: {source.title[:70]}",
                rationale=f"{_campaign_connection(source)} {source.credibility_note}"[:240],
            ))

    # 3. Rising high-urgency issues
    for issue in top_issues:
        supporting = evidence_by_issue.get(issue.name, [])
        if issue.urgency == "high" and issue.trend == "rising" and (not require_issue_evidence or len(supporting) >= 2):
            actions.append(SuggestedAction(
                priority="high",
                action=f"{issue.name} is rising in race-relevant evidence",
                rationale=(
                    f"Supported by {len(supporting)} distinct race-relevant source clusters. Confidence depends on the cited evidence."
                    if require_issue_evidence
                    else f"Rising issue with high urgency ({issue.mention_count} mentions). Treat this as evidence for campaign judgment, not a decision."
                ),
            ))
            break

    # 4. Rising medium issues
    for issue in top_issues:
        supporting = evidence_by_issue.get(issue.name, [])
        if issue.urgency == "medium" and issue.trend == "rising" and (not require_issue_evidence or len(supporting) >= 2):
            actions.append(SuggestedAction(
                priority="high",
                action=f"{issue.name} is appearing more often",
                rationale=(
                    f"{len(supporting)} distinct relevant source clusters mention this issue. Field follow-up can verify whether it is showing up with voters."
                    if require_issue_evidence
                    else f"{issue.mention_count} mentions and trending up. Coverage is still a signal, not proof of voter salience."
                ),
            ))
            break

    if coverage and coverage.source_coverage_strength == "weak":
        actions.append(SuggestedAction(
            priority="medium",
            action="Coverage is thin; confidence is limited",
            rationale="; ".join(coverage.reasons[:2]) or "The dashboard has too little race-relevant evidence to support confident recommendations.",
        ))

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
                action=f"Field feedback is clustering around {top_issue}",
                rationale=f"{count} recent negative canvassing contacts cited {top_issue}. Treat this as a field signal to compare against source evidence.",
            ))

    # 6. Campaign profile completeness
    if campaign and (not campaign.campaign_message or not campaign.election_date):
        actions.append(SuggestedAction(
            priority="medium",
            action="Campaign setup context is incomplete",
            rationale="A complete campaign profile (message, election date, priorities) improves AI-generated talking points and contextualises all analysis.",
        ))
    elif not campaign:
        actions.append(SuggestedAction(
            priority="medium",
            action="Campaign profile context is missing",
            rationale="Add your candidate name, office, district, and core message to personalise all AI outputs.",
        ))

    return actions[:6]


def _coverage_diagnostic(db: Session, campaign: CampaignConfig | None, sources: list[SourceItem]) -> SourceCoverageDiagnostic:
    relevant = [
        s for s in sources
        if not s.archived_as_irrelevant and s.content_category != "irrelevant" and (s.race_relevance_score or 0) >= 40
    ]
    unique_relevant = unique_by_cluster(relevant)
    manual_capture_source_ids = {
        row[0]
        for row in db.query(ManualCapture.source_item_id).all()
    }
    manual_types = {"campaign_note", "public_record", "manual", "webpage", "social"}
    manual_count = sum(1 for s in unique_relevant if s.source_type in manual_types or s.id in manual_capture_source_ids)
    manual_capture_count = sum(1 for s in unique_relevant if s.id in manual_capture_source_ids)
    manual_ratio = (manual_count / len(unique_relevant)) if unique_relevant else 0

    if len(unique_relevant) >= 8:
        strength = "strong"
    elif len(unique_relevant) >= 3:
        strength = "moderate"
    else:
        strength = "weak"

    if manual_ratio >= 0.67:
        manual_dep = "high"
    elif manual_ratio >= 0.34:
        manual_dep = "medium"
    else:
        manual_dep = "low"

    reasons: list[str] = []
    if not unique_relevant:
        reasons.append("No distinct race-relevant source clusters are available yet.")
    elif len(unique_relevant) < 3:
        reasons.append(f"Only {len(unique_relevant)} distinct race-relevant source cluster is available.")
    duplicate_count = len(relevant) - len(unique_relevant)
    if duplicate_count > 0:
        reasons.append(f"{duplicate_count} duplicate or near-duplicate source item was collapsed for quality checks.")
    if campaign and campaign.sparse_race_mode:
        reasons.append("Sparse race mode is active, so weaker local evidence is surfaced with caution.")
    if manual_dep == "high" and unique_relevant:
        reasons.append("Coverage depends heavily on manual, public-record, webpage, or social sources.")
    if manual_capture_count:
        reasons.append(
            f"{manual_capture_count} distinct manual capture source "
            f"{'is' if manual_capture_count == 1 else 'are'} carrying the current intelligence base."
        )
    if campaign and campaign.sparse_race_mode and manual_capture_count and len(unique_relevant) < 5:
        reasons.append("Public web coverage is thin; manually captured campaign material is especially important for this race.")

    geography_gaps: list[str] = []
    if campaign:
        raw_geo = [
            campaign.district,
            campaign.location,
            campaign.district_number,
        ]
        if campaign.neighborhood_keywords:
            try:
                raw_geo.extend(json.loads(campaign.neighborhood_keywords))
            except Exception:
                pass
        for term in [str(g).strip() for g in raw_geo if g]:
            if not any(term.lower() in " ".join(filter(None, [s.title, s.raw_text, s.summary])).lower() for s in unique_relevant):
                geography_gaps.append(term)

    issue_gaps: list[str] = []
    if campaign and campaign.key_priorities:
        try:
            priorities = json.loads(campaign.key_priorities) if isinstance(campaign.key_priorities, str) else campaign.key_priorities
        except Exception:
            priorities = []
        issue_names = {
            m.issue.name
            for s in unique_relevant
            for m in s.issue_mentions
            if m.issue
        }
        issue_gaps = [p for p in priorities or [] if not any(str(p).lower() in name.lower() for name in issue_names)]

    mode = "sparse" if campaign and campaign.sparse_race_mode else "high-coverage"
    monitors = db.query(SourceMonitor).filter(SourceMonitor.active == True).count()  # noqa: E712
    if monitors == 0:
        reasons.append("No active monitors are configured.")

    return SourceCoverageDiagnostic(
        source_coverage_strength=strength,
        manual_source_dependence=manual_dep,
        geography_coverage_gaps=geography_gaps[:6],
        issue_coverage_gaps=[str(i) for i in issue_gaps[:6]],
        race_coverage_mode=mode,
        reasons=reasons,
    )


def _build_race_header(
    campaign: CampaignConfig | None,
    opponents: list[Opponent],
    coverage: SourceCoverageDiagnostic,
    race: str,
) -> DashboardRaceHeader:
    return DashboardRaceHeader(
        candidate_name=campaign.candidate_name if campaign else "Candidate",
        race=race,
        office=campaign.office if campaign else None,
        district=campaign.district if campaign else None,
        election_type=campaign.election_type if campaign else None,
        opponents=[o.name for o in opponents[:3]],
        race_mode=coverage.race_coverage_mode,
        source_coverage_strength=coverage.source_coverage_strength,
    )


def _review_sources(sources: list[SourceItem]) -> list[SourceItem]:
    candidates = [
        s for s in sources
        if not s.reviewed and not s.dismissed and (
            (s.race_relevance_score or 0) >= 40 or s.actionability_label in {"review", "respond"}
        )
    ]
    unique = unique_by_cluster(sorted(candidates, key=_source_sort_score, reverse=True))
    return unique


def _build_review_snapshot(sources: list[SourceItem]) -> DashboardReviewSnapshot:
    queue = _review_sources(sources)
    top_items: list[DashboardReviewQueueItem] = []
    for source in queue[:3]:
        issues = _issue_names(source)
        top_items.append(DashboardReviewQueueItem(
            source_id=source.id,
            title=source.title,
            issue=issues[0] if issues else None,
            relevance_label=source.race_relevance_label,
            relevance_score=source.race_relevance_score or 0,
            actionability_label=source.actionability_label,
            source_type=source.source_type,
            geography=source.geo_relevance if source.geo_relevance != "none" else None,
        ))
    return DashboardReviewSnapshot(
        review_worthy_count=len(queue),
        respond_now_count=sum(1 for s in queue if s.actionability_label == "respond"),
        top_items=top_items,
    )


def _issue_sources(issue: Issue, sources: list[SourceItem]) -> list[SourceItem]:
    matched = [
        s for s in sources
        if any(m.issue_id == issue.id and (m.link_strength or 0) >= 30 for m in s.issue_mentions)
    ]
    return unique_by_cluster(sorted(matched, key=_source_sort_score, reverse=True))


def _build_priority_issues(top_issues: list[Issue], sources: list[SourceItem]) -> list[DashboardPriorityIssue]:
    curated: list[DashboardPriorityIssue] = []
    for issue in top_issues:
        linked = _issue_sources(issue, sources)
        if not linked:
            continue
        count = len(linked)
        avg_evidence = sum((s.evidence_score or 50) + (s.credibility_score or 50) for s in linked) / (2 * count)
        confidence = "strong" if count >= 3 and avg_evidence >= 65 else ("moderate" if count >= 2 else "thin")
        geos = [s.geo_relevance for s in linked if s.geo_relevance and s.geo_relevance != "none"]
        geo = Counter(geos).most_common(1)[0][0] if geos else None
        if issue.trend == "rising" and count >= 2:
            why = f"Rising with {count} distinct relevant developments."
        elif confidence == "thin":
            why = "Potentially important, but evidence is still narrow."
        else:
            why = f"{count} distinct relevant developments are attached."
        curated.append(DashboardPriorityIssue(
            issue_id=issue.id,
            name=issue.name,
            distinct_development_count=count,
            trend=issue.trend,
            evidence_confidence=confidence,
            geography_concentration=geo,
            why_now=why,
        ))
    return curated[:5]


def _build_opponent_watch(activities: list[OpponentActivity]) -> DashboardOpponentWatch:
    relevant = [
        a for a in activities
        if not a.source_item or (not a.source_item.archived_as_irrelevant and a.source_item.content_category != "irrelevant")
    ]
    themes = [a.repeated_theme for a in relevant if a.repeated_theme]
    repeated = [name for name, count in Counter(themes).most_common(3) if count >= 1]
    latest_attack_activity = next((a for a in relevant if a.attack), None)
    latest_attack = latest_attack_activity.attack if latest_attack_activity else None
    source = latest_attack_activity.source_item if latest_attack_activity else None
    if latest_attack:
        status = "response-needed"
        summary = "Latest notable opponent attack should be reviewed for response."
    elif repeated:
        status = "monitor"
        summary = "Opponent activity has repeated themes but no clear response trigger."
    else:
        status = "quiet"
        summary = "No notable recent opponent narrative shift."
    return DashboardOpponentWatch(
        repeated_themes=repeated,
        latest_attack=latest_attack,
        source_item_id=source.id if source else None,
        source_title=source.title if source else None,
        source_name=source.source_name if source else None,
        source_url=source.source_url if source else None,
        source_created_at=(source.published_at or source.created_at) if source else None,
        response_status=status,
        summary=summary,
    )


def _build_narrative_cards(db: Session) -> list[DashboardNarrativeCard]:
    from sqlalchemy.orm import joinedload
    narratives = (
        db.query(KGNarrative)
        .options(
            joinedload(KGNarrative.claim_links)
            .joinedload(KGNarrativeClaim.claim)
            .joinedload(KGClaim.source)
        )
        .filter(KGNarrative.status == "active")
        .order_by(KGNarrative.velocity_score.desc())
        .limit(5)
        .all()
    )
    return build_brief_cards(db=db, narratives=narratives, limit=5)
def _build_attention_cards(
    review_snapshot: DashboardReviewSnapshot,
    priority_issues: list[DashboardPriorityIssue],
    opponent_watch: DashboardOpponentWatch,
    coverage: SourceCoverageDiagnostic,
    risk_sources: list[SourceItem],
    narrative_cards: list[DashboardNarrativeCard] | None = None,
) -> list[DashboardAttentionCard]:
    cards: list[DashboardAttentionCard] = []
    top_narrative = next((n for n in narrative_cards or [] if n.status == "rising" or n.owner_type == "opponent"), None)
    if top_narrative:
        priority = "urgent" if top_narrative.response_status == "response_ready" else ("high" if top_narrative.status == "rising" else "medium")
        cards.append(DashboardAttentionCard(
            card_type="narrative",
            priority=priority,
            title=f"{top_narrative.short_label}",
            explanation=top_narrative.why_it_matters,
            action_label="confirmed" if top_narrative.response_status == "response_ready" else "watch",
            destination=f"/narratives/{top_narrative.narrative_id}",
        ))
    if opponent_watch.latest_attack and not top_narrative:
        cards.append(DashboardAttentionCard(
            card_type="opponent_attack",
            priority="urgent",
            title="Opponent attack confirmed in evidence",
            explanation=opponent_watch.latest_attack[:150],
            action_label="confirmed",
            destination="/opponents",
        ))
    if review_snapshot.respond_now_count:
        cards.append(DashboardAttentionCard(
            card_type="review_queue",
            priority="urgent",
            title=f"{review_snapshot.respond_now_count} high-signal source needs review",
            explanation="Inspect the strongest source evidence before drawing a campaign conclusion.",
            action_label="review evidence",
            destination="/review",
        ))
    strong_rising = next((i for i in priority_issues if i.trend == "rising" and i.evidence_confidence in {"strong", "moderate"}), None)
    if strong_rising:
        cards.append(DashboardAttentionCard(
            card_type="rising_issue",
            priority="high",
            title=f"{strong_rising.name} is moving",
            explanation=strong_rising.why_now,
            action_label="rising",
            destination="/issues",
        ))
    thin = next((i for i in priority_issues if i.evidence_confidence == "thin"), None)
    if thin:
        cards.append(DashboardAttentionCard(
            card_type="thin_evidence",
            priority="medium",
            title=f"{thin.name} evidence is thin",
            explanation="Coverage is thin; confidence is limited until another local source confirms it.",
            action_label="thin evidence",
            destination="/sources",
        ))
    if risk_sources:
        cards.append(DashboardAttentionCard(
            card_type="weak_evidence_warning",
            priority="medium",
            title="Verify high-urgency source before messaging",
            explanation=risk_sources[0].credibility_note or risk_sources[0].title,
            action_label="verify evidence",
            destination=f"/sources",
        ))
    if coverage.source_coverage_strength == "weak":
        cards.append(DashboardAttentionCard(
            card_type="coverage_gap",
            priority="medium",
            title="Coverage is not strong enough yet",
            explanation=(coverage.reasons[0] if coverage.reasons else "Add more race-specific sources before relying on dashboard conclusions."),
            action_label="coverage gap",
            destination="/monitors",
        ))
    if coverage.manual_source_dependence == "high":
        cards.append(DashboardAttentionCard(
            card_type="manual_source_warning",
            priority="medium",
            title="Manual sources are carrying the briefing",
            explanation="Useful for low-information races, but verify claims before public messaging.",
            action_label="source mix",
            destination="/sources",
        ))
    return cards[:5]


def _build_readiness(
    coverage: SourceCoverageDiagnostic,
    priority_issues: list[DashboardPriorityIssue],
) -> DashboardReadiness:
    ready = [i.name for i in priority_issues if i.evidence_confidence in {"strong", "moderate"}]
    thin = [i.name for i in priority_issues if i.evidence_confidence == "thin"]
    sparse_note = None
    if coverage.race_coverage_mode == "sparse":
        sparse_note = "Sparse race mode is active; favor local manual captures, public records, forums, and direct statements."
    return DashboardReadiness(
        coverage_strength=coverage.source_coverage_strength,
        manual_source_dependence=coverage.manual_source_dependence,
        geography_gaps=coverage.geography_coverage_gaps[:4],
        issue_gaps=coverage.issue_coverage_gaps[:4],
        ready_to_message_issues=ready[:4],
        thin_evidence_issues=thin[:4],
        sparse_race_note=sparse_note,
        reasons=coverage.reasons[:4],
    )


def _build_developments(sources: list[SourceItem]) -> list[DashboardDevelopment]:
    sorted_sources = unique_by_cluster(sorted(sources, key=lambda s: (s.published_at or s.created_at, _source_sort_score(s)), reverse=True))
    counts = _cluster_counts(sources)
    developments: list[DashboardDevelopment] = []
    for source in sorted_sources[:5]:
        issues = _issue_names(source)
        issue = issues[0] if issues else None
        if source.actionability_label == "respond":
            why = "Potential response item."
        elif issue:
            why = f"Adds evidence on {issue}."
        else:
            why = "Race-relevant development worth monitoring."
        developments.append(DashboardDevelopment(
            cluster_id=_cluster_key(source),
            title=source.title,
            issue=issue,
            why_it_matters=why,
            source_count=counts[_cluster_key(source)],
            recency=source.published_at or source.created_at,
            source_id=source.id,
        ))
    return developments


@router.get("/dashboard/changes", response_model=DashboardChangesOut)
def get_dashboard_changes(hours: int = 24, db: Session = Depends(get_db)):
    since = datetime.utcnow() - timedelta(hours=hours)
    changes: list[DashboardChange] = []

    new_sources = (
        db.query(SourceItem)
        .filter(SourceItem.created_at >= since)
        .filter(SourceItem.archived_as_irrelevant == False)  # noqa: E712
        .filter(SourceItem.content_category != "irrelevant")
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
    for a in [a for a in new_attacks if not a.source_item or not a.source_item.archived_as_irrelevant]:
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
    opponents = db.query(Opponent).order_by(Opponent.created_at.asc()).all()

    top_issues = (
        db.query(Issue)
        .order_by(Issue.mention_count.desc())
        .limit(5)
        .all()
    )

    source_pool = _active_relevant_sources(db)
    recent_sources = unique_by_cluster(source_pool)[:5]

    all_sources_for_diagnostic = (
        db.query(SourceItem)
        .all()
    )
    coverage = _coverage_diagnostic(db, config, all_sources_for_diagnostic)

    opponent_activity = (
        db.query(OpponentActivity)
        .options(joinedload(OpponentActivity.source_item))
        .order_by(OpponentActivity.created_at.desc())
        .limit(4)
        .all()
    )
    opponent_activity = [
        a for a in opponent_activity
        if not a.source_item or (
            not a.source_item.archived_as_irrelevant and a.source_item.content_category != "irrelevant"
        )
    ]

    # Risk warnings: high-urgency sources with credibility notes
    risk_sources = (
        db.query(SourceItem)
        .filter(SourceItem.credibility_note.isnot(None))
        .filter(SourceItem.urgency == "high")
        .filter(SourceItem.archived_as_irrelevant == False)  # noqa: E712
        .filter(SourceItem.content_category != "irrelevant")
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
        .options(joinedload(OpponentActivity.source_item))
        .filter(OpponentActivity.attack.isnot(None))
        .order_by(OpponentActivity.created_at.desc())
        .limit(3)
        .all()
    )
    recent_attacks = [
        a for a in recent_attacks
        if not a.source_item or (
            not a.source_item.archived_as_irrelevant and a.source_item.content_category != "irrelevant"
        )
    ]

    review_snapshot = _build_review_snapshot(source_pool)
    priority_issues = _build_priority_issues(top_issues, source_pool)
    opponent_watch = _build_opponent_watch(opponent_activity)
    narrative_briefing = _build_narrative_cards(db)
    comparison = compare_narratives(db=db)
    narrative_comparison = DashboardNarrativeComparison(
        top_opponent=comparison.top_opponent_narratives,
        top_candidate=comparison.top_candidate_narratives,
        candidate_owned_only=comparison.candidate_owned_only,
        candidate_broader_spread=comparison.candidate_broader_spread,
        needs_response=comparison.needs_response,
        ready_to_amplify=comparison.ready_to_amplify,
        summary=comparison.summary,
    )
    coverage_readiness = _build_readiness(coverage, priority_issues)
    recent_developments = _build_developments(source_pool)
    attention_now = _build_attention_cards(
        review_snapshot=review_snapshot,
        priority_issues=priority_issues,
        opponent_watch=opponent_watch,
        coverage=coverage,
        risk_sources=risk_sources,
        narrative_cards=narrative_briefing,
    )
    race_header = _build_race_header(config, opponents, coverage, race)

    suggested_actions = _build_suggested_actions(
        campaign=config,
        top_issues=top_issues,
        risk_sources=risk_sources,
        recent_attacks=recent_attacks,
        canvassing_notes=all_notes,
        evidence_sources=source_pool,
        coverage=coverage,
    )

    review_queue_count = (
        db.query(SourceItem)
        .filter(SourceItem.archived_as_irrelevant == False)  # noqa: E712
        .filter(SourceItem.reviewed == False)  # noqa: E712
        .filter(SourceItem.dismissed == False)  # noqa: E712
        .filter(
            (SourceItem.race_relevance_score >= 40)
            | (SourceItem.actionability_label.in_(["review", "respond"]))
        )
        .all()
    )
    review_queue_count = len(unique_by_cluster(review_queue_count))

    return DashboardOut(
        candidate_name=candidate_name,
        race=race,
        top_issues=[IssueOut.model_validate(i) for i in top_issues],
        recent_sources=[source_out(s) for s in recent_sources],
        opponent_activity=[OpponentActivityOut.model_validate(a) for a in opponent_activity],
        suggested_actions=suggested_actions,
        risk_warnings=risk_warnings,
        canvassing_summary=canvassing_summary,
        review_queue_count=review_queue_count,
        source_coverage=coverage,
        race_header=race_header,
        attention_now=attention_now,
        review_snapshot=review_snapshot,
        priority_issues=priority_issues,
        opponent_watch=opponent_watch,
        narrative_briefing=narrative_briefing,
        narrative_comparison=narrative_comparison,
        coverage_readiness=coverage_readiness,
        recent_developments=recent_developments,
        last_updated=datetime.utcnow(),
    )



@router.get("/briefing/today")
def get_daily_briefing(db: Session = Depends(get_db)):
    from app.services.daily_briefing import generate_daily_briefing
    return generate_daily_briefing(db)


@router.get("/briefing/morning")
def get_morning_briefing(db: Session = Depends(get_db)):
    """
    Single-page briefing: new articles, narrative pulse, needs-response, LLM race-situation memo.
    """
    from datetime import datetime, timedelta
    from sqlalchemy import func
    from app.models import SourceItem, NarrativeFrame, NarrativeFrameMention, Opponent
    from app.services import briefing_summary as briefing_svc

    cutoff_24h = datetime.utcnow() - timedelta(hours=24)
    cutoff_48h = datetime.utcnow() - timedelta(hours=48)
    cutoff_7d  = datetime.utcnow() - timedelta(days=7)
    cutoff_14d = datetime.utcnow() - timedelta(days=14)

    def _item_dict(item):
        return {
            "id": item.id,
            "title": item.title,
            "summary": item.summary,
            "source_name": item.source_name,
            "source_url": item.source_url,
            "published_at": item.published_at.isoformat() if item.published_at else None,
            "race_relevance_score": item.race_relevance_score,
            "actionability_label": item.actionability_label,
            "framing": getattr(item, "framing", None),
        }

    # Section 1 — Needs a response right now (published in last 48h)
    respond = (
        db.query(SourceItem)
        .filter(
            SourceItem.archived_as_irrelevant == False,
            SourceItem.published_at >= cutoff_48h,
            SourceItem.actionability_label == "respond",
        )
        .order_by(SourceItem.race_relevance_score.desc())
        .limit(5)
        .all()
    )

    # Section 2 — New since yesterday (published in last 48h, top relevant)
    respond_ids = {i.id for i in respond}
    new_articles_raw = (
        db.query(SourceItem)
        .filter(
            SourceItem.archived_as_irrelevant == False,
            SourceItem.published_at >= cutoff_48h,
            SourceItem.race_relevance_score >= 50,
        )
        .order_by(SourceItem.race_relevance_score.desc())
        .limit(50)
        .all()
    )

    def _is_llm_scored(item) -> bool:
        """Return False for articles whose summary looks like a raw RSS excerpt (not LLM-generated)."""
        s = item.summary or ""
        if "<" in s:
            return False
        if "[...]" in s:
            return False
        # Station attribution patterns: "(WBRE/WYOU) —" anywhere in summary
        if " —" in s and ("(WB" in s or "(WN" in s or "(WY" in s or "(AP)" in s):
            return False
        return True

    new_articles = [
        a for a in new_articles_raw
        if a.id not in respond_ids and _is_llm_scored(a)
    ][:5]

    # Section 3 — Narrative pulse
    frames = db.query(NarrativeFrame).filter(NarrativeFrame.active == True).all()
    pulse = []
    for frame in frames:
        this_week = (
            db.query(func.count(NarrativeFrameMention.id))
            .filter(NarrativeFrameMention.frame_id == frame.id,
                    NarrativeFrameMention.created_at >= cutoff_7d)
            .scalar()
        )
        last_week = (
            db.query(func.count(NarrativeFrameMention.id))
            .filter(NarrativeFrameMention.frame_id == frame.id,
                    NarrativeFrameMention.created_at >= cutoff_14d,
                    NarrativeFrameMention.created_at < cutoff_7d)
            .scalar()
        )
        trend = "up" if this_week > last_week else ("down" if this_week < last_week else "flat")
        pulse.append({
            "id": frame.id,
            "name": frame.name,
            "owner_type": frame.owner_type,
            "this_week": this_week,
            "last_week": last_week,
            "trend": trend,
        })
    pulse.sort(key=lambda x: x["this_week"], reverse=True)

    # Meta — ingested uses created_at, relevant uses published_at + same quality filter as new_articles
    total_today = (
        db.query(func.count(SourceItem.id))
        .filter(SourceItem.created_at >= cutoff_24h)
        .scalar()
    )
    relevant_candidates = (
        db.query(SourceItem)
        .filter(SourceItem.archived_as_irrelevant == False,
                SourceItem.published_at >= cutoff_48h,
                SourceItem.race_relevance_score >= 50)
        .all()
    )
    relevant_today = sum(1 for i in relevant_candidates if _is_llm_scored(i))

    # LLM race-situation memo
    campaign = db.query(CampaignConfig).first()
    opponents = db.query(Opponent).limit(3).all()
    all_articles = [_item_dict(i) for i in respond] + [_item_dict(i) for i in new_articles]
    race_memo = briefing_svc.get_or_generate(db, all_articles, campaign, opponents)

    return {
        "generated_at": datetime.utcnow().isoformat(),
        "meta": {
            "total_articles_today": total_today,
            "relevant_articles_today": relevant_today,
        },
        "race_memo": race_memo,
        "needs_response": [_item_dict(i) for i in respond],
        "new_articles": [_item_dict(i) for i in new_articles],
        "narrative_pulse": pulse,
    }
