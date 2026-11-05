"""Stable companion installation identity for safe re-enrollment.

Revision ID: 0015
Revises: 0014
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("devices", sa.Column("installation_id", UUID(as_uuid=True), nullable=True))
    op.create_index(
        "uq_devices_active_installation",
        "devices",
        ["org_id", "installation_id"],
        unique=True,
        postgresql_where=sa.text("installation_id IS NOT NULL AND status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index("uq_devices_active_installation", table_name="devices")
    op.drop_column("devices", "installation_id")
