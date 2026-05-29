"""add instagram_handle and facebook_page to campaign_config and opponents

Revision ID: 3b6e1d4a7f9c
Revises: 2a5b9c3d8e6f
Create Date: 2026-05-28 21:00:00.000000

Adds nullable handle/page columns to the candidate's CampaignConfig row and
to each Opponent row. Values are bare identifiers (e.g. "mayorpaigecognetti",
"RepBresnahan") — the platform URL prefix is appended by the discovery /
RSSHub adapter, not stored here. Empty/null means "no handle confirmed yet"
and the social RSS feed for that actor on that platform simply isn't
created.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "3b6e1d4a7f9c"
down_revision: Union[str, None] = "2a5b9c3d8e6f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("campaign_config", schema=None) as batch_op:
        batch_op.add_column(sa.Column("instagram_handle", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("facebook_page", sa.String(), nullable=True))

    with op.batch_alter_table("opponents", schema=None) as batch_op:
        batch_op.add_column(sa.Column("instagram_handle", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("facebook_page", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("opponents", schema=None) as batch_op:
        batch_op.drop_column("facebook_page")
        batch_op.drop_column("instagram_handle")

    with op.batch_alter_table("campaign_config", schema=None) as batch_op:
        batch_op.drop_column("facebook_page")
        batch_op.drop_column("instagram_handle")
