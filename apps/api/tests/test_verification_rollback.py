"""Independent verification and server-derived rollback safety contracts."""
from __future__ import annotations

import uuid

from sqlalchemy import text

from joltiq.services import verification
from joltiq.services.verification import V

BROWSER_STEPS = [
    {"action": "browser.navigate", "params": {"url": "http://app.test/settings"}},
    {"action": "browser.type", "params": {"selector": "testid=email", "text": "new@example.test"}},
    {"action": "browser.select", "params": {"selector": "testid=jobs", "value": "on"}},
    {"action": "browser.submit", "params": {"selector": 'role=button[name="Save"]'}},
]
DEVICE_CHANGE = {
    "action": "office.addin.set_load_behavior",
    "params": {"app": "Outlook", "addin_progid": "PdfPreview.Connect", "enabled": False},
}
DEVICE_RESTART = {"action": "app.restart", "params": {"process": "outlook.exe"}}


def test_browser_rollback_is_deterministic_and_bound_to_exact_plan_hash():
    from joltiq.services import rollback

    first, availability = rollback.derive(BROWSER_STEPS, "browser")
    second, second_availability = rollback.derive(BROWSER_STEPS, "browser")
    assert first == second
    assert availability == second_availability == {"status": rollback.AVAILABLE, "channel": "browser", "steps": 4}
    assert first[1]["params"]["text"] == {"$pre": "s1"}
    assert first[2]["params"]["value"] == {"$pre": "s2"}

    base = "a" * 64
    approved_hash = rollback.plan_hash(base, first, availability)
    assert approved_hash != base
    tampered = [dict(step) for step in first]
    tampered[-1] = {"action": "browser.submit", "params": {"selector": "#delete"}}
    assert rollback.plan_hash(base, tampered, availability) != approved_hash


def test_device_rollback_refers_only_to_local_journal_entry():
    from joltiq.services import rollback

    steps, availability = rollback.derive([DEVICE_CHANGE, DEVICE_RESTART], "desktop")
    assert availability["status"] == rollback.AVAILABLE
    assert [step["action"] for step in steps] == ["office.addin.restore_load_behavior", "app.restart"]
    assert steps[0]["params"] == {"source_command_id": {"$cmd": "s0"}}
    assert steps[0]["pre"]["source"] == "companion_journal"
    assert "before" not in steps[0]["params"]
    assert "old_value" not in steps[0]["params"]


def test_unsupported_action_is_marked_no_rollback_before_approval():
    from joltiq.services import rollback

    steps, availability = rollback.derive(
        [DEVICE_CHANGE, {"action": "service.restart", "params": {"name": "Spooler"}}],
        "desktop",
    )
    assert steps == []
    assert availability["status"] == rollback.NO_ROLLBACK_AVAILABLE
    assert availability["actions"] == ["service.restart"]
    assert rollback.plan_hash("b" * 64, steps, availability) != "b" * 64


def test_rollback_never_accepts_server_supplied_old_registry_value():
    from joltiq.services.actions.registry import ActionValidationError, validate_params

    accepted = validate_params("office.addin.restore_load_behavior", {"source_command_id": "change-123"})
    assert accepted == {"source_command_id": "change-123"}
    for forbidden in ("old_value", "value", "before", "enabled"):
        try:
            validate_params(
                "office.addin.restore_load_behavior",
                {"source_command_id": "change-123", forbidden: 3},
            )
        except ActionValidationError as exc:
            assert "unexpected parameter" in str(exc)
        else:
            raise AssertionError(f"server-supplied {forbidden} was accepted")


def test_partial_rollback_reports_changed_restored_and_unresolved_items():
    from joltiq.services import rollback

    report = rollback.partial_report(
        changed=["addin-setting", "outlook-restart", "browser-field"],
        restored=["addin-setting"],
        failed={"browser-field": "read-back failed"},
    )
    assert report == {
        "status": rollback.ROLLBACK_FAILED,
        "changed": ["addin-setting", "outlook-restart", "browser-field"],
        "restored": ["addin-setting"],
        "unresolved": ["outlook-restart", "browser-field"],
        "failures": {"browser-field": "read-back failed"},
    }


def test_pre_state_is_captured_before_browser_mutation(owner, monkeypatch):
    from joltiq.db import session_scope
    from joltiq.models import ActionExecution, AgentRun, SupportSession
    from joltiq.services import browser_client

    observed = []
    monkeypatch.setattr(
        browser_client,
        "readback",
        lambda ref, url, selector, *args: observed.append((ref, url, selector))
        or {"ok": True, "value": "previous-value"},
    )
    org_id = uuid.UUID(owner.org_id)
    with session_scope(org_id) as db:
        run = AgentRun(org_id=org_id, correlation_id=uuid.uuid4().hex, context={})
        session = SupportSession(
            org_id=org_id,
            owner_id=uuid.UUID(owner.me["user"]["id"]),
            kind="browser",
            state="active",
            worker_session_ref="worker-prestate",
        )
        db.add_all([run, session])
        db.flush()
        execution = ActionExecution(
            org_id=org_id,
            run_id=run.id,
            session_id=session.id,
            action="browser.type",
            target={},
            params={},
            risk="LOW_RISK",
            policy_decision={},
            status="running",
            idempotency_key=uuid.uuid4().hex,
        )
        db.add(execution)
        db.flush()
        pre = verification.capture_pre_state(
            db,
            execution,
            [{"type": "browser_readback", "params": {"url": "http://app.test/settings", "selector": "#email"}}],
            session,
        )
    assert observed == [("worker-prestate", "http://app.test/settings", "#email")]
    assert pre["0"]["value"] == "previous-value"


