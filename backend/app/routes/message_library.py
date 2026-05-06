import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import CampaignConfig, CandidateMessageLibrary, CandidateNarrative
from app.schemas import (
    CandidateMessageLibraryIn,
    CandidateMessageLibraryOut,
    CandidateNarrativeCreate,
    CandidateNarrativeOut,
    CandidateNarrativeUpdate,
)
from app.services import narratives as narrative_service

router = APIRouter()

KINDS = {"self_definition", "issue_frame", "contrast", "rebuttal"}


def _json_list(value: list[str] | None) -> str | None:
    return json.dumps(value) if value is not None else None


def _ensure_library(db: Session) -> CandidateMessageLibrary:
    library = db.query(CandidateMessageLibrary).first()
    if library:
        return library
    campaign = db.query(CampaignConfig).first()
    library = CandidateMessageLibrary(
        campaign_config_id=campaign.id if campaign else None,
        core_message=campaign.campaign_message if campaign else None,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(library)
    db.commit()
    db.refresh(library)
    return library


@router.get("/message-library", response_model=CandidateMessageLibraryOut)
def get_message_library(db: Session = Depends(get_db)):
    return CandidateMessageLibraryOut.model_validate(_ensure_library(db))


@router.put("/message-library", response_model=CandidateMessageLibraryOut)
def update_message_library(body: CandidateMessageLibraryIn, db: Session = Depends(get_db)):
    library = _ensure_library(db)
    library.core_message = body.core_message
    library.short_bio_frame = body.short_bio_frame
    library.tone_guidance = body.tone_guidance
    library.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(library)
    return CandidateMessageLibraryOut.model_validate(library)


@router.get("/message-library/narratives", response_model=list[CandidateNarrativeOut])
def list_candidate_narratives(db: Session = Depends(get_db)):
    library = _ensure_library(db)
    rows = (
        db.query(CandidateNarrative)
        .filter(CandidateNarrative.library_id == library.id)
        .order_by(CandidateNarrative.priority.desc(), CandidateNarrative.created_at.desc())
        .all()
    )
    return [CandidateNarrativeOut.model_validate(row) for row in rows]


@router.post("/message-library/narratives", response_model=CandidateNarrativeOut)
def create_candidate_narrative(body: CandidateNarrativeCreate, db: Session = Depends(get_db)):
    if body.narrative_kind not in KINDS:
        raise HTTPException(status_code=422, detail="Invalid narrative_kind")
    library = _ensure_library(db)
    row = CandidateNarrative(
        library_id=library.id,
        short_label=body.short_label,
        canonical_text=body.canonical_text,
        narrative_kind=body.narrative_kind,
        issue_name=body.issue_name,
        preferred_phrases=_json_list(body.preferred_phrases),
        avoid_phrases=_json_list(body.avoid_phrases),
        must_mention_points=_json_list(body.must_mention_points),
        red_lines=_json_list(body.red_lines),
        priority=body.priority,
        active=body.active,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    narrative_service.refresh_narratives(db)
    db.refresh(row)
    return CandidateNarrativeOut.model_validate(row)


@router.put("/message-library/narratives/{narrative_id}", response_model=CandidateNarrativeOut)
def update_candidate_narrative(narrative_id: int, body: CandidateNarrativeUpdate, db: Session = Depends(get_db)):
    row = db.get(CandidateNarrative, narrative_id)
    if not row:
        raise HTTPException(status_code=404, detail="Candidate narrative not found")
    data = body.model_dump(exclude_unset=True)
    if "narrative_kind" in data and data["narrative_kind"] not in KINDS:
        raise HTTPException(status_code=422, detail="Invalid narrative_kind")
    for field in ["short_label", "canonical_text", "narrative_kind", "issue_name", "priority", "active"]:
        if field in data:
            setattr(row, field, data[field])
    for field in ["preferred_phrases", "avoid_phrases", "must_mention_points", "red_lines"]:
        if field in data:
            setattr(row, field, _json_list(data[field]))
    row.updated_at = datetime.utcnow()
    db.commit()
    narrative_service.refresh_narratives(db)
    db.refresh(row)
    return CandidateNarrativeOut.model_validate(row)


@router.delete("/message-library/narratives/{narrative_id}")
def delete_candidate_narrative(narrative_id: int, db: Session = Depends(get_db)):
    row = db.get(CandidateNarrative, narrative_id)
    if not row:
        raise HTTPException(status_code=404, detail="Candidate narrative not found")
    db.delete(row)
    db.commit()
    narrative_service.refresh_narratives(db)
    return {"deleted": True}
