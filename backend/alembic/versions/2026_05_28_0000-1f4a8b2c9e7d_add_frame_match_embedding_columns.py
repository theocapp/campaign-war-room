"""add frame_match_embedding columns to source_items

Revision ID: 1f4a8b2c9e7d
Revises: 28b4f7fc89b4
Create Date: 2026-05-28 00:00:00.000000

Adds two nullable columns to source_items used by the rematch gate
(narrative_frames._shortlist_frames_for_article):

  • frame_match_embedding (TEXT)        — JSON-encoded float list
  • frame_match_embedding_model (VARCHAR) — provider/model identifier,
    used to invalidate cached embeddings when the provider changes

Both are nullable so existing rows continue to work; the gate falls back to
live-embedding when the column is NULL or the model doesn't match the
current provider.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "1f4a8b2c9e7d"
down_revision: Union[str, None] = "28b4f7fc89b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("source_items", schema=None) as batch_op:
        batch_op.add_column(sa.Column("frame_match_embedding", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("frame_match_embedding_model", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("source_items", schema=None) as batch_op:
        batch_op.drop_column("frame_match_embedding_model")
        batch_op.drop_column("frame_match_embedding")
