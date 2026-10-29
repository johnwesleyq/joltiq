"""Add up-front rollback availability for plans and computer-use runs.

Revision ID: 0011
Revises: 0010
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_plans", sa.Column("rollback_availability", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")))
    op.add_column("computer_use_runs", sa.Column("rollback", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")))


def downgrade() -> None:
    op.drop_column("computer_use_runs", "rollback")
    op.drop_column("agent_plans", "rollback_availability")
