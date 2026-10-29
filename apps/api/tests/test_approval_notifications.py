"""Approval notification eligibility, dedupe, reminders, outcomes, and settings."""
from __future__ import annotations

import uuid
from datetime import timedelta

from conftest import invite_and_join, register
from sqlalchemy import select


def _uid(client):
    return uuid.UUID(client.me["user"]["id"])


def _request(owner, *, started_by=None, roles=("technician", "admin", "org_owner")):
    from joltiq.db import session_scope
    from joltiq.models import AgentPlan, AgentRun, PlanStep
    from joltiq.services import approvals, tickets

    org_id = uuid.UUID(owner.org_id)
    with session_scope(org_id) as db:
        ticket = tickets.create(db, org_id, subject="Notify approvers", description="x", requester_id=started_by)
        run = AgentRun(
            org_id=org_id,
            ticket_id=ticket.id,
            state="AWAITING_APPROVAL",
            correlation_id=uuid.uuid4().hex,
            context={},
            started_by=started_by,
        )
        db.add(run)
        db.flush()
        plan = AgentPlan(org_id=org_id, run_id=run.id, summary="save", plan_hash="h" * 64)
        db.add(plan)
        db.flush()
        step = PlanStep(
            org_id=org_id,
            plan_id=plan.id,
            ordinal=0,
            action="browser.submit",
            target={},
            params={"selector": "#save"},
            risk="SENSITIVE",
            postconditions=[],
        )
        db.add(step)
        db.flush()
        approval = approvals.request(
            db,
            org_id=org_id,
            run_id=run.id,
            plan_id=plan.id,
            step=step,
            plan_hash=plan.plan_hash,
            ticket_id=ticket.id,
            session_id=None,
            reason="save the form",
            effects="saves",
            approver_roles=list(roles),
        )
        return approval.id, ticket.id


def _notifications(org_id, user_id, approval_id=None):
    from joltiq.db import session_scope
    from joltiq.models import Notification

    with session_scope(uuid.UUID(org_id)) as db:
        query = select(Notification).where(Notification.user_id == user_id)
        if approval_id:
            query = query.where(Notification.link == f"/approvals/{approval_id}")
        return db.execute(query).scalars().all()


def test_eligible_roles_exclude_requester_disabled_user_and_other_tenant(app, owner, new_client):
    from joltiq.db import session_scope
    from joltiq.models import User

    technician = invite_and_join(app, owner, "technician")
    admin = invite_and_join(app, owner, "admin")
    auditor = invite_and_join(app, owner, "auditor")
    disabled = invite_and_join(app, owner, "admin")
    with session_scope(uuid.UUID(owner.org_id)) as db:
        db.get(User, _uid(disabled)).disabled = True
    other = register(new_client(), "Other tenant")
    approval_id, _ = _request(owner, started_by=_uid(technician))
    assert _notifications(owner.org_id, _uid(owner), approval_id)
    assert _notifications(owner.org_id, _uid(admin), approval_id)
    assert not _notifications(owner.org_id, _uid(technician), approval_id)
    assert not _notifications(owner.org_id, _uid(auditor), approval_id)
    assert not _notifications(owner.org_id, _uid(disabled), approval_id)
    assert not _notifications(other.org_id, _uid(other))


def test_request_dispatch_is_deduplicated_and_preferences_suppress_email(app, owner):
    from joltiq.db import session_scope
    from joltiq.models import Approval, Delivery, NotificationDispatch
    from joltiq.services import approval_notify

    approval_id, _ = _request(owner)
    with session_scope(uuid.UUID(owner.org_id)) as db:
        approval_notify.on_requested(db, db.get(Approval, approval_id))
        dispatches = db.execute(select(NotificationDispatch).where(NotificationDispatch.approval_id == approval_id)).scalars().all()
        assert len(dispatches) == len({dispatch.dedupe_key for dispatch in dispatches})
    assert len(_notifications(owner.org_id, _uid(owner), approval_id)) == 1
    owner.put("/api/v1/notifications/preferences", json={"category": "approval", "in_app": True, "email": False})
    second_id, _ = _request(owner)
    with session_scope(uuid.UUID(owner.org_id)) as db:
        emails = db.execute(select(Delivery).where(Delivery.channel == "email")).scalars().all()
        assert all(str(second_id) not in str(delivery.payload) for delivery in emails)


def test_reminder_timing_once_and_expiry_outcome(app, owner):
    from joltiq.db import session_scope
    from joltiq.models import Approval, utcnow
    from joltiq.services import approval_notify, approvals

    approval_id, _ = _request(owner)
    with session_scope(uuid.UUID(owner.org_id)) as db:
        assert approval_notify.send_reminders(db) == 0
        approval = db.get(Approval, approval_id)
        approval.expires_at = utcnow() + timedelta(minutes=10)
        approval.created_at = utcnow() - timedelta(minutes=50)
    with session_scope(uuid.UUID(owner.org_id)) as db:
        assert approval_notify.send_reminders(db) >= 1
    with session_scope(uuid.UUID(owner.org_id)) as db:
        assert approval_notify.send_reminders(db) == 0
        db.get(Approval, approval_id).expires_at = utcnow() - timedelta(seconds=1)
    with session_scope(uuid.UUID(owner.org_id)) as db:
        assert approvals.expire_stale(db) >= 1


def test_no_recipient_is_audited_without_external_call(app, owner, monkeypatch):
    from joltiq.db import session_scope
    from joltiq.models import AuditEvent
    from joltiq.services import email

    called = []
    monkeypatch.setattr(email, "send_email", lambda *args, **kwargs: called.append(True))
    approval_id, _ = _request(owner, roles=("auditor",))
    with session_scope(uuid.UUID(owner.org_id)) as db:
        events = db.execute(
            select(AuditEvent).where(AuditEvent.action == "notification.approval_no_eligible_approver")
        ).scalars().all()
        assert any(str(event.resource_id) == str(approval_id) for event in events)
    assert called == []


def test_approval_notification_settings_validate_and_round_trip(app, owner):
    path = "/api/v1/admin/approval-notifications"
    response = owner.get(path)
    assert response.status_code == 200
    body = response.json()
    assert body["channels"]["approval_requested"]["slack"] is False
    invalid = {**body, "reminder_minutes": 0}
    assert owner.put(path, json=invalid).status_code == 422
    channels = body["channels"]
    channels["approval_requested"] = {**channels["approval_requested"], "in_app": False, "email": False}
    invalid = {**body, "channels": channels}
    assert owner.put(path, json=invalid).status_code == 400


def test_dispatch_unique_key_is_final_two_worker_race_barrier(app, owner):
    from sqlalchemy.exc import IntegrityError

    from joltiq.db import session_scope
    from joltiq.models import NotificationDispatch

    approval_id, _ = _request(owner)
    org_id = uuid.UUID(owner.org_id)
    key = f"race:{approval_id}:{_uid(owner)}"
    with session_scope(org_id) as db:
        db.add(NotificationDispatch(org_id=org_id, user_id=_uid(owner), approval_id=approval_id, dedupe_key=key, kind="approval_requested"))
    try:
        with session_scope(org_id) as db:
            db.add(NotificationDispatch(org_id=org_id, user_id=_uid(owner), approval_id=approval_id, dedupe_key=key, kind="approval_requested"))
            db.flush()
    except IntegrityError:
        pass
    else:
        raise AssertionError("duplicate dispatch escaped the database race barrier")
