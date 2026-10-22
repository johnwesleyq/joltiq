"""Add immutable Guide/Fix mode and per-run native consent state.

Revision ID: 0006
Revises: 0005
"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "computer_use_runs",
        sa.Column("mode", sa.String(10), server_default="fix", nullable=False),
    )
    op.add_column(
        "computer_use_runs",
        sa.Column("local_consent_state", sa.String(10), server_default="pending", nullable=False),
    )
    op.create_check_constraint("ck_computer_use_runs_mode", "computer_use_runs", "mode IN ('guide', 'fix')")
    op.create_check_constraint(
        "ck_computer_use_runs_local_consent_state",
        "computer_use_runs",
        "local_consent_state IN ('pending', 'approved', 'declined')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_computer_use_runs_local_consent_state", "computer_use_runs", type_="check")
    op.drop_constraint("ck_computer_use_runs_mode", "computer_use_runs", type_="check")
    op.drop_column("computer_use_runs", "local_consent_state")
    op.drop_column("computer_use_runs", "mode")
