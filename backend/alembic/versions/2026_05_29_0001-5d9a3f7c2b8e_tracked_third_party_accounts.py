"""create tracked_third_party_accounts table

Revision ID: 5d9a3f7c2b8e
Revises: 4c8f2e1b9a3d
Create Date: 2026-05-29 00:01:00.000000

Stores accounts/pages that are NOT the candidate's or opponent's own — local
news on FB, county committees, PACs, statewide subreddits, journalists
covering the race, etc. — that the user confirmed during Phase 2 setup
discovery. Distinct from the JSON-list handle columns on campaign_config /
opponents (which are the candidate's and opponent's own accounts).

Each row preserves the discovery context (snippet, inferred role) so the
user can remember WHY they added something, and stores the canonical
RSS URL for ingestable platforms so monitor generation is cheap.

A (platform, identifier) unique constraint prevents duplicate rows when
the discovery flow re-surfaces an already-tracked account.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "5d9a3f7c2b8e"
# Chained after a sibling migration (race-sentiment suspect flag) to keep
# a single linear head — both diverged from 4c8f2e1b9a3d.
down_revision: Union[str, None] = "5d9e3f2a8b1c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tracked_third_party_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        # Platform identifiers match the discovery service's sub_platform vocab:
        # instagram | facebook | bluesky | reddit_subreddit | reddit_user | youtube
        sa.Column("platform", sa.String(), nullable=False),
        # Bare identifier — handle / page slug / subreddit name / @handle / UCxxxx
        sa.Column("identifier", sa.String(), nullable=False),
        # Optional human label (often the page title from the discovery result)
        sa.Column("display_name", sa.String(), nullable=True),
        # Canonical URL we'd link to in the UI
        sa.Column("url", sa.String(), nullable=False),
        # Discovery output preserved for context — UI tooltip + audit trail
        sa.Column("inferred_role", sa.String(), nullable=True),
        sa.Column("snippet", sa.Text(), nullable=True),
        # RSS URL for ingestable platforms (Bluesky/Reddit/YouTube-with-id).
        # NULL for IG/FB and YouTube-needing-channel-id-lookup — those are
        # gated by SOCIAL_HANDLE_MONITORS_ENABLED or need an extra step.
        sa.Column("rss_url", sa.String(), nullable=True),
        # User-editable free text — "added because they cover Bresnahan", etc.
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("added_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("platform", "identifier", name="uq_tracked_account"),
    )


def downgrade() -> None:
    op.drop_table("tracked_third_party_accounts")
