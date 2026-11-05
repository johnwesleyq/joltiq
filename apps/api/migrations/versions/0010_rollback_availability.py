"""Up-front rollback availability for plans and computer-use runs (ADR 0019).

Revision ID: 0010
Revises: 0009
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("agent_plans", sa.Column("rollback_availability", JSONB, nullable=False, server_default="{}"))
    op.add_column("computer_use_runs", sa.Column("rollback", JSONB, nullable=False, server_default="{}"))


def downgrade() -> None:
    op.drop_column("computer_use_runs", "rollback")
    op.drop_column("agent_plans", "rollback_availability")
