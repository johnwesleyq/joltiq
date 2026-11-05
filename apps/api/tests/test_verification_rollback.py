"""ADR 0017: critical-check aggregation, evidence hashing into the audit chain, server-derived rollback, lease epochs."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from joltiq.services import rollback, verification
from joltiq.services.verification import V

STEPS = [
    {"action": "browser.navigate", "params": {"url": "http://app.test/settings"}},
    {"action": "browser.type", "params": {"selector": "testid=email", "text": "a@b.test"}},
    {"action": "browser.select", "params": {"selector": "testid=jobs", "value": "on"}},
    {"action": "browser.submit", "params": {"selector": 'role=button[name="Save"]'}},
]


def test_verified_requires_a_critical_pass():
    assert verification.aggregate_critical([{"status": V.VERIFIED}]) == V.VERIFIED
    # only non-critical checks passed → not VERIFIED
    assert verification.aggregate_critical([{"status": V.VERIFIED, "critical": False}]) == V.UNKNOWN
    assert verification.aggregate_critical([]) == V.UNKNOWN
    # a non-critical failure doesn't sink a passing critical check; a critical failure does
    assert verification.aggregate_critical([{"status": V.VERIFIED}, {"status": V.FAILED, "critical": False}]) == V.VERIFIED
    assert verification.aggregate_critical([{"status": V.VERIFIED}, {"status": V.FAILED}]) != V.VERIFIED


def test_rollback_is_derived_by_server_and_bound_into_plan_hash():
    rb = rollback.derive_browser_rollback(STEPS)
    assert [s["action"] for s in rb] == ["browser.navigate", "browser.type", "browser.select", "browser.submit"]
    assert rb[1]["params"]["text"] == {"$pre": "s1"} and rb[2]["params"]["value"] == {"$pre": "s2"}
    assert rb[1]["pre"] == {"key": "s1", "url": "http://app.test/settings", "selector": "testid=email"}
    base = "a" * 64
    h = rollback.plan_hash(base, rb)
    assert h != base and rollback.plan_hash(base, []) == base
    tampered = [dict(s) for s in rb]
    tampered[3] = {"action": "browser.submit", "params": {"selector": "#delete-everything"}}
    assert rollback.plan_hash(base, tampered) != h
    # no known inverse → no rollback (later NO_ROLLBACK_AVAILABLE + escalate)
    assert rollback.derive_browser_rollback([*STEPS, {"action": "browser.click", "params": {"selector": "#x"}}]) == []
    assert rollback.derive_browser_rollback([STEPS[0], STEPS[3]]) == []
    assert rollback.derive_browser_rollback(STEPS[1:]) == []  # no navigate → no URL to read the old value from


def test_resolve_refuses_missing_pre_values():
    assert rollback._resolve({"selector": "x", "text": {"$pre": "s1"}}, {"s1": ""}) == {"selector": "x", "text": ""}
    with pytest.raises(KeyError):
        rollback._resolve({"text": {"$pre": "s9"}}, {})


def _verification_with_readback(owner, monkeypatch, *, matched=True, critical=True):
    from joltiq.db import session_scope
    from joltiq.models import ActionExecution, AgentRun, SupportSession
    from joltiq.services import browser_client

    monkeypatch.setattr(browser_client, "readback", lambda *a, **k: {"ok": True, "matched": matched, "value": "x", "auth": {}})
    org = uuid.UUID(owner.org_id)
    with session_scope(org) as db:
        s = SupportSession(org_id=org, owner_id=uuid.UUID(owner.me["user"]["id"]), kind="browser", state="active", allowed_domains=["app.test"], consent={},
                           worker_session_ref="w-evidence")
        run = AgentRun(org_id=org, state="VERIFYING", correlation_id="evidence", context={})
        db.add_all([s, run])
        db.flush()
        ex = ActionExecution(org_id=org, run_id=run.id, session_id=s.id, action="browser.submit", target={}, params={},
                             risk="SENSITIVE", policy_decision={}, status="succeeded", idempotency_key=f"ev-{uuid.uuid4()}")
        db.add(ex)
        db.flush()
        pcs = [{"type": "browser_readback", "critical": critical, "params": {"url": "http://app.test/s", "selector": "#e", "expect_value": "x"}}]
        vr = verification.start(db, ex, pcs, {}, s, None)
        return org, vr.id, run.id, vr.status, vr.evidence_hash


def test_evidence_hash_is_sealed_into_audit_chain_and_tampering_is_detected(owner, monkeypatch):
    from joltiq.db import session_scope
    from joltiq.models import VerificationRun

    org, vid, run_id, status, h = _verification_with_readback(owner, monkeypatch)
    assert status == V.VERIFIED and h and len(h) == 64
    with session_scope(org) as db:
        r = verification.recompute_evidence(db, db.get(VerificationRun, vid))
        assert r["ok"] and r["stored_hash"] == r["recomputed_hash"] == r["audit_hash"] == h
    # An attacker with DB write access flips the result: the recomputed hash no longer matches the audited one.
    with session_scope(org) as db:
        db.execute(text("update verification_runs set details = jsonb_set(details, '{0,observed}', '\"forged\"') where id = :i"), {"i": vid})
    with session_scope(org) as db:
        r = verification.recompute_evidence(db, db.get(VerificationRun, vid))
        assert not r["ok"] and r["recomputed_hash"] != r["audit_hash"]


def test_non_critical_only_pass_is_not_verified(owner, monkeypatch):
    _, _, _, status, _ = _verification_with_readback(owner, monkeypatch, critical=False)
    assert status != V.VERIFIED


def test_verification_evidence_endpoint(app, owner, monkeypatch):
    _, _, run_id, _, h = _verification_with_readback(owner, monkeypatch)
    r = owner.get(f"/api/v1/agents/runs/{run_id}/verification-evidence")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["items"][0]["audit_hash"] == h


def test_rollback_blocked_when_hash_or_approval_does_not_match(owner):
    from joltiq.db import session_scope
    from joltiq.models import AgentPlan, AgentRun

    org = uuid.UUID(owner.org_id)
    rb = rollback.derive_browser_rollback(STEPS)
    with session_scope(org) as db:
        run = AgentRun(org_id=org, state="VERIFYING", correlation_id="rb", context={})
        db.add(run)
        db.flush()
        base = "b" * 64
        plan = AgentPlan(org_id=org, run_id=run.id, summary="x", plan_hash=rollback.plan_hash(base, rb), status="done", rollback_steps=rb)
        db.add(plan)
        db.flush()
        rollback.remember(run, plan, base)
        ok, why, _ = rollback.authorized(db, run, plan)
        assert not ok and "no approval" in why  # never run an un-approved rollback
        plan.rollback_steps = [*rb[:3], {"action": "browser.submit", "params": {"selector": "#other"}}]
        ok, why, _ = rollback.authorized(db, run, plan)
        assert not ok and "do not match" in why
        out = rollback.execute_browser(db, run, plan, None)
        assert out["status"] == rollback.ROLLBACK_BLOCKED


def test_lease_epoch_bumps_on_every_controller_change(owner, monkeypatch):
    from joltiq.db import session_scope
    from joltiq.models import SupportSession
    from joltiq.services import browser_client, sessions

    pushed = []
    monkeypatch.setattr(browser_client, "set_lease", lambda ref, epoch, controller: pushed.append((epoch, controller)) or {"ok": True})
    org = uuid.UUID(owner.org_id)
    with session_scope(org) as db:
        s = SupportSession(org_id=org, owner_id=uuid.UUID(owner.me["user"]["id"]), kind="browser", state="active", allowed_domains=["app.test"], consent={}, worker_session_ref="w-l")
        db.add(s)
        db.flush()
        sessions.acquire_lease(db, s, "ai", reason="t")
        e1 = s.lease_epoch
        sessions.release_lease(db, s, "done")
        sessions.acquire_lease(db, s, "ai", reason="t2")
        assert s.lease_epoch > e1 > 0
        assert [p[0] for p in pushed] == sorted(p[0] for p in pushed) and pushed[-1] == (s.lease_epoch, "ai")
