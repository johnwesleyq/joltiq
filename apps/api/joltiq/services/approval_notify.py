"""Approver notifications (ADR 0020).

* Requested: every *eligible* approver (role can decide it, same tenant, not the person who started the run — SoD)
  gets an in-app bell entry and an email (Mailpit in dev) with risk, expiry and a deep link.
* Reminder: once, `approval_reminder_minutes` (org setting, default 15) before expiry, to eligible approvers.
* Outcome: the requester gets approve/deny/expire, and the ticket timeline gets an event.
Per-user preferences (categories approval / approval_reminder / approval_outcome), a per-org dedupe key per
(kind, approval, user) and a per-user hourly email cap (`approval_email_rate_per_hour`, default 20). Every send,
suppression and skip is audited. Org-level per-event channel switches (`approval_channels`) and Slack fan-out
(services/slack.py) are ADR 0021."""
from __future__ import annotations

import uuid
from datetime import UTC, timedelta

from sqlalchemy import func, select
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

KIND_CATEGORY = {"approval_requested": "approval", "approval_reminder": "approval_reminder", "approval_outcome": "approval_outcome"}
STAFF = (Role.TECHNICIAN, Role.ADMIN, Role.ORG_OWNER)


def _org_setting(db: Session, org_id, key: str, default: int) -> int:
    org = db.get(Organization, org_id)
    try:
        return int(((org.settings if org else {}) or {}).get(key, default))
    except (TypeError, ValueError):
        return default


CHANNELS = ("in_app", "email", "slack", "teams")
SETTINGS_DEFAULTS = {"approval_reminder_minutes": 15, "approval_email_rate_per_hour": 20, "slack_rate_per_hour": 30, "teams_rate_per_hour": 30}


def channel_matrix(db: Session, org_id) -> dict[str, dict[str, bool]]:
    org = db.get(Organization, org_id)
    raw = ((org.settings if org else {}) or {}).get("approval_channels") or {}
    return {k: {c: bool((raw.get(k) or {}).get(c, True)) for c in CHANNELS} for k in KIND_CATEGORY}


def channel_enabled(db: Session, org_id, kind: str, channel: str) -> bool:
    return channel_matrix(db, org_id)[kind][channel]


def requester_id(db: Session, a: Approval) -> uuid.UUID | None:
    if a.run_id:
        run = db.get(AgentRun, a.run_id)
        if run and run.started_by:
            return run.started_by
    if a.ticket_id:
        t = db.get(Ticket, a.ticket_id)
        if t and t.requester_id:
            return t.requester_id
    return None


def eligible_approvers(db: Session, a: Approval) -> list[uuid.UUID]:
    """Mirror approvals.can_decide: listed roles, plus admin/org_owner for technician/admin approvals. Tenant = memberships
    of a.org_id. SoD: the staff member who started the run is never notified to approve their own request."""
    roles = set(a.approver_roles or [])
    staff_roles = {r for r in roles if r in STAFF}
    if staff_roles & {Role.TECHNICIAN, Role.ADMIN}:
        staff_roles |= {Role.ADMIN, Role.ORG_OWNER}
    ids: list[uuid.UUID] = []
    if staff_roles:
        ids = list(db.execute(select(Membership.user_id).where(Membership.org_id == a.org_id, Membership.role.in_(tuple(staff_roles)))).scalars())
        req = requester_id(db, a)
        if req in ids:
            ids.remove(req)
    if "customer" in roles and a.requested_for_user_id:
        ids.append(a.requested_for_user_id)
    return list(dict.fromkeys(ids))


def _link(a: Approval) -> str:
    return f"/approvals/{a.id}"


def _expiry(a: Approval) -> str:
    return a.expires_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def _dispatch(db: Session, a: Approval, uid: uuid.UUID, kind: str, title: str, body: str) -> str:
    """Send one notification to one user once. Returns sent|duplicate|muted."""
    key = f"{kind}:{a.id}:{uid}"
    if db.execute(select(NotificationDispatch.id).where(NotificationDispatch.org_id == a.org_id, NotificationDispatch.dedupe_key == key)).first():
        return "duplicate"
    category = KIND_CATEGORY[kind]
    pref = db.execute(select(NotificationPreference).where(NotificationPreference.user_id == uid,
                                                           NotificationPreference.category == category)).scalar_one_or_none()
    channels: list[str] = []
    suppressed: list[str] = []
    link = _link(a)
    org_on = channel_matrix(db, a.org_id)[kind]
    if not org_on["in_app"]:
        suppressed.append("in_app:org_setting")
    elif pref is None or pref.in_app:
        n = Notification(org_id=a.org_id, user_id=uid, category=category, title=title[:300], body=body, link=link)
        db.add(n)
        db.flush()
        publish(db, a.org_id, "notification", {"id": str(n.id), "title": n.title, "category": category, "link": link}, audience=f"user:{uid}")
        channels.append("in_app")
    else:
        suppressed.append("in_app:preference")
    if not org_on["email"]:
        suppressed.append("email:org_setting")
    elif pref is None or pref.email:
        cap = _org_setting(db, a.org_id, "approval_email_rate_per_hour", 20)
        sent = db.execute(select(func.count()).select_from(NotificationDispatch).where(
            NotificationDispatch.user_id == uid, NotificationDispatch.created_at > utcnow() - timedelta(hours=1),
            NotificationDispatch.channels.contains(["email"]))).scalar() or 0
        u = db.get(User, uid)
        if sent >= cap:
            suppressed.append("email:rate_limited")
        elif u:
            send_email(db, org_id=a.org_id, to=u.email, subject=title,
                       body=f"{body}\n\nOpen: {get_settings().web_origin.rstrip('/')}{link}")
            channels.append("email")
    else:
        suppressed.append("email:preference")
    try:
        with db.begin_nested():
            db.add(NotificationDispatch(org_id=a.org_id, user_id=uid, dedupe_key=key, kind=kind, approval_id=a.id,
                                        channels=channels, suppressed=suppressed))
    except IntegrityError:
        return "duplicate"
    audit.record(db, org_id=a.org_id, actor_kind="system", actor_id=None, action=f"notification.{kind}", resource_type="approval",
                 resource_id=a.id, data={"user_id": str(uid), "channels": channels, "suppressed": suppressed})
    return "sent" if channels else "muted"


