"""Allow tenant-level Slack and Teams dispatch rows without a user recipient.

Revision ID: 0013
Revises: 0012
"""
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("notification_dispatches", "user_id", nullable=True)


def downgrade() -> None:
    op.execute("DELETE FROM notification_dispatches WHERE user_id IS NULL")
    op.alter_column("notification_dispatches", "user_id", nullable=False)
