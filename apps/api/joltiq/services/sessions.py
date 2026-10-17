"""Live support sessions: consent, scope, exclusive control lease, pause/resume, emergency stop, termination, events."""
from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..core.errors import AppError, Conflict, Forbidden, NotFound
from ..models import ControlLease, Device, SessionEvent, SessionParticipant, SupportSession, Ticket, utcnow
from ..models.enums import RiskLevel
from ..security.redaction import redact_obj
from . import audit, browser_client
from .realtime import publish

LEASE_SECONDS = 15 * 60


def event(db: Session, s: SupportSession, kind: str, *, actor_kind: str, actor_id=None, data: dict | None = None) -> SessionEvent:
    seq = (db.execute(select(func.coalesce(func.max(SessionEvent.seq), 0)).where(SessionEvent.session_id == s.id)).scalar() or 0) + 1
    ev = SessionEvent(org_id=s.org_id, session_id=s.id, seq=seq, kind=kind, actor_kind=actor_kind, actor_id=actor_id, data=redact_obj(data or {}))
    db.add(ev)
    db.flush()
    publish(db, s.org_id, "session.event", {"session_id": str(s.id), "seq": seq, "kind": kind, "actor_kind": actor_kind, "data": ev.data})
    return ev


def get(db: Session, sid: uuid.UUID) -> SupportSession:
    s = db.get(SupportSession, sid)
    if not s:
        raise NotFound("Session not found")
    return s


def start(db: Session, org_id: uuid.UUID, *, kind: str, owner_id, ticket_id=None, device_id=None, allowed_domains: list[str] | None = None,
          run_id=None) -> SupportSession:
    if kind not in ("chat", "browser", "desktop", "technician_assisted", "escalated"):
        raise AppError("invalid session kind", code="validation_error")
    if kind == "desktop":
        d = db.get(Device, device_id) if device_id else None
        if not d or d.status != "active":
            raise AppError("Desktop sessions need an enrolled, active device", code="device_required")
    if kind == "browser":
        doms = [x.strip().lower() for x in (allowed_domains or []) if x.strip()]
        if not doms:
            raise AppError("Browser sessions need at least one approved domain", code="domains_required")
        allowed_domains = doms
    s = SupportSession(org_id=org_id, kind=kind, state="pending_consent", owner_id=owner_id, ticket_id=ticket_id, device_id=device_id,
                       allowed_domains=allowed_domains or [], run_id=run_id)
    db.add(s)
    db.flush()
    db.add(SessionParticipant(org_id=org_id, session_id=s.id, user_id=owner_id, role="customer"))
    event(db, s, "created", actor_kind="user", actor_id=owner_id, data={"kind": kind, "domains": allowed_domains or []})
    return s


def consent_holder_ok(db: Session, s: SupportSession, user_id, customer_id, is_staff: bool) -> bool:
    """Consent must come from the person whose system/account is being operated: the session owner, the ticket
    requester, or a member of the ticket's customer account. Technicians cannot consent on a customer's behalf
    (except for sessions they own, e.g. internal IT on their own machine)."""
    if s.owner_id == user_id:
        return True
    if s.ticket_id:
        t = db.get(Ticket, s.ticket_id)
        if t and (t.requester_id == user_id or (customer_id and t.customer_id == customer_id)):
            return True
    if s.device_id:
        d = db.get(Device, s.device_id)
        if d and d.owner_user_id == user_id:
            return True
    return False


