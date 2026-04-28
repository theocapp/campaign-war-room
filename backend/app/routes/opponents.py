from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.db import get_db
from app.models import Opponent, OpponentActivity
from app.schemas import OpponentOut, OpponentIn, OpponentActivityOut

router = APIRouter()


@router.get("/opponents", response_model=list[OpponentOut])
def list_opponents(db: Session = Depends(get_db)):
    return db.query(Opponent).all()


@router.post("/opponents", response_model=OpponentOut)
def add_opponent(body: OpponentIn, db: Session = Depends(get_db)):
    opp = Opponent(**body.model_dump())
    db.add(opp)
    db.commit()
    db.refresh(opp)
    return opp


@router.get("/opponents/{opponent_id}", response_model=OpponentOut)
def get_opponent(opponent_id: int, db: Session = Depends(get_db)):
    opp = db.get(Opponent, opponent_id)
    if not opp:
        raise HTTPException(status_code=404, detail="Opponent not found")
    return opp


@router.get("/opponents/{opponent_id}/activity", response_model=list[OpponentActivityOut])
def get_opponent_activity(opponent_id: int, db: Session = Depends(get_db)):
    opp = db.get(Opponent, opponent_id)
    if not opp:
        raise HTTPException(status_code=404, detail="Opponent not found")
    activities = (
        db.query(OpponentActivity)
        .options(joinedload(OpponentActivity.source_item))
        .filter(OpponentActivity.opponent_id == opponent_id)
        .order_by(OpponentActivity.created_at.desc())
        .all()
    )
    return activities
