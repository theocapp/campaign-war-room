"""add frame_content_hash to frame_cluster_matches

Revision ID: 2a5b9c3d8e6f
Revises: 1f4a8b2c9e7d
Create Date: 2026-05-28 00:01:00.000000

Adds a nullable SHA1 column that stores the hash of a frame's
name+description at the time a match was made. When a frame is edited,
its hash changes; matches whose stored hash differs from the frame's
current hash are stale and can be re-evaluated or cleaned up.

Nullable so existing rows continue to work; the auto-rematch will
populate it on next pass.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "2a5b9c3d8e6f"
down_revision: Union[str, None] = "1f4a8b2c9e7d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("frame_cluster_matches", schema=None) as batch_op:
        batch_op.add_column(sa.Column("frame_content_hash", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("frame_cluster_matches", schema=None) as batch_op:
        batch_op.drop_column("frame_content_hash")
