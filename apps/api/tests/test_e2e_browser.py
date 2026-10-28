"""REAL end-to-end tests: real PostgreSQL, real API, real isolated Chromium (Playwright) browser worker, real local
authorized test web app. The AI model and the web-search provider are LOCAL TEST FIXTURES (no credentials in this
environment) — those tests are additionally marked `mocked`; everything else in the path is real."""
from __future__ import annotations

import time
import uuid
from urllib.parse import urlsplit

import httpx
import pytest
from conftest import invite_and_join, publish_executable
from e2e_support import TESTAPP, start_services, stop_services
from sqlalchemy import select, text

pytestmark = [pytest.mark.browser, pytest.mark.mocked]

EMAIL = "dispatch@acmeplumbing.test"
PASSWORD_FD = "FieldDesk-Test-Pass-2026"
UPLOAD_BYTES = b"customer supplied evidence\n"
UPLOAD_SHA256 = "1c2cce5877b05bd8d5e448088e12552acc87efaa881e81e2333d4f3402b4bb5d"


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
    assert s["allowed_domains"] == [urlsplit(TESTAPP).hostname]
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


def create_upload_ticket(customer, *, subject="FieldDesk case evidence upload"):
    response = customer.post(
        "/api/v1/tickets",
        json={"subject": subject, "description": "Attach the customer-supplied case evidence to the FieldDesk case."},
    )
    assert response.status_code == 201, response.text
    return response.json()


def attach_evidence(customer, ticket_id):
    response = customer.post(
        f"/api/v1/tickets/{ticket_id}/attachments",
        files={"file": ("case-evidence.txt", UPLOAD_BYTES, "text/plain")},
    )
    assert response.status_code == 201, response.text
    return response.json()


def publish_upload_procedure(app, owner):
    evidence_url = f"{TESTAPP}/app/evidence"
    receipt = f"Received case-evidence.txt (27 bytes) sha256 {UPLOAD_SHA256}"
    body = {
        "procedure_schema": 3,
        "kind": "browser",
        "symptoms": ["FieldDesk case evidence is missing", "Customer supplied evidence must be attached to the FieldDesk case"],
        "environment": ["FieldDesk web app"],
        "prerequisites": ["Customer supplied a validated ticket attachment", "FieldDesk admin sign-in"],
        "diagnostics": ["Confirm the exact ticket attachment is present"],
        "remediation": ["Open the case evidence page", "Upload the approved ticket attachment"],
        "risk": "SENSITIVE (sends customer data to FieldDesk)",
        "verification": ["Reload the evidence page and confirm the server receipt"],
        "visibility": ["technician", "admin", "org_owner", "customer"],
        "procedure": [
            {
                "action": "browser.navigate",
                "params": {"url": evidence_url},
                "rationale": "Open the FieldDesk evidence page",
                "postconditions": [],
            },
            {
                "action": "browser.upload",
                "params": {
                    "selector": "testid=evidence-file",
                    "attachment_binding": "customer_selected_ticket_attachment",
                },
                "rationale": "Upload the exact customer-approved ticket attachment",
                "postconditions": [
                    {
                        "type": "browser_readback",
                        "params": {"url": evidence_url, "selector": "testid=evidence-receipt", "expect_text": receipt},
                        "description": "FieldDesk independently reports receipt of the exact file",
                    }
                ],
            },
        ],
    }
    article = owner.post(
        "/api/v1/knowledge/articles", json={"title": "Attach customer case evidence in FieldDesk", "body": body}
    ).json()
    publish_executable(app, owner, article["id"])
    drain()
    return article


