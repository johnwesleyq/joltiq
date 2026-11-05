"""ADR 0019: server-derived device inverse bound into the plan hash, auto rollback through ROLLING_BACK,
NO_ROLLBACK_AVAILABLE up front, and computer-use rollback markers (mocked provider, no keys)."""
from __future__ import annotations

import uuid

from sqlalchemy import select

from joltiq.services import rollback

CHANGE = {"action": "office.addin.set_load_behavior", "params": {"app": "Outlook", "addin_progid": "PdfPreview.Connect", "enabled": False}}
RESTART = {"action": "app.restart", "params": {"process": "outlook.exe"}}


def test_device_inverse_is_derived_from_journal_not_server_state():
    rb = rollback.derive_device_rollback([CHANGE, RESTART])
    assert [s["action"] for s in rb] == ["office.addin.restore_load_behavior", "app.restart"]
    assert rb[0]["params"] == {"source_command_id": {"$cmd": "s0"}}
    assert rb[0]["pre"]["source"] == "companion_journal"
    assert "value" not in repr(rb) and "$cmd" in repr(rb[0]["params"])
    rb2, av = rollback.derive([CHANGE, RESTART], "desktop")
    assert rb2 == rb and av["status"] == rollback.AVAILABLE


def test_actions_without_inverse_are_marked_up_front_and_hashed():
    svc = {"action": "service.restart", "params": {"name": "Spooler"}}
    rb, av = rollback.derive([CHANGE, svc], "desktop")
    assert rb == [] and av["status"] == rollback.NO_ROLLBACK_AVAILABLE and "service.restart" in av["actions"]
    _, ro = rollback.derive([RESTART], "desktop")
    assert ro["status"] == rollback.NOT_NEEDED
    base = "b" * 64
    h = rollback.plan_hash(base, [], av)
    assert h != base  # the "no rollback" fact itself is approved
    assert rollback.plan_hash(base, [], {**av, "status": rollback.NOT_NEEDED}) != h
    rbd, avd = rollback.derive([CHANGE, RESTART], "desktop")
    assert rollback.plan_hash(base, rbd, avd) != rollback.plan_hash(base, rbd[:1], avd)


def _uid(owner):
    return uuid.UUID(owner.me["user"]["id"])


def _device_run(owner, *, approve=True, before=True):
    """An executed device change with a server-derived rollback approved by the plan hash."""
    from test_procedures import outlook_body

    from joltiq.models import (
        ActionExecution,
        AgentPlan,
        AgentRun,
        Approval,
        Device,
        KnowledgeArticle,
        PlanStep,
        SupportSession,
        Ticket,
        utcnow,
    )
    from joltiq.services.procedures import procedure_hash, validate_executable_body

    org_id = uuid.UUID(owner.org_id)
    user_id = _uid(owner)
    from joltiq.db import session_scope

    db_cm = session_scope(org_id)
    db = db_cm.__enter__()
    body = outlook_body()
    article = KnowledgeArticle(org_id=org_id, title="Outlook fix", status="published", executable=True, body=body,
                               procedure_hash=procedure_hash(validate_executable_body(body)), version=1)
    device = Device(org_id=org_id, owner_user_id=user_id, hostname="WIN11-RB", status="active",
                    capabilities=["office.addin.set_load_behavior", "office.addin.restore_load_behavior",
                                  "office.addin.verify_restored", "app.restart"])
    db.add_all([article, device])
    db.flush()
    ticket = Ticket(org_id=org_id, number=880000 + uuid.uuid4().int % 10000, subject="Outlook crash", description="x",
                    requester_id=user_id, status="IN_PROGRESS", device_id=device.id)
    db.add(ticket)
    db.flush()
    run = AgentRun(org_id=org_id, ticket_id=ticket.id, state="VERIFYING", correlation_id="auto-rb", started_by=user_id,
                   context={"evidence": []})
    db.add(run)
    db.flush()
    session = SupportSession(org_id=org_id, kind="desktop", state="active", owner_id=user_id, ticket_id=ticket.id,
                             device_id=device.id, run_id=run.id, consent={"max_risk": "SENSITIVE", "device_local_consent": True})
    db.add(session)
    db.flush()
    rb, av = rollback.derive([CHANGE, RESTART], "desktop")
    base = "c" * 64
    ph = rollback.plan_hash(base, rb, av)
    plan = AgentPlan(org_id=org_id, run_id=run.id, revision=1, summary="fix", hypothesis="h", plan_hash=ph, status="executing",
                     procedure_article_id=article.id, procedure_version=1, procedure_hash=article.procedure_hash,
                     rollback_steps=rb, rollback_availability=av)
    db.add(plan)
    db.flush()
    run.context = {**run.context, "current_plan_id": str(plan.id), "rollback_base_hashes": {str(plan.id): base}}
    target = {"session_id": str(session.id), "device_id": str(device.id)}
    s0 = PlanStep(org_id=org_id, plan_id=plan.id, ordinal=0, action=CHANGE["action"], target=target, params=CHANGE["params"],
                  rationale="r", risk="SENSITIVE", postconditions=[], status="done")
    s1 = PlanStep(org_id=org_id, plan_id=plan.id, ordinal=1, action="app.restart", target=target, params=RESTART["params"],
                  rationale="r", risk="SENSITIVE", postconditions=[], status="done")
    db.add_all([s0, s1])
    db.flush()
    from joltiq.services import approvals as approval_svc

    a = approval_svc.request_each_customer_step(db, run=run, plan=plan, steps=[s0, s1], ticket=ticket, session_id=session.id)
    if approve:
        for ap in db.execute(select(Approval).where(Approval.plan_id == plan.id)).scalars():
            approval_svc.decide(db, ap.id, approve=True, user_id=user_id, role="customer", customer_id=None)
    dev = {"ok": True, "changed": True, "after": {"exists": True, "kind": "DWord", "value": 2}}
    if before:
        dev["before"] = {"exists": True, "kind": "DWord", "value": 3}
    ex = ActionExecution(org_id=org_id, run_id=run.id, step_id=s0.id, session_id=session.id, action=CHANGE["action"],
                         target=target, params=CHANGE["params"], risk="SENSITIVE", policy_decision={}, status="succeeded",
                         idempotency_key=f"rb-{uuid.uuid4()}", result={"command_id": "chg-1", "device": dev}, finished_at=utcnow())
    db.add(ex)
    db.flush()
    del a
    return db_cm, db, run, ex


