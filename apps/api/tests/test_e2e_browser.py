"""REAL end-to-end tests: real PostgreSQL, real API, real isolated Chromium (Playwright) browser worker, real local
authorized test web app. The AI model and the web-search provider are LOCAL TEST FIXTURES (no credentials in this
environment) — those tests are additionally marked `mocked`; everything else in the path is real."""
from __future__ import annotations

import time
import uuid

import httpx
import pytest
from conftest import invite_and_join, publish_executable
from e2e_support import TESTAPP, start_services, stop_services
from sqlalchemy import select, text

pytestmark = [pytest.mark.browser, pytest.mark.mocked]

EMAIL = "dispatch@acmeplumbing.test"
PASSWORD_FD = "FieldDesk-Test-Pass-2026"


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
    cust_acct = owner.post("/api/v1/customers", json={"name": "Acme Plumbing"}).json()
    tech = invite_and_join(app, owner, "technician")
    customer = invite_and_join(app, owner, "customer", cust_acct["id"])
    return owner, tech, customer


def publish_procedure(app, owner, *, readback_value=EMAIL):
    settings_url = f"{TESTAPP}/app/settings"
    body = {
        "symptoms": ["FieldDesk is not sending job notifications to dispatch", "Dispatch email missing in notification settings"],
        "environment": ["FieldDesk web app"], "prerequisites": ["FieldDesk admin sign-in (customer enters it)"],
        "diagnostics": ["Open Notification settings and check the dispatch email and job notifications toggle"],
        "remediation": ["Set dispatch notification email", "Turn job notifications on", "Save"],
        "risk": "SENSITIVE (changes customer application settings)", "verification": ["Reload settings page and confirm values persisted"],
        "visibility": ["technician", "admin", "org_owner", "customer"],
        "procedure": [
            {"action": "browser.navigate", "params": {"url": settings_url}, "rationale": "Open notification settings", "postconditions": []},
            {"action": "browser.type", "params": {"selector": "testid=notify-email", "text": EMAIL}, "rationale": "Set dispatch email", "postconditions": []},
            {"action": "browser.select", "params": {"selector": "testid=job-notifications", "value": "on"}, "rationale": "Enable job notifications", "postconditions": []},
            {"action": "browser.submit", "params": {"selector": 'role=button[name="Save settings"]'}, "rationale": "Save the settings",
             "postconditions": [
                 {"type": "browser_readback", "params": {"url": settings_url, "selector": "testid=notify-email", "expect_value": readback_value},
                  "description": "dispatch email persisted after reload"},
                 {"type": "browser_readback", "params": {"url": settings_url, "selector": "testid=job-notifications", "expect_value": "on"},
                  "description": "job notifications persisted as On"}]},
        ],
    }
    art = owner.post("/api/v1/knowledge/articles", json={"title": "FieldDesk job notifications not sending to dispatch", "body": body}).json()
    publish_executable(app, owner, art["id"])
    drain()  # index the published article
    return art


def wait_state(c, run_id, predicate, timeout=60):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        drain()
        last = c.get(f"/api/v1/agents/runs/{run_id}").json()
        if predicate(last):
            return last
        time.sleep(0.3)
    raise AssertionError(f"run did not reach expected state; last={last and (last['state'], last['paused_for'], last['transitions'][-3:])}")


def start_chat(customer):
    r = customer.post("/api/v1/agents/chat", json={"message": "Our FieldDesk web app is not sending job notifications to our dispatch team. "
                                                              "The dispatch email seems to be missing in the notification settings."})
    assert r.status_code == 201, r.text
    return r.json()


def approve_and_consent(customer, run, run_id):
    sess_id = None
    for m in run["messages"]:
        if m["data"].get("session_id"):
            sess_id = m["data"]["session_id"]
    assert sess_id, "AI should have requested a browser session"
    s = customer.get(f"/api/v1/sessions/{sess_id}").json()
    assert s["state"] == "pending_consent" and s["can_consent"] is True
    assert s["allowed_domains"] == ["127.0.0.1"]
    r = customer.post(f"/api/v1/sessions/{sess_id}/consent", json={"minutes": 30, "max_risk": "SENSITIVE"})
    assert r.status_code == 200, r.text
    pending = [a for a in run["approvals"] if a["status"] == "PENDING"]
    assert [a["action"] for a in pending] == ["browser.submit"]  # only the SENSITIVE step needs approval
    return sess_id, pending


def human_sign_in(customer, sess_id):
    """Customer signs in directly through the viewer: Tab to the username box, type, Tab, PROTECTED password, Enter."""
    inp = lambda **kw: customer.post(f"/api/v1/sessions/{sess_id}/input", json=kw)  # noqa: E731
    assert inp(kind="key", key="Tab").status_code == 200
    inp(kind="type", text="dispatch.admin")
    inp(kind="key", key="Tab")
    inp(kind="type", text=PASSWORD_FD, protected=True)
    r = inp(kind="key", key="Enter")
    assert r.status_code == 200, r.text
    time.sleep(0.5)
    assert customer.post(f"/api/v1/sessions/{sess_id}/handback").status_code == 200


