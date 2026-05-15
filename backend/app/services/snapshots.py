"""Campaign-facing source and issue snapshots.

These are deliberately derived from existing analysis signals. They should help
campaign staff decide whether to act, not create new unsupported claims.
"""
from __future__ import annotations

import re
from collections import Counter

from sqlalchemy.orm import Session

from app.models import CampaignConfig, Issue, OpponentActivity, SourceItem
from app.schemas import IssueSnapshot, SourceItemOut, SourceSnapshot
from app.services.story_clustering import unique_by_cluster
from app.services.text_utils import strip_html_to_text


def _clean_sentence(text: str | None, fallback: str) -> str:
    # Strip HTML tags + decode entities before any further processing — RSS
    # summary fields and older raw_text values can still contain markup that
    # would otherwise leak into user-facing summaries (e.g. anchor tags).
    cleaned = strip_html_to_text(text)
    if not cleaned:
        return fallback
    parts = re.split(r"(?<=[.!?])\s+", cleaned)
    return " ".join(parts[:2])[:280]


def build_source_summary(item: SourceItem) -> str:
    poor_extraction = item.extraction_quality_label == "poor" or (item.extraction_quality_score or 0) < 45
    title = _clean_sentence(item.title, "")
    if poor_extraction:
        if title:
            return f"{title}. Clean summary unavailable because article extraction quality is poor. Verify against the original source."
        return "Clean summary unavailable because article extraction quality is poor. Verify against the original source."
    summary_source = item.summary or item.raw_text
    summary = _clean_sentence(summary_source, item.title or "Source")
    if not summary and title:
        return title
    return summary


def source_out(item: SourceItem) -> SourceItemOut:
    out = SourceItemOut.model_validate(item)
    out.summary = build_source_summary(item)
    return out


def _evidence_label(item: SourceItem) -> str:
    if item.extraction_quality_label == "poor":
        return "weak"
    avg = ((item.evidence_score or 50) + (item.credibility_score or 50)) / 2
    if avg >= 75:
        return "strong"
    if avg >= 55:
        return "moderate"
    return "weak"


def _issue_names(item: SourceItem) -> list[str]:
    return [m.issue.name for m in item.issue_mentions if m.issue]


def _owner_label(item: SourceItem) -> str:
    labels = {
        "candidate_statement": "Candidate",
        "opponent_statement": "Opponent",
        "outside_group_statement": "Outside group",
        "party_committee_statement": "Party committee",
        "media": "Media",
        "community/manual": "Community/manual",
    }
    return labels.get(item.source_owner_type, "Source")


def _distinct_text(text: str | None, *avoid: str | None) -> str | None:
    cleaned = _clean_sentence(text, "")
    if not cleaned:
        return None
    normalized = re.sub(r"\s+", " ", cleaned).lower()
    for candidate in avoid:
        if candidate and normalized == re.sub(r"\s+", " ", candidate).lower():
            return None
    return cleaned


def _actor_summary(db: Session, item: SourceItem) -> str:
    actors: list[str] = []
    campaign = db.query(CampaignConfig).first()
    if item.candidate_mentioned and campaign:
        actors.append(campaign.candidate_name)
    if item.opponent_mentioned:
        opp_activities = (
            db.query(OpponentActivity)
            .filter(OpponentActivity.source_item_id == item.id)
            .all()
        )
        names = [a.opponent.name for a in opp_activities if a.opponent]
        actors.extend(names or ["opponent"])
    if item.source_name:
        actors.append(item.source_name)
    if not actors:
        return "No specific campaign actor is clearly identified."
    return ", ".join(dict.fromkeys(actors[:4]))


def _key_claim(db: Session, item: SourceItem, avoid: list[str] | None = None) -> str | None:
    if item.extraction_quality_label == "poor":
        return None
    activity = (
        db.query(OpponentActivity)
        .filter(OpponentActivity.source_item_id == item.id)
        .filter((OpponentActivity.attack.isnot(None)) | (OpponentActivity.claim.isnot(None)))
        .first()
    )
    if activity:
        text = activity.attack or activity.claim
        if text:
            claim = _distinct_text(text, *(avoid or []))
            if claim:
                return claim
    text = _distinct_text(item.raw_text, *(avoid or []))
    if item.actionability_label in {"respond", "review"} and text:
        return text[:220]
    return None


