"""Devices, live sessions, agent runs, plans, policy, approvals, execution, verification, jobs."""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base
from .base import IdMixin, TenantMixin, TimestampMixin


# ---------------- Devices ----------------
class Device(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "devices"
    customer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("customers.id", ondelete="SET NULL"), nullable=True)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    hostname: Mapped[str] = mapped_column(String(255))
    os_name: Mapped[str] = mapped_column(String(120), default="")
    os_version: Mapped[str] = mapped_column(String(120), default="")
    agent_version: Mapped[str] = mapped_column(String(40), default="")
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    # Device-bound public key (ECDSA P-256, SPKI PEM). Private key lives in Windows DPAPI/CNG on device.
    public_key_pem: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_counter: Mapped[int] = mapped_column(Integer, default=0)  # monotonic message counter from device
    capabilities: Mapped[list] = mapped_column(JSONB, default=list)
    facts: Mapped[dict] = mapped_column(JSONB, default=dict)


class DeviceEnrollment(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "device_enrollments"
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    device_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("devices.id", ondelete="SET NULL"), nullable=True)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)


class DeviceNonce(Base):
    """Replay protection: every signed device message nonce is stored until its timestamp window closes."""

    __tablename__ = "device_nonces"
    __tenant__ = True
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    device_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), primary_key=True)
    nonce: Mapped[str] = mapped_column(String(64), primary_key=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DeviceSupportRequest(IdMixin, TimestampMixin, TenantMixin, Base):
    """Device-scoped idempotency record for the first direct-help message."""

    __tablename__ = "device_support_requests"
    __table_args__ = (UniqueConstraint("device_id", "client_request_id"),)
    device_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    client_request_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    ticket_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="CASCADE"))
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"))
    response: Mapped[dict] = mapped_column(JSONB)


class DeviceCommand(IdMixin, TimestampMixin, TenantMixin, Base):
    """Server -> device signed action request queue (device polls over outbound HTTPS / WebSocket)."""

    __tablename__ = "device_commands"
    device_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("support_sessions.id", ondelete="CASCADE"), nullable=True)
    execution_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(String(80))
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    envelope: Mapped[dict] = mapped_column(JSONB, default=dict)  # signed request as delivered
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)  # queued|delivered|done|failed|expired|rejected
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RemoteAccessBinding(IdMixin, TimestampMixin, TenantMixin, Base):
    """Maps one JoltIQ device to an opaque node in a deployment-owned remote access provider."""

    __tablename__ = "remote_access_bindings"
    __table_args__ = (UniqueConstraint("device_id"), UniqueConstraint("org_id", "provider", "node_id"))
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(30), default="meshcentral")
    node_id: Mapped[str] = mapped_column(String(300))
    created_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))


