"""Tamper-evident audit log (per-tenant SHA-256 hash chain) and security events."""
from __future__ import annotations

import uuid
from datetime import UTC
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..core.context import current_request_id
from ..models import AuditEvent, SecurityEvent, utcnow
from ..security.crypto import canonical_json, sha256_hex
from ..security.redaction import redact_obj

GENESIS = "0" * 64


def _iso(dt) -> str:
    return dt.astimezone(UTC).isoformat(timespec="microseconds")


def _row_digest(prev_hash: str, row: dict[str, Any]) -> str:
    return sha256_hex(prev_hash + canonical_json(row))


def record(db: Session, *, org_id: uuid.UUID | None, actor_kind: str, actor_id: Any, action: str,
           resource_type: str, resource_id: Any = None, data: dict | None = None) -> AuditEvent:
    data = redact_obj(data or {})
    prev = GENESIS
    if org_id is not None:
        # Serialize appends per org so the chain is linear.
        db.execute(text("select pg_advisory_xact_lock(hashtext(:k))"), {"k": f"audit:{org_id}"})
        last = db.execute(
            select(AuditEvent.hash).where(AuditEvent.org_id == org_id).order_by(AuditEvent.id.desc()).limit(1)
        ).scalar()
        prev = last or GENESIS
    at = utcnow()
    row = {
        "org_id": str(org_id) if org_id else None, "at": _iso(at), "actor_kind": actor_kind,
        "actor_id": str(actor_id) if actor_id else None, "action": action, "resource_type": resource_type,
        "resource_id": str(resource_id) if resource_id else None, "data": data,
    }
    ev = AuditEvent(
        org_id=org_id, at=at, actor_kind=actor_kind, actor_id=row["actor_id"], action=action,
        resource_type=resource_type, resource_id=row["resource_id"], data=data,
        request_id=current_request_id() or None, prev_hash=prev, hash=_row_digest(prev, row),
    )
    db.add(ev)
    db.flush()
    return ev


def verify_chain(db: Session, org_id: uuid.UUID) -> dict[str, Any]:
    rows = db.execute(select(AuditEvent).where(AuditEvent.org_id == org_id).order_by(AuditEvent.id)).scalars().all()
    prev = GENESIS
    for r in rows:
        row = {
            "org_id": str(r.org_id) if r.org_id else None, "at": _iso(r.at), "actor_kind": r.actor_kind,
            "actor_id": r.actor_id, "action": r.action, "resource_type": r.resource_type,
            "resource_id": r.resource_id, "data": r.data,
        }
        if r.prev_hash != prev or r.hash != _row_digest(prev, row):
            return {"ok": False, "broken_at": r.id, "checked": len(rows)}
        prev = r.hash
    return {"ok": True, "checked": len(rows), "head": prev}


def security_event(db: Session, kind: str, *, org_id=None, user_id=None, severity: str = "info",
                   ip: str | None = None, data: dict | None = None) -> None:
    db.add(SecurityEvent(org_id=org_id, user_id=user_id, kind=kind, severity=severity, ip=ip, data=redact_obj(data or {})))
    db.flush()
