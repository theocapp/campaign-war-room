"""Routes for the proposed-narrative auto-triage feature (Phase B).

GET  /api/narrative-triage              List current verdicts for the UI.
POST /api/narrative-triage/run          Walk current clusters + emit/refresh
                                        verdicts. Costs LLM money — gated
                                        behind explicit POST so it's not
                                        accidentally called from the UI.
POST /api/narrative-triage/{id}/dismiss Mark a single verdict dismissed.
POST /api/narrative-triage/{id}/apply   Mark a verdict applied (used after
                                        the user accepts an auto-suggestion).

The actual auto-merge / auto-promote actions still go through the
existing /api/narrative-frames endpoints so the user has one canonical
write path. This service just emits verdicts + decorations.
"""

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import CandidateFrame, NarrativeFrame, ProposedClusterTriage
from app.services.access_codes import require_admin
from app.services.narrative_triage import list_triage_verdicts, run_triage_pass

router = APIRouter(prefix="/narrative-triage", tags=["narrative-triage"])

# Only /run is LLM-cost (~$0.40 per pass). Dismiss/apply/execute-merge are
# pure DB state operations and intentionally stay open.
_admin_only = [Depends(require_admin)]


class TriageRunRequest(BaseModel):
    days_back: int = 21
    force_refresh: bool = False
    dry_run: bool = False
    # V13.10e — when True, high-confidence verdicts are automatically
    # executed (auto-promoted into new tracked frames OR auto-merged into
    # existing frames). Default True now that validation confirmed the
    # system produces 0 wrong decisions. Set False to revert to the legacy
    # click-to-confirm flow.
    hands_off: bool = True


@router.get("")
def list_verdicts(
    include_dismissed: bool = False,
    db: Session = Depends(get_db),
):
    """List triage verdicts. Drives the Review Queue UI decorations."""
    return {
        "verdicts": list_triage_verdicts(db, include_dismissed=include_dismissed),
    }


@router.post("/run", dependencies=_admin_only)
def trigger_run(
    req: TriageRunRequest = TriageRunRequest(),
    db: Session = Depends(get_db),
):
    """Walk current proposed clusters + emit/refresh triage verdicts.

    Calls gpt-4o once per non-noise cluster (~$0.40 per pass at current
    cluster counts). Use force_refresh=True to re-evaluate even clusters
    that already have a stored verdict.
    """
    result = run_triage_pass(
        db,
        days_back=req.days_back,
        force_refresh=req.force_refresh,
        dry_run=req.dry_run,
        hands_off=req.hands_off,
    )
    return result


@router.post("/{triage_id}/dismiss")
def dismiss_verdict(triage_id: int, db: Session = Depends(get_db)):
    """User dismisses a proposed cluster — hide it from the review queue."""
    row = db.query(ProposedClusterTriage).filter(
        ProposedClusterTriage.id == triage_id,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="triage verdict not found")
    row.dismissed_at = datetime.utcnow()
    db.commit()
    return {"id": row.id, "dismissed_at": row.dismissed_at.isoformat()}


@router.post("/{triage_id}/apply")
def apply_verdict(triage_id: int, db: Session = Depends(get_db)):
    """Mark a verdict applied. Called after the user confirms an auto-suggestion.

    Doesn't perform the underlying merge/promote — that's the caller's
    responsibility via /api/narrative-frames endpoints. This just stamps
    the triage row so we can audit which suggestions the user accepted.
    """
    row = db.query(ProposedClusterTriage).filter(
        ProposedClusterTriage.id == triage_id,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="triage verdict not found")
    row.applied_at = datetime.utcnow()
    db.commit()
    return {"id": row.id, "applied_at": row.applied_at.isoformat()}


@router.post("/{triage_id}/execute-merge")
def execute_merge(triage_id: int, db: Session = Depends(get_db)):
    """Execute an auto_merge verdict: mark cluster's candidate frames resolved.

    For auto_merge verdicts, the AI judged the proposed cluster IS one of
    the user's existing tracked narratives. Executing the merge means:

      1. Mark each member CandidateFrame as resolved_to_frame_id = target
         (so the next clustering pass doesn't re-surface them as proposed).
      2. Stamp the triage row as applied_at so we have an audit trail.

    Note: this does NOT create NarrativeFrameMention rows linking the
    underlying source items to the target frame. Those get created naturally
    on the next rescore pass (or you can trigger /api/narrative-frames/rematch
    manually if you want immediate mention counts). Keeping merge cheap and
    LLM-free here so it can be a single click.
    """
    row = db.query(ProposedClusterTriage).filter(
        ProposedClusterTriage.id == triage_id,
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="triage verdict not found")
    if row.verdict != "auto_merge":
        raise HTTPException(
            status_code=400,
            detail=f"verdict is '{row.verdict}', expected 'auto_merge'",
        )
    if not row.suggested_merge_frame_id:
        raise HTTPException(status_code=400, detail="no suggested_merge_frame_id on this verdict")

    # Verify the target frame still exists (user might have deleted it).
    target = db.query(NarrativeFrame).filter(
        NarrativeFrame.id == row.suggested_merge_frame_id,
    ).first()
    if not target:
        raise HTTPException(
            status_code=400,
            detail=f"target frame {row.suggested_merge_frame_id} no longer exists",
        )

    try:
        member_ids = json.loads(row.member_candidate_frame_ids_json)
    except Exception:
        member_ids = []

    if not member_ids:
        raise HTTPException(status_code=400, detail="no member candidate frames")

    now = datetime.utcnow()
    updated = (
        db.query(CandidateFrame)
        .filter(CandidateFrame.id.in_(member_ids))
        .filter(CandidateFrame.resolved_to_frame_id.is_(None))  # don't overwrite prior decisions
        .update(
            {"resolved_to_frame_id": target.id, "resolved_at": now},
            synchronize_session=False,
        )
    )
    row.applied_at = now
    db.commit()
    # Drop the candidate-landscape cache so the merged cluster disappears
    # from the Review Queue's "Proposed narratives" list on the next fetch.
    # Without this, the merged cluster stays visible until the 25h cache
    # TTL expires.
    try:
        from app.services.narrative_landscape import invalidate_cache
        invalidate_cache()
    except Exception:
        pass  # best-effort; never block the merge on cache-bust failure
    # Stamp the snapshot row by member ids so the proposal disappears from
    # the open snapshot list. Best-effort.
    try:
        from app.services.proposed_cluster_snapshot import mark_applied_by_member_ids
        mark_applied_by_member_ids(db, member_ids, frame_id=target.id)
    except Exception:
        pass
    return {
        "id": row.id,
        "merged_into_frame_id": target.id,
        "merged_into_frame_name": target.name,
        "candidate_frames_marked": updated,
        "applied_at": now.isoformat(),
    }
