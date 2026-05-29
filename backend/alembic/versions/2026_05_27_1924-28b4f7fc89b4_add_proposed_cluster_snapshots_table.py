"""add proposed_cluster_snapshots table

Revision ID: 28b4f7fc89b4
Revises: bfbb065b4b7e
Create Date: 2026-05-27 19:24:40.095059

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '28b4f7fc89b4'
down_revision: Union[str, None] = 'bfbb065b4b7e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'proposed_cluster_snapshots',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('cluster_fingerprint', sa.String(), nullable=False),
        sa.Column('cluster_id', sa.Integer(), nullable=False),
        sa.Column('representative_name', sa.String(), nullable=False),
        sa.Column('size', sa.Integer(), nullable=False),
        sa.Column('outlet_count', sa.Integer(), nullable=False),
        sa.Column('outlet_names_json', sa.Text(), nullable=False),
        sa.Column('outlet_tier_counts_json', sa.Text(), nullable=False),
        sa.Column('owner_type_hint', sa.String(), nullable=False),
        sa.Column('subject_type_hint', sa.String(), nullable=True),
        sa.Column('member_candidate_frame_ids_json', sa.Text(), nullable=False),
        sa.Column('points_json', sa.Text(), nullable=False),
        sa.Column('x', sa.Float(), nullable=True),
        sa.Column('y', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('refreshed_at', sa.DateTime(), nullable=False),
        sa.Column('dismissed_at', sa.DateTime(), nullable=True),
        sa.Column('applied_at', sa.DateTime(), nullable=True),
        sa.Column('applied_to_frame_id', sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('proposed_cluster_snapshots', schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f('ix_proposed_cluster_snapshots_cluster_fingerprint'),
            ['cluster_fingerprint'], unique=True,
        )
        batch_op.create_index(
            batch_op.f('ix_proposed_cluster_snapshots_applied_to_frame_id'),
            ['applied_to_frame_id'], unique=False,
        )


def downgrade() -> None:
    with op.batch_alter_table('proposed_cluster_snapshots', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_proposed_cluster_snapshots_applied_to_frame_id'))
        batch_op.drop_index(batch_op.f('ix_proposed_cluster_snapshots_cluster_fingerprint'))
    op.drop_table('proposed_cluster_snapshots')
