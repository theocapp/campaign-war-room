"""CRUD + auto-suggest routes for campaign narrative frames."""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import NarrativeFrame, NarrativeFrameMention
from app.services import narrative_frames as svc

router = APIRouter(prefix="/narrative-frames", tags=["narrative-frames"])


class FrameCreate(BaseModel):
    name: str
    description: Optional[str] = None
    owner_type: str = "candidate"


class FrameUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    owner_type: Optional[str] = None
    active: Optional[bool] = None


@router.get("")
def list_frames(db: Session = Depends(get_db)):
    return svc.get_frames_with_counts(db)


@router.post("")
def create_frame(body: FrameCreate, db: Session = Depends(get_db)):
    owner = body.owner_type if body.owner_type in ("candidate", "opponent", "media") else "candidate"
    frame = NarrativeFrame(
        name=body.name.strip(),
        description=(body.description or "").strip() or None,
        owner_type=owner,
        source="human",
        active=True,
    )
    db.add(frame)
    db.commit()
    db.refresh(frame)
    return {"id": frame.id, "name": frame.name, "description": frame.description, "owner_type": frame.owner_type}


@router.put("/{frame_id}")
def update_frame(frame_id: int, body: FrameUpdate, db: Session = Depends(get_db)):
    frame = db.query(NarrativeFrame).get(frame_id)
    if not frame:
        raise HTTPException(status_code=404, detail="Frame not found")
    if body.name is not None:
        frame.name = body.name.strip()
    if body.description is not None:
        frame.description = body.description.strip() or None
    if body.owner_type is not None and body.owner_type in ("candidate", "opponent", "media"):
        frame.owner_type = body.owner_type
    if body.active is not None:
        frame.active = body.active
    frame.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


@router.delete("/{frame_id}")
def delete_frame(frame_id: int, db: Session = Depends(get_db)):
    frame = db.query(NarrativeFrame).get(frame_id)
    if not frame:
        raise HTTPException(status_code=404, detail="Frame not found")
    db.delete(frame)
    db.commit()
    return {"ok": True}


@router.post("/suggest")
def suggest_frames(days_back: int = 14, db: Session = Depends(get_db)):
    """Ask the LLM to suggest narrative frames from recent article summaries."""
    frames = svc.suggest_frames(db, days_back=days_back)
    return {"suggested": len(frames), "frames": frames}


@router.post("/rematch")
def rematch_articles(days_back: int = 30, db: Session = Depends(get_db)):
    """Rematch all recent relevant articles to current active frames."""
    count = svc.rematch_all(db, days_back=days_back)
    return {"matched_mentions": count}


@router.delete("/{frame_id}/mentions/{source_item_id}")
def remove_mention(frame_id: int, source_item_id: int, db: Session = Depends(get_db)):
    mention = (
        db.query(NarrativeFrameMention)
        .filter_by(frame_id=frame_id, source_item_id=source_item_id)
        .first()
    )
    if mention:
        db.delete(mention)
        db.commit()
    return {"ok": True}
