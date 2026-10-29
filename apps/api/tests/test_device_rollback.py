"""Device rollback binds only to authenticated companion-local journal entries."""
from __future__ import annotations

import uuid

CHANGE = {
    "action": "office.addin.set_load_behavior",
    "params": {"app": "Outlook", "addin_progid": "PdfPreview.Connect", "enabled": False},
}
RESTART = {"action": "app.restart", "params": {"process": "outlook.exe"}}


def _device_plan(owner, *, device_result: dict):
    from joltiq.db import session_scope
    from joltiq.models import ActionExecution, AgentPlan, AgentRun, PlanStep, utcnow
    from joltiq.services import rollback

    org_id = uuid.UUID(owner.org_id)
    context = session_scope(org_id)
    db = context.__enter__()
    run = AgentRun(org_id=org_id, correlation_id=uuid.uuid4().hex, context={})
    db.add(run)
    db.flush()
    rollback_steps, availability = rollback.derive([CHANGE, RESTART], "desktop")
    base_hash = "c" * 64
    plan = AgentPlan(
        org_id=org_id,
        run_id=run.id,
        revision=1,
        summary="approved device change",
        plan_hash=rollback.plan_hash(base_hash, rollback_steps, availability),
        rollback_steps=rollback_steps,
        rollback_availability=availability,
    )
    db.add(plan)
    db.flush()
    change = PlanStep(
        org_id=org_id,
        plan_id=plan.id,
        ordinal=0,
        action=CHANGE["action"],
        target={},
        params=CHANGE["params"],
        rationale="test",
        risk="SENSITIVE",
        postconditions=[],
        status="done",
    )
    restart = PlanStep(
        org_id=org_id,
        plan_id=plan.id,
        ordinal=1,
        action=RESTART["action"],
        target={},
        params=RESTART["params"],
        rationale="test",
        risk="SENSITIVE",
        postconditions=[],
        status="done",
    )
    db.add_all([change, restart])
    db.flush()
    execution = ActionExecution(
        org_id=org_id,
        run_id=run.id,
        step_id=change.id,
        action=CHANGE["action"],
        target={},
        params=CHANGE["params"],
        risk="SENSITIVE",
        policy_decision={},
        status="succeeded",
        idempotency_key=uuid.uuid4().hex,
        result={"command_id": "change-123", "device": device_result},
        finished_at=utcnow(),
    )
    db.add(execution)
    db.flush()
    return context, db, run, plan, execution


def test_device_rollback_resolves_only_local_journal_reference(owner):
    from joltiq.services import rollback

    context, db, run, plan, execution = _device_plan(
        owner,
        device_result={
            "source_command_id": "change-123",
            "before": {"exists": True, "kind": "DWord", "value": 3},
            "after": {"exists": True, "kind": "DWord", "value": 2},
            "enrolled_user_sid": "S-1-5-21-100",
            "interactive_user_sid": "S-1-5-21-100",
        },
    )
    try:
        steps, snapshot, problem = rollback.resolve_device_steps(db, run, plan)
        assert problem == ""
        assert steps[0]["params"] == {"source_command_id": "change-123"}
        assert "before" not in repr([step["params"] for step in steps])
        assert snapshot["s0"]["before"]["value"] == 3
        assert execution.result["device"]["before"]["value"] == 3
    finally:
        context.__exit__(None, None, None)


def test_device_rollback_refuses_missing_local_journal(owner):
    from joltiq.services import rollback

    context, db, run, plan, _ = _device_plan(
        owner,
        device_result={"source_command_id": "change-123", "after": {"value": 2}},
    )
    try:
        steps, _, problem = rollback.resolve_device_steps(db, run, plan)
        assert steps == []
        assert "pre-change" in problem
    finally:
        context.__exit__(None, None, None)


def test_device_rollback_refuses_enrolled_user_mismatch(owner):
    from joltiq.services import rollback

    context, db, run, plan, _ = _device_plan(
        owner,
        device_result={
            "source_command_id": "change-123",
            "before": {"value": 3},
            "after": {"value": 2},
            "enrolled_user_sid": "S-1-5-21-100",
            "interactive_user_sid": "S-1-5-21-999",
        },
    )
    try:
        steps, _, problem = rollback.resolve_device_steps(db, run, plan)
        assert steps == []
        assert "user" in problem.lower() and "match" in problem.lower()
    finally:
        context.__exit__(None, None, None)


def test_screen_control_fix_discloses_no_safe_rollback_before_approval():
    from joltiq.services.computer_use import rollback_marker

    fix = rollback_marker("Uninstall Adobe Reader", "fix")
    guide = rollback_marker("Show me how to uninstall Adobe Reader", "guide")
    assert fix["status"] == "NO_ROLLBACK_AVAILABLE"
    assert guide["status"] == "NOT_NEEDED"
    assert len(fix["approval_hash"]) == 64
    assert fix["approval_hash"] != rollback_marker("Uninstall a different app", "fix")["approval_hash"]
