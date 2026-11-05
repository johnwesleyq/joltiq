"""Central configuration. All values come from environment (see /.env.example)."""
from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

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
    slack_mock_base_url: str = ""  # DEV/MOCK Slack (ADR 0021); ignored in production
    teams_mock_base_url: str = ""  # DEV/MOCK Teams (ADR 0022); ignored in production
    api_public_url: str = "http://localhost:8000"

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
    # OpenAI-compatible endpoint (default: OpenAI). Deployment-owned, never tenant input. Tests/demos point it at a local mock.
    openai_base_url: str = "https://api.openai.com/v1"
    # DEV ONLY: if JOLTIQ_OPENAI_API_KEY is set, auto-enable + approve OpenAI for the first organization so Ask works
    # without visiting Admin → Models. Ignored in production.
    dev_bootstrap_openai_from_env: bool = True
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
    # Production cloud computer-use models are deployment-owned and priced. A tenant may select only from this list.
    computer_use_cloud_models: list[str] = Field(default_factory=lambda: ["gpt-6.1-terra", "gpt-6.1-sol"])

    # Research providers
    brave_search_api_key: str | None = None
    tavily_api_key: str | None = None

    # Stripe
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    stripe_live_mode_authorized: bool = False

    # Storage. Production must use S3; local is an explicit development/test backend.
    storage_backend: Literal["local", "s3"] = "local"
    s3_endpoint: str | None = None
    s3_bucket: str = "joltiq"
    s3_region: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_kms_key_id: str | None = None
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
        if self.env != "production":
            return

        problems: list[str] = []
        for name in ("secret_key", "approval_signing_key"):
            value = getattr(self, name)
            if value.startswith("dev-only") or len(value) < 32:
                problems.append(f"{name} must be a non-development secret of at least 32 characters")
        if len(self.encryption_key) < 32:
            problems.append("encryption_key must be configured")
        if self.enable_local_fixture_providers:
            problems.append("enable_local_fixture_providers must be false")
        if not self.cookie_secure:
            problems.append("cookie_secure must be true")

        for name in ("web_origin", "api_public_url"):
            parsed = urlsplit(getattr(self, name))
            if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
                problems.append(f"{name} must be an HTTPS origin without embedded credentials")

        local_hosts = {"localhost", "127.0.0.1", "::1", "host.docker.internal", "postgres", "redis"}
        for name in ("database_url", "migration_database_url"):
            parsed = urlsplit(getattr(self, name))
            if not parsed.hostname or parsed.hostname.lower() in local_hosts:
                problems.append(f"{name} must point to a non-local managed PostgreSQL service")
        redis = urlsplit(self.redis_url)
        if redis.scheme != "rediss" or not redis.hostname or redis.hostname.lower() in local_hosts:
            problems.append("redis_url must use TLS (rediss://) and a non-local managed Redis service")

        if len(self.browser_worker_token) < 32 or self.browser_worker_token.startswith("dev-"):
            problems.append("browser_worker_token must be a random secret of at least 32 characters")
        if not self.openai_api_key:
            problems.append("openai_api_key is required for centrally managed JoltIQ AI")
        openai_url = urlsplit(self.openai_base_url)
        if openai_url.scheme != "https" or not openai_url.hostname:
            problems.append("openai_base_url must use HTTPS")
        if self.allow_external_computer_use_models:
            if openai_url.hostname.lower() != "api.openai.com" or openai_url.port not in (None, 443) or openai_url.path.rstrip("/") != "/v1":
                problems.append("openai_base_url must be the official https://api.openai.com/v1 origin for production computer use")
            if not self.computer_use_cloud_models:
                problems.append("computer_use_cloud_models must contain at least one priced production model")

        if self.storage_backend != "s3":
            problems.append("storage_backend must be s3")
        if not self.s3_bucket.strip():
            problems.append("s3_bucket must be configured")
        if bool(self.s3_access_key) != bool(self.s3_secret_key):
            problems.append("s3_access_key and s3_secret_key must be configured together")
        if self.s3_endpoint:
            endpoint = urlsplit(self.s3_endpoint)
            if endpoint.scheme != "https" or not endpoint.hostname:
                problems.append("s3_endpoint must use HTTPS in production")

        if problems:
            raise RuntimeError("Unsafe production configuration: " + "; ".join(problems))


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.assert_production_safe()
    return s
