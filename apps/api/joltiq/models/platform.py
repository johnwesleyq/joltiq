"""Integrations, notifications, billing/usage, audit and security events."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import IdMixin, TenantMixin, TimestampMixin


class Credential(IdMixin, TimestampMixin, TenantMixin, Base):
    """Encrypted secret (Fernet). Plaintext never returned by API; only a fingerprint."""

    __tablename__ = "credentials"
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(40))  # api_key|basic|oauth|webhook_secret
    ciphertext: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(16))
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Integration(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "integrations"
    __table_args__ = (UniqueConstraint("org_id", "kind", "name"),)
    kind: Mapped[str] = mapped_column(String(40))  # freshdesk|generic_webhook|generic_rest|smtp|oidc|...
    name: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), default="disconnected")  # disconnected|connected|error|revoked
    config: Mapped[dict] = mapped_column(JSONB, default=dict)  # non-secret config (domain, scopes, mapping)
    credential_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("credentials.id", ondelete="SET NULL"), nullable=True)
    scopes: Mapped[list] = mapped_column(JSONB, default=list)
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sync_cursor: Mapped[str | None] = mapped_column(String(200), nullable=True)


class SyncJob(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "sync_jobs"
    __table_args__ = (UniqueConstraint("org_id", "idempotency_key"),)
    integration_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("integrations.id", ondelete="CASCADE"), index=True)
    direction: Mapped[str] = mapped_column(String(10))  # push|pull
    entity: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|ok|failed|conflict|dead
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    idempotency_key: Mapped[str] = mapped_column(String(150))
    request: Mapped[dict] = mapped_column(JSONB, default=dict)
    response: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    conflict: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class Notification(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "notifications"
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(40))  # ticket|approval|session|escalation|resolution|device|security|integration|usage
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str | None] = mapped_column(String(500), nullable=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NotificationPreference(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "notification_preferences"
    __table_args__ = (UniqueConstraint("org_id", "user_id", "category"),)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    category: Mapped[str] = mapped_column(String(40))
    in_app: Mapped[bool] = mapped_column(Boolean, default=True)
    email: Mapped[bool] = mapped_column(Boolean, default=True)


class WebhookEndpoint(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "webhook_endpoints"
    url: Mapped[str] = mapped_column(String(2000))
    categories: Mapped[list] = mapped_column(JSONB, default=list)
    credential_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("credentials.id", ondelete="SET NULL"), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Delivery(IdMixin, TimestampMixin, TenantMixin, Base):
    """Outbound email/webhook deliveries with retry state."""

    __tablename__ = "deliveries"
    channel: Mapped[str] = mapped_column(String(20))  # email|webhook
    target: Mapped[str] = mapped_column(String(2000))
    subject: Mapped[str] = mapped_column(String(300), default="")
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Subscription(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "subscriptions"
    __table_args__ = (UniqueConstraint("org_id"),)
    plan: Mapped[str] = mapped_column(String(40), default="trial")
    interval: Mapped[str] = mapped_column(String(10), default="month")
    status: Mapped[str] = mapped_column(String(30), default="trialing")
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False)
    stripe_customer_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(100), nullable=True, unique=True)
    live_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    latest_invoice_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)


class UsageEvent(IdMixin, TimestampMixin, TenantMixin, Base):
    """Metered usage. idempotency_key makes double-recording impossible (no duplicate charges)."""

    __tablename__ = "usage_events"
    __table_args__ = (UniqueConstraint("org_id", "idempotency_key"),)
    meter: Mapped[str] = mapped_column(String(40), index=True)
    quantity: Mapped[float] = mapped_column(Float)
    idempotency_key: Mapped[str] = mapped_column(String(150))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    reported_to_stripe_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BillingEvent(IdMixin, TimestampMixin, Base):
    """Stripe webhook events (global; idempotent by stripe event id)."""

    __tablename__ = "billing_events"
    stripe_event_id: Mapped[str] = mapped_column(String(100), unique=True)
    type: Mapped[str] = mapped_column(String(100))
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)


class AuditEvent(Base):
    """Tamper-evident audit log: each row hashes the previous row of the same org (hash chain).

    DB permissions: the app role has INSERT/SELECT only (no UPDATE/DELETE) on this table.
    """

    __tablename__ = "audit_events"
    __tenant__ = True
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actor_kind: Mapped[str] = mapped_column(String(20))  # user|ai|system|device|integration
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(100), index=True)
    resource_type: Mapped[str] = mapped_column(String(50))
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    data: Mapped[dict] = mapped_column(JSONB, default=dict)
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


class SecurityEvent(IdMixin, TimestampMixin, Base):
    __tablename__ = "security_events"
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    kind: Mapped[str] = mapped_column(String(60), index=True)  # login_failed|lockout|cross_tenant_denied|replay|injection|ssrf_blocked...
    severity: Mapped[str] = mapped_column(String(10), default="info")
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    data: Mapped[dict] = mapped_column(JSONB, default=dict)


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"
    worker_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))
    last_beat: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    info: Mapped[dict] = mapped_column(JSONB, default=dict)
