"""Approver notifications (ADR 0020): eligibility (roles, SoD, tenant), email with risk/expiry/deep link, dedupe,
preferences, rate limit, reminders before expiry, outcome notices to the requester + ticket timeline, audit events."""
import uuid
from datetime import timedelta

from conftest import invite_and_join, register
from sqlalchemy import select


def _uid(c):
    return uuid.UUID(c.me["user"]["id"])


def _request(owner, started_by=None, roles=("technician", "admin", "org_owner"), risk="SENSITIVE"):
    from joltiq.db import session_scope
    from joltiq.models import AgentPlan, AgentRun, PlanStep
    from joltiq.services import approvals, tickets

    org = uuid.UUID(owner.org_id)
    with session_scope(org) as db:
        t = tickets.create(db, org, subject="Notify approvers", description="x", requester_id=started_by)
        run = AgentRun(org_id=org, ticket_id=t.id, state="AWAITING_APPROVAL", correlation_id="x", context={}, started_by=started_by)
        db.add(run)
        db.flush()
        plan = AgentPlan(org_id=org, run_id=run.id, summary="save", plan_hash="h" * 64, status="proposed")
        db.add(plan)
        db.flush()
        st = PlanStep(org_id=org, plan_id=plan.id, ordinal=0, action="browser.submit", target={}, params={"selector": "#save"},
                      risk=risk, postconditions=[])
        db.add(st)
        db.flush()
        a = approvals.request(db, org_id=org, run_id=run.id, plan_id=plan.id, step=st, plan_hash=plan.plan_hash, ticket_id=t.id,
                              session_id=None, reason="save the form", effects="saves", approver_roles=list(roles))
        return a.id, t.id


def _notes(org, uid, aid=None):
    from joltiq.db import session_scope
    from joltiq.models import Notification

    with session_scope(uuid.UUID(org)) as db:
        q = select(Notification).where(Notification.user_id == uid)
        if aid:
            q = q.where(Notification.link == f"/approvals/{aid}")
        return [(n.category, n.title, n.body) for n in db.execute(q).scalars()]


def _emails(org, to):
    from joltiq.db import session_scope
    from joltiq.models import Delivery

    with session_scope(uuid.UUID(org)) as db:
        return [(d.subject, d.payload["body"]) for d in db.execute(select(Delivery).where(Delivery.channel == "email", Delivery.target == to)).scalars()]


def _audit(org, action):
    from joltiq.db import session_scope
    from joltiq.models import AuditEvent

    with session_scope(uuid.UUID(org)) as db:
        return [e.data for e in db.execute(select(AuditEvent).where(AuditEvent.action == action)).scalars()]


def test_only_eligible_approvers_are_notified_with_risk_expiry_and_link(app, owner, new_client):
    tech = invite_and_join(app, owner, "technician")
    admin = invite_and_join(app, owner, "admin")
    auditor = invite_and_join(app, owner, "auditor")
    other = register(new_client(), "Other MSP")
    aid, _ = _request(owner, started_by=_uid(tech))
    # SoD: the technician who started the run is not asked to approve it; auditors can't decide; other tenants never see it.
    assert _notes(owner.org_id, _uid(owner), aid) and _notes(owner.org_id, _uid(admin), aid)
    assert not _notes(owner.org_id, _uid(tech), aid)
    assert not _notes(owner.org_id, _uid(auditor), aid)
    assert not _notes(other.org_id, _uid(other))
    cat, title, body = _notes(owner.org_id, _uid(owner), aid)[0]
    assert cat == "approval" and "SENSITIVE" in title and "expires" in title and "Expires:" in body
    (subject, mail), = [m for m in _emails(owner.org_id, owner.email) if "Approval needed" in m[0]]
    assert "Risk: SENSITIVE" in mail and f"/approvals/{aid}" in mail and "UTC" in mail
    assert len(_audit(owner.org_id, "notification.approval_requested")) == 2
    # Bell API shows it too.
    items = owner.get("/api/v1/notifications").json()["items"]
    assert any(i["link"] == f"/approvals/{aid}" for i in items)


