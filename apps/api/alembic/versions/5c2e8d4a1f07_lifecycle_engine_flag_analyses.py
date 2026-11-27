"""lifecycle engine: flag analyses

Revision ID: 5c2e8d4a1f07
Revises: 70b4db92fde1
Create Date: 2026-11-27 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5c2e8d4a1f07'
down_revision: Union[str, Sequence[str], None] = '70b4db92fde1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('flag_analyses',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('flag_id', sa.String(length=36), nullable=False),
    sa.Column('engine_version', sa.String(length=20), nullable=False),
    sa.Column('stale_status', sa.String(length=40), nullable=False),
    sa.Column('stale_confidence', sa.Float(), nullable=False),
    sa.Column('cleanup_direction', sa.String(length=40), nullable=False),
    sa.Column('suggested_state', sa.String(length=50), nullable=False),
    sa.Column('recommended_action', sa.String(length=50), nullable=False),
    sa.Column('confirmed_references', sa.Integer(), nullable=False),
    sa.Column('potential_references', sa.Integer(), nullable=False),
    sa.Column('dynamic_references', sa.Integer(), nullable=False),
    sa.Column('llm_status', sa.String(length=40), nullable=False),
    sa.Column('result', sa.JSON(), nullable=False),
    sa.Column('created_by', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=True),
    sa.ForeignKeyConstraint(['flag_id'], ['flags.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_flag_analyses_flag_id'), 'flag_analyses', ['flag_id'], unique=False)
    op.create_index(op.f('ix_flag_analyses_stale_status'), 'flag_analyses', ['stale_status'], unique=False)
    op.create_index(op.f('ix_flag_analyses_created_at'), 'flag_analyses', ['created_at'], unique=False)
    op.create_index(op.f('ix_flag_analyses_workspace_id'), 'flag_analyses', ['workspace_id'], unique=False)
    # Block Supabase's public Data API (backend connects as owner, bypasses RLS).
    op.execute("ALTER TABLE flag_analyses ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_flag_analyses_workspace_id'), table_name='flag_analyses')
    op.drop_index(op.f('ix_flag_analyses_created_at'), table_name='flag_analyses')
    op.drop_index(op.f('ix_flag_analyses_stale_status'), table_name='flag_analyses')
    op.drop_index(op.f('ix_flag_analyses_flag_id'), table_name='flag_analyses')
    op.drop_table('flag_analyses')
