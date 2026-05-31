"""add indexes for story_cluster_id and nfm source_item_id

Revision ID: 84a1922627dd
Revises: 2df994cdd1f9
Create Date: 2026-05-31 07:23:33.083901

Why these indexes exist
-----------------------
Both columns are heavy lookup/join keys that were left unindexed:

1. source_items.story_cluster_id — the SimHash story-cluster key, joined or
   filtered in ~10 places (cluster member fetches, dedup, dashboard rollups).
   A seq scan over ~24k rows showed ~366ms on EXPLAIN. Its sibling columns
   platform and publisher_domain are already index=True; this one was missed.

2. narrative_frame_mentions.source_item_id — an FK with no standalone index.
   The UniqueConstraint (frame_id, source_item_id) is leading-column frame_id,
   so it does NOT serve "all frames mentioning this article" lookups, which
   filter on source_item_id alone (article-detail frame chips, rematch).

Purely additive and read-accelerating; no data is touched. Index names match
SQLAlchemy's default for `index=True` (ix_<table>_<column>), so models.py and
the schema stay in sync and future autogenerate sees no drift.

CREATE INDEX IF NOT EXISTS: this migration runs automatically at app startup;
the guard keeps a re-run or partial-apply from crashing the boot.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '84a1922627dd'
down_revision: Union[str, None] = '2df994cdd1f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_source_items_story_cluster_id "
        "ON source_items (story_cluster_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_narrative_frame_mentions_source_item_id "
        "ON narrative_frame_mentions (source_item_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_narrative_frame_mentions_source_item_id")
    op.execute("DROP INDEX IF EXISTS ix_source_items_story_cluster_id")
