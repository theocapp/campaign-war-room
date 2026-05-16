from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import SourceMonitor

router = APIRouter()


class SourceMonitorOut(BaseModel):
    id: int
    name: str
    monitor_type: str
    query: Optional[str]
    url: Optional[str]
    source_type: str
    category: Optional[str]
    active: bool
    required_terms: Optional[list[str]]
    excluded_terms: Optional[list[str]]
    relevance_hint: Optional[str]
    last_checked_at: Optional[str]
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True

    @classmethod
    def from_orm_obj(cls, m: SourceMonitor) -> "SourceMonitorOut":
        import json
        def _parse(v):
            if v is None:
                return None
            try:
                return json.loads(v)
            except Exception:
                return None
        return cls(
            id=m.id,
            name=m.name,
            monitor_type=m.monitor_type,
            query=m.query,
            url=m.url,
            source_type=m.source_type or "news",
            category=m.category,
            active=bool(m.active),
            required_terms=_parse(m.required_terms),
            excluded_terms=_parse(m.excluded_terms),
            relevance_hint=m.relevance_hint,
            last_checked_at=m.last_checked_at.isoformat() if m.last_checked_at else None,
            created_at=m.created_at.isoformat() if m.created_at else "",
            updated_at=m.updated_at.isoformat() if m.updated_at else "",
        )


class SourceMonitorCreate(BaseModel):
    name: str
    monitor_type: str
    query: Optional[str] = None
    url: Optional[str] = None
    source_type: str = "news"
    category: Optional[str] = None
    relevance_hint: Optional[str] = None
    required_terms: Optional[list[str]] = None
    excluded_terms: Optional[list[str]] = None


class SourceMonitorUpdate(BaseModel):
    name: Optional[str] = None
    query: Optional[str] = None
    url: Optional[str] = None
    source_type: Optional[str] = None
    category: Optional[str] = None
    active: Optional[bool] = None
    relevance_hint: Optional[str] = None


@router.get("/source-monitors", response_model=list[SourceMonitorOut])
def list_monitors(db: Session = Depends(get_db)):
    monitors = db.query(SourceMonitor).order_by(SourceMonitor.name).all()
    return [SourceMonitorOut.from_orm_obj(m) for m in monitors]


@router.post("/source-monitors", response_model=SourceMonitorOut, status_code=201)
def create_monitor(body: SourceMonitorCreate, db: Session = Depends(get_db)):
    import json
    monitor = SourceMonitor(
        name=body.name,
        monitor_type=body.monitor_type,
        query=body.query,
        url=body.url,
        source_type=body.source_type,
        category=body.category,
        relevance_hint=body.relevance_hint,
        required_terms=json.dumps(body.required_terms) if body.required_terms else None,
        excluded_terms=json.dumps(body.excluded_terms) if body.excluded_terms else None,
        active=True,
    )
    db.add(monitor)
    db.commit()
    db.refresh(monitor)
    return SourceMonitorOut.from_orm_obj(monitor)


@router.put("/source-monitors/{monitor_id}", response_model=SourceMonitorOut)
def update_monitor(monitor_id: int, body: SourceMonitorUpdate, db: Session = Depends(get_db)):
    monitor = db.query(SourceMonitor).filter_by(id=monitor_id).first()
    if not monitor:
        raise HTTPException(status_code=404, detail="Monitor not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(monitor, field, value)
    monitor.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(monitor)
    return SourceMonitorOut.from_orm_obj(monitor)


@router.delete("/source-monitors/{monitor_id}", status_code=204)
def delete_monitor(monitor_id: int, db: Session = Depends(get_db)):
    monitor = db.query(SourceMonitor).filter_by(id=monitor_id).first()
    if not monitor:
        raise HTTPException(status_code=404, detail="Monitor not found")
    db.delete(monitor)
    db.commit()
