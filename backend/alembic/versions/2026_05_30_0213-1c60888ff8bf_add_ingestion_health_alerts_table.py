"""add ingestion_health_alerts table

Revision ID: 1c60888ff8bf
Revises: 9e1b4f0a3c2d
Create Date: 2026-05-30 02:13:47.038032

Tracks per-source ingestion-quality regressions so we can surface a
notification when a feed silently degrades. Motivated by the 2026-05-26
Google News body-excerpt collapse going unnoticed for 3 days.

Two alert kinds carried in a single table:
  - 'short_body': trailing-24h avg raw_text length is < 50% of the
                  trailing-7d baseline AND below an absolute threshold.
  - 'silent':     source historically posted ≥1 item/day but has gone
                  silent in the last 24h.

Alerts are mutated in-place: `detected_at` is set on first detection,
`resolved_at` flips back to None → datetime when the source recovers.
Only one row exists per (source_name, kind) — re-running the detection
job updates the same row.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1c60888ff8bf'
down_revision: Union[str, None] = '9e1b4f0a3c2d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ingestion_health_alerts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_name", sa.String(length=256), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("detected_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        # Snapshot of the metrics that triggered the alert. Stored so the
        # notification can render "avg body dropped from 1800→90 chars"
        # without having to recompute on the fly.
        sa.Column("baseline_avg_len", sa.Float(), nullable=True),
        sa.Column("current_avg_len", sa.Float(), nullable=True),
        sa.Column("sample_count_24h", sa.Integer(), nullable=True),
        sa.Column("sample_count_7d", sa.Integer(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(), nullable=False),
        # One active+resolved row per (source_name, kind). Re-running the
        # detection job updates the existing row instead of creating
        # duplicates. The unique constraint covers both states because the
        # row sticks around with `resolved_at` populated so the frontend
        # can render "Citizens' Voice: recovered 2h ago" if we want it
        # later. If we ever need to allow multiple historical alerts per
        # (source, kind), this becomes an audit log instead — drop the
        # unique constraint and add a `superseded_at` column.
        sa.UniqueConstraint(
            "source_name", "kind",
            name="uq_ingestion_health_source_kind",
        ),
    )
    op.create_index(
        "ix_ingestion_health_active",
        "ingestion_health_alerts",
        ["resolved_at"],
        postgresql_where=sa.text("resolved_at IS NULL"),
    )


def downgrade() -> None:
    # IF EXISTS so a partial-apply state (alembic_version bumped but the
    # CREATE TABLE rolled back — observed once in dev during the
    # 2026-05-30 boot) can still be cleanly downgraded by hand. Without
    # this, the index drop errors with "relation does not exist" and the
    # whole downgrade aborts before reaching `drop_table`.
    op.execute("DROP INDEX IF EXISTS ix_ingestion_health_active")
    op.execute("DROP TABLE IF EXISTS ingestion_health_alerts")
