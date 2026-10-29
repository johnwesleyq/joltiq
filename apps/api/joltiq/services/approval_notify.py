"""Tenant-safe, deduplicated approval alerts, reminders, and outcomes."""
from __future__ import annotations

import uuid
from datetime import UTC, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import (
    AgentRun,
    Approval,
    Membership,
    Notification,
    NotificationDispatch,
    NotificationPreference,
    Organization,
    Ticket,
    User,
    utcnow,
)
from ..models.enums import ApprovalStatus as A
from ..models.enums import Role
from . import audit
from .email import send_email
from .realtime import publish

KIND_CATEGORY = {
    "approval_requested": "approval",
    "approval_reminder": "approval_reminder",
    "approval_outcome": "approval_outcome",
}
CHANNELS = ("in_app", "email", "slack", "teams")
SETTINGS_DEFAULTS = {
    "approval_reminder_minutes": 15,
    "approval_email_rate_per_hour": 20,
    "slack_rate_per_hour": 30,
    "teams_rate_per_hour": 30,
}
STAFF = (Role.TECHNICIAN, Role.ADMIN, Role.ORG_OWNER)


def _org_setting(db: Session, org_id, key: str, default: int) -> int:
    org = db.get(Organization, org_id)
    try:
        return int(((org.settings if org else {}) or {}).get(key, default))
    except (TypeError, ValueError):
        return default


def channel_matrix(db: Session, org_id) -> dict[str, dict[str, bool]]:
    org = db.get(Organization, org_id)
    raw = ((org.settings if org else {}) or {}).get("approval_channels") or {}
    defaults = {"in_app": True, "email": True, "slack": False, "teams": False}
    return {
        kind: {
            channel: bool((raw.get(kind) or {}).get(channel, defaults[channel]))
            for channel in CHANNELS
        }
        for kind in KIND_CATEGORY
    }


def requester_id(db: Session, approval: Approval) -> uuid.UUID | None:
    if approval.run_id:
        run = db.get(AgentRun, approval.run_id)
        if run and run.started_by:
            return run.started_by
    if approval.ticket_id:
        ticket = db.get(Ticket, approval.ticket_id)
        if ticket and ticket.requester_id:
            return ticket.requester_id
    return None


def eligible_approvers(db: Session, approval: Approval) -> list[uuid.UUID]:
    roles = set(approval.approver_roles or [])
    staff_roles = {role for role in roles if role in STAFF}
    if staff_roles & {Role.TECHNICIAN, Role.ADMIN}:
        staff_roles |= {Role.ADMIN, Role.ORG_OWNER}
    ids: list[uuid.UUID] = []
    if staff_roles:
        ids = list(db.execute(
            select(Membership.user_id)
            .join(User, User.id == Membership.user_id)
            .where(
                Membership.org_id == approval.org_id,
                Membership.role.in_(tuple(staff_roles)),
                User.disabled.is_(False),
            )
        ).scalars())
        requester = requester_id(db, approval)
        if requester in ids:
            ids.remove(requester)
    if "customer" in roles and approval.requested_for_user_id:
        user = db.get(User, approval.requested_for_user_id)
        if user and not user.disabled:
            ids.append(approval.requested_for_user_id)
    return list(dict.fromkeys(ids))


