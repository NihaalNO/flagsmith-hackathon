"""flagsmith environment states and feature metadata

Revision ID: 8a41c7e2d9b3
Revises: 5c2e8d4a1f07
Create Date: 2026-11-27 23:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8a41c7e2d9b3'
down_revision: Union[str, Sequence[str], None] = '5c2e8d4a1f07'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('flags', sa.Column('flagsmith_created_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('flags', sa.Column('flagsmith_tags', sa.JSON(), nullable=True))
    op.add_column('flags', sa.Column('flagsmith_owner_ids', sa.JSON(), nullable=True))
    op.add_column('flags', sa.Column('flagsmith_type', sa.String(length=20), nullable=True))
    op.add_column('flags', sa.Column('is_archived_in_flagsmith', sa.Boolean(), nullable=False,
                                     server_default=sa.false()))
    op.create_table('flag_environment_states',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('flag_id', sa.String(length=36), nullable=False),
    sa.Column('flagsmith_environment_id', sa.Integer(), nullable=False),
    sa.Column('environment_name', sa.String(length=255), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('value', sa.JSON(), nullable=True),
    sa.Column('segment_overrides', sa.Integer(), nullable=False),
    sa.Column('identity_overrides', sa.Integer(), nullable=True),
    sa.Column('percentage_allocation', sa.Float(), nullable=True),
    sa.Column('last_modified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('synced_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=True),
    sa.ForeignKeyConstraint(['flag_id'], ['flags.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('flag_id', 'flagsmith_environment_id', name='uq_flag_env_state')
    )
    op.create_index(op.f('ix_flag_environment_states_flag_id'), 'flag_environment_states', ['flag_id'], unique=False)
    op.create_index(op.f('ix_flag_environment_states_workspace_id'), 'flag_environment_states', ['workspace_id'], unique=False)
    # Block Supabase's public Data API (backend connects as owner, bypasses RLS).
    op.execute("ALTER TABLE flag_environment_states ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_flag_environment_states_workspace_id'), table_name='flag_environment_states')
    op.drop_index(op.f('ix_flag_environment_states_flag_id'), table_name='flag_environment_states')
    op.drop_table('flag_environment_states')
    op.drop_column('flags', 'is_archived_in_flagsmith')
    op.drop_column('flags', 'flagsmith_type')
    op.drop_column('flags', 'flagsmith_owner_ids')
    op.drop_column('flags', 'flagsmith_tags')
    op.drop_column('flags', 'flagsmith_created_at')