def give_consent(db: Session, s: SupportSession, *, user_id, customer_id, is_staff: bool, minutes: int = 60,
                 max_risk: str = RiskLevel.SENSITIVE, actions: list[str] | None = None, recording: bool = False) -> SupportSession:
    if s.state not in ("pending_consent", "paused"):
        raise Conflict(f"Session is {s.state}", code="invalid_state")
    if not consent_holder_ok(db, s, user_id, customer_id, is_staff):
        raise Forbidden("Only the customer whose system is being accessed can consent")
    if max_risk not in (RiskLevel.READ_ONLY, RiskLevel.LOW_RISK, RiskLevel.SENSITIVE, RiskLevel.PRIVILEGED):
        raise AppError("Consent cannot cover destructive actions", code="validation_error")
    minutes = max(5, min(240, minutes))
    s.consent = {"max_risk": max_risk, "actions": actions or None, "minutes": minutes, "recording": recording,
                 "domains": list(s.allowed_domains or []), "device_id": str(s.device_id) if s.device_id else None}
    s.consented_by, s.consented_at = user_id, utcnow()
    s.expires_at = utcnow() + timedelta(minutes=minutes)
    s.recording_enabled = bool(recording)
    s.state = "active"
    if s.kind == "browser":
        browser_client.create(str(s.id), list(s.allowed_domains))
        s.worker_session_ref = str(s.id)
        from .billing import check_entitlement

        check_entitlement(db, s.org_id, "browser_minutes", adding=1)
    if s.kind == "desktop":
        from .billing import check_entitlement

        check_entitlement(db, s.org_id, "desktop_minutes", adding=1)
    event(db, s, "consent_granted", actor_kind="user", actor_id=user_id, data=s.consent)
    audit.record(db, org_id=s.org_id, actor_kind="user", actor_id=user_id, action="session.consent", resource_type="support_session", resource_id=s.id, data=s.consent)
    return s


def active_lease(db: Session, s: SupportSession) -> ControlLease | None:
    lease = db.execute(select(ControlLease).where(ControlLease.session_id == s.id, ControlLease.released_at.is_(None))).scalar_one_or_none()
    if lease and lease.expires_at < utcnow():
        lease.released_at, lease.release_reason = utcnow(), "expired"
        db.flush()
        return None
    return lease


def acquire_lease(db: Session, s: SupportSession, holder_kind: str, user_id=None, *, preempt: bool = False, reason: str = "") -> ControlLease:
    """Exactly one active lease per session (enforced by a partial unique index). Humans preempt the AI;
    the customer preempts everyone; a technician can only take control from the AI or by customer handoff."""
    if s.state not in ("active", "awaiting_user_auth", "paused") or s.emergency_stopped:
        raise Conflict(f"Session is {s.state}", code="invalid_state")
    cur = active_lease(db, s)
    if cur:
        same = cur.holder_kind == holder_kind and cur.holder_user_id == user_id
        if same:
            cur.expires_at = utcnow() + timedelta(seconds=LEASE_SECONDS)
            return cur
        allowed = preempt and (holder_kind == "customer" or (holder_kind == "technician" and cur.holder_kind == "ai"))
        if not allowed:
            raise Conflict(f"Control is held by {cur.holder_kind}", code="lease_held", details={"holder": cur.holder_kind})
        cur.released_at, cur.release_reason = utcnow(), f"preempted by {holder_kind}"
        db.flush()
    lease = ControlLease(org_id=s.org_id, session_id=s.id, holder_kind=holder_kind, holder_user_id=user_id,
                         acquired_at=utcnow(), expires_at=utcnow() + timedelta(seconds=LEASE_SECONDS))
    db.add(lease)
    try:
        db.flush()
    except IntegrityError as e:  # concurrent acquire lost the race
        raise Conflict("Another participant acquired control first", code="lease_held") from e
    event(db, s, "control_acquired", actor_kind=holder_kind, actor_id=user_id, data={"reason": reason})
    return lease


def release_lease(db: Session, s: SupportSession, reason: str) -> None:
    db.execute(update(ControlLease).where(ControlLease.session_id == s.id, ControlLease.released_at.is_(None))
               .values(released_at=utcnow(), release_reason=reason))
    event(db, s, "control_released", actor_kind="system", data={"reason": reason})


def holds_lease(db: Session, s: SupportSession, holder_kind: str, user_id=None) -> bool:
    lease = active_lease(db, s)
    return bool(lease and lease.holder_kind == holder_kind and (holder_kind == "ai" or lease.holder_user_id == user_id))


