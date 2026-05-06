"""Reprocess existing source items without re-ingesting them."""
from dataclasses import dataclass
from datetime import datetime
import json
from typing import Any

from sqlalchemy.orm import Session

from app.models import Issue, IssueMention, OpponentActivity, SourceItem
from app.services import intelligence, issue_clustering, narratives, opponent_analysis, race_relevance, scoring, story_clustering
from app.services.ingestion import _assess_extraction_quality, _compute_priority_score
from app.services.snapshots import build_source_summary
from app.services.source_ownership import classify_source_owner


WATCHED_FIELDS = [
    "summary",
    "urgency",
    "priority_score",
    "evidence_score",
    "credibility_score",
    "race_relevance_score",
    "race_relevance_label",
    "relevance_reasons",
    "actionability_score",
    "actionability_label",
    "content_category",
    "geo_relevance",
    "candidate_mentioned",
    "opponent_mentioned",
    "district_mentioned",
    "priority_issue_mentioned",
    "archived_as_irrelevant",
    "extraction_quality_score",
    "extraction_quality_label",
    "extraction_quality_reasons",
    "source_owner_type",
    "source_owner_confidence",
]


@dataclass
class ReanalysisOptions:
    limit: int | None = None
    source_id: int | None = None
    include_reviewed: bool = False
    include_dismissed: bool = False
    include_archived: bool = True
    dry_run: bool = False


def _snapshot(item: SourceItem) -> dict[str, Any]:
    return {field: getattr(item, field) for field in WATCHED_FIELDS}


def _diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        field: {"before": before.get(field), "after": after.get(field)}
        for field in WATCHED_FIELDS
        if before.get(field) != after.get(field)
    }


def _selected_sources(db: Session, options: ReanalysisOptions) -> list[SourceItem]:
    q = db.query(SourceItem).order_by(SourceItem.created_at.desc())
    if options.source_id is not None:
        q = q.filter(SourceItem.id == options.source_id)
    if not options.include_reviewed:
        q = q.filter(SourceItem.reviewed == False)  # noqa: E712
    if not options.include_dismissed:
        q = q.filter(SourceItem.dismissed == False)  # noqa: E712
    if not options.include_archived:
        q = q.filter(SourceItem.archived_as_irrelevant == False)  # noqa: E712
    if options.limit is not None:
        q = q.limit(options.limit)
    return q.all()


def _issue_names_for_text(text: str) -> list[str]:
    return [name for name, _has_bump in issue_clustering._match_taxonomy(text)]


def _dry_run_after(db: Session, item: SourceItem) -> tuple[dict[str, Any], list[str]]:
    summary = item.summary
    if not summary and item.raw_text:
        if item.extraction_quality_label == "poor":
            summary = build_source_summary(item)
        else:
            summary = intelligence.summarize_source(item.raw_text)

    urgency = item.urgency
    if not urgency or urgency == "low":
        urgency = intelligence.classify_urgency(f"{item.title} {item.raw_text or ''}")

    probe = SourceItem(
        title=item.title,
        source_name=item.source_name,
        source_url=item.source_url,
        source_type=item.source_type,
        source_owner_type=item.source_owner_type,
        source_owner_confidence=item.source_owner_confidence,
        raw_text=item.raw_text,
        summary=summary,
        published_at=item.published_at,
        created_at=item.created_at,
        urgency=urgency,
        credibility_note=item.credibility_note,
        reviewed=item.reviewed,
        dismissed=item.dismissed,
        review_note=item.review_note,
        evidence_score=item.evidence_score,
        credibility_score=item.credibility_score,
        extraction_quality_score=item.extraction_quality_score,
        extraction_quality_label=item.extraction_quality_label,
        extraction_quality_reasons=item.extraction_quality_reasons,
    )
    ownership = classify_source_owner(db, probe)
    probe.source_owner_type = ownership.source_owner_type
    probe.source_owner_confidence = ownership.source_owner_confidence
    if probe.raw_text and probe.source_url:
        quality_score, quality_label, quality_reasons = _assess_extraction_quality(probe.raw_text, probe.title)
        probe.extraction_quality_score = quality_score
        probe.extraction_quality_label = quality_label
        probe.extraction_quality_reasons = json.dumps(quality_reasons)
    relevance = race_relevance.analyze_source_item(db, probe)
    probe.race_relevance_score = relevance.race_relevance_score
    probe.race_relevance_label = relevance.race_relevance_label
    probe.relevance_reasons = json.dumps(relevance.relevance_reasons)
    probe.actionability_score = relevance.actionability_score
    probe.actionability_label = relevance.actionability_label
    probe.content_category = relevance.content_category
    probe.geo_relevance = relevance.geo_relevance
    probe.candidate_mentioned = relevance.candidate_mentioned
    probe.opponent_mentioned = relevance.opponent_mentioned
    probe.district_mentioned = relevance.district_mentioned
    probe.priority_issue_mentioned = relevance.priority_issue_mentioned
    probe.archived_as_irrelevant = relevance.archived_as_irrelevant
    probe.evidence_score = scoring.compute_evidence_score(probe)
    probe.credibility_score = scoring.compute_credibility_score(probe)
    text = " ".join(filter(None, [probe.title, probe.raw_text, probe.summary]))
    issue_names = _issue_names_for_text(text) if not relevance.archived_as_irrelevant else []
    probe.priority_score = int((probe.race_relevance_score or 0) * 0.6) + int((probe.actionability_score or 0) * 0.35)
    if probe.urgency == "high":
        probe.priority_score += 10
    elif probe.urgency == "medium":
        probe.priority_score += 5
    if issue_names:
        probe.priority_score += 10
    if probe.opponent_mentioned:
        probe.priority_score += 20
    if probe.credibility_note:
        probe.priority_score += 15
    if probe.published_at:
        age = max(0, (datetime.utcnow() - probe.published_at).days)
        if age <= 3:
            probe.priority_score += 10
        elif age <= 7:
            probe.priority_score += 5

    after = _snapshot(probe)
    return after, issue_names


