"""add text_overrides table for admin manual text edits

Revision ID: 9e1b4f0a3c2d
Revises: 5a5d8ae2f0ec
Create Date: 2026-05-29 23:02:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9e1b4f0a3c2d'
down_revision: Union[str, None] = '5a5d8ae2f0ec'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "text_overrides",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("input_hash", sa.String(length=128), nullable=True),
        sa.Column("created_by_name", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("key", name="uq_text_overrides_key"),
    )
    op.create_index(
        "ix_text_overrides_key",
        "text_overrides",
        ["key"],
    )


def downgrade() -> None:
    op.drop_index("ix_text_overrides_key", table_name="text_overrides")
    op.drop_table("text_overrides")
