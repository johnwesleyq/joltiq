"""Persist procedure-bound Windows workflow identity and outcomes.

Revision ID: 0005
Revises: 0004
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("knowledge_articles", sa.Column("procedure_hash", sa.String(64), nullable=True))
    op.add_column("article_reviews", sa.Column("procedure_hash", sa.String(64), nullable=True))
    op.add_column("article_reviews", sa.Column("auth_session_id", UUID(as_uuid=True), nullable=True))
    op.add_column("article_reviews", sa.Column("mfa_passed", sa.Boolean(), server_default=sa.false(), nullable=False))
    op.create_foreign_key(
        "fk_article_reviews_auth_session", "article_reviews", "auth_sessions", ["auth_session_id"], ["id"], ondelete="SET NULL"
    )
    op.add_column("support_sessions", sa.Column("request_details", JSONB(), server_default="{}", nullable=False))
    op.add_column("agent_runs", sa.Column("outcome_stage", sa.String(40), server_default="UNCONFIRMED", nullable=False))
    op.add_column("agent_plans", sa.Column("procedure_article_id", UUID(as_uuid=True), nullable=True))
    op.add_column("agent_plans", sa.Column("procedure_version", sa.Integer(), nullable=True))
    op.add_column("agent_plans", sa.Column("procedure_hash", sa.String(64), nullable=True))
    op.create_foreign_key(
        "fk_agent_plans_procedure_article", "agent_plans", "knowledge_articles", ["procedure_article_id"], ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_agent_plans_procedure_article", "agent_plans", type_="foreignkey")
    op.drop_column("agent_plans", "procedure_hash")
    op.drop_column("agent_plans", "procedure_version")
    op.drop_column("agent_plans", "procedure_article_id")
    op.drop_column("agent_runs", "outcome_stage")
    op.drop_column("support_sessions", "request_details")
    op.drop_constraint("fk_article_reviews_auth_session", "article_reviews", type_="foreignkey")
    op.drop_column("article_reviews", "mfa_passed")
    op.drop_column("article_reviews", "auth_session_id")
    op.drop_column("article_reviews", "procedure_hash")
    op.drop_column("knowledge_articles", "procedure_hash")