def _unlink_issue_mentions(db: Session, item: SourceItem) -> None:
    mentions = db.query(IssueMention).filter_by(source_item_id=item.id).all()
    affected_issue_ids = {m.issue_id for m in mentions}
    for mention in mentions:
        db.delete(mention)
    db.flush()
    for issue_id in affected_issue_ids:
        issue = db.get(Issue, issue_id)
        if not issue:
            continue
        issue.mention_count = issue_clustering._count_issue_clusters(db, issue.id)
        issue_clustering._update_trend(db, issue)
        issue_clustering._update_urgency(db, issue)


def reanalyze_source(db: Session, item: SourceItem, dry_run: bool = False) -> dict[str, Any]:
    before = _snapshot(item)

    if dry_run:
        after, issue_names = _dry_run_after(db, item)
        return {
            "source_id": item.id,
            "title": item.title,
            "dry_run": True,
            "changed": bool(_diff(before, after)),
            "changes": _diff(before, after),
            "issue_names": issue_names,
        }

    ownership = classify_source_owner(db, item)
    item.source_owner_type = ownership.source_owner_type
    item.source_owner_confidence = ownership.source_owner_confidence
    if not item.summary and item.raw_text:
        if item.extraction_quality_label == "poor":
            item.summary = build_source_summary(item)
        else:
            item.summary = intelligence.summarize_source(item.raw_text)
    if not item.urgency or item.urgency == "low":
        item.urgency = intelligence.classify_urgency(f"{item.title} {item.raw_text or ''}")

    story_clustering.assign_story_cluster(db, item)
    if item.raw_text and item.source_url:
        quality_score, quality_label, quality_reasons = _assess_extraction_quality(item.raw_text, item.title)
        item.extraction_quality_score = quality_score
        item.extraction_quality_label = quality_label
        item.extraction_quality_reasons = json.dumps(quality_reasons)
        if item.extraction_quality_label == "poor":
            item.summary = build_source_summary(item)
    race_relevance.apply_relevance(db, item)
    _unlink_issue_mentions(db, item)
    db.query(OpponentActivity).filter_by(source_item_id=item.id).delete()
    db.flush()

    issue_names: list[str] = []
    if not item.archived_as_irrelevant:
        issues = issue_clustering.assign_issues_to_source(db, item)
        issue_names = [issue.name for issue in issues]
        opponent_analysis.analyze_source_for_opponents(db, item)
        race_relevance.apply_relevance(db, item)

    item.evidence_score = scoring.compute_evidence_score(item)
    item.credibility_score = scoring.compute_credibility_score(item)
    item.priority_score = _compute_priority_score(db, item)
    db.commit()
    narratives.refresh_narratives(db)
    db.refresh(item)

    after = _snapshot(item)
    return {
        "source_id": item.id,
        "title": item.title,
        "dry_run": False,
        "changed": bool(_diff(before, after)),
        "changes": _diff(before, after),
        "issue_names": issue_names,
    }


def reanalyze_sources(db: Session, options: ReanalysisOptions) -> dict[str, Any]:
    sources = _selected_sources(db, options)
    results = [reanalyze_source(db, item, dry_run=options.dry_run) for item in sources]
    return {
        "dry_run": options.dry_run,
        "matched_count": len(sources),
        "updated_count": 0 if options.dry_run else sum(1 for r in results if r["changed"]),
        "results": results,
    }