def start_upload_run(customer, ticket_id, attachment_id):
    response = customer.post(
        "/api/v1/agents/chat",
        json={
            "ticket_id": ticket_id,
            "attachment_id": attachment_id,
            "message": "The customer supplied evidence is missing from our FieldDesk web app case. Please attach it.",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def consent_upload_session(customer, run):
    assert [step["action"] for step in run["plans"][-1]["steps"]] == ["browser.navigate", "browser.upload"]
    session_id = next(message["data"]["session_id"] for message in run["messages"] if message["data"].get("session_id"))
    consent = customer.post(
        f"/api/v1/sessions/{session_id}/consent", json={"minutes": 30, "max_risk": "SENSITIVE"}
    )
    assert consent.status_code == 200, consent.text
    approvals = [approval for approval in run["approvals"] if approval["status"] == "PENDING"]
    assert [approval["action"] for approval in approvals] == ["browser.upload"]
    assert f"Destination: {urlsplit(TESTAPP).hostname}." in approvals[0]["reason"]
    return session_id, approvals[0]


def prepare_upload_run(app, owner, customer, *, attachment_ticket_id=None):
    run_ticket = create_upload_ticket(customer)
    source_ticket = run_ticket if attachment_ticket_id is None else attachment_ticket_id
    attachment = attach_evidence(customer, source_ticket["id"])
    publish_upload_procedure(app, owner)
    chat = start_upload_run(customer, run_ticket["id"], attachment["id"])
    run = wait_state(customer, chat["run_id"], lambda value: value["state"] == "AWAITING_APPROVAL")
    session_id, approval = consent_upload_session(customer, run)
    return chat, session_id, approval


def test_browser_upload_denial_sends_no_file(services, app, team):
    owner, _tech, customer = team
    httpx.post(f"{TESTAPP}/__test__/reset")
    chat, _session_id, approval = prepare_upload_run(app, owner, customer)

    decision = customer.post(
        f"/api/v1/approvals/{approval['id']}/decision", json={"decision": "deny", "comment": "Do not send this file"}
    )

    assert decision.status_code == 200, decision.text
    run = wait_state(customer, chat["run_id"], lambda value: value["state"] == "ESCALATED")
    assert "denied" in run["transitions"][-1]["reason"].lower()
    assert httpx.get(f"{TESTAPP}/__test__/state").json()["upload"] is None


def test_browser_upload_approved_attachment_is_received_and_verified(services, app, team):
    owner, _tech, customer = team
    httpx.post(f"{TESTAPP}/__test__/reset")
    chat, session_id, approval = prepare_upload_run(app, owner, customer)
    decision = customer.post(f"/api/v1/approvals/{approval['id']}/decision", json={"decision": "approve"})
    assert decision.status_code == 200, decision.text

    wait_state(customer, chat["run_id"], lambda value: value["paused_for"] == "user_auth")
    human_sign_in(customer, session_id)
    run = wait_state(
        customer,
        chat["run_id"],
        lambda value: value["state"] in ("RESOLVED", "ESCALATED", "FAILED", "PARTIALLY_RESOLVED"),
        timeout=120,
    )

    assert run["state"] == "RESOLVED", run["transitions"][-8:]
    assert run["verifications"][-1]["status"] == "VERIFIED"
    assert httpx.get(f"{TESTAPP}/__test__/state").json()["upload"] == {
        "filename": "case-evidence.txt",
        "size": 27,
        "sha256": UPLOAD_SHA256,
    }


def test_browser_upload_wrong_ticket_attachment_is_refused(services, app, team):
    owner, _tech, customer = team
    httpx.post(f"{TESTAPP}/__test__/reset")
    source_ticket = create_upload_ticket(customer, subject="Unrelated FieldDesk case")
    run_ticket = create_upload_ticket(customer)
    attachment = attach_evidence(customer, source_ticket["id"])
    publish_upload_procedure(app, owner)

    refused = customer.post(
        "/api/v1/agents/chat",
        json={
            "ticket_id": run_ticket["id"],
            "attachment_id": attachment["id"],
            "message": "Attach this evidence to FieldDesk.",
        },
    )

    assert refused.status_code == 404
    assert httpx.get(f"{TESTAPP}/__test__/state").json()["upload"] is None


def test_browser_upload_emergency_stop_sends_no_file(services, app, team):
    owner, _tech, customer = team
    httpx.post(f"{TESTAPP}/__test__/reset")
    chat, session_id, approval = prepare_upload_run(app, owner, customer)

    stopped = customer.post(f"/api/v1/sessions/{session_id}/emergency-stop")

    assert stopped.status_code == 200 and stopped.json()["emergency_stopped"] is True
    customer.post(f"/api/v1/approvals/{approval['id']}/decision", json={"decision": "approve"})
    drain()
    assert customer.get(f"/api/v1/agents/runs/{chat['run_id']}").json()["state"] == "ESCALATED"
    assert httpx.get(f"{TESTAPP}/__test__/state").json()["upload"] is None


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
    run = wait_state(tech, run_id, lambda r: r["state"] in ("ESCALATED", "AWAITING_APPROVAL", "RESOLVED") or _awaiting_guidance(r))
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
    # Only unverified external fixes → no automatic execution. The customer gets self-service steps (never executed by
    # JoltIQ), and a technician takes over on request.
    assert _awaiting_guidance(run)
    assert not run["approvals"] and not run["plans"]
    guidance = [m for m in run["messages"] if (m.get("data") or {}).get("kind") == "guidance"]
    assert guidance and "Ignore previous instructions" not in guidance[-1]["content"]
    customer.post("/api/v1/agents/chat", json={"ticket_id": run["ticket_id"], "message": "Please get me a technician"})
    run = wait_state(tech, run_id, lambda r: r["state"] == "ESCALATED")
    assert not run["approvals"]


def _awaiting_guidance(run):
    return run["state"] == "PLANNING" and run["paused_for"] == "customer" and any(
        (m.get("data") or {}).get("kind") == "guidance" for m in run.get("messages", []))


def test_general_it_question_gets_guidance_and_customer_confirms_fix(services, app, team):
    owner, tech, customer = team
    r = customer.post("/api/v1/agents/chat", json={"message": "My laptop can't connect to the office Wi-Fi since this morning, "
                                                              "other people are connected fine and my phone works on it."})
    assert r.status_code == 201, r.text
    run_id, ticket_id = r.json()["run_id"], r.json()["ticket_id"]
    run = wait_state(customer, run_id, _awaiting_guidance)
    guide = [m for m in run["messages"] if (m.get("data") or {}).get("kind") == "guidance"][-1]
    assert "1." in guide["content"] and "fixed" in guide["content"].lower()
    assert customer.get(f"/api/v1/tickets/{ticket_id}").json()["status"] == "AWAITING_CUSTOMER"

    customer.post("/api/v1/agents/chat", json={"ticket_id": ticket_id, "message": "That worked, it's fixed now"})
    run = wait_state(customer, run_id, lambda r: r["state"] in ("RESOLVED", "ESCALATED"))
    assert run["state"] == "RESOLVED"
    t = customer.get(f"/api/v1/tickets/{ticket_id}").json()
    # Resolved by the customer's confirmation, honestly recorded as NOT independently verified.
    assert t["status"] in ("RESOLVED", "OPEN") and t["resolution_verified"] is False


def test_guidance_that_does_not_work_retries_then_escalates(services, app, team):
    owner, tech, customer = team
    r = customer.post("/api/v1/agents/chat", json={"message": "Excel freezes for about a minute every time I save a large "
                                                              "workbook to the shared drive, started after last week's update."})
    run_id, ticket_id = r.json()["run_id"], r.json()["ticket_id"]
    wait_state(customer, run_id, _awaiting_guidance)
    customer.post("/api/v1/agents/chat", json={"ticket_id": ticket_id, "message": "No luck, it still freezes after a restart"})
    run = wait_state(customer, run_id, lambda r: _awaiting_guidance(r) and len(
        [m for m in r["messages"] if (m.get("data") or {}).get("kind") == "guidance"]) == 2)
    customer.post("/api/v1/agents/chat", json={"ticket_id": ticket_id, "message": "Still broken, didn't help"})
    run = wait_state(customer, run_id, lambda r: r["state"] == "ESCALATED")
    t = tech.get(f"/api/v1/tickets/{ticket_id}").json()
    assert t["status"] == "ESCALATED" and t["resolution_verified"] is False
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


def _questions_always(self, ctx, messages):
    # Mimic the live model in ticket #1011: it judged an ordinary Wi-Fi request "insufficient" and returned six
    # questions even though JoltIQ only displays five.
    return {"sufficient": False, "questions": ["Which Wi-Fi network?", "Windows or Mac?", "Any error message?",
                                               "Did anything change?", "Do other devices connect?", "Has it worked before?"],
            "known_facts": {}}


def test_ordinary_request_gets_steps_even_when_model_wants_to_ask_questions(services, app, team, monkeypatch):
    from joltiq.services.ai.fixture import LocalFixtureProvider
    monkeypatch.setattr(LocalFixtureProvider, "_Clarification", _questions_always)
    owner, tech, customer = team
    r = customer.post("/api/v1/agents/chat", json={"message": "My laptop won't connect to the office Wi-Fi but my phone connects fine."})
    run_id, ticket_id = r.json()["run_id"], r.json()["ticket_id"]
    run = wait_state(customer, run_id, _awaiting_guidance)
    ai_msgs = [m for m in run["messages"] if m["role"] == "assistant"]
    assert not any("need a bit more detail" in m["content"] for m in ai_msgs), "should give steps, not interrogate"
    customer.post("/api/v1/agents/chat", json={"ticket_id": ticket_id, "message": "that worked, it’s fixed"})
    run = wait_state(customer, run_id, lambda r: r["state"] in ("RESOLVED", "ESCALATED"))
    assert run["state"] == "RESOLVED"
    assert customer.get(f"/api/v1/tickets/{ticket_id}").json()["resolution_verified"] is False


def test_fixed_reply_resolves_even_during_clarifying_questions(services, app, team, monkeypatch):
    from joltiq.services.ai.fixture import LocalFixtureProvider
    monkeypatch.setattr(LocalFixtureProvider, "_Clarification", _questions_always)
    owner, tech, customer = team
    r = customer.post("/api/v1/agents/chat", json={"message": "Internet broken"})  # too vague -> one round of questions
    run_id, ticket_id = r.json()["run_id"], r.json()["ticket_id"]
    run = wait_state(customer, run_id, lambda r: r["paused_for"] == "customer")
    assert any("need a bit more detail" in m["content"] for m in run["messages"] if m["role"] == "assistant")
    customer.post("/api/v1/agents/chat", json={"ticket_id": ticket_id, "message": "Never mind, restarted the router and it works now"})
    run = wait_state(customer, run_id, lambda r: r["state"] in ("RESOLVED", "ESCALATED"))
    assert run["state"] == "RESOLVED"
    assert customer.get(f"/api/v1/tickets/{ticket_id}").json()["status"] in ("RESOLVED", "OPEN")


def test_not_fixed_reply_is_not_mistaken_for_resolution():
    from joltiq.services.agent import is_resolution_message as f
    for yes in ("that worked, it’s fixed", "Fixed!", "works now thanks", "The fix worked", "resolved", "all good now", "Never mind, restarted the router and it works now"):
        assert f(yes), yes
    for no in ("not fixed", "still broken", "didn't work", "nothing worked", "it never worked", "nope", "Wi-Fi isn't working"):
        assert not f(no), no
