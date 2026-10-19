"""Computer-use runs and steps (screen-driving agent).

Revision ID: 0004
Revises: 0003
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

CTX = "nullif(current_setting('app.org_id', true), '')::uuid"
PLATFORM = "coalesce(current_setting('app.platform_admin', true), 'off') = 'on'"


def _base():
    return [
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True),
    ]


def upgrade() -> None:
    op.create_table(
        "computer_use_runs", *_base(),
        sa.Column("session_id", UUID(as_uuid=True), sa.ForeignKey("support_sessions.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("ticket_id", UUID(as_uuid=True), sa.ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True),
        sa.Column("started_by", UUID(as_uuid=True), nullable=True),
        sa.Column("goal", sa.Text, nullable=False),
        sa.Column("state", sa.String(30), nullable=False, index=True),
        sa.Column("waiting_for", JSONB, nullable=True),
        sa.Column("provider", sa.String(40), nullable=True),
        sa.Column("model", sa.String(120), nullable=True),
        sa.Column("step_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_steps", sa.Integer, nullable=False, server_default="60"),
        sa.Column("transcript", JSONB, nullable=False, server_default="[]"),
        sa.Column("pending_command_id", UUID(as_uuid=True), nullable=True),
        sa.Column("queue", JSONB, nullable=False, server_default="[]"),
        sa.Column("results", JSONB, nullable=False, server_default="[]"),
        sa.Column("last_screenshot_key", sa.String(300), nullable=True),
        sa.Column("display", JSONB, nullable=True),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("success", sa.Boolean, nullable=True),
        sa.Column("cost_usd", sa.Float, nullable=False, server_default="0"),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "computer_use_steps", *_base(),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("computer_use_runs.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("idx", sa.Integer, nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("params", JSONB, nullable=False, server_default="{}"),
        sa.Column("narration", sa.Text, nullable=False, server_default=""),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("error", sa.Text, nullable=True),
        sa.Column("command_id", UUID(as_uuid=True), nullable=True),
        sa.Column("screenshot_key", sa.String(300), nullable=True),
        sa.UniqueConstraint("run_id", "idx"),
    )
    for t in ("computer_use_runs", "computer_use_steps"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {t} TO joltiq_app")
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(f"CREATE POLICY tenant_isolation ON {t} USING (org_id = {CTX} OR {PLATFORM}) WITH CHECK (org_id = {CTX} OR {PLATFORM})")


def downgrade() -> None:
    op.drop_table("computer_use_steps")
    op.drop_table("computer_use_runs")
