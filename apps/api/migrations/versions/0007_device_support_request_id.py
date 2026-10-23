"""Add device-scoped direct-help request idempotency.

Revision ID: 0007
Revises: 0006
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

CTX = "nullif(current_setting('app.org_id', true), '')::uuid"
PLATFORM = "coalesce(current_setting('app.platform_admin', true), 'off') = 'on'"


def upgrade() -> None:
    op.create_table(
        "device_support_requests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "org_id",
            UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "device_id",
            UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("client_request_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "ticket_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tickets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("response", JSONB, nullable=False),
        sa.UniqueConstraint("device_id", "client_request_id"),
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON device_support_requests TO joltiq_app")
    op.execute("ALTER TABLE device_support_requests ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE device_support_requests FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON device_support_requests "
        f"USING (org_id = {CTX} OR {PLATFORM}) WITH CHECK (org_id = {CTX} OR {PLATFORM})"
    )


def downgrade() -> None:
    op.drop_table("device_support_requests")
