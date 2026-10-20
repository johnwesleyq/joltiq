"""Tickets, knowledge, research."""
from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .._dims import EMBED_DIM  # noqa: E402,F401
from ..db import Base
from .base import IdMixin, TenantMixin, TimestampMixin


class SlaPolicy(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "sla_policies"
    name: Mapped[str] = mapped_column(String(100))
    priority: Mapped[str] = mapped_column(String(20))
    first_response_minutes: Mapped[int] = mapped_column(Integer)
    resolution_minutes: Mapped[int] = mapped_column(Integer)


class Ticket(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "tickets"
    __table_args__ = (UniqueConstraint("org_id", "number"),)
    number: Mapped[int] = mapped_column(Integer)
    subject: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="NEW", index=True)
    priority: Mapped[str] = mapped_column(String(20), default="normal")
    category: Mapped[str | None] = mapped_column(String(80), nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="portal")  # portal|email|api|integration|chat
    customer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("customers.id", ondelete="SET NULL"), nullable=True)
    requester_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    assignee_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    device_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("devices.id", ondelete="SET NULL"), nullable=True)
    sla_policy_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("sla_policies.id", ondelete="SET NULL"), nullable=True)
    first_response_due: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_due: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(60)), default=list)
    external_ref: Mapped[str | None] = mapped_column(String(200), nullable=True)  # id in external system
    resolution_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    search_tsv: Mapped[str | None] = mapped_column(TSVECTOR, nullable=True)


class TicketComment(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "ticket_comments"
    ticket_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    author_kind: Mapped[str] = mapped_column(String(20), default="user")  # user|ai|system|integration
    body: Mapped[str] = mapped_column(Text)
    internal: Mapped[bool] = mapped_column(Boolean, default=False)  # internal notes hidden from customers


class TicketAttachment(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "ticket_attachments"
    ticket_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str] = mapped_column(String(500))
    sha256: Mapped[str] = mapped_column(String(64))
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class TicketEvent(IdMixin, TimestampMixin, TenantMixin, Base):
    """Timeline: every status change, assignment, AI action, approval, verification."""

    __tablename__ = "ticket_events"
    ticket_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(50))
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    actor_kind: Mapped[str] = mapped_column(String(20), default="user")
    data: Mapped[dict] = mapped_column(JSONB, default=dict)


class TicketCounter(Base):
    __tablename__ = "ticket_counters"
    __tenant__ = True
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True)
    next_number: Mapped[int] = mapped_column(Integer, default=1)


# ---------------- Knowledge ----------------
class KnowledgeDocument(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "knowledge_documents"
    title: Mapped[str] = mapped_column(String(300))
    source_type: Mapped[str] = mapped_column(String(30))  # upload|url|ticket|article|transcript
    source_uri: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    content_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_version: Mapped[int] = mapped_column(Integer, default=0)
    # Permission filtering: roles allowed to retrieve. Customers only see 'customer'-visible docs.
    visibility: Mapped[list[str]] = mapped_column(ARRAY(String(30)), default=lambda: ["technician", "admin", "org_owner"])
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class KnowledgeDocumentVersion(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "knowledge_document_versions"
    __table_args__ = (UniqueConstraint("document_id", "version"),)
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    extracted: Mapped[dict] = mapped_column(JSONB, default=dict)  # symptoms, error codes, env, fixes


class KnowledgeChunk(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "knowledge_chunks"
    document_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("knowledge_documents.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    ordinal: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section: Mapped[str | None] = mapped_column(String(300), nullable=True)
    embedding = mapped_column(Vector(EMBED_DIM), nullable=True)
    embedder: Mapped[str] = mapped_column(String(60), default="")
    tsv: Mapped[str | None] = mapped_column(TSVECTOR, nullable=True)


Index("ix_chunks_tsv", KnowledgeChunk.tsv, postgresql_using="gin")


class KnowledgeArticle(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "knowledge_articles"
    title: Mapped[str] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    body: Mapped[dict] = mapped_column(JSONB, default=dict)  # prerequisites, diagnostics, remediation, risk, verification
    citations: Mapped[list] = mapped_column(JSONB, default=list)
    source_ticket_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="SET NULL"), nullable=True)
    source_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    verified_resolution: Mapped[bool] = mapped_column(Boolean, default=False)
    # External fixes must never be promoted to executable procedures automatically.
    executable: Mapped[bool] = mapped_column(Boolean, default=False)
    drafted_by_ai: Mapped[bool] = mapped_column(Boolean, default=True)
    published_document_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    previous_version_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    procedure_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ArticleReview(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "article_reviews"
    article_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("knowledge_articles.id", ondelete="CASCADE"), index=True)
    reviewer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"))
    decision: Mapped[str] = mapped_column(String(20))  # approve|reject|request_changes|publish|archive
    comment: Mapped[str] = mapped_column(Text, default="")
    procedure_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    auth_session_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("auth_sessions.id", ondelete="SET NULL"), nullable=True
    )
    mfa_passed: Mapped[bool] = mapped_column(Boolean, default=False)


# ---------------- Research ----------------
class ResearchQuery(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "research_queries"
    run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=True, index=True)
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tickets.id", ondelete="CASCADE"), nullable=True)
    original_text_hash: Mapped[str] = mapped_column(String(64))
    redacted_query: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(40))
    live: Mapped[bool] = mapped_column(Boolean, default=True)  # false = labelled fixture provider
    status: Mapped[str] = mapped_column(String(20), default="ok")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    redactions: Mapped[list] = mapped_column(JSONB, default=list)


class ResearchSource(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "research_sources"
    query_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("research_queries.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(String(2000))
    domain: Mapped[str] = mapped_column(String(253))
    title: Mapped[str] = mapped_column(String(500), default="")
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    excerpt: Mapped[str] = mapped_column(Text, default="")  # short; never reproduced wholesale
    source_kind: Mapped[str] = mapped_column(String(30))  # official_vendor|forum|issue_tracker|qa|other
    reliability: Mapped[float] = mapped_column(Float, default=0.5)
    relevance: Mapped[float] = mapped_column(Float, default=0.0)
    applicable_versions: Mapped[list] = mapped_column(JSONB, default=list)
    fetched: Mapped[bool] = mapped_column(Boolean, default=False)
    injection_flags: Mapped[list] = mapped_column(JSONB, default=list)


class ResearchFinding(IdMixin, TimestampMixin, TenantMixin, Base):
    __tablename__ = "research_findings"
    query_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("research_queries.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # evidence|hypothesis
    statement: Mapped[str] = mapped_column(Text)
    source_ids: Mapped[list] = mapped_column(JSONB, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    conflicts_with: Mapped[list] = mapped_column(JSONB, default=list)
