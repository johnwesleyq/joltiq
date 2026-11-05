"""Unsupervised Fix removed by request (D-023): drop its org settings keys.

Revision ID: 0013
Revises: 0012
"""
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE organizations SET settings = settings - 'unsupervised_browser_fix' - 'unsupervised_device_fix' "
               "WHERE settings ?| array['unsupervised_browser_fix','unsupervised_device_fix']")


def downgrade() -> None:
    pass  # the feature is gone; nothing to restore