def test_dedupe_preferences_and_rate_limit(app, owner):
    from joltiq.db import session_scope
    from joltiq.models import Approval, Organization
    from joltiq.services import approval_notify

    aid, _ = _request(owner)
    with session_scope(uuid.UUID(owner.org_id)) as db:
        approval_notify.on_requested(db, db.get(Approval, aid))  # retried event → no duplicate
    assert len(_notes(owner.org_id, _uid(owner), aid)) == 1
    assert len([m for m in _emails(owner.org_id, owner.email) if "Approval needed" in m[0]]) == 1

    assert owner.put("/api/v1/notifications/preferences", json={"category": "approval", "in_app": True, "email": False}).status_code == 200
    prefs = {p["category"] for p in owner.get("/api/v1/notifications/preferences").json()}
    assert {"approval", "approval_reminder", "approval_outcome"} <= prefs
    aid2, _ = _request(owner)
    assert _notes(owner.org_id, _uid(owner), aid2)
    assert len([m for m in _emails(owner.org_id, owner.email) if "Approval needed" in m[0]]) == 1
    assert any("email:preference" in d["suppressed"] for d in _audit(owner.org_id, "notification.approval_requested"))

    owner.put("/api/v1/notifications/preferences", json={"category": "approval", "in_app": True, "email": True})
    with session_scope(uuid.UUID(owner.org_id)) as db:
        org = db.get(Organization, uuid.UUID(owner.org_id))
        org.settings = {**(org.settings or {}), "approval_email_rate_per_hour": 1}
    _request(owner)
    assert len([m for m in _emails(owner.org_id, owner.email) if "Approval needed" in m[0]]) == 1
    assert any("email:rate_limited" in d["suppressed"] for d in _audit(owner.org_id, "notification.approval_requested"))


def test_reminder_once_before_expiry(app, owner):
    from joltiq.db import session_scope
    from joltiq.models import Approval, utcnow
    from joltiq.services import approval_notify

    aid, _ = _request(owner)
    with session_scope(uuid.UUID(owner.org_id)) as db:
        assert approval_notify.send_reminders(db) == 0  # 60 min TTL, not due yet
        a = db.get(Approval, aid)
        a.expires_at, a.created_at = utcnow() + timedelta(minutes=10), utcnow() - timedelta(minutes=50)
    with session_scope(uuid.UUID(owner.org_id)) as db:
        assert approval_notify.send_reminders(db) >= 1
    with session_scope(uuid.UUID(owner.org_id)) as db:
        assert approval_notify.send_reminders(db) == 0
    rem = [n for n in _notes(owner.org_id, _uid(owner), aid) if n[0] == "approval_reminder"]
    assert len(rem) == 1 and "expires in" in rem[0][1]
    assert any("Reminder" in s for s, _ in _emails(owner.org_id, owner.email))


def test_requester_and_ticket_hear_the_outcome(app, owner):
    from joltiq.db import session_scope
    from joltiq.models import Approval, TicketEvent, utcnow
    from joltiq.services import approvals

    tech = invite_and_join(app, owner, "technician")
    aid, tid = _request(owner, started_by=_uid(tech))
    assert owner.post(f"/api/v1/approvals/{aid}/decision", json={"decision": "deny", "comment": "not now"}).status_code == 200
    out = [n for n in _notes(owner.org_id, _uid(tech), aid) if n[0] == "approval_outcome"]
    assert len(out) == 1 and "denied" in out[0][1] and "not now" in out[0][2]

    aid2, tid2 = _request(owner, started_by=_uid(tech))
    with session_scope(uuid.UUID(owner.org_id)) as db:
        db.get(Approval, aid2).expires_at = utcnow() - timedelta(minutes=1)
    with session_scope(uuid.UUID(owner.org_id)) as db:
        assert approvals.expire_stale(db) >= 1
        kinds = {e.kind for e in db.execute(select(TicketEvent).where(TicketEvent.ticket_id.in_([tid, tid2]))).scalars()}
    assert {"approval.denied", "approval.expired"} <= kinds
    assert any("expired" in n[1] for n in _notes(owner.org_id, _uid(tech), aid2) if n[0] == "approval_outcome")
    assert _audit(owner.org_id, "approval.expired")
    # The decider is not notified about their own decision.
    assert not [n for n in _notes(owner.org_id, _uid(owner), aid) if n[0] == "approval_outcome"]