def pause(db: Session, s: SupportSession, *, by_kind: str, user_id=None, reason: str = "paused") -> None:
    if s.state not in ("active", "awaiting_user_auth"):
        raise Conflict(f"Session is {s.state}", code="invalid_state")
    s.state, s.pause_reason = "paused", reason[:200]
    lease = active_lease(db, s)
    if lease and lease.holder_kind == "ai":
        release_lease(db, s, "paused")
    if s.kind == "browser" and s.worker_session_ref:
        try:
            browser_client.pause(s.worker_session_ref, True)
        except Exception:  # noqa: BLE001,S110
            pass
    event(db, s, "paused", actor_kind=by_kind, actor_id=user_id, data={"reason": reason})


def resume(db: Session, s: SupportSession, *, user_id) -> None:
    if s.state != "paused" or s.emergency_stopped:
        raise Conflict("Session cannot be resumed", code="invalid_state")
    if s.expires_at and s.expires_at < utcnow():
        s.state = "expired"
        raise Conflict("Consent expired; start a new session", code="consent_expired")
    s.state, s.pause_reason = "active", None
    if s.kind == "browser" and s.worker_session_ref:
        browser_client.pause(s.worker_session_ref, False)
    event(db, s, "resumed", actor_kind="user", actor_id=user_id)


def emergency_stop(db: Session, s: SupportSession, *, user_id) -> None:
    """Immediate, irreversible for this session: revoke control, stop AI, close the browser/desktop channel."""
    s.emergency_stopped = True
    terminate(db, s, by_kind="user", user_id=user_id, reason="emergency stop")
    audit.record(db, org_id=s.org_id, actor_kind="user", actor_id=user_id, action="session.emergency_stop", resource_type="support_session", resource_id=s.id)


def terminate(db: Session, s: SupportSession, *, by_kind: str, user_id=None, reason: str = "terminated") -> None:
    if s.state == "terminated":
        return
    release_lease(db, s, reason)
    s.state, s.terminated_at = "terminated", utcnow()
    if s.kind == "browser" and s.worker_session_ref:
        browser_client.close(s.worker_session_ref)
    if s.consented_at:
        from .billing import meter

        minutes = max(1.0, (utcnow() - s.consented_at).total_seconds() / 60)
        m = "browser_minutes" if s.kind == "browser" else ("desktop_minutes" if s.kind == "desktop" else None)
        if m:
            meter(db, s.org_id, m, round(minutes, 2), f"session-minutes:{s.id}")
        meter(db, s.org_id, "sessions", 1, f"session:{s.id}")
    event(db, s, "terminated", actor_kind=by_kind, actor_id=user_id, data={"reason": reason})


def awaiting_user_auth(db: Session, s: SupportSession, kind: str) -> None:
    s.state = "awaiting_user_auth"
    lease = active_lease(db, s)
    if lease and lease.holder_kind == "ai":
        release_lease(db, s, f"{kind} required")
    event(db, s, "auth_required", actor_kind="system", data={"kind": kind,
          "message": "Sign-in needed. The assistant has paused; please enter your credentials directly in the viewer."})


def ser(s: SupportSession, lease: ControlLease | None = None) -> dict[str, Any]:
    return {"id": str(s.id), "kind": s.kind, "state": s.state, "ticket_id": str(s.ticket_id) if s.ticket_id else None,
            "device_id": str(s.device_id) if s.device_id else None, "allowed_domains": s.allowed_domains, "consent": s.consent,
            "expires_at": s.expires_at.isoformat() if s.expires_at else None, "emergency_stopped": s.emergency_stopped,
            "recording_enabled": s.recording_enabled, "pause_reason": s.pause_reason, "run_id": str(s.run_id) if s.run_id else None,
            "control": {"holder_kind": lease.holder_kind, "holder_user_id": str(lease.holder_user_id) if lease.holder_user_id else None,
                        "expires_at": lease.expires_at.isoformat()} if lease else None,
            "created_at": s.created_at.isoformat()}
