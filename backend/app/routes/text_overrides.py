"""Admin-only endpoints for manual text overrides.

The admin (you) can pencil-edit AI-generated text on the live page. The
edit is stored in the `text_overrides` table keyed by a stable string
(e.g. `briefing.memo.headline`) and pinned to the `input_hash` of the
inputs the LLM was working from. The consumer (currently
`briefing_summary.get_or_generate_grounded`) substitutes the value into
its response when the current hash matches; when the hash differs the
row is auto-deleted on read.

Keys are validated against a per-consumer allow-list so this endpoint
can't be used to inject arbitrary text into unrelated parts of the app.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import TextOverride
from app.services.access_codes import AccessUser, require_admin
from app.services.briefing_summary import BRIEFING_OVERRIDE_KEYS

router = APIRouter()


# Allow-list. Every consumer registers its keys here so this endpoint
# can't be used to inject arbitrary overrides into unrelated paths.
_ALLOWED_KEYS: set[str] = set(BRIEFING_OVERRIDE_KEYS)


class OverrideBody(BaseModel):
    value: str
    # The hash of the inputs the admin was looking at when they saved.
    # When the consumer reads, it compares this against the current hash
    # and auto-clears the row if it no longer matches.
    input_hash: str | None = None


class OverrideOut(BaseModel):
    key: str
    value: str
    input_hash: str | None
    created_by_name: str | None
    created_at: str | None
    updated_at: str | None


def _to_out(row: TextOverride) -> OverrideOut:
    return OverrideOut(
        key=row.key,
        value=row.value,
        input_hash=row.input_hash,
        created_by_name=row.created_by_name,
        created_at=row.created_at.isoformat() if row.created_at else None,
        updated_at=row.updated_at.isoformat() if row.updated_at else None,
    )


@router.put("/admin/text-overrides/{key}", response_model=OverrideOut)
def upsert_override(
    body: OverrideBody,
    key: str = Path(..., min_length=1, max_length=128),
    db: Session = Depends(get_db),
    user: AccessUser = Depends(require_admin),
) -> OverrideOut:
    if key not in _ALLOWED_KEYS:
        raise HTTPException(status_code=400, detail=f"unknown override key: {key}")
    value = (body.value or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="value must not be empty")
    row = db.query(TextOverride).filter(TextOverride.key == key).first()
    now = datetime.utcnow()
    if row is None:
        row = TextOverride(
            key=key,
            value=value,
            input_hash=body.input_hash,
            created_by_name=user.name or None,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
    else:
        row.value = value
        row.input_hash = body.input_hash
        row.created_by_name = user.name or row.created_by_name
        row.updated_at = now
    db.commit()
    db.refresh(row)
    return _to_out(row)


@router.delete("/admin/text-overrides/{key}")
def clear_override(
    key: str = Path(..., min_length=1, max_length=128),
    db: Session = Depends(get_db),
    user: AccessUser = Depends(require_admin),  # noqa: ARG001 — auth gate
) -> dict:
    if key not in _ALLOWED_KEYS:
        raise HTTPException(status_code=400, detail=f"unknown override key: {key}")
    row = db.query(TextOverride).filter(TextOverride.key == key).first()
    if row is not None:
        db.delete(row)
        db.commit()
    return {"cleared": True, "key": key}
