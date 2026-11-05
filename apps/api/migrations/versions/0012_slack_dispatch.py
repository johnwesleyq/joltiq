"""Slack approval alerts: dispatch rows without a user (ADR 0021).

Revision ID: 0012
Revises: 0011
"""
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("notification_dispatches", "user_id", nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM notification_dispatches WHERE user_id IS NULL")
    op.alter_column("notification_dispatches", "user_id", nullable=False)
