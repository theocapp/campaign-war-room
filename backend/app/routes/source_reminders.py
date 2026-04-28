"""Manual source reminders — non-RSS sources that need periodic checking."""
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ManualSourceReminder
from app.schemas import (
    ManualSourceReminderOut, ManualSourceReminderIn, ManualSourceReminderUpdate,
)

router = APIRouter()


@router.get("/source-reminders", response_model=list[ManualSourceReminderOut])
def list_reminders(db: Session = Depends(get_db)):
    return (
        db.query(ManualSourceReminder)
        .order_by(ManualSourceReminder.last_checked_at.asc().nullsfirst(),
                  ManualSourceReminder.created_at.asc())
        .all()
    )


@router.post("/source-reminders", response_model=ManualSourceReminderOut)
def create_reminder(body: ManualSourceReminderIn, db: Session = Depends(get_db)):
    r = ManualSourceReminder(
        name=body.name,
        category=body.category,
        source_type=body.source_type,
        url=body.url,
        setup_note=body.setup_note,
        active=True,
        created_at=datetime.utcnow(),
    )
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


@router.put("/source-reminders/{reminder_id}", response_model=ManualSourceReminderOut)
def update_reminder(
    reminder_id: int, body: ManualSourceReminderUpdate, db: Session = Depends(get_db)
):
    r = db.get(ManualSourceReminder, reminder_id)
    if not r:
        raise HTTPException(status_code=404, detail="Reminder not found")
    for field, val in body.model_dump(exclude_unset=True).items():
        setattr(r, field, val)
    db.commit()
    db.refresh(r)
    return r


@router.delete("/source-reminders/{reminder_id}")
def delete_reminder(reminder_id: int, db: Session = Depends(get_db)):
    r = db.get(ManualSourceReminder, reminder_id)
    if not r:
        raise HTTPException(status_code=404, detail="Reminder not found")
    db.delete(r)
    db.commit()
    return {"deleted": reminder_id}


@router.post("/source-reminders/{reminder_id}/mark-checked", response_model=ManualSourceReminderOut)
def mark_checked(reminder_id: int, db: Session = Depends(get_db)):
    r = db.get(ManualSourceReminder, reminder_id)
    if not r:
        raise HTTPException(status_code=404, detail="Reminder not found")
    r.last_checked_at = datetime.utcnow()
    db.commit()
    db.refresh(r)
    return r
