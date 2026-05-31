"""Race setup CSV import — bulk-create campaign profile, opponents, feeds, reminders."""
import csv
import io
import json
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, UploadFile, File
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import CampaignConfig, Opponent, RssFeed, ManualSourceReminder
from app.schemas import RaceImportResult
from app.services.access_codes import require_admin

router = APIRouter()

# Bulk CSV import overwrites the campaign profile and mass-creates opponents /
# feeds / reminders — a privileged config mutation, so gate it.
_admin_only = [Depends(require_admin)]

_VALID_TYPES = {"campaign", "opponent", "rss_feed", "reminder"}


def _parse_date(s: str) -> Optional[datetime]:
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s.strip(), fmt)
        except ValueError:
            continue
    return None


@router.post("/race/import-csv", response_model=RaceImportResult, dependencies=_admin_only)
async def import_race_csv(file: UploadFile = File(...), db: Session = Depends(get_db)):
    content = await file.read()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))

    campaign_updated = False
    opponents_created = 0
    feeds_created = 0
    reminders_created = 0
    skipped = 0
    errors: list[str] = []

    for i, row in enumerate(reader, start=2):  # row 1 is header
        row_type = (row.get("type") or "").strip().lower()
        if not row_type:
            skipped += 1
            continue
        if row_type not in _VALID_TYPES:
            errors.append(f"Row {i}: unknown type '{row_type}'")
            continue

        name = (row.get("name") or "").strip()
        url = (row.get("url") or "").strip() or None
        notes = (row.get("notes") or "").strip() or None
        category = (row.get("category") or "").strip() or None
        source_type = (row.get("source_type") or "news").strip()

        # SAVEPOINT-per-row so a bad row doesn't discard earlier valid rows.
        # Context-manager form: commits on normal exit (including early
        # `continue`), rolls back on exception.
        try:
          with db.begin_nested():
            if row_type == "campaign":
                if not name:
                    errors.append(f"Row {i}: campaign row requires 'name'")
                    continue
                config = db.query(CampaignConfig).first()
                election_date = _parse_date(row.get("election_date") or "")
                kp_raw = (row.get("key_priorities") or "").strip()
                key_priorities = json.dumps([p.strip() for p in kp_raw.split("|") if p.strip()]) if kp_raw else None
                if config:
                    config.candidate_name = name
                    config.office = (row.get("office") or "").strip() or config.office
                    config.district = (row.get("district") or "").strip() or config.district
                    config.party = (row.get("party") or "").strip() or config.party
                    config.location = (row.get("location") or "").strip() or config.location
                    if election_date:
                        config.election_date = election_date
                    if notes:
                        config.campaign_message = notes
                    if key_priorities:
                        config.key_priorities = key_priorities
                    config.updated_at = datetime.utcnow()
                else:
                    config = CampaignConfig(
                        candidate_name=name,
                        office=(row.get("office") or "").strip() or None,
                        district=(row.get("district") or "").strip() or None,
                        party=(row.get("party") or "").strip() or None,
                        location=(row.get("location") or "").strip() or None,
                        election_date=election_date,
                        campaign_message=notes,
                        key_priorities=key_priorities,
                        created_at=datetime.utcnow(),
                        updated_at=datetime.utcnow(),
                    )
                    db.add(config)
                db.flush()
                campaign_updated = True

            elif row_type == "opponent":
                if not name:
                    errors.append(f"Row {i}: opponent row requires 'name'")
                    continue
                existing = db.query(Opponent).filter_by(name=name).first()
                if existing:
                    skipped += 1
                    continue
                db.add(Opponent(
                    name=name,
                    office=(row.get("office") or "").strip() or None,
                    party=(row.get("party") or "").strip() or None,
                    notes=notes,
                    created_at=datetime.utcnow(),
                ))
                db.flush()
                opponents_created += 1

            elif row_type == "rss_feed":
                if not url:
                    errors.append(f"Row {i}: rss_feed row requires 'url'")
                    continue
                existing = db.query(RssFeed).filter_by(url=url).first()
                if existing:
                    skipped += 1
                    continue
                feed_name = name or url
                db.add(RssFeed(
                    name=feed_name,
                    url=url,
                    source_type=source_type,
                    active=True,
                    created_at=datetime.utcnow(),
                ))
                db.flush()
                feeds_created += 1

            elif row_type == "reminder":
                if not name:
                    errors.append(f"Row {i}: reminder row requires 'name'")
                    continue
                existing = db.query(ManualSourceReminder).filter_by(name=name).first()
                if existing:
                    skipped += 1
                    continue
                db.add(ManualSourceReminder(
                    name=name,
                    category=category,
                    source_type=source_type,
                    url=url,
                    setup_note=notes,
                    active=True,
                    created_at=datetime.utcnow(),
                ))
                db.flush()
                reminders_created += 1

        except Exception as e:
            errors.append(f"Row {i}: {e}")
            continue

    db.commit()
    return RaceImportResult(
        campaign_updated=campaign_updated,
        opponents_created=opponents_created,
        feeds_created=feeds_created,
        reminders_created=reminders_created,
        skipped=skipped,
        errors=errors,
    )
