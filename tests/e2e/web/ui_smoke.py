"""Real-browser UI smoke test against a running stack (web :3000 → API :8000). Registers an org, invites a customer,
files a ticket through the portal chat, and visits every major page, failing on any uncaught page error or API 5xx.
Usage: python tests/e2e/web/ui_smoke.py [base_url] [screenshot_dir]"""

import json
import re
import sys
import uuid

from playwright.sync_api import expect, sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:3000"
SHOTS = sys.argv[2] if len(sys.argv) > 2 else None
PW = "correct-horse-battery-staple"
errors: list[str] = []


def watch(page):
    page.on("pageerror", lambda e: errors.append(f"pageerror {page.url}: {e}"))
    page.on("response", lambda r: r.status >= 500 and errors.append(f"HTTP {r.status} {r.url}"))


def shot(page, name):
    if SHOTS:
        page.screenshot(path=f"{SHOTS}/{name}.png", full_page=True)


def run_fixture(
    *, stage="UNCONFIRMED", paused_for=None, approvals=None, messages=None, recovery=None, symptom_response=None
):
    """Complete API-shaped local fixture for rendering safety states in the real browser UI."""
    return {
        "id": "ui-run",
        "ticket_id": "ui-ticket",
        "state": "AWAITING_SYMPTOM_CHECK" if paused_for else "EXECUTING",
        "paused_for": paused_for,
        "retries": 0,
        "max_retries": 2,
        "cost_usd": 0,
        "outcome_summary": None,
        "outcome_stage": stage,
        "symptom_response": symptom_response,
        "recovery": recovery,
        "messages": messages or [],
        "transitions": [],
        "plans": [],
        "evidence": [],
        "hypotheses": [],
        "observations": [],
        "approvals": approvals or [],
        "verifications": [],
        "model_calls": [],
        "uses_test_fixture_model": False,
        "article_id": None,
        "research_query_id": None,
    }


def approval_fixture(action, reason):
    return {
        "id": "approval-" + action.replace(".", "-"),
        "status": "PENDING",
        "action": action,
        "risk": "SENSITIVE",
        "params": {},
        "reason": reason,
        "effects": "Changes Outlook for this Windows account.",
        "approver_roles": ["customer"],
        "expires_at": "2026-10-21T05:00:00Z",
        "decided_at": None,
    }


