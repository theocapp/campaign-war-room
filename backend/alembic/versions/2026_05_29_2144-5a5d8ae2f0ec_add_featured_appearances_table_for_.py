"""add featured_appearances table for saturation penalty

Revision ID: 5a5d8ae2f0ec
Revises: 7f3a1c9d5e4b
Create Date: 2026-05-29 21:44:18.853405

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5a5d8ae2f0ec'
down_revision: Union[str, None] = '7f3a1c9d5e4b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "featured_appearances",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "frame_id",
            sa.Integer(),
            sa.ForeignKey("narrative_frames.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("appeared_on", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("frame_id", "appeared_on", name="uq_featured_frame_day"),
    )
    # Index on (appeared_on) speeds the "last 7 days" window scan used by
    # get_frames_with_counts to populate days_featured_last_7.
    op.create_index(
        "ix_featured_appearances_appeared_on",
        "featured_appearances",
        ["appeared_on"],
    )


def downgrade() -> None:
    op.drop_index("ix_featured_appearances_appeared_on", table_name="featured_appearances")
    op.drop_table("featured_appearances")