class RemoteAccessSession(IdMixin, TimestampMixin, TenantMixin, Base):
    """JoltIQ's auditable authorization record; the provider enforces endpoint consent."""

    __tablename__ = "remote_access_sessions"
    __table_args__ = (CheckConstraint("state IN ('active', 'ended')", name="ck_remote_access_session_state"),)
    device_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), index=True
    )
    binding_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("remote_access_bindings.id", ondelete="RESTRICT"), index=True
    )
    operator_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"))
    reason: Mapped[str] = mapped_column(String(500))
    state: Mapped[str] = mapped_column(String(20), default="active", index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# ---------------- Live support sessions ----------------
class SupportSession(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "support_sessions"
    kind: Mapped[str] = mapped_column(String(30))
    state: Mapped[str] = mapped_column(String(30), default="pending_consent", index=True)
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True, index=True)
    device_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("devices.id", ondelete="SET NULL"), nullable=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # Consent scope: what the user agreed to (domains, actions, expiry). Enforced server side.
    consent: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    consented_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    consented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    allowed_domains: Mapped[list[str]] = mapped_column(ARRAY(String(253)), default=list)
    worker_session_ref: Mapped[str | None] = mapped_column(String(100), nullable=True)
    recording_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    emergency_stopped: Mapped[bool] = mapped_column(Boolean, default=False)
    terminated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pause_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    request_details: Mapped[dict] = mapped_column(JSONB, default=dict)
    lease_epoch: Mapped[int] = mapped_column(Integer, default=0)


class SessionParticipant(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "session_participants"
    __table_args__ = (UniqueConstraint("session_id", "user_id"),)
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("support_sessions.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(20))  # customer|technician|observer


class ControlLease(IdMixin, TimestampMixin, TenantMixin, Base):
    """Exactly one active input-control lease per session (partial unique index)."""

    __tablename__ = "control_leases"
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("support_sessions.id", ondelete="CASCADE"), index=True)
    holder_kind: Mapped[str] = mapped_column(String(20))  # ai|customer|technician
    holder_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    release_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)


Index(
    "uq_control_lease_active", ControlLease.session_id, unique=True, postgresql_where=text("released_at IS NULL")
)


class SessionEvent(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "session_events"
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("support_sessions.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(50))
    actor_kind: Mapped[str] = mapped_column(String(20))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    data: Mapped[dict] = mapped_column(JSONB, default=dict)  # always redacted before storage


# ---------------- AI configuration ----------------
class ModelProviderConfig(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "model_provider_configs"
    __table_args__ = (UniqueConstraint("org_id", "provider"),)
    provider: Mapped[str] = mapped_column(String(30))  # openai|anthropic|gemini|local_fixture
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_for_tenant_data: Mapped[bool] = mapped_column(Boolean, default=False)
    credential_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    models: Mapped[dict] = mapped_column(JSONB, default=dict)  # task -> model id
    input_cost_per_mtok: Mapped[float] = mapped_column(Float, default=0.0)
    output_cost_per_mtok: Mapped[float] = mapped_column(Float, default=0.0)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=60)
    max_retries: Mapped[int] = mapped_column(Integer, default=2)
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_test_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class AIBudget(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "ai_budgets"
    __table_args__ = (UniqueConstraint("org_id"),)
    monthly_usd_limit: Mapped[float] = mapped_column(Float, default=200.0)
    per_run_usd_limit: Mapped[float] = mapped_column(Float, default=5.0)
    research_requests_per_day: Mapped[int] = mapped_column(Integer, default=200)
    routing: Mapped[dict] = mapped_column(JSONB, default=dict)  # task -> [provider order] fallbacks


# ---------------- Agent runs ----------------
class AgentRun(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "agent_runs"
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="CASCADE"), nullable=True, index=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("support_sessions.id", ondelete="SET NULL"), nullable=True)
    state: Mapped[str] = mapped_column(String(30), default="NEW", index=True)
    correlation_id: Mapped[str] = mapped_column(String(64))
    started_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    context: Mapped[dict] = mapped_column(JSONB, default=dict)  # observations, hypotheses, citations
    retries: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=2)
    step_count: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    paused_for: Mapped[str | None] = mapped_column(String(50), nullable=True)  # approval|customer|auth
    outcome_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcome_stage: Mapped[str] = mapped_column(String(40), default="UNCONFIRMED")
    workflow_engine: Mapped[str] = mapped_column(String(20), default="db")  # db|temporal
    lock_owner: Mapped[str | None] = mapped_column(String(80), nullable=True)
    lock_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentTransition(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "agent_transitions"
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    from_state: Mapped[str] = mapped_column(String(30))
    to_state: Mapped[str] = mapped_column(String(30))
    reason: Mapped[str] = mapped_column(Text, default="")
    data: Mapped[dict] = mapped_column(JSONB, default=dict)


class AgentMessage(IdMixin, TimestampMixin, TenantMixin, Base):
    """Chat transcript between customer/technician and the AI (per run)."""

    __tablename__ = "agent_messages"
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))  # user|assistant|system|tool
    author_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSONB, default=dict)  # citations etc


