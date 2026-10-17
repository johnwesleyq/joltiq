from enum import StrEnum


class Role(StrEnum):
    PLATFORM_OWNER = "platform_owner"
    ORG_OWNER = "org_owner"
    ADMIN = "admin"
    TECHNICIAN = "technician"
    CUSTOMER = "customer"
    AUDITOR = "auditor"


class TicketStatus(StrEnum):
    NEW = "NEW"
    OPEN = "OPEN"
    AI_INVESTIGATING = "AI_INVESTIGATING"
    AWAITING_CUSTOMER = "AWAITING_CUSTOMER"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    IN_PROGRESS = "IN_PROGRESS"
    ESCALATED = "ESCALATED"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"
    REOPENED = "REOPENED"
    CANCELLED = "CANCELLED"


class Priority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class RunState(StrEnum):
    NEW = "NEW"
    INTAKE = "INTAKE"
    COLLECTING_CONTEXT = "COLLECTING_CONTEXT"
    SEARCHING_INTERNAL = "SEARCHING_INTERNAL"
    RESEARCHING_EXTERNAL = "RESEARCHING_EXTERNAL"
    DIAGNOSING = "DIAGNOSING"
    PLANNING = "PLANNING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    RETRYING = "RETRYING"
    ESCALATED = "ESCALATED"
    RESOLVED = "RESOLVED"
    PARTIALLY_RESOLVED = "PARTIALLY_RESOLVED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL_RUN_STATES = {RunState.ESCALATED, RunState.RESOLVED, RunState.PARTIALLY_RESOLVED, RunState.FAILED, RunState.CANCELLED}


class RiskLevel(StrEnum):
    READ_ONLY = "READ_ONLY"
    LOW_RISK = "LOW_RISK"
    SENSITIVE = "SENSITIVE"
    PRIVILEGED = "PRIVILEGED"
    DESTRUCTIVE = "DESTRUCTIVE"


RISK_ORDER = [RiskLevel.READ_ONLY, RiskLevel.LOW_RISK, RiskLevel.SENSITIVE, RiskLevel.PRIVILEGED, RiskLevel.DESTRUCTIVE]


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    SUPERSEDED = "SUPERSEDED"


class VerificationStatus(StrEnum):
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"


class SessionKind(StrEnum):
    CHAT = "chat"
    BROWSER = "browser"
    DESKTOP = "desktop"
    TECHNICIAN_ASSISTED = "technician_assisted"
    ESCALATED = "escalated"


class SessionState(StrEnum):
    PENDING_CONSENT = "pending_consent"
    ACTIVE = "active"
    PAUSED = "paused"
    AWAITING_USER_AUTH = "awaiting_user_auth"
    TERMINATED = "terminated"
    EXPIRED = "expired"


class ArticleStatus(StrEnum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    REJECTED = "rejected"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class DocStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    INDEXED = "indexed"
    FAILED = "failed"
    DELETED = "deleted"


class DeviceStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    REVOKED = "revoked"
