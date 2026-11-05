"""Approver notification dispatch log: dedupe + rate limiting for approval notifications (ADR 0020).

Revision ID: 0011
Revises: 0010
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

CTX = "nullif(current_setting('app.org_id', true), '')::uuid"
PLATFORM = "coalesce(current_setting('app.platform_admin', true), 'off') = 'on'"


def upgrade() -> None:
    op.create_table(
        "notification_dispatches",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("org_id", sa.UUID(), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("dedupe_key", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("approval_id", sa.UUID(), nullable=True),
        sa.Column("channels", JSONB, nullable=False, server_default="[]"),
        sa.Column("suppressed", JSONB, nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("org_id", "dedupe_key"),
    )
    op.create_index("ix_notification_dispatches_user_created", "notification_dispatches", ["user_id", "created_at"])
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON notification_dispatches TO joltiq_app")
    op.execute("ALTER TABLE notification_dispatches ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE notification_dispatches FORCE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation ON notification_dispatches USING (org_id = {CTX} OR {PLATFORM}) "
               f"WITH CHECK (org_id = {CTX} OR {PLATFORM})")


def downgrade() -> None:
    op.drop_table("notification_dispatches")
