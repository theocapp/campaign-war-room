"""create search_result_cache table

Revision ID: 6e2b8c4a9d1f
Revises: 5d9a3f7c2b8e
Create Date: 2026-05-29 02:00:00.000000

Disk-backed cache for `search_provider.SearchProvider.search()` calls.
Cuts dev-iteration burn on the Tavily free tier to near-zero and reduces
per-campaign quota usage during setup by ~80%, since users re-click
Discover repeatedly with the same query.

Cache key: `(provider_name, query, limit)` — limit matters because the
same query at limit=4 and limit=8 should be treated as different cache
entries (truncating limit=8 to 4 would silently drop results when
callers re-query at the higher limit).

TTL: 7 days by default (configurable via `SEARCH_CACHE_TTL_DAYS`).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "6e2b8c4a9d1f"
down_revision: Union[str, None] = "5d9a3f7c2b8e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "search_result_cache",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("limit_n", sa.Integer(), nullable=False),
        sa.Column("cached_at", sa.DateTime(), nullable=False),
        # JSON-encoded list of SearchResult records (title/url/source_name/snippet/published_at)
        sa.Column("results_json", sa.Text(), nullable=False),
        # Optional message from the inner provider (e.g. "all keys exhausted")
        sa.Column("message", sa.Text(), nullable=True),
        sa.UniqueConstraint("provider", "query", "limit_n", name="uq_search_cache_key"),
    )
    # Index on cached_at makes TTL sweeps cheap.
    op.create_index(
        "ix_search_cache_cached_at",
        "search_result_cache",
        ["cached_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_search_cache_cached_at", table_name="search_result_cache")
    op.drop_table("search_result_cache")
