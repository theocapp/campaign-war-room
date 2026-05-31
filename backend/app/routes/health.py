"""Ingestion-quality alerts surface.

Powers the dashboard notifications bell — the frontend
`lib/notifications.ts` calls `/api/health/ingestion-alerts` to add
notifications for currently-firing alerts. The detector itself runs in
the scheduler (see `services/scheduler.py:_run_ingestion_health_check`)
and persists rows to `ingestion_health_alerts`.

Public read endpoint (not admin-only). Admin-only POST exists so we
can trigger an out-of-band check from the admin tools.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.access_codes import require_admin
from app.services import ingestion_health


router = APIRouter()


@router.get("/health/ingestion-alerts")
def list_ingestion_alerts(db: Session = Depends(get_db)):
    """All currently-firing (unresolved) ingestion-quality alerts.

    Returns a list of dicts; the frontend turns each into a notification
    of kind `ingestion_quality`. Empty list when everything is healthy.
    """
    return {"alerts": ingestion_health.get_active_alerts(db)}


@router.post(
    "/admin/health/ingestion-alerts/run",
    dependencies=[Depends(require_admin)],
)
def run_ingestion_health_check(db: Session = Depends(get_db)):
    """Trigger an immediate health check. Normally runs once a day via
    the scheduler; this endpoint exists for admin debugging and for the
    first-time rollout (so we don't have to wait 24h for the alert
    surface to populate)."""
    return ingestion_health.run_health_check(db)
