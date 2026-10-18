"""Native ticketing: numbering, SLA, guarded status transitions, timeline, notifications."""
from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..core.errors import AppError, Conflict, NotFound
from ..models import Organization, SlaPolicy, Ticket, TicketComment, TicketCounter, TicketEvent, utcnow
from ..models.enums import TicketStatus as S
from . import audit
from .notifications import notify, staff_user_ids
from .realtime import publish

TRANSITIONS: dict[str, set[str]] = {
    S.NEW: {S.OPEN, S.AI_INVESTIGATING, S.AWAITING_CUSTOMER, S.IN_PROGRESS, S.ESCALATED, S.CANCELLED, S.RESOLVED},
    S.OPEN: {S.AI_INVESTIGATING, S.AWAITING_CUSTOMER, S.AWAITING_APPROVAL, S.IN_PROGRESS, S.ESCALATED, S.RESOLVED, S.CANCELLED},
    S.AI_INVESTIGATING: {S.AWAITING_CUSTOMER, S.AWAITING_APPROVAL, S.IN_PROGRESS, S.ESCALATED, S.RESOLVED, S.OPEN, S.CANCELLED},
    S.AWAITING_CUSTOMER: {S.OPEN, S.AI_INVESTIGATING, S.IN_PROGRESS, S.ESCALATED, S.RESOLVED, S.CANCELLED},
    S.AWAITING_APPROVAL: {S.IN_PROGRESS, S.AI_INVESTIGATING, S.ESCALATED, S.OPEN, S.CANCELLED},
    S.IN_PROGRESS: {S.AWAITING_CUSTOMER, S.AWAITING_APPROVAL, S.AI_INVESTIGATING, S.ESCALATED, S.RESOLVED, S.OPEN, S.CANCELLED},
    S.ESCALATED: {S.IN_PROGRESS, S.AWAITING_CUSTOMER, S.RESOLVED, S.OPEN, S.CANCELLED},
    S.RESOLVED: {S.CLOSED, S.REOPENED},
    S.CLOSED: {S.REOPENED},
    S.REOPENED: {S.OPEN, S.AI_INVESTIGATING, S.IN_PROGRESS, S.AWAITING_CUSTOMER, S.ESCALATED, S.RESOLVED, S.CANCELLED},
    S.CANCELLED: {S.REOPENED},
}
CUSTOMER_VISIBLE_EVENTS = {"created", "status_changed", "comment", "resolved", "reopened", "closed", "approval_requested",
                           "approval_decided", "verification", "escalated", "session"}


def next_number(db: Session, org_id: uuid.UUID) -> int:
    row = db.execute(
        text("update ticket_counters set next_number = next_number + 1 where org_id = :o returning next_number - 1"), {"o": org_id}
    ).scalar()
    if row is None:
        db.add(TicketCounter(org_id=org_id, next_number=1001))
        db.flush()
        return 1000
    return int(row)


def _apply_sla(db: Session, t: Ticket) -> None:
    pol = db.execute(select(SlaPolicy).where(SlaPolicy.priority == t.priority).limit(1)).scalar_one_or_none()
    if pol:
        base = t.created_at or utcnow()
        t.sla_policy_id = pol.id
        t.first_response_due = base + timedelta(minutes=pol.first_response_minutes)
        t.resolution_due = base + timedelta(minutes=pol.resolution_minutes)


def add_event(db: Session, t: Ticket, kind: str, *, actor_kind: str, actor_id: Any = None, data: dict | None = None) -> TicketEvent:
    ev = TicketEvent(org_id=t.org_id, ticket_id=t.id, kind=kind, actor_kind=actor_kind, actor_id=actor_id, data=data or {})
    db.add(ev)
    db.flush()
    audience = "all" if kind in CUSTOMER_VISIBLE_EVENTS else "staff"
    publish(db, t.org_id, "ticket.event", {"ticket_id": str(t.id), "kind": kind, "data": data or {}}, audience=audience)
    return ev


def create(db: Session, org_id: uuid.UUID, *, subject: str, description: str, priority: str = "normal", category: str | None = None,
           source: str = "portal", customer_id=None, requester_id=None, device_id=None, actor_kind: str = "user",
           external_ref: str | None = None) -> Ticket:
    if priority not in ("low", "normal", "high", "urgent"):
        raise AppError("invalid priority", code="validation_error")
    t = Ticket(org_id=org_id, number=next_number(db, org_id), subject=subject.strip()[:300], description=description,
               priority=priority, category=category, source=source, customer_id=customer_id, requester_id=requester_id,
               device_id=device_id, status=S.NEW, created_at=utcnow(), external_ref=external_ref)
    db.add(t)
    db.flush()
    _apply_sla(db, t)
    add_event(db, t, "created", actor_kind=actor_kind, actor_id=requester_id, data={"source": source, "priority": priority})
    audit.record(db, org_id=org_id, actor_kind=actor_kind, actor_id=requester_id, action="ticket.created", resource_type="ticket", resource_id=t.id)
    notify(db, org_id, staff_user_ids(db, org_id), "ticket", f"New ticket #{t.number}: {t.subject}", description[:400], f"/console/tickets/{t.id}", email=False)
    return t


def get(db: Session, ticket_id: uuid.UUID) -> Ticket:
    t = db.get(Ticket, ticket_id)
    if not t:
        raise NotFound("Ticket not found")
    return t


