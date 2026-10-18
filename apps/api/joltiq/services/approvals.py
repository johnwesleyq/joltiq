"""Approvals bound to an exact plan step (hash). A changed step => old approvals SUPERSEDED, re-approval required.
Decisions are signed (HMAC) and verified + consumed atomically before execution. Model output can never create
an APPROVED approval: only `decide()` from an authenticated human principal does."""
from __future__ import annotations

import uuid
from datetime import UTC, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..core.errors import Conflict, Forbidden, NotFound
from ..models import Approval, Organization, PlanStep, Ticket, utcnow
from ..models.enums import ApprovalStatus as A
from ..security.crypto import canonical_json, hmac_sign, hmac_verify, sha256_hex
from . import audit
from .notifications import notify, staff_user_ids
from .realtime import publish


def step_hash(action: str, target: dict, params: dict, plan_hash: str) -> str:
    return sha256_hex(canonical_json({"action": action, "target": target, "params": params, "plan": plan_hash}))


def _grant_message(a: Approval) -> str:
    return f"{a.id}|{a.step_hash}|{a.decided_by}|{a.decided_at.astimezone(UTC).isoformat(timespec='microseconds') if a.decided_at else ''}|{a.status}"


def request(db: Session, *, org_id, run_id, plan_id, step: PlanStep, plan_hash: str, ticket_id, session_id, reason: str,
            effects: str, approver_roles: list[str], requested_for_user_id=None) -> Approval:
    org = db.get(Organization, org_id)
    ttl = int(((org.settings if org else {}) or {}).get("approval_ttl_minutes", 60))
    sh = step_hash(step.action, step.target, step.params, plan_hash)
    a = Approval(org_id=org_id, run_id=run_id, plan_id=plan_id, step_id=step.id, ticket_id=ticket_id, session_id=session_id,
                 status=A.PENDING, risk=step.risk, action=step.action, target=step.target, params=step.params, reason=reason,
                 effects=effects, plan_hash=plan_hash, step_hash=sh, approver_roles=approver_roles,
                 requested_for_user_id=requested_for_user_id, expires_at=utcnow() + timedelta(minutes=ttl))
    db.add(a)
    db.flush()
    targets = list(staff_user_ids(db, org_id)) if any(r != "customer" for r in approver_roles) else []
    if "customer" in approver_roles and requested_for_user_id:
        targets.append(requested_for_user_id)
    notify(db, org_id, targets, "approval", f"Approval needed: {step.action} ({step.risk})", reason, f"/approvals/{a.id}")
    publish(db, org_id, "approval.requested", {"approval_id": str(a.id), "run_id": str(run_id) if run_id else None,
                                               "ticket_id": str(ticket_id) if ticket_id else None})
    audit.record(db, org_id=org_id, actor_kind="system", actor_id=None, action="approval.requested", resource_type="approval",
                 resource_id=a.id, data={"action": step.action, "risk": step.risk, "params": step.params})
    return a


def can_decide(db: Session, a: Approval, *, user_id, role: str | None, customer_id) -> tuple[bool, str]:
    if role is None:
        return False, "no role"
    if role in a.approver_roles or (role in ("org_owner", "admin") and any(r in a.approver_roles for r in ("technician", "admin"))):
        if role == "customer":
            # Customers may only approve actions on their own ticket/session.
            if a.requested_for_user_id and a.requested_for_user_id != user_id:
                return False, "approval belongs to another customer"
            if a.ticket_id:
                t = db.get(Ticket, a.ticket_id)
                if not t or (t.requester_id != user_id and (not customer_id or t.customer_id != customer_id)):
                    return False, "not your ticket"
        return True, ""
    return False, f"role {role} cannot decide {a.risk} approvals"


def decide(db: Session, approval_id: uuid.UUID, *, approve: bool, user_id, role: str | None, customer_id, comment: str = "") -> Approval:
    a = db.execute(select(Approval).where(Approval.id == approval_id).with_for_update()).scalar_one_or_none()
    if not a:
        raise NotFound("Approval not found")
    if a.status != A.PENDING:
        raise Conflict(f"Approval is already {a.status}", code="approval_not_pending")
    if a.expires_at < utcnow():
        a.status = A.EXPIRED
        raise Conflict("Approval request expired", code="approval_expired")
    ok, why = can_decide(db, a, user_id=user_id, role=role, customer_id=customer_id)
    if not ok:
        audit.security_event(db, "approval_unauthorized", org_id=a.org_id, user_id=user_id, severity="warn", data={"approval": str(a.id), "why": why})
        raise Forbidden(f"You cannot decide this approval: {why}")
    a.status = A.APPROVED if approve else A.DENIED
    a.decided_by, a.decided_at, a.decision_comment = user_id, utcnow(), comment[:2000]
    a.grant_signature = hmac_sign(get_settings().approval_signing_key, _grant_message(a))
    audit.record(db, org_id=a.org_id, actor_kind="user", actor_id=user_id, action=f"approval.{'approved' if approve else 'denied'}",
                 resource_type="approval", resource_id=a.id, data={"action": a.action, "risk": a.risk, "comment": comment[:200]})
    publish(db, a.org_id, "approval.decided", {"approval_id": str(a.id), "status": a.status, "run_id": str(a.run_id) if a.run_id else None})
    return a


def verify_and_consume(db: Session, a: Approval, step: PlanStep, plan_hash: str) -> tuple[bool, str]:
    """Called by the executor immediately before running a step."""
    a = db.execute(select(Approval).where(Approval.id == a.id).with_for_update()).scalar_one()
    if a.status != A.APPROVED:
        return False, f"approval status {a.status}"
    if a.consumed_at:
        return False, "approval already used"
    if a.expires_at < utcnow() and (a.decided_at is None or a.decided_at > a.expires_at):
        return False, "approval expired"
    if a.step_hash != step_hash(step.action, step.target, step.params, plan_hash):
        a.status = A.SUPERSEDED
        return False, "step changed after approval; re-approval required"
    if not hmac_verify(get_settings().approval_signing_key, _grant_message(a), a.grant_signature or ""):
        audit.security_event(db, "approval_signature_invalid", org_id=a.org_id, severity="critical", data={"approval": str(a.id)})
        return False, "approval signature invalid"
    a.consumed_at = utcnow()
    return True, ""


def supersede_for_plan(db: Session, plan_id) -> int:
    n = 0
    for a in db.execute(select(Approval).where(Approval.plan_id == plan_id, Approval.status == A.PENDING)).scalars():
        a.status = A.SUPERSEDED
        n += 1
    return n


def expire_stale(db: Session) -> int:
    n = 0
    for a in db.execute(select(Approval).where(Approval.status == A.PENDING, Approval.expires_at < utcnow())).scalars():
        a.status = A.EXPIRED
        n += 1
    return n

