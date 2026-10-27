"""Add tenant-scoped remote access bindings and sessions.

Revision ID: 0008
Revises: 0007
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

CTX = "nullif(current_setting('app.org_id', true), '')::uuid"
PLATFORM = "coalesce(current_setting('app.platform_admin', true), 'off') = 'on'"


def _tenant_table(name: str) -> None:
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {name} TO joltiq_app")
    op.execute(f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON {name} "
        f"USING (org_id = {CTX} OR {PLATFORM}) WITH CHECK (org_id = {CTX} OR {PLATFORM})"
    )


def upgrade() -> None:
    common = (
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True),
    )
    op.create_table(
        "remote_access_bindings",
        *common,
        sa.Column("device_id", UUID(as_uuid=True), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("provider", sa.String(30), nullable=False, server_default="meshcentral"),
        sa.Column("node_id", sa.String(300), nullable=False),
        sa.Column("created_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.UniqueConstraint("device_id"),
        sa.UniqueConstraint("org_id", "provider", "node_id"),
    )
    op.create_table(
        "remote_access_sessions",
        *common,
        sa.Column("device_id", UUID(as_uuid=True), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("binding_id", UUID(as_uuid=True), sa.ForeignKey("remote_access_bindings.id", ondelete="RESTRICT"), nullable=False, index=True),
        sa.Column("operator_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("state", sa.String(20), nullable=False, server_default="active", index=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("state IN ('active', 'ended')", name="ck_remote_access_session_state"),
    )
    _tenant_table("remote_access_bindings")
    _tenant_table("remote_access_sessions")


def downgrade() -> None:
    op.drop_table("remote_access_sessions")
    op.drop_table("remote_access_bindings")
