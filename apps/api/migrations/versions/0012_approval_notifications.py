"""Add tenant-scoped approval notification dispatch records.

Revision ID: 0012
Revises: 0011
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

CTX = "nullif(current_setting('app.org_id', true), '')::uuid"
PLATFORM = "coalesce(current_setting('app.platform_admin', true), 'off') = 'on'"


def upgrade() -> None:
    op.create_table(
        "notification_dispatches",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dedupe_key", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("approval_id", UUID(as_uuid=True), sa.ForeignKey("approvals.id", ondelete="SET NULL"), nullable=True),
        sa.Column("channels", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("suppressed", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.UniqueConstraint("org_id", "dedupe_key", name="uq_notification_dispatch_org_dedupe"),
    )
    op.create_index("ix_notification_dispatches_org_id", "notification_dispatches", ["org_id"])
    op.create_index("ix_notification_dispatches_user_created", "notification_dispatches", ["user_id", "created_at"])
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON notification_dispatches TO joltiq_app")
    op.execute("ALTER TABLE notification_dispatches ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE notification_dispatches FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON notification_dispatches "
        f"USING (org_id = {CTX} OR {PLATFORM}) WITH CHECK (org_id = {CTX} OR {PLATFORM})"
    )


def downgrade() -> None:
    op.drop_table("notification_dispatches")
