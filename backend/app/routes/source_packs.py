"""Source packs — curated collections of feed/reminder templates for common race types."""
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.db import get_db
from app.models import SourcePack, SourcePackItem, RssFeed, ManualSourceReminder
from app.schemas import (
    SourcePackOut, SourcePackCreate, SourcePackApplyResult,
)

router = APIRouter()

_PLACEHOLDER = "{"  # items whose URL starts with { are template placeholders


def _is_real_url(url: str | None) -> bool:
    """True if the URL looks like a real (non-placeholder) RSS-style URL."""
    if not url:
        return False
    if _PLACEHOLDER in url:
        return False
    lower = url.lower()
    return any(tok in lower for tok in ("/rss", "/feed", "/atom", ".rss", ".atom", "rss.xml", "feed.xml"))


@router.get("/source-packs", response_model=list[SourcePackOut])
def list_packs(db: Session = Depends(get_db)):
    return (
        db.query(SourcePack)
        .options(joinedload(SourcePack.items))
        .order_by(SourcePack.created_at)
        .all()
    )


@router.get("/source-packs/{pack_id}", response_model=SourcePackOut)
def get_pack(pack_id: int, db: Session = Depends(get_db)):
    pack = db.query(SourcePack).options(joinedload(SourcePack.items)).filter_by(id=pack_id).first()
    if not pack:
        raise HTTPException(status_code=404, detail="Pack not found")
    return pack


@router.post("/source-packs", response_model=SourcePackOut)
def create_pack(body: SourcePackCreate, db: Session = Depends(get_db)):
    pack = SourcePack(
        name=body.name,
        description=body.description,
        race_level=body.race_level,
        geography=body.geography,
        created_at=datetime.utcnow(),
    )
    db.add(pack)
    db.flush()
    for item_data in body.items:
        item = SourcePackItem(
            source_pack_id=pack.id,
            name=item_data.get("name", ""),
            category=item_data.get("category"),
            source_type=item_data.get("source_type", "news"),
            url=item_data.get("url"),
            setup_note=item_data.get("setup_note"),
        )
        db.add(item)
    db.commit()
    db.refresh(pack)
    return pack


@router.post("/source-packs/{pack_id}/apply", response_model=SourcePackApplyResult)
def apply_pack(pack_id: int, db: Session = Depends(get_db)):
    pack = db.query(SourcePack).options(joinedload(SourcePack.items)).filter_by(id=pack_id).first()
    if not pack:
        raise HTTPException(status_code=404, detail="Pack not found")

    feeds_created = 0
    reminders_created = 0
    skipped = 0

    for item in pack.items:
        if not item.active:
            continue

        if _is_real_url(item.url):
            # Create as RSS feed — skip if URL already exists
            existing = db.query(RssFeed).filter_by(url=item.url).first()
            if existing:
                skipped += 1
            else:
                db.add(RssFeed(
                    name=item.name,
                    url=item.url,
                    source_type=item.source_type or "news",
                    active=True,
                    created_at=datetime.utcnow(),
                ))
                feeds_created += 1
        else:
            # Create as manual reminder — skip if same name exists
            existing = db.query(ManualSourceReminder).filter_by(name=item.name).first()
            if existing:
                skipped += 1
            else:
                db.add(ManualSourceReminder(
                    name=item.name,
                    category=item.category,
                    source_type=item.source_type or "news",
                    url=item.url if item.url and _PLACEHOLDER not in item.url else None,
                    setup_note=item.setup_note,
                    active=True,
                    created_at=datetime.utcnow(),
                ))
                reminders_created += 1

    db.commit()
    return SourcePackApplyResult(
        pack_name=pack.name,
        feeds_created=feeds_created,
        reminders_created=reminders_created,
        skipped_duplicate_feeds=skipped,
    )