def test_browser_vertical_slice_verified_resolution(services, app, team):
    owner, tech, customer = team
    httpx.post(f"{TESTAPP}/__test__/reset")
    publish_procedure(app, owner)

    chat = start_chat(customer)
    run_id = chat["run_id"]
    run = wait_state(customer, run_id, lambda r: r["state"] == "AWAITING_APPROVAL")
    assert any(e["kind"] == "internal" for e in run["evidence"])  # retrieved tenant knowledge with citations
    plan = run["plans"][-1]
    assert [s["action"] for s in plan["steps"]] == ["browser.navigate", "browser.type", "browser.select", "browser.submit"]
    assert plan["steps"][-1]["risk"] == "SENSITIVE"   # risk from registry/policy, not the model
    assert run["uses_test_fixture_model"] is True

    sess_id, pending = approve_and_consent(customer, run, run_id)
    r = customer.post(f"/api/v1/approvals/{pending[0]['id']}/decision", json={"decision": "approve"})
    assert r.status_code == 200, r.text

    # The app requires sign-in: the AI must pause and hand control to the human.
    run = wait_state(customer, run_id, lambda r: r["paused_for"] == "user_auth")
    s = customer.get(f"/api/v1/sessions/{sess_id}").json()
    assert s["state"] == "awaiting_user_auth"
    frame = customer.get(f"/api/v1/sessions/{sess_id}/frame")
    assert frame.status_code == 200 and frame.headers["content-type"] == "image/jpeg" and len(frame.content) > 1000

    human_sign_in(customer, sess_id)
    run = wait_state(customer, run_id, lambda r: r["state"] in ("RESOLVED", "ESCALATED", "FAILED", "PARTIALLY_RESOLVED"), timeout=90)
    assert run["state"] == "RESOLVED", run["transitions"][-5:]
    v = run["verifications"][-1]
    assert v["status"] == "VERIFIED" and all(d["status"] == "VERIFIED" for d in v["details"])

    # Independent truth: the test app actually persisted the change.
    state = httpx.get(f"{TESTAPP}/__test__/state").json()
    assert state["settings"] == {"notify_email": EMAIL, "job_notifications": "on"}

    t = customer.get(f"/api/v1/tickets/{chat['ticket_id']}").json()
    assert t["status"] == "RESOLVED" and t["resolution_verified"] is True

    # A knowledge article is DRAFTED for human review (not auto-published, not executable).
    drain()
    arts = tech.get("/api/v1/knowledge/articles", params={"status": "draft"}).json()["items"]
    drafted = [a for a in arts if a["source_ticket_id"] == chat["ticket_id"]]
    assert drafted and drafted[0]["executable"] is False and drafted[0]["verified_resolution"] is True

    # The password typed by the customer is nowhere in the database.
    from joltiq.db import session_scope

    with session_scope(uuid.UUID(owner.org_id)) as db:
        for table in ("session_events", "agent_messages", "audit_events", "ticket_comments", "action_executions", "tool_calls"):
            hit = db.execute(text(f"select count(*) from {table} where cast(row_to_json({table}) as text) like :p"),  # noqa: S608
                             {"p": f"%{PASSWORD_FD}%"}).scalar()
            assert hit == 0, f"secret leaked into {table}"
        from joltiq.services.audit import verify_chain

        assert verify_chain(db, uuid.UUID(owner.org_id))["ok"]

    # Session can be ended; browser context is destroyed on the worker.
    assert customer.post(f"/api/v1/sessions/{sess_id}/terminate").status_code == 200


def test_execution_success_but_verification_failure_escalates(services, app, team):
    owner, tech, customer = team
    httpx.post(f"{TESTAPP}/__test__/reset", params={"buggy_save": True})  # app says "saved" but doesn't persist
    publish_procedure(app, owner)
    chat = start_chat(customer)
    run_id = chat["run_id"]
    run = wait_state(customer, run_id, lambda r: r["state"] == "AWAITING_APPROVAL")
    sess_id, pending = approve_and_consent(customer, run, run_id)
    customer.post(f"/api/v1/approvals/{pending[0]['id']}/decision", json={"decision": "approve"})
    wait_state(customer, run_id, lambda r: r["paused_for"] == "user_auth")
    human_sign_in(customer, sess_id)
    run = wait_state(customer, run_id, lambda r: r["state"] in ("RESOLVED", "ESCALATED", "FAILED", "PARTIALLY_RESOLVED"), timeout=120)
    assert httpx.get(f"{TESTAPP}/__test__/state").json()["save_count"] >= 1   # the click "worked"
    assert run["state"] == "ESCALATED"                                         # ...but verification caught it
    assert run["verifications"][0]["status"] == "FAILED"
    assert any("Loop detected" in t["reason"] or "Retry" in t["reason"] or "verification" in t["reason"].lower() for t in run["transitions"])
    t = customer.get(f"/api/v1/tickets/{chat['ticket_id']}").json()
    assert t["status"] == "ESCALATED" and t["resolution_verified"] is False


