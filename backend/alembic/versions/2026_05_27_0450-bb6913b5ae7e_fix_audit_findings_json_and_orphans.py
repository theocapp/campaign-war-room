"""Phase 0.5 audit fixes — wrap non-JSON relevance_reasons + drop orphan stage_history.

Two data-only cleanups surfaced by preflight_audit.py before Phase 1:

1. source_items.relevance_reasons: ~1,384 rows (of 12,507 non-empty) hold a
   plain LLM sentence instead of the documented JSON array. Wrap each
   plain string in a one-element JSON array so the column shape is
   consistent. No data lost — the original text is preserved as the array's
   sole element.

2. frame_stage_history: 2 rows (id 7, 11) point at narrative_frames that
   no longer exist (frame_id 18, 43). Postgres will reject these as FK
   violations during the data migration. The frames themselves are gone
   so the stage history is orphan; deleting is the right call.

Both fixes are idempotent — re-running upgrade() on an already-cleaned DB
is a no-op.

Downgrade:
- Reverses (1) cleanly when the wrapped array is still single-element.
- CANNOT reverse (2): we don't have the deleted rows. Logs a warning.

Revision ID: bb6913b5ae7e
Revises: 8658705d5116
Create Date: 2026-05-27 04:50:00
"""
from __future__ import annotations

import json
import logging
from typing import Sequence, Union

from alembic import op

revision: str = "bb6913b5ae7e"
down_revision: Union[str, None] = "8658705d5116"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

log = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    conn = op.get_bind()

    # ── (1) Wrap non-JSON relevance_reasons strings into one-element arrays ──
    cur = conn.exec_driver_sql(
        "SELECT id, relevance_reasons FROM source_items "
        "WHERE relevance_reasons IS NOT NULL AND relevance_reasons != ''"
    )
    to_fix: list[tuple[int, str]] = []
    for row_id, raw in cur:
        try:
            json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            to_fix.append((row_id, raw))

    if to_fix:
        log.info(
            "Wrapping %d non-JSON relevance_reasons values as ['<string>']",
            len(to_fix),
        )
        # Batch update — sqlite handles up to a few thousand parameterized
        # statements per transaction easily.
        for row_id, raw in to_fix:
            wrapped = json.dumps([raw])
            conn.exec_driver_sql(
                "UPDATE source_items SET relevance_reasons = :v WHERE id = :id",
                {"v": wrapped, "id": row_id},
            )
    else:
        log.info("No non-JSON relevance_reasons rows to fix.")

    # ── (2) Delete frame_stage_history rows pointing at deleted frames ──
    orphan_ids = [
        r[0]
        for r in conn.exec_driver_sql(
            "SELECT fsh.id FROM frame_stage_history fsh "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM narrative_frames nf WHERE nf.id = fsh.frame_id"
            ")"
        )
    ]
    if orphan_ids:
        log.info(
            "Deleting %d orphan frame_stage_history rows: %s",
            len(orphan_ids), orphan_ids,
        )
        conn.exec_driver_sql(
            f"DELETE FROM frame_stage_history WHERE id IN ({','.join('?' for _ in orphan_ids)})",
            tuple(orphan_ids),
        )
    else:
        log.info("No orphan frame_stage_history rows to delete.")


def downgrade() -> None:
    conn = op.get_bind()

    # Reverse (1): if a relevance_reasons value is a one-element JSON array
    # containing a single string AND that string doesn't itself parse as JSON,
    # unwrap it. This is conservative — we only revert wrappings we likely
    # created. Anything that was already a real one-element array before this
    # migration ran will also be unwrapped (lossy in principle, but the result
    # — a plain string — was the original shape anyway).
    cur = conn.exec_driver_sql(
        "SELECT id, relevance_reasons FROM source_items "
        "WHERE relevance_reasons IS NOT NULL AND relevance_reasons != ''"
    )
    reverted = 0
    for row_id, raw in cur:
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if (
            isinstance(parsed, list)
            and len(parsed) == 1
            and isinstance(parsed[0], str)
        ):
            try:
                # If the inner string itself parses, it was a real array
                # element — leave it.
                json.loads(parsed[0])
                continue
            except (json.JSONDecodeError, TypeError):
                pass
            conn.exec_driver_sql(
                "UPDATE source_items SET relevance_reasons = :v WHERE id = :id",
                {"v": parsed[0], "id": row_id},
            )
            reverted += 1
    log.info("Reverted %d wrapped relevance_reasons values back to plain string.", reverted)

    # (2) is irreversible — the deleted rows are gone. Log explicitly.
    log.warning(
        "downgrade(): the 2 orphan frame_stage_history rows deleted by upgrade() "
        "cannot be restored — they're lost. (frame_id 18 and 43 were already "
        "deleted before the cleanup, so the stage_history rows had nothing real "
        "to point at anyway.)"
    )
