"""Separate tenant approval for computer screenshots.

Revision ID: 0016
Revises: 0015
"""
import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "model_provider_configs",
        sa.Column("approved_for_computer_use", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("model_provider_configs", "approved_for_computer_use", server_default=None)


def downgrade() -> None:
    op.drop_column("model_provider_configs", "approved_for_computer_use")
