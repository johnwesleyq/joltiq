"""Tenant-scoped feedback on cited assistant answers.

Revision ID: 0014
Revises: 0013
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

CTX = "nullif(current_setting('app.org_id', true), '')::uuid"
PLATFORM = "coalesce(current_setting('app.platform_admin', true), 'off') = 'on'"


def upgrade() -> None:
    op.create_table(
        "knowledge_answer_feedback",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("org_id", UUID(as_uuid=True), sa.ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("message_id", UUID(as_uuid=True), sa.ForeignKey("agent_messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("helpful", sa.Boolean(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False, server_default=""),
        sa.UniqueConstraint("org_id", "message_id", "user_id", name="uq_knowledge_answer_feedback_user_message"),
    )
    op.create_index("ix_knowledge_answer_feedback_org_id", "knowledge_answer_feedback", ["org_id"])
    op.create_index("ix_knowledge_answer_feedback_run_id", "knowledge_answer_feedback", ["run_id"])
    op.create_index("ix_knowledge_answer_feedback_message_id", "knowledge_answer_feedback", ["message_id"])
    op.create_index("ix_knowledge_answer_feedback_user_id", "knowledge_answer_feedback", ["user_id"])
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON knowledge_answer_feedback TO joltiq_app")
    op.execute("ALTER TABLE knowledge_answer_feedback ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE knowledge_answer_feedback FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON knowledge_answer_feedback "
        f"USING (org_id = {CTX} OR {PLATFORM}) WITH CHECK (org_id = {CTX} OR {PLATFORM})"
    )


def downgrade() -> None:
    op.drop_table("knowledge_answer_feedback")
