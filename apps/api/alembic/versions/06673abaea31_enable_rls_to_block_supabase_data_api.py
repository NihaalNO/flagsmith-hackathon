"""enable rls to block supabase data api

Revision ID: 06673abaea31
Revises: ae3b22aa364f
Create Date: 2026-11-27 17:48:20.963080

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '06673abaea31'
down_revision: Union[str, Sequence[str], None] = 'ae3b22aa364f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLES = ("flags", "repositories", "integrations", "audit_logs", "alembic_version")


def upgrade() -> None:
    """Enable RLS with no policies: Supabase's public REST API (anon/authenticated
    roles) gets no access. The backend connects as the table owner and bypasses RLS."""
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
