from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional

from app.db import get_db
from app.models import Outlet

router = APIRouter()


class OutletOut(BaseModel):
    id: int
    name: str
    domain: str
    outlet_type: str
    state: Optional[str]
    city: Optional[str]
    authority_score: int
    active: bool
    notes: Optional[str]

    class Config:
        from_attributes = True


class OutletUpdate(BaseModel):
    name: Optional[str] = None
    outlet_type: Optional[str] = None
    state: Optional[str] = None
    city: Optional[str] = None
    authority_score: Optional[int] = None
    active: Optional[bool] = None
    notes: Optional[str] = None


class OutletCreate(BaseModel):
    name: str
    domain: str
    outlet_type: str = "local_news"
    state: Optional[str] = None
    city: Optional[str] = None
    authority_score: int = 5
    notes: Optional[str] = None


@router.get("/outlets", response_model=list[OutletOut])
def list_outlets(db: Session = Depends(get_db)):
    return db.query(Outlet).order_by(Outlet.authority_score.desc(), Outlet.name).all()


@router.post("/outlets", response_model=OutletOut, status_code=201)
def create_outlet(body: OutletCreate, db: Session = Depends(get_db)):
    if db.query(Outlet).filter_by(domain=body.domain).first():
        raise HTTPException(status_code=409, detail="An outlet with that domain already exists")
    outlet = Outlet(**body.model_dump())
    db.add(outlet)
    db.commit()
    db.refresh(outlet)
    return outlet


@router.put("/outlets/{outlet_id}", response_model=OutletOut)
def update_outlet(outlet_id: int, body: OutletUpdate, db: Session = Depends(get_db)):
    outlet = db.query(Outlet).filter_by(id=outlet_id).first()
    if not outlet:
        raise HTTPException(status_code=404, detail="Outlet not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(outlet, field, value)
    db.commit()
    db.refresh(outlet)
    return outlet


@router.delete("/outlets/{outlet_id}", status_code=204)
def delete_outlet(outlet_id: int, db: Session = Depends(get_db)):
    outlet = db.query(Outlet).filter_by(id=outlet_id).first()
    if not outlet:
        raise HTTPException(status_code=404, detail="Outlet not found")
    db.delete(outlet)
    db.commit()