def build_source_snapshot(db: Session, item: SourceItem) -> SourceSnapshot:
    issues = _issue_names(item)
    evidence = _evidence_label(item)
    poor_extraction = item.extraction_quality_label == "poor"
    owner_label = _owner_label(item)
    campaign = db.query(CampaignConfig).first()
    candidate_name = campaign.candidate_name if campaign else "the candidate"

    if item.archived_as_irrelevant:
        what = _distinct_text(item.summary or item.title, item.title) or "This source was captured but does not currently appear campaign-relevant."
        why = "It is currently archived because the race relevance or actionability signal is too weak."
    elif poor_extraction:
        what = _distinct_text(item.title, item.summary) or item.title
        if item.source_owner_type in {"party_committee_statement", "outside_group_statement", "opponent_statement", "candidate_statement"}:
            what = f"{owner_label} statement targeting {candidate_name}."
        why = "Extraction quality is weak; verify against the original source before treating this as campaign evidence."
    else:
        summary_text = _distinct_text(item.summary or item.raw_text, item.title)
        if item.source_owner_type in {"party_committee_statement", "outside_group_statement", "opponent_statement", "candidate_statement"}:
            if item.source_owner_type in {"party_committee_statement", "outside_group_statement"}:
                what = f"{owner_label} attack statement targeting {candidate_name}."
            elif item.source_owner_type == "opponent_statement":
                what = f"Opponent statement targeting {candidate_name}."
            else:
                what = f"Candidate statement about {candidate_name}."
        else:
            what = summary_text or item.title
        if item.actionability_label == "respond":
            why = "It may require a campaign response because it contains a high-relevance claim, attack, or opponent activity."
        elif issues:
            why = f"It adds campaign-relevant evidence on {', '.join(issues[:2])}."
        elif item.race_relevance_score >= 60:
            why = "It is strongly connected to the race and should be reviewed for campaign use."
        else:
            why = "It is plausibly relevant but should be treated as monitoring intelligence."

    if evidence == "weak" and not poor_extraction:
        why += " Evidence is limited; verify before using this publicly."

    geo = item.geo_relevance if item.geo_relevance and item.geo_relevance != "none" else ""
    title_meta = " ".join(filter(None, [item.title, item.source_name, item.source_url])).lower()
    title_geo = bool(campaign and any(
        term and str(term).lower() in title_meta
        for term in [campaign.district, campaign.location, campaign.district_number]
    ))
    if item.district_mentioned:
        geography = "District-level connection detected."
    elif poor_extraction and title_geo:
        geography = "Geography appears in title or source metadata, but body extraction quality is weak."
    elif geo:
        geography = f"{geo.title()} geography connection detected."
    elif poor_extraction:
        geography = "Geography is unclear because article extraction quality is weak."
    else:
        geography = "No specific geography was confidently extracted."

    key_claim = _key_claim(db, item, avoid=[what, item.title, item.summary])

    return SourceSnapshot(
        what_happened=what,
        why_it_matters=why,
        geography_summary=geography,
        actors_summary=_actor_summary(db, item),
        action_signal=item.actionability_label or "monitor",
        evidence_summary=evidence,
        key_claim_or_quote=key_claim,
    )


def _source_actors(db: Session, sources: list[SourceItem]) -> list[str]:
    actors: list[str] = []
    campaign = db.query(CampaignConfig).first()
    for source in sources:
        if source.candidate_mentioned and campaign:
            actors.append(campaign.candidate_name)
        if source.opponent_mentioned:
            activities = (
                db.query(OpponentActivity)
                .filter(OpponentActivity.source_item_id == source.id)
                .all()
            )
            actors.extend([a.opponent.name for a in activities if a.opponent])
        if source.source_name:
            actors.append(source.source_name)
    return [name for name, _ in Counter(actors).most_common(5)]


def build_issue_snapshot(db: Session, issue: Issue, sources: list[SourceItem]) -> IssueSnapshot:
    unique_sources = unique_by_cluster(sources)
    count = len(unique_sources)
    if count == 0:
        return IssueSnapshot(
            issue_snapshot=f"No strong race-relevant source cluster is currently linked to {issue.name}.",
            why_it_matters_now="Evidence is too limited to draw a campaign conclusion.",
            top_geographies=[],
            top_actors=[],
            top_distinct_developments=[],
            messaging_readiness="weak",
            evidence_strength="weak",
        )

    avg = sum((s.evidence_score or 50) + (s.credibility_score or 50) for s in unique_sources) / (2 * count)
    evidence = "strong" if count >= 3 and avg >= 70 else ("moderate" if count >= 2 and avg >= 55 else "weak")
    poor_count = sum(1 for s in unique_sources if s.extraction_quality_label == "poor")
    if poor_count and poor_count >= max(1, count // 2):
        evidence = "weak" if evidence == "moderate" else ("moderate" if evidence == "strong" else evidence)
    readiness = "ready" if evidence == "strong" else ("partial" if evidence == "moderate" else "weak")
    geos = [s.geo_relevance for s in unique_sources if s.geo_relevance and s.geo_relevance != "none"]
    top_geos = [geo for geo, _ in Counter(geos).most_common(3)]
    developments = [_clean_sentence(s.summary or s.title, s.title) for s in unique_sources[:3]]

    if evidence == "weak":
        why = "There is some signal, but the evidence is too thin for confident messaging."
    elif issue.trend == "rising":
        why = f"{issue.name} is rising across {count} distinct source clusters."
    else:
        why = f"{issue.name} has {count} distinct race-relevant source clusters."

    issue_text = (
        f"{issue.name} is supported by {count} distinct source cluster"
        f"{'' if count == 1 else 's'}."
    )
    if top_geos:
        issue_text += f" The clearest geography signal is {', '.join(top_geos)}."
    if evidence == "weak":
        issue_text += " Treat this as a lead, not a campaign-ready conclusion."
    if poor_count:
        issue_text += f" {poor_count} linked source cluster has weak extraction quality and should be verified."

    return IssueSnapshot(
        issue_snapshot=issue_text,
        why_it_matters_now=why,
        top_geographies=top_geos,
        top_actors=_source_actors(db, unique_sources),
        top_distinct_developments=developments,
        messaging_readiness=readiness,
        evidence_strength=evidence,
    )