def test_auto_rollback_executes_inverse_approved_with_plan_hash(owner):
    from joltiq.models import AgentPlan, Approval, PlanStep
    from joltiq.services.agent import offer_rollback

    cm, db, run, ex = _device_run(owner)
    try:
        plan = offer_rollback(db, run, ex)
        assert run.state == "ROLLING_BACK" and run.context["rollback_auto"] is True
        steps = db.execute(select(PlanStep).where(PlanStep.plan_id == plan.id).order_by(PlanStep.ordinal)).scalars().all()
        assert [(s.action, s.params) for s in steps] == [
            ("office.addin.restore_load_behavior", {"source_command_id": "chg-1"}), ("app.restart", {"process": "outlook.exe"})]
        aps = db.execute(select(Approval).where(Approval.plan_id == plan.id)).scalars().all()
        assert len(aps) == 2 and all(a.status == "APPROVED" and a.plan_hash == plan.plan_hash and a.decided_by for a in aps)
        assert run.context["rollback_pre"]["s0"]["before"]["value"] == 3
        assert db.get(AgentPlan, plan.id).status == "approved"
    finally:
        cm.__exit__(None, None, None)


def test_auto_rollback_blocked_without_valid_approval_falls_back(owner):
    from joltiq.models import Approval
    from joltiq.services.agent import offer_rollback

    cm, db, run, ex = _device_run(owner, approve=False)
    try:
        plan = offer_rollback(db, run, ex)
        assert run.state == "AWAITING_APPROVAL" and "rollback_auto" not in run.context
        assert run.context["rollback_auto_blocked"]
        aps = db.execute(select(Approval).where(Approval.plan_id == plan.id)).scalars().all()
        assert aps and all(a.status == "PENDING" for a in aps)
    finally:
        cm.__exit__(None, None, None)


def test_auto_rollback_needs_device_pre_change_readback(owner):
    from joltiq.services.agent import offer_rollback

    cm, db, run, ex = _device_run(owner, before=False)
    try:
        offer_rollback(db, run, ex)
        assert run.state == "AWAITING_APPROVAL" and "pre-change" in run.context["rollback_auto_blocked"]
    finally:
        cm.__exit__(None, None, None)


def test_rollback_outcome_codes_are_recorded(owner):
    from joltiq.services.agent import _finish_rollback, _rollback_failed, offer_rollback

    for kind in ("verified", "unverified", "failed", "blocked"):
        cm, db, run, ex = _device_run(owner)
        try:
            offer_rollback(db, run, ex)
            if kind == "blocked":
                _rollback_failed(db, run, "session ended", rollback.ROLLBACK_BLOCKED)
                assert run.context["rollback"]["status"] == rollback.ROLLBACK_BLOCKED
            elif kind == "failed":
                _rollback_failed(db, run, "restore failed")
                assert run.context["rollback"]["status"] == rollback.ROLLBACK_FAILED
            else:
                _finish_rollback(db, run, restore_verified=kind == "verified", restart_completed=True,
                                 **({"unverified": True} if kind == "unverified" else {}))
                assert run.context["rollback"]["status"] == (rollback.ROLLED_BACK if kind == "verified" else rollback.ROLLBACK_UNVERIFIED)
            assert run.state == "ESCALATED" and run.context["rollback"]["auto"] is True
        finally:
            cm.__exit__(None, None, None)
