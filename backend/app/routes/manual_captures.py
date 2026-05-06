import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.db import get_db
from app.models import ManualCapture, SourceItem
from app.schemas import (
    IssueOut,
    ManualCaptureCreate,
    ManualCaptureCreateResult,
    ManualCaptureOut,
    SourceItemOut,
)
from app.services import ingestion, scoring
from app.services.snapshots import source_out

router = APIRouter()

CAPTURE_TYPES = {
    "pasted_text", "flyer", "endorsement", "debate_notes", "newsletter",
    "social_post", "forum_notes", "press_release", "other",
}


def _clean_terms(values: list[str] | None) -> list[str]:
    return [str(v).strip() for v in values or [] if str(v).strip()]


def _source_type_for_capture(source_type: str, capture_type: str) -> str:
    if source_type and source_type != "manual_capture":
        return source_type
    if capture_type == "social_post":
        return "social"
    if capture_type in {"endorsement", "press_release"}:
        return "campaign_note"
    if capture_type in {"debate_notes", "forum_notes"}:
        return "public_record"
    return "campaign_note"


def _pipeline_text(body: ManualCaptureCreate) -> str:
    parts = [body.raw_text.strip()]
    geo = _clean_terms(body.geography_tags)
    issues = _clean_terms(body.issue_tags)
    context: list[str] = []
    if body.capture_type:
        context.append(f"capture type: {body.capture_type.replace('_', ' ')}")
    if geo:
        context.append(f"geography tags: {', '.join(geo)}")
    if issues:
        context.append(f"issue tags: {', '.join(issues)}")
    if body.candidate_related:
        context.append("marked candidate-related by campaign user")
    if body.opponent_related:
        context.append("marked opponent-related by campaign user")
    if body.notes:
        context.append(f"capture notes: {body.notes.strip()}")
    if context:
        parts.append("\n\nManual capture context for classification: " + "; ".join(context))
    return "\n".join(parts)


def _manual_credibility_note(body: ManualCaptureCreate) -> str:
    if body.source_type == "opponent_statement" or body.capture_type in {"press_release", "social_post"}:
        return "Manually captured direct statement or post. Use as evidence of what was said, but verify broader factual claims before repeating them."
    if body.capture_type in {"flyer", "endorsement", "debate_notes", "forum_notes", "newsletter"}:
        return "Manually captured campaign material. Useful for low-information races, but verify claims with another source when possible."
    return "Manually pasted source. Treat as user-provided evidence and verify unsupported factual claims before public use."


def _related_issues(item: SourceItem) -> list[IssueOut]:
    return [IssueOut.model_validate(m.issue) for m in item.issue_mentions if m.issue]


@router.post("/manual-captures", response_model=ManualCaptureCreateResult)
def create_manual_capture(body: ManualCaptureCreate, db: Session = Depends(get_db)):
    if body.capture_type not in CAPTURE_TYPES:
        raise HTTPException(status_code=422, detail="Unsupported capture_type")
    if not body.title.strip() or not body.raw_text.strip():
        raise HTTPException(status_code=422, detail="title and raw_text are required")

    source_type = _source_type_for_capture(body.source_type, body.capture_type)
    item = ingestion.ingest_text(
        db,
        title=body.title.strip(),
        raw_text=_pipeline_text(body),
        source_name=body.source_name or "Manual Capture",
        source_type=source_type,
        source_url=body.source_url,
    )
    item.credibility_note = _manual_credibility_note(body)
    item.evidence_score = scoring.compute_evidence_score(item)
    item.credibility_score = scoring.compute_credibility_score(item)
    db.commit()
    db.refresh(item)

    capture = ManualCapture(
        source_item_id=item.id,
        title=body.title.strip(),
        source_name=body.source_name or "Manual Capture",
        source_type=source_type,
        source_url=body.source_url,
        capture_type=body.capture_type,
        raw_text=body.raw_text.strip(),
        geography_tags=json.dumps(_clean_terms(body.geography_tags)),
        issue_tags=json.dumps(_clean_terms(body.issue_tags)),
        candidate_related=body.candidate_related,
        opponent_related=body.opponent_related,
        notes=body.notes,
    )
    db.add(capture)
    db.commit()
    db.refresh(capture)

    return ManualCaptureCreateResult(
        capture=ManualCaptureOut.model_validate(capture),
        source_item=source_out(item),
        related_issues=_related_issues(item),
        message=(
            "Captured source analyzed and sent to the main intelligence pipeline. "
            f"Disposition: {item.actionability_label}; relevance {item.race_relevance_score}/100."
        ),
    )


@router.get("/manual-captures", response_model=list[ManualCaptureOut])
def list_manual_captures(limit: int = 50, db: Session = Depends(get_db)):
    return (
        db.query(ManualCapture)
        .options(joinedload(ManualCapture.source_item))
        .order_by(ManualCapture.created_at.desc())
        .limit(min(limit, 200))
        .all()
    )


@router.get("/manual-captures/{capture_id}", response_model=ManualCaptureOut)
def get_manual_capture(capture_id: int, db: Session = Depends(get_db)):
    capture = (
        db.query(ManualCapture)
        .options(joinedload(ManualCapture.source_item))
        .filter(ManualCapture.id == capture_id)
        .first()
    )
    if not capture:
        raise HTTPException(status_code=404, detail="Manual capture not found")
    return capture
