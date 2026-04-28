from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.db import get_db
from app.models import Issue, IssueMention, SourceItem
from app.schemas import IssueOut, IssueDetail, SourceItemOut

router = APIRouter()


@router.get("/issues", response_model=list[IssueOut])
def list_issues(db: Session = Depends(get_db)):
    return db.query(Issue).order_by(Issue.mention_count.desc()).all()


@router.get("/issues/{issue_id}", response_model=IssueDetail)
def get_issue(issue_id: int, db: Session = Depends(get_db)):
    issue = db.get(Issue, issue_id)
    if not issue:
        raise HTTPException(status_code=404, detail="Issue not found")

    mention_source_ids = [m.source_item_id for m in issue.mentions]
    recent_sources = (
        db.query(SourceItem)
        .filter(SourceItem.id.in_(mention_source_ids))
        .order_by(SourceItem.published_at.desc())
        .limit(5)
        .all()
    )

    result = IssueDetail.model_validate(issue)
    result.recent_sources = [SourceItemOut.model_validate(s) for s in recent_sources]
    return result
