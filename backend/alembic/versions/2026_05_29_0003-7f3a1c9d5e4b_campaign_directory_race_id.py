"""link campaign_config to its race_directory entry

Revision ID: 7f3a1c9d5e4b
Revises: 6e2b8c4a9d1f
Create Date: 2026-05-29 12:00:00.000000

Adds nullable campaign_config.directory_race_id → race_directory.id. Set by
select_directory_race() when the user picks a race in the Setup page; powers
the "reset field to FEC default" affordance on individual auto-filled fields
(party, district, office, election date, etc.) without making the frontend
guess which race the campaign was derived from.

ON DELETE SET NULL — wiping the directory shouldn't drop the campaign.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7f3a1c9d5e4b"
down_revision: Union[str, None] = "6e2b8c4a9d1f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Postgres auto-named the FK as `campaign_config_directory_race_id_fkey` when
# this was applied; the downgrade matches that name so a rollback works on
# the current live DB. Keep this in sync if the FK is ever recreated with a
# different name.
_FK_NAME = "campaign_config_directory_race_id_fkey"


def upgrade() -> None:
    op.add_column(
        "campaign_config",
        sa.Column("directory_race_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        _FK_NAME,
        "campaign_config",
        "race_directory",
        ["directory_race_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(_FK_NAME, "campaign_config", type_="foreignkey")
    op.drop_column("campaign_config", "directory_race_id")
