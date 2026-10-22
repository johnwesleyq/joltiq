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

    # Workers
    browser_worker_url: str = "http://localhost:8100"
    browser_worker_token: str = "dev-browser-worker-token-change-me"  # noqa: S105 - dev default

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
    # When true, a deterministic local model fixture is registered. Never enabled in production.
    enable_local_fixture_providers: bool = True

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
