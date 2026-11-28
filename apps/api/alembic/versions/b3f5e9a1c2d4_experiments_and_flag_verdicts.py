"""experiments and flag verdicts

Revision ID: b3f5e9a1c2d4
Revises: 8a41c7e2d9b3
Create Date: 2026-11-28 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3f5e9a1c2d4'
down_revision: Union[str, Sequence[str], None] = '8a41c7e2d9b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('flags', sa.Column('flagsmith_variants', sa.JSON(), nullable=True))
    op.add_column('flag_analyses', sa.Column('activity', sa.String(length=20), nullable=True))
    op.add_column('flag_analyses', sa.Column('usefulness', sa.String(length=20), nullable=True))
    op.add_column('flag_analyses', sa.Column('verdict_confidence', sa.Float(), nullable=True))
    op.create_table('experiments',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('flag_id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('hypothesis', sa.Text(), nullable=True),
    sa.Column('primary_metric', sa.String(length=100), nullable=False),
    sa.Column('environment_name', sa.String(length=255), nullable=True),
    sa.Column('variants', sa.JSON(), nullable=False),
    sa.Column('minimum_detectable_effect', sa.Float(), nullable=False),
    sa.Column('alpha', sa.Float(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('stopped_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('concluded_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('winner', sa.String(length=100), nullable=True),
    sa.Column('conclusion_note', sa.Text(), nullable=True),
    sa.Column('ingest_key_hash', sa.String(length=64), nullable=False),
    sa.Column('created_by', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('workspace_id', sa.String(length=36), nullable=True),
    sa.ForeignKeyConstraint(['flag_id'], ['flags.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_experiments_flag_id'), 'experiments', ['flag_id'], unique=False)
    op.create_index(op.f('ix_experiments_status'), 'experiments', ['status'], unique=False)
    op.create_index(op.f('ix_experiments_workspace_id'), 'experiments', ['workspace_id'], unique=False)
    op.create_table('experiment_subjects',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('experiment_id', sa.String(length=36), nullable=False),
    sa.Column('subject_hash', sa.String(length=64), nullable=False),
    sa.Column('variant', sa.String(length=100), nullable=False),
    sa.Column('exposed_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('converted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('workspace_id', sa.String(length=36), nullable=True),
    sa.ForeignKeyConstraint(['experiment_id'], ['experiments.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('experiment_id', 'subject_hash', name='uq_experiment_subject')
    )
    op.create_index(op.f('ix_experiment_subjects_experiment_id'), 'experiment_subjects', ['experiment_id'], unique=False)
    op.create_index(op.f('ix_experiment_subjects_workspace_id'), 'experiment_subjects', ['workspace_id'], unique=False)
    # Block Supabase's public Data API (backend connects as owner, bypasses RLS).
    op.execute("ALTER TABLE experiments ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE experiment_subjects ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_experiment_subjects_workspace_id'), table_name='experiment_subjects')
    op.drop_index(op.f('ix_experiment_subjects_experiment_id'), table_name='experiment_subjects')
    op.drop_table('experiment_subjects')
    op.drop_index(op.f('ix_experiments_workspace_id'), table_name='experiments')
    op.drop_index(op.f('ix_experiments_status'), table_name='experiments')
    op.drop_index(op.f('ix_experiments_flag_id'), table_name='experiments')
    op.drop_table('experiments')
    op.drop_column('flag_analyses', 'verdict_confidence')
    op.drop_column('flag_analyses', 'usefulness')
    op.drop_column('flag_analyses', 'activity')
    op.drop_column('flags', 'flagsmith_variants')