class ModelCall(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "model_calls"
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=True, index=True)
    task: Mapped[str] = mapped_column(String(40))
    provider: Mapped[str] = mapped_column(String(30))
    model: Mapped[str] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    live: Mapped[bool] = mapped_column(Boolean, default=True)


class AgentPlan(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "agent_plans"
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    summary: Mapped[str] = mapped_column(Text)
    hypothesis: Mapped[str] = mapped_column(Text, default="")
    risks: Mapped[list] = mapped_column(JSONB, default=list)
    plan_hash: Mapped[str] = mapped_column(String(64))  # sha256 of canonical steps; approvals bind to this
    status: Mapped[str] = mapped_column(String(20), default="proposed")  # proposed|approved|executing|done|superseded|rejected
    citations: Mapped[list] = mapped_column(JSONB, default=list)
    procedure_article_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("knowledge_articles.id", ondelete="SET NULL"), nullable=True
    )
    procedure_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    procedure_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rollback_steps: Mapped[list] = mapped_column(JSONB, default=list)
    rollback_availability: Mapped[dict] = mapped_column(JSONB, default=dict)


class PlanStep(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "plan_steps"
    __table_args__ = (UniqueConstraint("plan_id", "ordinal"),)
    plan_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_plans.id", ondelete="CASCADE"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(80))  # typed action id from the action registry
    target: Mapped[dict] = mapped_column(JSONB, default=dict)
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    rationale: Mapped[str] = mapped_column(Text, default="")
    risk: Mapped[str] = mapped_column(String(20))  # computed by policy engine, NOT the model
    postconditions: Mapped[list] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(20), default="pending")


class ToolCall(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "tool_calls"
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    tool: Mapped[str] = mapped_column(String(80))
    input: Mapped[dict] = mapped_column(JSONB, default=dict)
    output: Mapped[dict] = mapped_column(JSONB, default=dict)
    ok: Mapped[bool] = mapped_column(Boolean, default=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)


# ---------------- Policy, approvals, execution ----------------
class PolicyRule(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "policy_rules"
    name: Mapped[str] = mapped_column(String(120))
    priority: Mapped[int] = mapped_column(Integer, default=100)
    effect: Mapped[str] = mapped_column(String(20))  # allow|deny|require_approval
    match: Mapped[dict] = mapped_column(JSONB, default=dict)  # action, risk_at_least, domain, device_id, role...
    approver_roles: Mapped[list] = mapped_column(JSONB, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Approval(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "approvals"
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=True, index=True)
    plan_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_plans.id", ondelete="CASCADE"), nullable=True)
    step_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("plan_steps.id", ondelete="CASCADE"), nullable=True)
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)
    risk: Mapped[str] = mapped_column(String(20))
    action: Mapped[str] = mapped_column(String(80))
    target: Mapped[dict] = mapped_column(JSONB, default=dict)
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    reason: Mapped[str] = mapped_column(Text)
    effects: Mapped[str] = mapped_column(Text, default="")
    plan_hash: Mapped[str] = mapped_column(String(64))
    step_hash: Mapped[str] = mapped_column(String(64))
    approver_roles: Mapped[list] = mapped_column(JSONB, default=list)
    requested_for_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_comment: Mapped[str] = mapped_column(Text, default="")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # HMAC over (approval id, step_hash, decided_by, decided_at) — verified before execution.
    grant_signature: Mapped[str | None] = mapped_column(String(128), nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ActionExecution(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "action_executions"
    __table_args__ = (UniqueConstraint("org_id", "idempotency_key"),)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=True, index=True)
    step_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("plan_steps.id", ondelete="SET NULL"), nullable=True)
    approval_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("approvals.id", ondelete="SET NULL"), nullable=True)
    session_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(String(80))
    target: Mapped[dict] = mapped_column(JSONB, default=dict)
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
    risk: Mapped[str] = mapped_column(String(20))
    policy_decision: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|running|succeeded|failed|blocked
    result: Mapped[dict] = mapped_column(JSONB, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(100))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class VerificationRun(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "verification_runs"
    execution_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("action_executions.id", ondelete="CASCADE"), nullable=True, index=True)
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=True, index=True)
    postconditions: Mapped[list] = mapped_column(JSONB, default=list)
    pre_state: Mapped[dict] = mapped_column(JSONB, default=dict)
    post_state: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="UNKNOWN")
    details: Mapped[list] = mapped_column(JSONB, default=list)  # per-postcondition result
    unintended_effects: Mapped[list] = mapped_column(JSONB, default=list)
    persistence_checked: Mapped[bool] = mapped_column(Boolean, default=False)
    kind: Mapped[str] = mapped_column(String(20), default="verification")
    evidence_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class VerificationEvidence(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "verification_evidence"
    verification_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("verification_runs.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(30))  # readback|screenshot|http|device_result|db_refetch
    summary: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSONB, default=dict)  # redacted
    storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)


