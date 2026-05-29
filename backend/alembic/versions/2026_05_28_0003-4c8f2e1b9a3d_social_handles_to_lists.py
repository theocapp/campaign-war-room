"""convert social handle columns from single strings to JSON-array lists

Revision ID: 4c8f2e1b9a3d
Revises: 3b6e1d4a7f9c
Create Date: 2026-05-28 22:00:00.000000

Replaces the single-string `instagram_handle` / `facebook_page` columns
(added in revision 3b6e1d4a7f9c) with JSON-array list columns
`instagram_handles` / `facebook_pages`. Politicians routinely run multiple
parallel accounts (campaign / office / personal) and we want to track all
of them, so a single-handle model was wrong from the start.

Migration steps per table:
  1. Add the new TEXT columns (nullable).
  2. Backfill: where the old column has a value, JSON-encode it as a
     1-element array into the new column.
  3. Drop the old columns.

Storage: TEXT with a JSON-encoded `list[str]`. Matches the pattern already
used for `campaign_config.key_priorities`, `relevance_keywords`, etc.
"""
import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4c8f2e1b9a3d"
down_revision: Union[str, None] = "3b6e1d4a7f9c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _migrate_table(table: str, old_col: str, new_col: str) -> None:
    bind = op.get_bind()
    with op.batch_alter_table(table, schema=None) as batch_op:
        batch_op.add_column(sa.Column(new_col, sa.Text(), nullable=True))

    rows = bind.execute(sa.text(
        f"SELECT id, {old_col} FROM {table} WHERE {old_col} IS NOT NULL AND {old_col} != ''"
    )).fetchall()
    for row in rows:
        encoded = json.dumps([row[1]])
        bind.execute(
            sa.text(f"UPDATE {table} SET {new_col} = :v WHERE id = :id"),
            {"v": encoded, "id": row[0]},
        )

    with op.batch_alter_table(table, schema=None) as batch_op:
        batch_op.drop_column(old_col)


def upgrade() -> None:
    for table, old, new in (
        ("campaign_config", "instagram_handle", "instagram_handles"),
        ("campaign_config", "facebook_page", "facebook_pages"),
        ("opponents", "instagram_handle", "instagram_handles"),
        ("opponents", "facebook_page", "facebook_pages"),
    ):
        _migrate_table(table, old, new)


def _revert_table(table: str, list_col: str, single_col: str) -> None:
    bind = op.get_bind()
    with op.batch_alter_table(table, schema=None) as batch_op:
        batch_op.add_column(sa.Column(single_col, sa.String(), nullable=True))

    rows = bind.execute(sa.text(
        f"SELECT id, {list_col} FROM {table} WHERE {list_col} IS NOT NULL AND {list_col} != ''"
    )).fetchall()
    for row in rows:
        try:
            arr = json.loads(row[1])
        except (TypeError, ValueError):
            continue
        if isinstance(arr, list) and arr:
            bind.execute(
                sa.text(f"UPDATE {table} SET {single_col} = :v WHERE id = :id"),
                {"v": arr[0], "id": row[0]},
            )

    with op.batch_alter_table(table, schema=None) as batch_op:
        batch_op.drop_column(list_col)


def downgrade() -> None:
    for table, list_col, single in (
        ("opponents", "facebook_pages", "facebook_page"),
        ("opponents", "instagram_handles", "instagram_handle"),
        ("campaign_config", "facebook_pages", "facebook_page"),
        ("campaign_config", "instagram_handles", "instagram_handle"),
    ):
        _revert_table(table, list_col, single)
