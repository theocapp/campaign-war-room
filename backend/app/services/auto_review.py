"""
Automatic review queue triage.

Runs after every ingest cycle so the manual review queue stays small and
focused on genuinely ambiguous articles. The AI scoring pipeline already
assigns a relevance score and actionability label to every article; this
service acts on those signals without requiring human confirmation for
high-confidence cases.

Thresholds
----------
Auto-approve  (reviewed=True):
  - race_relevance_score >= 70   (AI very confident it's relevant)
  - OR actionability_label in {"respond", "review"} with score >= 60
    (AI explicitly flagged these as worth acting on)

Auto-dismiss  (dismissed=True, archived_as_irrelevant=True):
  - race_relevance_score < 40 AND actionability_label = "ignore"
    (AI confident it's not relevant to the race)

Manual queue  (everything else: score 40–69 without strong label)
"""

import logging
from sqlalchemy.orm import Session
from app.models import SourceItem

log = logging.getLogger(__name__)

AUTO_APPROVE_SCORE = 55       # confident relevant: auto-approve
STRONG_ACTION_SCORE = 40     # "respond"/"review" label at this score: auto-approve
AUTO_DISMISS_SCORE = 30      # low score: not worth manual review


def auto_review_queue(db: Session) -> dict:
    """
    Triage all unreviewed, un-dismissed items in the queue.
    Returns counts: {approved, dismissed, skipped}.
    """
    pending = (
        db.query(SourceItem)
        .filter(
            SourceItem.reviewed == False,       # noqa: E712
            SourceItem.dismissed == False,      # noqa: E712
            SourceItem.archived_as_irrelevant == False,  # noqa: E712
            SourceItem.race_relevance_score.isnot(None),
        )
        .all()
    )

    approved = dismissed = skipped = 0

    for item in pending:
        score = item.race_relevance_score or 0
        action = item.actionability_label or "ignore"

        if score >= AUTO_APPROVE_SCORE or (
            score >= STRONG_ACTION_SCORE and action in ("respond", "review")
        ):
            item.reviewed = True
            approved += 1

        elif score < AUTO_DISMISS_SCORE:
            item.dismissed = True
            item.archived_as_irrelevant = True
            dismissed += 1

        else:
            skipped += 1

    db.commit()
    log.info(
        "auto_review_queue: approved=%d  dismissed=%d  left_for_manual=%d",
        approved, dismissed, skipped,
    )
    return {"approved": approved, "dismissed": dismissed, "skipped": skipped}