def test_denied_approval_escalates_without_executing(services, app, team):
    owner, tech, customer = team
    httpx.post(f"{TESTAPP}/__test__/reset")
    publish_procedure(app, owner)
    chat = start_chat(customer)
    run = wait_state(customer, chat["run_id"], lambda r: r["state"] == "AWAITING_APPROVAL")
    sess_id, pending = approve_and_consent(customer, run, chat["run_id"])
    customer.post(f"/api/v1/approvals/{pending[0]['id']}/decision", json={"decision": "deny", "comment": "Not now"})
    run = wait_state(customer, chat["run_id"], lambda r: r["state"] == "ESCALATED")
    assert "denied" in run["transitions"][-1]["reason"].lower()
    assert httpx.get(f"{TESTAPP}/__test__/state").json()["save_count"] == 0  # nothing was submitted


def test_unfamiliar_issue_uses_research_and_never_executes_forum_commands(services, app, team):
    owner, tech, customer = team
    r = customer.post("/api/v1/agents/chat", json={"message": "Outlook crashes every time I open a PDF attachment since this week. "
                                                              "Version 2408, Microsoft 365. My name is Jane Contoso, jane@acmeplumbing.test"})
    run_id = r.json()["run_id"]
    run = wait_state(tech, run_id, lambda r: r["state"] in ("ESCALATED", "AWAITING_APPROVAL", "RESOLVED"))
    ext = [e for e in run["evidence"] if e["kind"] == "external"]
    assert ext, "expected external research evidence"
    assert all(e["live"] is False for e in ext)  # honestly labelled as test fixture search
    rq = tech.get(f"/api/v1/research/{run['research_query_id']}").json()
    assert "jane" not in rq["query"].lower() and "@" not in rq["query"]          # privacy redaction
    forum = [s for s in rq["sources"] if "forum" in s["url"]][0]
    assert "prompt_injection" in forum["flags"] and "contains_commands_never_executed" in forum["flags"]
    assert "Ignore previous instructions" not in forum["excerpt"]
    official = [s for s in rq["sources"] if "attachment-crash" in s["url"]][0]
    assert official["source_kind"] == "test_fixture" and official["retrieved_at"]
    # Only unverified external fixes → no automatic execution, escalated to a technician with citations.
    assert run["state"] == "ESCALATED"
    assert not run["approvals"]


def test_cross_tenant_cannot_touch_session_or_approval(services, app, team, new_client):
    from conftest import register

    owner, tech, customer = team
    httpx.post(f"{TESTAPP}/__test__/reset")
    publish_procedure(app, owner)
    chat = start_chat(customer)
    run = wait_state(customer, chat["run_id"], lambda r: r["state"] == "AWAITING_APPROVAL")
    approval_id = run["approvals"][0]["id"]
    sess_id = next(m["data"]["session_id"] for m in run["messages"] if m["data"].get("session_id"))
    attacker = register(new_client(), "Evil MSP")
    assert attacker.post(f"/api/v1/approvals/{approval_id}/decision", json={"decision": "approve"}).status_code == 404
    assert attacker.post(f"/api/v1/sessions/{sess_id}/consent", json={}).status_code == 404
    assert attacker.get(f"/api/v1/agents/runs/{chat['run_id']}").status_code == 404
    # A technician of the SAME org cannot consent on the customer's behalf.
    assert tech.post(f"/api/v1/sessions/{sess_id}/consent", json={}).status_code == 403


def test_emergency_stop_terminates_and_escalates(services, app, team):
    owner, tech, customer = team
    httpx.post(f"{TESTAPP}/__test__/reset")
    publish_procedure(app, owner)
    chat = start_chat(customer)
    run = wait_state(customer, chat["run_id"], lambda r: r["state"] == "AWAITING_APPROVAL")
    sess_id, pending = approve_and_consent(customer, run, chat["run_id"])
    r = customer.post(f"/api/v1/sessions/{sess_id}/emergency-stop")
    assert r.status_code == 200 and r.json()["state"] == "terminated" and r.json()["emergency_stopped"] is True
    run = customer.get(f"/api/v1/agents/runs/{chat['run_id']}").json()
    assert run["state"] == "ESCALATED"
    # Approving afterwards cannot revive execution.
    customer.post(f"/api/v1/approvals/{pending[0]['id']}/decision", json={"decision": "approve"})
    drain()
    assert customer.get(f"/api/v1/agents/runs/{chat['run_id']}").json()["state"] == "ESCALATED"
    assert httpx.get(f"{TESTAPP}/__test__/state").json()["save_count"] == 0


_ = select