def _slack(db: Session, a: Approval, kind: str) -> None:
    """Org-level chat fan-out: Slack (ADR 0021) and Teams (ADR 0022)."""
    if channel_enabled(db, a.org_id, kind, "slack"):
        from .slack import fanout

        fanout(db, a, kind)
    if channel_enabled(db, a.org_id, kind, "teams"):
        from .teams import fanout as teams_fanout

        teams_fanout(db, a, kind)


def _summary(a: Approval) -> str:
    return f"Risk: {a.risk}\nAction: {a.action}\nExpires: {_expiry(a)}\nWhy: {a.reason}"


def on_requested(db: Session, a: Approval) -> list[uuid.UUID]:
    targets = eligible_approvers(db, a)
    if not targets:
        audit.record(db, org_id=a.org_id, actor_kind="system", actor_id=None, action="notification.approval_no_eligible_approver",
                     resource_type="approval", resource_id=a.id, data={"roles": list(a.approver_roles or [])})
    for uid in targets:
        _dispatch(db, a, uid, "approval_requested", f"Approval needed: {a.action} ({a.risk}) — expires {_expiry(a)}", _summary(a))
    _slack(db, a, "approval_requested")
    return targets


def send_reminders(db: Session, now=None) -> int:
    """Remind eligible approvers once per approval, `approval_reminder_minutes` before it expires."""
    now = now or utcnow()
    n = 0
    for a in db.execute(select(Approval).where(Approval.status == A.PENDING, Approval.expires_at > now,
                                               Approval.expires_at < now + timedelta(hours=24))).scalars():
        lead = timedelta(minutes=_org_setting(db, a.org_id, "approval_reminder_minutes", 15))
        if a.expires_at - now > lead or a.expires_at - a.created_at <= lead:
            continue  # not due yet / TTL shorter than the lead (the first notice already said when it expires)
        mins = max(1, int((a.expires_at - now).total_seconds() // 60))
        _slack(db, a, "approval_reminder")
        for uid in eligible_approvers(db, a):
            if _dispatch(db, a, uid, "approval_reminder", f"Reminder: approval expires in {mins} min — {a.action} ({a.risk})",
                         _summary(a)) == "sent":
                n += 1
    return n


def on_outcome(db: Session, a: Approval) -> None:
    """Approve / deny / expire: tell the requester (unless they decided it themselves) and record it on the ticket."""
    if a.decision_comment and a.decision_comment.startswith("inherited from approval"):
        return
    verb = {"APPROVED": "approved", "DENIED": "denied", "EXPIRED": "expired"}.get(str(a.status).split(".")[-1].upper())
    if not verb:
        return
    req = requester_id(db, a)
    if req and req != a.decided_by:
        who = ""
        if a.decided_by:
            u = db.get(User, a.decided_by)
            who = f" by {u.name or u.email}" if u else ""
        body = f"{a.action} ({a.risk}) was {verb}{who}." + (f"\nComment: {a.decision_comment}" if a.decision_comment else "")
        _dispatch(db, a, req, "approval_outcome", f"Approval {verb}: {a.action}", body)
    _slack(db, a, "approval_outcome")
    if a.ticket_id:
        from .tickets import add_event

        t = db.get(Ticket, a.ticket_id)
        if t:
            add_event(db, t, f"approval.{verb}", actor_kind="user" if a.decided_by else "system", actor_id=a.decided_by,
                      data={"approval_id": str(a.id), "action": a.action, "risk": a.risk})
    if a.status == A.EXPIRED:
        audit.record(db, org_id=a.org_id, actor_kind="system", actor_id=None, action="approval.expired", resource_type="approval",
                     resource_id=a.id, data={"action": a.action, "risk": a.risk})
