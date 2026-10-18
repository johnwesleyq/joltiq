"""Real-browser UI smoke test against a running stack (web :3000 → API :8000). Registers an org, invites a customer,
files a ticket through the portal chat, and visits every major page, failing on any uncaught page error or API 5xx.
Usage: python tests/e2e/web/ui_smoke.py [base_url] [screenshot_dir]"""
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


with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(viewport={"width": 1360, "height": 900})
    page = ctx.new_page()
    watch(page)
    page.goto(BASE + "/")
    expect(page.get_by_role("heading", level=1)).to_contain_text("checks its own work")
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

    for path, heading in [("/admin/customers", "Customers"), ("/admin/users", "People"), ("/admin/knowledge", "Knowledge"), ("/admin/integrations", "Integrations"),
                          ("/admin/policies", "Policies"), ("/admin/models", "AI models"), ("/admin/analytics", "Analytics"), ("/admin/billing", "Billing"),
                          ("/admin/settings", "Settings & branding"), ("/admin/audit", "Audit log"), ("/console/runs", "AI runs"), ("/console/sessions", "Live sessions"),
                          ("/console/research", "Research"), ("/approvals", "Approvals"), ("/notifications", "Notifications"), ("/account", "Your account"),
                          ("/admin/devices", "Devices")]:
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
    page.locator("input[type=file]").set_input_files({"name": "outlook.md", "mimeType": "text/markdown",
        "buffer": b"# Outlook crashes on PDF attachments\n\nDisable the PdfPreview add-in from File > Options > Add-ins, then restart Outlook.\n"})
    expect(page.get_by_text("Indexed").first).to_be_visible(timeout=20000)

    # Portal chat → ticket with assistant activity
    page.goto(BASE + "/portal")
    page.get_by_label("Describe the problem").fill("Outlook crashes every time I open a PDF attachment since Tuesday's update. Version 2408.")
    page.get_by_role("button", name="Get help").click()
    page.wait_for_url(re.compile(r"/portal/tickets/"), timeout=15000)
    expect(page.get_by_text("Assistant", exact=True).first).to_be_visible()
    page.wait_for_timeout(6000)
    shot(page, "04-ticket")
    page.goto(BASE + "/console")
    expect(page.get_by_text("Outlook crashes").first).to_be_visible()
    shot(page, "05-queue")

    # Mobile layout renders
    m = b.new_context(viewport={"width": 390, "height": 844})
    mp = m.new_page()
    watch(mp)
    mp.goto(BASE + "/")
    mp.wait_for_timeout(6000)
    shot(mp, "06-home-mobile")

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