def _sealed_verification(owner, monkeypatch, *, matched=True, critical=True):
    from joltiq.db import session_scope
    from joltiq.models import ActionExecution, AgentPlan, AgentRun, SupportSession
    from joltiq.services import browser_client

    monkeypatch.setattr(
        browser_client,
        "readback",
        lambda *args, **kwargs: {"ok": True, "matched": matched, "value": "expected", "auth": {}},
    )
    org_id = uuid.UUID(owner.org_id)
    with session_scope(org_id) as db:
        session = SupportSession(
            org_id=org_id,
            owner_id=uuid.UUID(owner.me["user"]["id"]),
            kind="browser",
            state="active",
            worker_session_ref="worker-evidence",
        )
        run = AgentRun(
            org_id=org_id,
            state="VERIFYING",
            correlation_id=uuid.uuid4().hex,
            context={
                "rollback": {"status": "ROLLED_BACK", "reason": "restored", "channel": "browser"},
                "rollback_pre": {"s0": "previous-value"},
            },
        )
        db.add_all([session, run])
        db.flush()
        db.add(AgentPlan(
            org_id=org_id,
            run_id=run.id,
            summary="Change with a safe inverse",
            plan_hash="a" * 64,
            rollback_steps=[{"action": "browser.type", "params": {"text": {"$pre": "s0"}}}],
            rollback_availability={"status": "AVAILABLE", "channel": "browser", "steps": 1},
        ))
        execution = ActionExecution(
            org_id=org_id,
            run_id=run.id,
            session_id=session.id,
            action="browser.submit",
            target={},
            params={},
            risk="SENSITIVE",
            policy_decision={},
            status="succeeded",
            idempotency_key=uuid.uuid4().hex,
        )
        db.add(execution)
        db.flush()
        postconditions = [{
            "type": "browser_readback",
            "critical": critical,
            "params": {"url": "http://app.test/settings", "selector": "#email", "expect_value": "expected"},
        }]
        result = verification.start(db, execution, postconditions, {}, session, None)
        return org_id, run.id, result.id, result.status, result.evidence_hash


def test_evidence_tampering_is_detected(owner, monkeypatch):
    from joltiq.db import session_scope
    from joltiq.models import VerificationRun

    org_id, _, verification_id, status, evidence_hash = _sealed_verification(owner, monkeypatch)
    assert status == V.VERIFIED
    assert evidence_hash and len(evidence_hash) == 64
    with session_scope(org_id) as db:
        result = verification.recompute_evidence(db, db.get(VerificationRun, verification_id))
        assert result["ok"] is True
    with session_scope(org_id) as db:
        db.execute(
            text("update verification_runs set details = jsonb_set(details, '{0,observed}', '\"forged\"') where id = :id"),
            {"id": verification_id},
        )
    with session_scope(org_id) as db:
        result = verification.recompute_evidence(db, db.get(VerificationRun, verification_id))
        assert result["ok"] is False
        assert result["recomputed_hash"] != result["audit_hash"]


def test_critical_check_aggregation_and_false_success_refusal(owner, monkeypatch):
    assert verification.aggregate_critical([{"status": V.VERIFIED}]) == V.VERIFIED
    assert verification.aggregate_critical([{"status": V.VERIFIED, "critical": False}]) == V.UNKNOWN
    assert verification.aggregate_critical([]) == V.UNKNOWN
    assert verification.aggregate_critical(
        [{"status": V.VERIFIED}, {"status": V.FAILED, "critical": False}]
    ) == V.VERIFIED
    assert verification.aggregate_critical(
        [{"status": V.VERIFIED}, {"status": V.FAILED}]
    ) != V.VERIFIED

    _, _, _, status, _ = _sealed_verification(owner, monkeypatch, matched=True, critical=False)
    assert status == V.UNKNOWN


def test_run_api_exposes_rollback_and_recomputed_evidence(owner, monkeypatch):
    _, run_id, verification_id, status, evidence_hash = _sealed_verification(owner, monkeypatch)

    detail = owner.get(f"/api/v1/agents/runs/{run_id}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["plans"][0]["rollback_availability"]["status"] == "AVAILABLE"
    assert body["plans"][0]["rollback_steps"][0]["action"] == "browser.type"
    assert body["rollback"]["channel"] == "browser"
    assert body["rollback_snapshot"] == {"s0": "previous-value"}
    verification_row = next(item for item in body["verifications"] if item["id"] == str(verification_id))
    assert verification_row["kind"] == "verification"
    assert verification_row["status"] == status
    assert verification_row["evidence_hash"] == evidence_hash

    evidence = owner.get(f"/api/v1/agents/runs/{run_id}/verification-evidence")
    assert evidence.status_code == 200, evidence.text
    item = evidence.json()["items"][0]
    assert item["verification_id"] == str(verification_id)
    assert item["ok"] is True
    assert item["stored_hash"] == evidence_hash
