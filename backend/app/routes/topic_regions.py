"""Routes for editing topic-region labels on the Landscape page.

The READ side of topic regions piggybacks on /narrative-frames/landscape-established
(the `regions` array in that response). This module only handles the
edit endpoint — kept separate to make the URL space clean and so the
landscape route doesn't need to know about persistence.
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.topic_regions import update_label

router = APIRouter(prefix="/topic-regions", tags=["topic-regions"])


class LabelUpdate(BaseModel):
    label: str


@router.put("/{region_id}/label")
def update_topic_label(region_id: int, body: LabelUpdate, db: Session = Depends(get_db)):
    """Manually rename a region. Marks edited_by_user=True so subsequent
    LLM recomputes won't overwrite it (assuming the region's frame
    membership stays similar — Jaccard match in topic_regions.py).
    """
    if not body.label or not body.label.strip():
        raise HTTPException(status_code=400, detail="Label cannot be empty")
    row = update_label(db, region_id, body.label.strip())
    if not row:
        raise HTTPException(status_code=404, detail="Region label not found")
    return {
        "id": row.id,
        "label": row.label,
        "edited_by_user": row.edited_by_user,
    }
