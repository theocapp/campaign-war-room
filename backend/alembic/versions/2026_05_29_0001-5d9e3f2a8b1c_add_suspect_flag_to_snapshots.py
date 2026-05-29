"""add suspect flag to race_sentiment_snapshots

Revision ID: 5d9e3f2a8b1c
Revises: 4c8f2e1b9a3d
Create Date: 2026-05-29 00:01:00.000000

Two new columns on race_sentiment_snapshots:

  * `suspect`         BOOL  NOT NULL DEFAULT 0
  * `suspect_reason`  STR   NULL

A snapshot is "suspect" when there's good reason to believe it reflects
a data-quality glitch rather than a real market move. Suspect rows
stay in the DB for audit but are filtered out of charts and impact
computations by default.

We use TWO checks, applied together — a row is suspect if EITHER fires:

  1. Gross coherence failure
     candidate_pct + opponent_pct outside [80%, 120%]. Two sides of the
     same binary contract should sum to ~$1.00; gross deviation means
     the scraper read desynchronized / stale prices. We use a wide
     band because Polymarket's natural bid-ask spread on House races
     can drive sums into the 85-115% range legitimately; the wide band
     catches only egregious cases.

  2. Temporal isolation (the main check)
     Snapshot value differs from BOTH temporal neighbors by > 15
     percentage points IN OPPOSITE DIRECTIONS — i.e., the value
     spikes/dips and then immediately snaps back. This is the
     signature of a momentary order-book glitch or a stale-price
     flash that gets corrected on the next sync. Crucially, REAL
     catastrophic events would NOT be isolated — a real Cognetti
     collapse would show as a sustained move with no snap-back.

The 2026-05-26 22:56 Kalshi row (cand=9.5, opp=94.0; sum=103.5%) is
caught by check 2 — neighbors 19h before and 12m after both show
~60%, so the dip is sandwiched.

Suspect rows are NEVER deleted. The history endpoint filters them out
by default; pass ?include_suspect=true to see them.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "5d9e3f2a8b1c"
down_revision: Union[str, None] = "4c8f2e1b9a3d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_COHERENCE_MIN = 80.0
_COHERENCE_MAX = 120.0
_ISOLATION_PT = 15.0          # threshold for "wildly different from neighbor"
_NEIGHBOR_MAX_DAYS = 7        # don't compare against neighbors > 7 days away


def _backfill_suspect_flags(bind) -> int:
    """Apply both checks to existing rows. Returns count flagged.

    Runs the coherence check first (cheap, per-row), then the temporal
    isolation check (per-source, requires sorted scan).
    """
    flagged: dict[int, str] = {}  # row_id → reason

    # ─── Check 1: coherence ─────────────────────────────────────────────
    rows = bind.execute(sa.text(
        "SELECT id, candidate_pct, opponent_pct "
        "FROM race_sentiment_snapshots "
        "WHERE candidate_pct IS NOT NULL AND opponent_pct IS NOT NULL"
    )).fetchall()
    for row_id, cand, opp in rows:
        total = float(cand) + float(opp)
        if total < _COHERENCE_MIN or total > _COHERENCE_MAX:
            flagged[row_id] = f"incoherent: cand+opp={total:.1f}%"

    # ─── Check 2: temporal isolation ────────────────────────────────────
    # For each source, walk through snapshots ordered by captured_at.
    # An isolated outlier has value V where V-prev and next-V are BOTH
    # > _ISOLATION_PT in the same direction (i.e., V is way off and the
    # next snapshot snaps back to where prev was).
    sources = [r[0] for r in bind.execute(sa.text(
        "SELECT DISTINCT source FROM race_sentiment_snapshots "
        "WHERE candidate_pct IS NOT NULL"
    )).fetchall()]
    for source in sources:
        rows = bind.execute(sa.text(
            "SELECT id, captured_at, candidate_pct "
            "FROM race_sentiment_snapshots "
            "WHERE source = :source AND candidate_pct IS NOT NULL "
            "ORDER BY captured_at"
        ), {"source": source}).fetchall()
        for i in range(1, len(rows) - 1):
            prev_id, prev_at, prev_v = rows[i - 1]
            this_id, this_at, this_v = rows[i]
            next_id, next_at, next_v = rows[i + 1]
            # SQLite returns timestamps as strings; parse to compare
            from datetime import datetime
            def _parse(x):
                if isinstance(x, datetime):
                    return x
                return datetime.fromisoformat(str(x))
            prev_dt = _parse(prev_at)
            this_dt = _parse(this_at)
            next_dt = _parse(next_at)
            if (this_dt - prev_dt).days > _NEIGHBOR_MAX_DAYS:
                continue
            if (next_dt - this_dt).days > _NEIGHBOR_MAX_DAYS:
                continue
            delta_in = float(this_v) - float(prev_v)
            delta_out = float(next_v) - float(this_v)
            # Opposite signs + both larger than threshold = isolated outlier
            if (
                delta_in >  _ISOLATION_PT and delta_out < -_ISOLATION_PT
            ) or (
                delta_in < -_ISOLATION_PT and delta_out >  _ISOLATION_PT
            ):
                reason = (
                    f"isolated outlier: {prev_v}→{this_v}→{next_v} "
                    f"({delta_in:+.1f}, then {delta_out:+.1f})"
                )
                # Don't clobber a coherence reason if both fire
                if this_id not in flagged:
                    flagged[this_id] = reason

    # ─── Persist ────────────────────────────────────────────────────────
    # `:flag` carries a real Python bool so SQLAlchemy renders it correctly
    # on both SQLite (INTEGER 1) and Postgres (boolean true).
    for row_id, reason in flagged.items():
        bind.execute(
            sa.text(
                "UPDATE race_sentiment_snapshots "
                "SET suspect = :flag, suspect_reason = :reason WHERE id = :id"
            ),
            {"flag": True, "reason": reason, "id": row_id},
        )
    return len(flagged)


def upgrade() -> None:
    # `sa.false()` renders as `false` on Postgres and `0` on SQLite — both
    # are valid for a NOT NULL BOOLEAN default.
    with op.batch_alter_table("race_sentiment_snapshots", schema=None) as batch_op:
        batch_op.add_column(sa.Column(
            "suspect", sa.Boolean(),
            nullable=False, server_default=sa.false(),
        ))
        batch_op.add_column(sa.Column(
            "suspect_reason", sa.String(), nullable=True,
        ))
        batch_op.create_index(
            "ix_race_sentiment_snapshots_suspect",
            ["suspect"],
        )

    bind = op.get_bind()
    n = _backfill_suspect_flags(bind)
    print(f"[5d9e3f2a8b1c] flagged {n} historical snapshot(s) as suspect")


def downgrade() -> None:
    with op.batch_alter_table("race_sentiment_snapshots", schema=None) as batch_op:
        batch_op.drop_index("ix_race_sentiment_snapshots_suspect")
        batch_op.drop_column("suspect_reason")
        batch_op.drop_column("suspect")