# ---------------- Durable job queue ----------------
class Job(IdMixin, TimestampMixin, Base):
    """Postgres-backed durable queue (FOR UPDATE SKIP LOCKED) with retries and dead-lettering.

    Jobs carry org_id so the worker sets tenant context before running them. The jobs table itself is
    not tenant-RLS'd because workers must claim jobs across tenants; job payloads hold IDs only.
    """

    __tablename__ = "jobs"
    org_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(60), index=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)  # queued|running|done|dead
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    locked_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    dedupe_key: Mapped[str | None] = mapped_column(String(200), nullable=True, unique=True)


# ---------------- Computer use (screen-driving agent on an enrolled device) ----------------
class ComputerUseRun(IdMixin, TimestampMixin, TenantMixin, Base):
    """One goal-driven 'see the screen, act, repeat' run on a consented desktop session."""

    __tablename__ = "computer_use_runs"
    __table_args__ = (
        CheckConstraint("mode IN ('guide', 'fix')", name="ck_computer_use_runs_mode"),
        CheckConstraint(
            "local_consent_state IN ('pending', 'approved', 'declined')",
            name="ck_computer_use_runs_local_consent_state",
        ),
    )
    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("support_sessions.id", ondelete="CASCADE"), index=True)
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True)
    started_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    goal: Mapped[str] = mapped_column(Text)
    mode: Mapped[str] = mapped_column(String(10), default="fix")
    local_consent_state: Mapped[str] = mapped_column(String(10), default="pending")
    # pending_approval|waiting_local_consent|running|waiting_device|waiting_customer|completed|failed|stopped
    state: Mapped[str] = mapped_column(String(30), default="pending_approval", index=True)
    waiting_for: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # {kind: approval|question|secure_input, text, tool_use_id}
    provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    step_count: Mapped[int] = mapped_column(Integer, default=0)
    max_steps: Mapped[int] = mapped_column(Integer, default=60)
    # Model transcript with screenshots referenced by storage key (never inline base64 in the DB).
    transcript: Mapped[list] = mapped_column(JSONB, default=list)
    pending_command_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    # Tool uses from the current model turn not yet executed, and results gathered so far for that turn.
    queue: Mapped[list] = mapped_column(JSONB, default=list)
    results: Mapped[list] = mapped_column(JSONB, default=list)
    last_screenshot_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    display: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # {width, height} of screenshots the model sees
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    success: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rollback: Mapped[dict] = mapped_column(JSONB, default=dict)


class ComputerUseStep(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "computer_use_steps"
    __table_args__ = (UniqueConstraint("run_id", "idx"),)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("computer_use_runs.id", ondelete="CASCADE"), index=True)
    idx: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(40))  # model action name or control tool (ask_customer, request_approval, ...)
    params: Mapped[dict] = mapped_column(JSONB, default=dict)  # typed text is stored redacted
    narration: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|done|failed|blocked
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    command_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    screenshot_key: Mapped[str | None] = mapped_column(String(300), nullable=True)  # screen AFTER the step
