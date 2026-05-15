"""
Background job: rescore existing articles with the LLM-based campaign_analysis pipeline.

Replaces keyword-based relevance scores for all articles that have raw text.
Runs in a background thread so the server stays responsive.

Progress is tracked in _state so the /api/admin/rescore-status endpoint can report it.
"""
import logging
import threading
import time
from datetime import datetime

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# In-memory job state — survives as long as the process is running
_state: dict = {
    "running": False,
    "started_at": None,
    "finished_at": None,
    "total": 0,
    "processed": 0,
    "updated": 0,
    "errors": 0,
    "current_title": None,
}
_lock = threading.Lock()


def get_status() -> dict:
    with _lock:
        return dict(_state)


def _rescore_one(db: Session, item_id: int) -> bool:
    """Rescore a single article. Returns True if the article was updated."""
    from app.models import SourceItem
    from app.services import campaign_analysis
    from app.services.campaign_analysis import framing_to_action
    from app.services.ingestion import _compute_priority_score
    from app.services import scoring

    item = db.get(SourceItem, item_id)
    if not item:
        return False

    analysis = campaign_analysis.analyze(db, item)
    if analysis.get("_used_fallback"):
        return False

    if analysis.get("one_sentence"):
        item.summary = analysis["one_sentence"]

    item.race_relevance_score = analysis["relevance_score"]
    item.archived_as_irrelevant = not analysis["relevant"]
    item.actionability_label = framing_to_action(analysis["framing"])

    if analysis.get("needs_attention"):
        item.urgency = "high"
    elif analysis["relevant"]:
        item.urgency = "medium"
    else:
        item.urgency = "low"

    item.priority_score = _compute_priority_score(db, item)
    item.evidence_score = scoring.compute_evidence_score(item)
    db.commit()
    return True


def _run_rescore(item_ids: list[int], delay_seconds: float) -> None:
    """Background thread: rescore articles one at a time with a rate-limit delay."""
    from app.db import SessionLocal

    with _lock:
        _state["running"] = True
        _state["started_at"] = datetime.utcnow().isoformat()
        _state["finished_at"] = None
        _state["total"] = len(item_ids)
        _state["processed"] = 0
        _state["updated"] = 0
        _state["errors"] = 0
        _state["current_title"] = None

    for item_id in item_ids:
        if not _state["running"]:
            break
        try:
            with SessionLocal() as db:
                from app.models import SourceItem
                item = db.get(SourceItem, item_id)
                title = (item.title or "")[:60] if item else str(item_id)

            with _lock:
                _state["current_title"] = title

            with SessionLocal() as db:
                updated = _rescore_one(db, item_id)

            with _lock:
                _state["processed"] += 1
                if updated:
                    _state["updated"] += 1

        except Exception as e:
            logger.warning("rescore: failed on item %d: %s", item_id, e)
            with _lock:
                _state["processed"] += 1
                _state["errors"] += 1

        time.sleep(delay_seconds)

    with _lock:
        _state["running"] = False
        _state["finished_at"] = datetime.utcnow().isoformat()
        _state["current_title"] = None

    logger.info(
        "rescore: done. processed=%d updated=%d errors=%d",
        _state["processed"], _state["updated"], _state["errors"],
    )


def start_rescore(db: Session, delay_seconds: float = 4.0) -> dict:
    """
    Start a background rescore job. Returns immediately.
    Only one job can run at a time.
    """
    from app.models import SourceItem

    with _lock:
        if _state["running"]:
            return {"started": False, "reason": "A rescore job is already running."}

    # Fetch IDs of all articles with raw text, ordered oldest first
    # (so newer articles get processed last — they're already better scored)
    item_ids = [
        row[0] for row in
        db.query(SourceItem.id)
        .filter(SourceItem.raw_text.isnot(None), SourceItem.raw_text != "")
        .order_by(SourceItem.created_at.asc())
        .all()
    ]

    if not item_ids:
        return {"started": False, "reason": "No articles with text found."}

    thread = threading.Thread(
        target=_run_rescore,
        args=(item_ids, delay_seconds),
        daemon=True,
        name="rescore-worker",
    )
    thread.start()

    return {
        "started": True,
        "total": len(item_ids),
        "delay_seconds": delay_seconds,
        "estimated_minutes": round(len(item_ids) * delay_seconds / 60, 1),
    }


def stop_rescore() -> dict:
    with _lock:
        if not _state["running"]:
            return {"stopped": False, "reason": "No job running."}
        _state["running"] = False
    return {"stopped": True}