def transition(db: Session, t: Ticket, to: str, *, actor_kind: str, actor_id: Any = None, reason: str = "",
               verified: bool | None = None, force_close_reason: str | None = None) -> Ticket:
    to = S(to)
    cur = S(t.status)
    if to == cur:
        return t
    if to not in TRANSITIONS.get(cur, set()):
        raise Conflict(f"Cannot move ticket from {cur} to {to}", code="invalid_transition")
    org = db.get(Organization, t.org_id)
    settings = (org.settings if org else {}) or {}
    if to in (S.RESOLVED, S.CLOSED) and actor_kind == "ai":
        # The AI may only resolve with an independent VERIFIED outcome, never on its own say-so.
        if not verified:
            raise Conflict("AI cannot resolve a ticket without a VERIFIED outcome", code="verification_required")
    if to == S.RESOLVED and actor_kind == "user" and settings.get("require_verification_to_resolve") and not (verified or t.resolution_verified):
        if not force_close_reason:
            raise Conflict("This organization requires a verified outcome or a technician resolution note to resolve",
                           code="verification_required")
    prev = t.status
    t.status = to
    now = utcnow()
    if to == S.RESOLVED:
        t.resolved_at = now
        if verified:
            t.resolution_verified = True
    if to == S.CLOSED:
        t.closed_at = now
    if to == S.REOPENED:
        t.resolved_at, t.closed_at, t.resolution_verified = None, None, False
    data = {"from": prev, "to": to, "reason": reason}
    if force_close_reason:
        data["technician_resolution_note"] = force_close_reason
    add_event(db, t, "status_changed", actor_kind=actor_kind, actor_id=actor_id, data=data)
    audit.record(db, org_id=t.org_id, actor_kind=actor_kind, actor_id=actor_id, action="ticket.status_changed", resource_type="ticket", resource_id=t.id, data=data)
    if actor_kind != "integration":
        from .integrations import on_ticket_event

        on_ticket_event(db, t, "status", {"to": to})
    watchers = [u for u in (t.requester_id, t.assignee_id) if u]
    if to in (S.RESOLVED, S.ESCALATED, S.AWAITING_CUSTOMER, S.REOPENED) and watchers:
        cat = {"RESOLVED": "resolution", "ESCALATED": "escalation"}.get(to, "ticket")
        notify(db, t.org_id, watchers, cat, f"Ticket #{t.number} is now {to.replace('_', ' ').lower()}", reason, f"/portal/tickets/{t.id}")
    if to == S.RESOLVED and t.resolution_verified:
        from .billing import meter

        meter(db, t.org_id, "verified_resolutions", 1, f"verified-resolution:{t.id}:{t.resolved_at.isoformat() if t.resolved_at else ''}")
        if settings.get("auto_close_on_verified"):
            transition(db, t, S.CLOSED, actor_kind="system", reason="auto-closed after verified resolution (tenant policy)")
    return t


def comment(db: Session, t: Ticket, body: str, *, author_id=None, author_kind: str = "user", internal: bool = False) -> TicketComment:
    c = TicketComment(org_id=t.org_id, ticket_id=t.id, author_id=author_id, author_kind=author_kind, body=body, internal=internal)
    db.add(c)
    db.flush()
    if author_kind == "user" and author_id and author_id != t.requester_id and not t.first_responded_at and not internal:
        t.first_responded_at = utcnow()
    add_event(db, t, "internal_note" if internal else "comment", actor_kind=author_kind, actor_id=author_id,
              data={"comment_id": str(c.id), "preview": body[:140]})
    if author_kind != "integration":
        from .integrations import on_ticket_event

        on_ticket_event(db, t, "comment", {"comment_id": str(c.id), "body": body, "internal": internal})
    if not internal:
        targets = [u for u in (t.requester_id, t.assignee_id) if u and u != author_id]
        if targets:
            notify(db, t.org_id, targets, "ticket", f"New reply on ticket #{t.number}", body[:400], f"/portal/tickets/{t.id}")
        if author_id and author_id == t.requester_id and t.status == S.AWAITING_CUSTOMER:
            transition(db, t, S.OPEN, actor_kind="system", reason="customer replied")
    return c


def assign(db: Session, t: Ticket, assignee_id: uuid.UUID | None, *, actor_id) -> None:
    prev = t.assignee_id
    t.assignee_id = assignee_id
    add_event(db, t, "assigned", actor_kind="user", actor_id=actor_id, data={"from": str(prev) if prev else None, "to": str(assignee_id) if assignee_id else None})
    if assignee_id:
        notify(db, t.org_id, [assignee_id], "ticket", f"Ticket #{t.number} assigned to you", t.subject, f"/console/tickets/{t.id}")


def find_similar(db: Session, t: Ticket, limit: int = 5) -> list[dict]:
    """Duplicate/related-case suggestions via full-text similarity on subject+description."""
    q = func.plainto_tsquery("english", f"{t.subject} {t.description[:500]}")
    # websearch-like OR semantics: rebuild the tsquery with | between lexemes.
    or_q = func.to_tsquery("english", func.replace(func.cast(q, type_=_text_type()), " & ", " | "))
    rank = func.ts_rank(Ticket.search_tsv, or_q)
    rows = db.execute(
        select(Ticket, rank.label("rank")).where(Ticket.id != t.id, Ticket.search_tsv.op("@@")(or_q))
        .order_by(rank.desc()).limit(limit)
    ).all()
    return [{"id": str(r[0].id), "number": r[0].number, "subject": r[0].subject, "status": r[0].status,
             "resolution_verified": r[0].resolution_verified, "score": round(float(r[1]), 4)} for r in rows]


def _text_type():
    from sqlalchemy import Text

    return Text()
