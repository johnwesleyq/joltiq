"""Tamper-evident verification evidence, server-computed rollback steps, browser lease epoch.

Revision ID: 0009
Revises: 0008
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("verification_runs", sa.Column("kind", sa.String(20), nullable=False, server_default="verification"))
    op.add_column("verification_runs", sa.Column("evidence_hash", sa.String(64), nullable=True))
    op.add_column("agent_plans", sa.Column("rollback_steps", JSONB, nullable=False, server_default="[]"))
    op.add_column("support_sessions", sa.Column("lease_epoch", sa.Integer, nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("support_sessions", "lease_epoch")
    op.drop_column("agent_plans", "rollback_steps")
    op.drop_column("verification_runs", "evidence_hash")
    op.drop_column("verification_runs", "kind")
