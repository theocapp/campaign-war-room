"""add platform column to source_items

Revision ID: 2df994cdd1f9
Revises: 1c60888ff8bf
Create Date: 2026-05-31 03:29:46.239016

Why this column exists
----------------------
`source_items.source_type` does NOT track which social platform a post lives
on. The RSS ingestion path stamps every feed-delivered item "news"/"reference"
regardless of the feed's configured type, so Twitter (via Nitter RSS),
YouTube, and Reddit-via-RSS all hide inside "news". That made social content
look like <1% of the corpus when, classified by where the post actually lives,
it is ~4.4%.

`platform` is an orthogonal, nullable tag computed by
app.services.platform_classify.derive_platform from the item URL (primary
signal) and source_name (fallback). NULL means plain news/web. Existing
source_type logic is untouched — this is additive.

Indexed because the Articles-page platform filter and admin distribution
queries group/filter by it.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2df994cdd1f9'
down_revision: Union[str, None] = '1c60888ff8bf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "source_items",
        sa.Column("platform", sa.String(length=32), nullable=True),
    )
    op.create_index(
        "ix_source_items_platform", "source_items", ["platform"]
    )


def downgrade() -> None:
    # IF EXISTS so a partial-apply state (alembic_version bumped but the
    # ADD COLUMN rolled back) can still be cleanly downgraded by hand —
    # same defensive pattern as the ingestion_health_alerts downgrade.
    op.execute("DROP INDEX IF EXISTS ix_source_items_platform")
    op.execute("ALTER TABLE source_items DROP COLUMN IF EXISTS platform")