def _expiry(approval: Approval) -> str:
    return approval.expires_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def _dispatch(
    db: Session,
    approval: Approval,
    user_id: uuid.UUID,
    kind: str,
    title: str,
    body: str,
) -> str:
    key = f"{kind}:{approval.id}:{user_id}"
    try:
        with db.begin_nested():
            row = NotificationDispatch(
                org_id=approval.org_id,
                user_id=user_id,
                dedupe_key=key,
                kind=kind,
                approval_id=approval.id,
                channels=[],
                suppressed=[],
            )
            db.add(row)
            db.flush()
    except IntegrityError:
        return "duplicate"

    category = KIND_CATEGORY[kind]
    preference = db.execute(select(NotificationPreference).where(
        NotificationPreference.user_id == user_id,
        NotificationPreference.category == category,
    )).scalar_one_or_none()
    enabled = channel_matrix(db, approval.org_id)[kind]
    channels: list[str] = []
    suppressed: list[str] = []
    link = f"/approvals/{approval.id}"
    if enabled["in_app"] and (preference is None or preference.in_app):
        notification = Notification(
            org_id=approval.org_id,
            user_id=user_id,
            category=category,
            title=title[:300],
            body=body,
            link=link,
        )
        db.add(notification)
        db.flush()
        publish(
            db,
            approval.org_id,
            "notification",
            {"id": str(notification.id), "title": notification.title, "category": category, "link": link},
            audience=f"user:{user_id}",
        )
        channels.append("in_app")
    else:
        suppressed.append("in_app:preference" if enabled["in_app"] else "in_app:org_setting")
    if enabled["email"] and (preference is None or preference.email):
        user = db.get(User, user_id)
        if user:
            try:
                send_email(
                    db,
                    org_id=approval.org_id,
                    to=user.email,
                    subject=title,
                    body=f"{body}\n\nOpen: {get_settings().web_origin.rstrip('/')}{link}",
                )
                channels.append("email")
            except Exception as exc:  # noqa: BLE001
                suppressed.append(f"email:error:{type(exc).__name__}")
    else:
        suppressed.append("email:preference" if enabled["email"] else "email:org_setting")
    row.channels = channels
    row.suppressed = suppressed
    audit.record(
        db,
        org_id=approval.org_id,
        actor_kind="system",
        actor_id=None,
        action=f"notification.{kind}",
        resource_type="approval",
        resource_id=approval.id,
        data={"user_id": str(user_id), "channels": channels, "suppressed": suppressed},
    )
    return "sent" if channels else "muted"


def _summary(approval: Approval) -> str:
    return (
        f"Risk: {approval.risk}\nAction: {approval.action}\n"
        f"Expires: {_expiry(approval)}\nWhy: {approval.reason}"
    )


def on_requested(db: Session, approval: Approval) -> list[uuid.UUID]:
    targets = eligible_approvers(db, approval)
    if not targets:
        audit.record(
            db,
            org_id=approval.org_id,
            actor_kind="system",
            actor_id=None,
            action="notification.approval_no_eligible_approver",
            resource_type="approval",
            resource_id=approval.id,
            data={"roles": list(approval.approver_roles or [])},
        )
    for user_id in targets:
        _dispatch(
            db,
            approval,
            user_id,
            "approval_requested",
            f"Approval needed: {approval.action} ({approval.risk}) — expires {_expiry(approval)}",
            _summary(approval),
        )
    return targets


def send_reminders(db: Session, now=None) -> int:
    now = now or utcnow()
    sent = 0
    approvals = db.execute(select(Approval).where(
        Approval.status == A.PENDING,
        Approval.expires_at > now,
        Approval.expires_at < now + timedelta(hours=24),
    )).scalars()
    for approval in approvals:
        lead = timedelta(minutes=_org_setting(
            db,
            approval.org_id,
            "approval_reminder_minutes",
            SETTINGS_DEFAULTS["approval_reminder_minutes"],
        ))
        if approval.expires_at - now > lead or approval.expires_at - approval.created_at <= lead:
            continue
        minutes = max(1, int((approval.expires_at - now).total_seconds() // 60))
        for user_id in eligible_approvers(db, approval):
            if _dispatch(
                db,
                approval,
                user_id,
                "approval_reminder",
                f"Reminder: approval expires in {minutes} min — {approval.action}",
                _summary(approval),
            ) == "sent":
                sent += 1
    return sent


def on_outcome(db: Session, approval: Approval) -> None:
    if approval.decision_comment and approval.decision_comment.startswith("inherited from approval"):
        return
    verb = {A.APPROVED: "approved", A.DENIED: "denied", A.EXPIRED: "expired"}.get(approval.status)
    if not verb:
        return
    requester = requester_id(db, approval)
    if requester and requester != approval.decided_by:
        _dispatch(
            db,
            approval,
            requester,
            "approval_outcome",
            f"Approval {verb}: {approval.action}",
            f"{approval.action} ({approval.risk}) was {verb}."
            + (f"\nComment: {approval.decision_comment}" if approval.decision_comment else ""),
        )
    if approval.ticket_id:
        from .tickets import add_event

        ticket = db.get(Ticket, approval.ticket_id)
        if ticket:
            add_event(
                db,
                ticket,
                f"approval.{verb}",
                actor_kind="user" if approval.decided_by else "system",
                actor_id=approval.decided_by,
                data={"approval_id": str(approval.id), "action": approval.action, "risk": approval.risk},
            )
    if approval.status == A.EXPIRED:
        audit.record(
            db,
            org_id=approval.org_id,
            actor_kind="system",
            actor_id=None,
            action="approval.expired",
            resource_type="approval",
            resource_id=approval.id,
            data={"action": approval.action, "risk": approval.risk},
        )
