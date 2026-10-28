"""Central configuration. All values come from environment (see /.env.example)."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JOLTIQ_", env_file=".env", extra="ignore")

    # Branding: product name is config, never hard-coded in UI or emails.
    product_name: str = "JoltIQ"
    product_domain: str = "joltiq.io"
    support_email: str = "support@joltiq.io"

    env: str = "development"  # development | test | production
    # App connects as a NON-superuser role so Postgres RLS is enforced.
    database_url: str = "postgresql+psycopg://joltiq_app:joltiq_app@localhost:5432/joltiq"
    # Migrations run as the owner role.
    migration_database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/joltiq"
    redis_url: str = "redis://localhost:6379/0"

    # 32+ byte secrets. Defaults are for local dev only and refused in production.
    secret_key: str = "dev-only-change-me-dev-only-change-me-0000"  # noqa: S105 - dev default, refused in production
    encryption_key: str = ""  # Fernet key for integration credentials; derived from secret_key in dev
    approval_signing_key: str = "dev-only-approval-key-change-me-0000000000"

    session_ttl_minutes: int = 60 * 12
    session_idle_minutes: int = 120
    cookie_secure: bool = False
    cookie_domain: str | None = None
    web_origin: str = "http://localhost:3000"
    api_public_url: str = "http://localhost:8000"
    oidc_allowed_issuer_hosts: list[str] = Field(default_factory=lambda: ["login.microsoftonline.com"])
    oidc_http_timeout_seconds: float = 10.0

    # Workers
    browser_worker_url: str = "http://localhost:8100"
    browser_worker_token: str = "dev-browser-worker-token-change-me"  # noqa: S105 - dev default

    # Optional deployment-owned MeshCentral endpoint for technician remote desktop.
    # This is never accepted from tenant input, preventing remote-access URL/SSRF injection.
    meshcentral_url: str | None = None

    # Email
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_starttls: bool = False
    email_from: str = "no-reply@joltiq.io"

    # AI providers (keys may also be configured per tenant in admin UI, encrypted)
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    gemini_api_key: str | None = None
    # Customer-controlled local model gateway. Deployment-owned so tenants cannot turn it into an SSRF target.
    ollama_base_url: str | None = None
    ollama_model: str = "gemma3:4b"
    # When true, a deterministic local model fixture is registered. Never enabled in production.
    enable_local_fixture_providers: bool = True
    # Deployment-level opt-in for sending computer screenshots/task context to a tenant-approved external model.
    # Tenant provider enablement + approved_for_tenant_data are independently required.
    allow_external_computer_use_models: bool = False

    # Research providers
    brave_search_api_key: str | None = None
    tavily_api_key: str | None = None

    # Stripe
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    stripe_live_mode_authorized: bool = False

    # Storage (S3-compatible). Local filesystem fallback for dev.
    s3_endpoint: str | None = None
    s3_bucket: str = "joltiq"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    local_storage_dir: str = "./.storage"
    max_upload_mb: int = 25

    rate_limit_login_per_minute: int = 10
    rate_limit_register_per_hour: int = 10
    rate_limit_device_enroll_per_hour: int = 20
    otel_exporter_endpoint: str | None = None

    allowed_research_domains: list[str] = Field(
        default_factory=lambda: [
            "learn.microsoft.com", "support.microsoft.com", "answers.microsoft.com",
            "techcommunity.microsoft.com", "quickbooks.intuit.com", "helpx.adobe.com",
            "community.adobe.com", "github.com", "stackoverflow.com", "superuser.com",
            "serverfault.com", "support.google.com", "docs.aws.amazon.com",
            # Broad IT coverage: cloud, networking, OS, dev tooling
            "aws.amazon.com", "repost.aws", "cloud.google.com", "azure.microsoft.com", "support.apple.com",
            "developer.apple.com", "cisco.com", "community.cisco.com", "docs.fortinet.com", "kb.netgear.com",
            "ubiquiti.com", "help.ui.com", "docs.docker.com", "kubernetes.io", "docs.python.org",
            "developer.mozilla.org", "nodejs.org", "git-scm.com", "docs.github.com", "ubuntu.com", "help.ubuntu.com",
            "askubuntu.com", "access.redhat.com", "wiki.archlinux.org", "unix.stackexchange.com",
            "support.zoom.com", "help.dropbox.com", "support.hp.com", "dell.com", "support.lenovo.com",
            "cloudflare.com", "developers.cloudflare.com", "nist.gov", "cisa.gov",
        ]
    )

    def assert_production_safe(self) -> None:
        if self.env == "production":
            bad = [
                n for n in ("secret_key", "approval_signing_key")
                if getattr(self, n).startswith("dev-only")
            ]
            if bad or not self.encryption_key:
                raise RuntimeError(f"Refusing to start in production with dev secrets: {bad or ['encryption_key']}")
            if self.enable_local_fixture_providers:
                raise RuntimeError("Local fixture providers must be disabled in production")
            if not self.cookie_secure:
                raise RuntimeError("cookie_secure must be true in production")


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.assert_production_safe()
    return s
