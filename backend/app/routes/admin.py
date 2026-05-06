"""Admin / workspace management endpoints."""
import json
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import (
    CampaignConfig, SourceItem, Issue, IssueMention,
    Opponent, OpponentActivity, CanvassingNote,
    GeneratedTalkingPoint, RssFeed, SourceMonitor,
    ManualCapture,
)
from app.schemas import (
    ReanalyzeSourcesRequest,
    ReanalyzeSourcesResult,
    ResetWorkspaceRequest,
    ResetWorkspaceResult,
)
from app.services.reanalysis import ReanalysisOptions, reanalyze_sources

router = APIRouter()


@router.post("/admin/reset-workspace", response_model=ResetWorkspaceResult)
def reset_workspace(body: ResetWorkspaceRequest, db: Session = Depends(get_db)):
    if body.confirm != "RESET WORKSPACE":
        raise HTTPException(
            status_code=400,
            detail="Confirmation string must be exactly 'RESET WORKSPACE'",
        )

    # Count before deletion for the result summary
    n_sources = db.query(SourceItem).count()
    n_issues = db.query(Issue).count()
    n_opponents = db.query(Opponent).count()
    n_canvassing = db.query(CanvassingNote).count()
    n_tp = db.query(GeneratedTalkingPoint).count()
    n_feeds = db.query(RssFeed).count()

    # Delete in dependency order
    db.query(IssueMention).delete()
    db.query(OpponentActivity).delete()
    db.query(ManualCapture).delete()
    db.query(SourceItem).delete()
    db.query(Issue).delete()
    db.query(Opponent).delete()
    db.query(CanvassingNote).delete()
    db.query(GeneratedTalkingPoint).delete()
    db.query(SourceMonitor).delete()

    preserved_feeds = 0
    cleared_feeds = 0
    if body.preserve_feeds:
        preserved_feeds = n_feeds
    else:
        db.query(RssFeed).delete()
        cleared_feeds = n_feeds

    # Replace campaign config
    db.query(CampaignConfig).delete()
    config = CampaignConfig(
        candidate_name=body.candidate_name,
        office=body.office,
        district=body.district,
        party=body.party,
        location=body.location,
        sparse_race_mode=False,
        election_date=body.election_date,
        campaign_message=body.campaign_message,
        key_priorities=json.dumps(body.key_priorities) if body.key_priorities else None,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(config)
    db.commit()

    return ResetWorkspaceResult(
        cleared_sources=n_sources,
        cleared_issues=n_issues,
        cleared_opponents=n_opponents,
        cleared_canvassing=n_canvassing,
        cleared_talking_points=n_tp,
        cleared_feeds=cleared_feeds,
        preserved_feeds=preserved_feeds,
        candidate_name=body.candidate_name,
    )


@router.post("/admin/reanalyze-sources", response_model=ReanalyzeSourcesResult)
def reanalyze_sources_endpoint(body: ReanalyzeSourcesRequest, db: Session = Depends(get_db)):
    if body.confirm != "REANALYZE SOURCES":
        raise HTTPException(
            status_code=400,
            detail="Confirmation string must be exactly 'REANALYZE SOURCES'",
        )

    return reanalyze_sources(
        db,
        ReanalysisOptions(
            limit=body.limit,
            source_id=body.source_id,
            include_reviewed=body.include_reviewed,
            include_dismissed=body.include_dismissed,
            include_archived=body.include_archived,
            dry_run=body.dry_run,
        ),
    )
