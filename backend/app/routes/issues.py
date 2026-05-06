from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.db import get_db
import json

from app.models import Issue, IssueMention, SourceItem
from app.schemas import IssueOut, IssueDetail, SourceItemOut
from app.services.snapshots import build_issue_snapshot, source_out

router = APIRouter()


@router.get("/issues", response_model=list[IssueOut])
def list_issues(db: Session = Depends(get_db)):
    return db.query(Issue).order_by(Issue.mention_count.desc()).all()


@router.get("/issues/{issue_id}", response_model=IssueDetail)
def get_issue(issue_id: int, db: Session = Depends(get_db)):
    issue = db.get(Issue, issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")

    mention_rows = (
        db.query(IssueMention, SourceItem)
        .join(SourceItem, SourceItem.id == IssueMention.source_item_id)
        .filter(IssueMention.issue_id == issue.id)
        .filter(SourceItem.archived_as_irrelevant == False)  # noqa: E712
        .filter(SourceItem.race_relevance_score >= 40)
        .filter((IssueMention.link_strength >= 30) | (IssueMention.link_strength == 0))
        .order_by(IssueMention.link_strength.desc(), SourceItem.published_at.desc())
        .limit(20)
        .all()
    )
    seen_clusters: set[str] = set()
    recent_sources: list[SourceItemOut] = []
    for mention, source in mention_rows:
        cluster_key = source.story_cluster_id or f"source-{source.id}"
        if cluster_key in seen_clusters:
            continue
        seen_clusters.add(cluster_key)
        out = source_out(source)
        out.issue_link_strength = mention.link_strength or None
        if mention.link_reasons:
            try:
                out.issue_link_reasons = [str(r) for r in json.loads(mention.link_reasons)]
            except Exception:
                out.issue_link_reasons = [mention.link_reasons]
        recent_sources.append(out)
        if len(recent_sources) >= 5:
            break

    result = IssueDetail.model_validate(issue)
    result.recent_sources = recent_sources
    snapshot_sources = [
        source
        for _mention, source in mention_rows
        if (source.story_cluster_id or f"source-{source.id}") in seen_clusters
    ]
    result.snapshot = build_issue_snapshot(db, issue, snapshot_sources)
    return result