with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1360, "height": 900})
    page = ctx.new_page()
    watch(page)
    page.goto(BASE + "/")
    expect(page.get_by_role("heading", level=1)).to_contain_text("Tell it what's broken")
    page.wait_for_timeout(6000)
    shot(page, "01-home")
    for path in ("/features", "/pricing", "/security", "/contact"):
        page.goto(BASE + path)
        expect(page.get_by_role("heading", level=1)).to_be_visible()
    shot(page, "02-security")

    email = f"owner-{uuid.uuid4().hex[:8]}@example.com"
    page.goto(BASE + "/register")
    page.get_by_label("Your name").fill("Dana Owner")
    page.get_by_label("Work email").fill(email)
    page.get_by_label("Company").fill("Bayside Plumbing IT")
    page.get_by_label("Password").fill(PW)
    page.get_by_role("button", name="Create organization").click()
    expect(page.get_by_role("heading", name="Ticket queue")).to_be_visible(timeout=15000)

    for path, heading in [
        ("/admin/customers", "Customers"),
        ("/admin/users", "People"),
        ("/admin/knowledge", "Knowledge"),
        ("/admin/integrations", "Integrations"),
        ("/admin/policies", "Policies"),
        ("/admin/models", "AI models"),
        ("/admin/analytics", "Analytics"),
        ("/admin/billing", "Billing"),
        ("/admin/settings", "Settings & branding"),
        ("/admin/audit", "Audit log"),
        ("/console/runs", "AI runs"),
        ("/console/sessions", "Live sessions"),
        ("/console/research", "Research"),
        ("/approvals", "Approvals"),
        ("/notifications", "Notifications"),
        ("/account", "Your account"),
        ("/admin/devices", "Devices"),
    ]:
        page.goto(BASE + path)
        expect(page.get_by_role("heading", name=heading, exact=True)).to_be_visible(timeout=10000)
        if path in ("/admin/policies", "/admin/models", "/admin/analytics"):
            page.wait_for_timeout(800)
            shot(page, "03-" + path.strip("/").replace("/", "-"))

    # Customer account + invite through the UI
    page.goto(BASE + "/admin/customers")
    page.get_by_label("Company").fill("Acme HVAC")
    page.get_by_role("button", name="Add customer").click()
    expect(page.get_by_text("Acme HVAC")).to_be_visible()
    # Policy simulator shows a real decision
    page.goto(BASE + "/admin/policies")
    page.get_by_role("button", name="Check").click()
    expect(page.get_by_text("Require approval").first).to_be_visible()
    # Upload knowledge
    page.goto(BASE + "/admin/knowledge")
    with page.expect_response(
        lambda response: response.request.method == "GET"
        and "/api/v1/knowledge/documents?page_size=100" in response.url
        and response.status == 200
    ):
        with page.expect_response(
            lambda response: response.request.method == "POST"
            and "/api/v1/knowledge/documents" in response.url
            and response.status == 201
        ):
            page.locator("input[type=file]").set_input_files(
                {
                    "name": "outlook.md",
                    "mimeType": "text/markdown",
                    "buffer": b"# Outlook crashes on PDF attachments\n\nDisable the PdfPreview add-in from File > Options > Add-ins, then restart Outlook.\n",
                }
            )
    expect(page.get_by_text("Indexed").first).to_be_visible(timeout=20000)

    # Portal chat → ticket with assistant activity
    page.goto(BASE + "/portal")
    page.get_by_label(re.compile(r"^Message ")).fill(
        "Outlook crashes every time I open a PDF attachment since Tuesday's update. Version 2408."
    )
    page.get_by_role("button", name="Send").click()
    page.wait_for_url(re.compile(r"/portal\?t="), timeout=15000)
    ticket_id = page.url.split("?t=", 1)[1].split("&", 1)[0]
    ticket_url = BASE + f"/console/tickets/{ticket_id}"
    expect(page.get_by_text(re.compile(r"Saved as ticket"))).to_be_visible()
    page.wait_for_timeout(6000)
    shot(page, "04-ticket")

    # Real rendered components with API-shaped state fixtures exercise the safety UI without pretending a browser
    # can supply the separately tested signed Windows protocol evidence.
    run_state = {"value": run_fixture()}
    symptom_posts = []

    def route_runs(route):
        route.fulfill(status=200, content_type="application/json", body=json.dumps([run_state["value"]]))

    def route_symptom(route):
        symptom_posts.append(route.request.post_data_json)
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(
                {"result": route.request.post_data_json["result"], "outcome_stage": "FIX_APPLIED_VERIFIED"}
            ),
        )

    page.route(re.compile(r".*/api/v1/tickets/.*/runs.*"), route_runs)
    page.route("**/api/v1/agents/runs/ui-run/symptom-check", route_symptom)

    disable_message = (
        "Disable Contoso PDF Preview (Contoso.PdfPreview) for your Windows account. JoltIQ will save the current "
        "setting so it can be restored."
    )
    run_state["value"] = run_fixture(approvals=[approval_fixture("office.addin.set_load_behavior", disable_message)])
    page.goto(ticket_url)
    expect(page.get_by_text(disable_message, exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Approve this step")).to_be_visible()

    run_state["value"] = run_fixture(stage="FIX_APPLIED_VERIFIED", paused_for="symptom_check")
    page.goto(ticket_url)
    expect(page.get_by_text("Fix applied and checked", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="The PDF opens now")).to_be_visible()
    expect(page.get_by_role("button", name="I can't test this now")).to_be_visible()
    page.get_by_role("button", name="I can't test this now").click()
    assert symptom_posts and symptom_posts[-1]["result"] == "cannot_test"

    run_state["value"] = run_fixture(
        stage="FIX_PARTIALLY_APPLIED",
        paused_for="approval",
        recovery={"rollback_status": "OFFERED", "change_remains": True},
        messages=[
            {
                "id": "partial",
                "role": "assistant",
                "content": "The add-in was disabled, but Outlook could not be safely restarted. JoltIQ did not force it closed. The change has not been undone.",
                "data": {"outcome_stage": "FIX_PARTIALLY_APPLIED"},
                "at": "2026-10-21T02:00:00Z",
            }
        ],
        approvals=[
            approval_fixture(
                "office.addin.restore_load_behavior",
                "Restore the add-in setting saved by this PC for the earlier approved change",
            )
        ],
    )
    page.goto(ticket_url)
    expect(page.get_by_text("Part of the fix changed this PC", exact=True)).to_be_visible()
    expect(page.get_by_text("The change remains in place until rollback is verified.", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Approve this step")).to_be_visible()
    shot(page, "05-windows-recovery")

    # The portal session page renders the exact signed request copy rather than generic desktop wording.
    portal_message = (
        "JoltIQ found an approved fix for the Outlook PDF crash and is requesting permission to diagnose and work "
        "on this PC. You can stop the session at any time."
    )
    session_fixture = {
        "id": "ui-session",
        "kind": "desktop",
        "state": "pending_consent",
        "ticket_id": None,
        "device_id": "ui-device",
        "allowed_domains": [],
        "consent": None,
        "expires_at": None,
        "emergency_stopped": False,
        "pause_reason": None,
        "run_id": "ui-run",
        "control": None,
        "created_at": "2026-10-21T02:00:00Z",
        "can_consent": True,
        "you_hold_control": False,
        "request_details": {
            "portal_message": portal_message,
            "addin_name": "Contoso PDF Preview",
            "addin_progid": "Contoso.PdfPreview",
        },
    }
    page.route(
        "**/api/v1/sessions/ui-session",
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(session_fixture)),
    )
    page.goto(BASE + "/sessions/ui-session")
    expect(page.get_by_text(portal_message, exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Allow session")).to_be_visible()

    # An active desktop session offers two explicit control contracts. The mode, current operator, and STOP control
    # remain visible, and the selected mode is sent to the API rather than inferred from the goal text.
    control_session = {**session_fixture, "id": "ui-control", "state": "active", "consent": {"device_local_consent": True}}
    computer_use_posts = []

    page.route(
        "**/api/v1/sessions/ui-control",
        lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(control_session)),
    )

    def route_computer_use(route):
        if route.request.method == "GET":
            route.fulfill(status=200, content_type="application/json", body="[]")
            return
        computer_use_posts.append(route.request.post_data_json)
        route.fulfill(status=201, content_type="application/json", body=json.dumps({
            "id": "started-run", "goal": route.request.post_data_json["goal"], "mode": route.request.post_data_json["mode"],
            "state": "waiting_local_consent", "local_consent_state": "pending", "waiting_for": None,
            "step_count": 0, "max_steps": 60, "summary": None, "success": None, "live_model": True,
            "has_screen": False, "cost_usd": 0,
        }))

    page.route("**/api/v1/sessions/ui-control/computer-use", route_computer_use)
    page.goto(BASE + "/sessions/ui-control")
    expect(page.get_by_role("heading", name="How should JoltIQ help?")).to_be_visible()
    expect(page.get_by_text("You make every click", exact=True)).to_be_visible()
    expect(page.get_by_text("JoltIQ controls the computer", exact=True)).to_be_visible()
    expect(page.get_by_text("You have control", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Stop JoltIQ")).to_be_visible()
    shot(page, "06-computer-help")

    page.get_by_label("What should we fix?").fill("Show me how to open Windows Settings")
    page.get_by_text("Guide me", exact=True).click()
    expect(page.get_by_role("radio", name=re.compile("Guide me"))).to_be_checked()
    page.get_by_role("button", name="Start Guide mode").click()
    page.wait_for_timeout(250)
    assert len(computer_use_posts) == 1
    assert computer_use_posts[-1]["mode"] == "guide"

    page.get_by_label("What should we fix?").fill("Open Windows Settings for me")
    page.get_by_text("Fix it for me", exact=True).click()
    expect(page.get_by_role("radio", name=re.compile("Fix it for me"))).to_be_checked()
    page.get_by_role("button", name="Start Fix mode").click()
    page.wait_for_timeout(250)
    assert len(computer_use_posts) == 2
    assert computer_use_posts[-1]["mode"] == "fix"

    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(BASE + "/sessions/ui-control")
    expect(page.get_by_text("You have control", exact=True)).to_be_visible()
    expect(page.get_by_role("button", name="Stop JoltIQ")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    shot(page, "07-computer-help-mobile")
    page.set_viewport_size({"width": 1360, "height": 900})

    # Mobile customer controls stay within the viewport and retain accessible names.
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(ticket_url)
    expect(page.get_by_text("Part of the fix changed this PC", exact=True)).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    shot(page, "08-windows-recovery-mobile")
    page.set_viewport_size({"width": 1360, "height": 900})

    page.goto(BASE + "/console")
    expect(page.get_by_text("Outlook crashes").first).to_be_visible()
    shot(page, "09-queue")

    # Mobile layout renders
    m = b.new_context(viewport={"width": 390, "height": 844})
    mp = m.new_page()
    watch(mp)
    mp.goto(BASE + "/")
    mp.wait_for_timeout(6000)
    shot(mp, "10-home-mobile")

    # Sign out + protected route redirects to login
    page.get_by_role("button", name="Sign out").click()
    page.wait_for_url(re.compile(r"/login"))
    page.goto(BASE + "/admin/users")
    page.wait_for_url(re.compile(r"/login\?next="))
    b.close()

if errors:
    print("\n".join(errors))
    sys.exit(1)
print("UI smoke: all pages rendered without page errors or 5xx")
