"""Idempotency records for POST endpoints.

Revision ID: 0003
Revises: 0002
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "create table idempotency_records (key text primary key, response jsonb not null, "
        "created_at timestamptz not null default now())"
    )
    op.execute("create index ix_idempotency_created on idempotency_records (created_at)")
    op.execute("grant select, insert, delete on idempotency_records to joltiq_app")


def downgrade() -> None:
    op.execute("drop table idempotency_records")
