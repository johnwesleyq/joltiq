"""Fix it for me (Browser Fix, approval-once) — REAL end-to-end: real PostgreSQL, real API, real isolated Chromium
browser worker, real local FieldDesk test app. The AI is the LOCAL BROWSER FIXTURE script (no credentials here), so
these tests are also marked `mocked`. Verification is independent: a fresh-page read-back plus the test app's state."""
from __future__ import annotations

import time

import httpx
import pytest
from conftest import invite_and_join
from e2e_support import TESTAPP, start_services, stop_services
from test_e2e_browser import human_sign_in

pytestmark = [pytest.mark.browser, pytest.mark.mocked]

EMAIL = "dispatch@acmeplumbing.test"
GOAL = f"FieldDesk isn't sending job notifications to dispatch. Set the dispatch notification email to {EMAIL} and turn job notifications on."


@pytest.fixture(scope="module")
def services():
    procs = start_services()
    yield
    stop_services(procs)


def drain():
    from joltiq.services.jobs import drain as _d

    return _d()


@pytest.fixture
def team(app, owner):
    acct = owner.post("/api/v1/customers", json={"name": "Acme Plumbing"}).json()
    customer = invite_and_join(app, owner, "customer", acct["id"])
    r = owner.patch("/api/v1/organizations/current", json={"settings": {"fix_sites": [
        {"name": "FieldDesk", "url": f"{TESTAPP}/app/settings"}]}})
    assert r.status_code == 200, r.text
    return owner, customer


def wait_run(c, rid, pred, timeout=60):
    deadline, last = time.time() + timeout, None
    while time.time() < deadline:
        drain()
        last = c.get(f"/api/v1/computer-use/{rid}").json()
        if pred(last):
            return last
        time.sleep(0.2)
    raise AssertionError(f"run stuck: {last and (last['state'], last['waiting_for'], [s['kind'] for s in last['steps']][-8:])}")


def start_fix(customer):
    r = customer.post("/api/v1/agents/fix", json={"message": GOAL})
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["channel"] == "browser" and out["run_id"]
    run = customer.get(f"/api/v1/computer-use/{out['run_id']}").json()
    assert run["state"] == "pending_approval" and run["channel"] == "browser"
    assert run["rollback"]["status"] == "BROWSER_FIELD_ROLLBACK"
    assert run["you_can_answer"] is True
    s = customer.get(f"/api/v1/sessions/{out['session_id']}").json()
    assert s["state"] == "pending_consent"
    return out


def approve_and_sign_in(customer, out):
    # ONE approval: consent + goal + start.
    r = customer.post(f"/api/v1/computer-use/{out['run_id']}/approve", json={"approved": True})
    assert r.status_code == 200, r.text
    run = wait_run(customer, out["run_id"], lambda r: r["state"] == "waiting_customer")
    assert run["waiting_for"]["kind"] == "login"  # FieldDesk asks for sign-in: clean pause, AI never types it
    assert customer.get(f"/api/v1/sessions/{out['session_id']}").json()["state"] == "awaiting_user_auth"
    human_sign_in(customer, out["session_id"])  # protected entry + handback resumes the fix


def test_fix_it_for_me_end_to_end_one_approval(services, team, app):
    owner, customer = team
    httpx.post(f"{TESTAPP}/__test__/reset")
    out = start_fix(customer)
    approve_and_sign_in(customer, out)
    run = wait_run(customer, out["run_id"], lambda r: r["state"] in ("completed", "failed", "stopped"), timeout=90)
    assert run["state"] == "completed" and run["success"] is True, (run["summary"], [(s["kind"], s["status"], s["error"]) for s in run["steps"]])
    kinds = [s["kind"] for s in run["steps"]]
    assert {"open_url", "needs_login", "observe", "fill", "select_option", "click_element", "verify"} <= set(kinds), " | ".join(f"{s['kind']}:{s['params']}" for s in run["steps"])
    assert "request_approval" not in kinds  # approval-once: no per-step re-asking
    verify = next(s for s in run["steps"] if s["kind"] == "verify")
    assert all(c["matched"] for c in verify["params"]["readback"])
    st = httpx.get(f"{TESTAPP}/__test__/state").json()["settings"]
    assert st == {"notify_email": EMAIL, "job_notifications": "on"}
    t = customer.get(f"/api/v1/tickets/{out['ticket_id']}").json()
    assert t["status"] == "RESOLVED"
    assert customer.get(f"/api/v1/computer-use/{out['run_id']}/screen").status_code == 200


def test_fix_rolls_back_when_save_does_not_stick(services, team, app):
    owner, customer = team
    httpx.post(f"{TESTAPP}/__test__/reset", params={"partial_save": True})
    out = start_fix(customer)
    approve_and_sign_in(customer, out)
    run = wait_run(customer, out["run_id"], lambda r: r["state"] in ("completed", "failed", "stopped"), timeout=90)
    assert run["state"] == "failed" and run["success"] is False
    assert run["rollback"]["outcome"] == "ROLLED_BACK", run["rollback"]
    assert "undone" in run["summary"]
    st = httpx.get(f"{TESTAPP}/__test__/state").json()["settings"]
    assert st == {"notify_email": "", "job_notifications": "off"}  # the partial change was reverted
    t = customer.get(f"/api/v1/tickets/{out['ticket_id']}").json()
    assert t["status"] == "ESCALATED"


def test_fix_requires_allowlisted_site_and_customer_approval(services, team, app):
    owner, customer = team
    r = customer.post("/api/v1/agents/fix", json={"message": GOAL, "url": "https://evil.example/app"})
    assert r.status_code >= 400 and r.json()["error"]["code"] == "domain_not_allowed"
    out = start_fix(customer)
    other = invite_and_join(app, owner, "customer", owner.post("/api/v1/customers", json={"name": "Other"}).json()["id"])
    assert other.post(f"/api/v1/computer-use/{out['run_id']}/approve", json={"approved": True}).status_code in (403, 404)
    r = customer.post(f"/api/v1/computer-use/{out['run_id']}/approve", json={"approved": False})
    assert r.json()["state"] == "stopped"


def test_high_risk_still_asks_and_in_scope_is_covered():
    from joltiq.services.computer_use import high_risk_reason

    assert high_risk_reason("Click Save settings") is None
    assert high_risk_reason("Restart the Wi-Fi adapter") is None
    assert high_risk_reason("Confirm the purchase of the Pro plan") == "money is involved"
    assert high_risk_reason("Delete the old user account") == "it deletes data"
    assert high_risk_reason("Change the admin password") == "it changes security settings"
