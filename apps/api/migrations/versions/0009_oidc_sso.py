"""Add organization-scoped OIDC SSO persistence.

Revision ID: 0009
Revises: 0008
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0009"
down_revision = "0008"
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


def _common() -> tuple[sa.Column, ...]:
    return (
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
    )


def upgrade() -> None:
    op.create_table(
        "oidc_providers",
        *_common(),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False, server_default="entra"),
        sa.Column("issuer", sa.String(500), nullable=False),
        sa.Column("client_id", sa.String(500), nullable=False),
        sa.Column("client_secret_enc", sa.Text(), nullable=True),
        sa.Column("scopes", sa.String(500), nullable=False, server_default="openid email profile"),
        sa.Column("default_role", sa.String(30), nullable=False, server_default="technician"),
        sa.Column("role_mapping", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("jit_provisioning", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("enforce_sso", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tested_fingerprint", sa.String(64), nullable=True),
        sa.UniqueConstraint("org_id", name="uq_oidc_provider_org"),
    )
    op.create_table(
        "oidc_provider_domains",
        *_common(),
        sa.Column(
            "provider_id",
            UUID(as_uuid=True),
            sa.ForeignKey("oidc_providers.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("domain", sa.String(253), nullable=False),
        sa.UniqueConstraint("domain", name="uq_oidc_provider_domain_global"),
    )
    op.create_table(
        "user_identities",
        *_common(),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column(
            "provider_id", UUID(as_uuid=True), sa.ForeignKey("oidc_providers.id", ondelete="CASCADE"), nullable=False, index=True
        ),
        sa.Column("issuer", sa.String(500), nullable=False),
        sa.Column("subject", sa.String(500), nullable=False),
        sa.Column("email_at_link", sa.String(320), nullable=False),
        sa.Column("linked_via", sa.String(32), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("provider_id", "subject", name="uq_user_identity_provider_subject"),
        sa.UniqueConstraint("org_id", "user_id", "provider_id", name="uq_user_identity_user_provider"),
    )
    op.create_table(
        "oidc_login_states",
        *_common(),
        sa.Column(
            "provider_id", UUID(as_uuid=True), sa.ForeignKey("oidc_providers.id", ondelete="CASCADE"), nullable=False, index=True
        ),
        sa.Column("state_hash", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("nonce", sa.String(255), nullable=False),
        sa.Column("code_verifier_enc", sa.Text(), nullable=False),
        sa.Column("return_to", sa.String(500), nullable=False, server_default="/portal"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "sso_mfa_challenges",
        *_common(),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column(
            "provider_id", UUID(as_uuid=True), sa.ForeignKey("oidc_providers.id", ondelete="CASCADE"), nullable=False, index=True
        ),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "sso_recovery_grants",
        *_common(),
        sa.Column("user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True, index=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "auth_sessions",
        sa.Column("auth_method", sa.String(30), nullable=False, server_default="password"),
    )
    op.add_column(
        "auth_sessions",
        sa.Column(
            "oidc_provider_id",
            UUID(as_uuid=True),
            sa.ForeignKey("oidc_providers.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_auth_sessions_oidc_provider_id", "auth_sessions", ["oidc_provider_id"])
    for table in (
        "oidc_providers",
        "oidc_provider_domains",
        "user_identities",
        "oidc_login_states",
        "sso_mfa_challenges",
        "sso_recovery_grants",
    ):
        _tenant_table(table)


def downgrade() -> None:
    op.drop_index("ix_auth_sessions_oidc_provider_id", table_name="auth_sessions")
    op.drop_column("auth_sessions", "oidc_provider_id")
    op.drop_column("auth_sessions", "auth_method")
    for table in (
        "sso_recovery_grants",
        "sso_mfa_challenges",
        "oidc_login_states",
        "user_identities",
        "oidc_provider_domains",
        "oidc_providers",
    ):
        op.drop_table(table)
