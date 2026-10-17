import uuid

from conftest import register


def _new_ticket(c, **kw):
    body = {"subject": "Outlook crashes when opening attachments", "description": "Since Tuesday Outlook closes when I open a PDF attachment.", **kw}
    r = c.post("/api/v1/tickets", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_ticket_lifecycle(org_team):
    owner, tech, cust = org_team["owner"], org_team["tech"], org_team["customer"]
    t = _new_ticket(cust)
    assert t["status"] == "NEW" and t["number"] >= 1000
    assert t["customer_id"] == org_team["customer_account"]["id"]
    assert t["resolution_due"] is not None  # SLA applied

    tid = t["id"]
    tech_id = tech.me["user"]["id"]
    assert owner.post(f"/api/v1/tickets/{tid}/assign", json={"assignee_id": tech_id}).status_code == 200
    assert tech.post(f"/api/v1/tickets/{tid}/status", json={"status": "IN_PROGRESS"}).status_code == 200
    tech.post(f"/api/v1/tickets/{tid}/comments", json={"body": "Internal: likely add-in", "internal": True})
    tech.post(f"/api/v1/tickets/{tid}/comments", json={"body": "Can you tell me your Outlook version?"})

    seen_by_customer = cust.get(f"/api/v1/tickets/{tid}").json()
    assert [c["body"] for c in seen_by_customer["comments"]] == ["Can you tell me your Outlook version?"]
    assert all(e["kind"] != "internal_note" for e in seen_by_customer["timeline"])
    seen_by_tech = tech.get(f"/api/v1/tickets/{tid}").json()
    assert len(seen_by_tech["comments"]) == 2
    assert seen_by_tech["first_responded_at"] is not None

    # Invalid transition is rejected.
    r = tech.post(f"/api/v1/tickets/{tid}/status", json={"status": "CLOSED"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "invalid_transition"
    # Org requires verification or a technician note to resolve.
    r = tech.post(f"/api/v1/tickets/{tid}/status", json={"status": "RESOLVED"})
    assert r.json()["error"]["code"] == "verification_required"
    r = tech.post(f"/api/v1/tickets/{tid}/status", json={"status": "RESOLVED", "resolution_note": "Disabled faulty add-in remotely; customer confirmed."})
    assert r.status_code == 200 and r.json()["status"] == "RESOLVED"
    # Customer may reopen but not escalate.
    assert cust.post(f"/api/v1/tickets/{tid}/status", json={"status": "ESCALATED"}).status_code == 403
    assert cust.post(f"/api/v1/tickets/{tid}/status", json={"status": "REOPENED", "reason": "Crashed again"}).status_code == 200


def test_customer_sees_only_own_tickets(app, org_team):
    from conftest import invite_and_join

    owner, cust = org_team["owner"], org_team["customer"]
    other_account = owner.post("/api/v1/customers", json={"name": "Other HVAC"}).json()
    other_cust = invite_and_join(app, owner, "customer", other_account["id"])
    mine = _new_ticket(cust)
    theirs = _new_ticket(other_cust)
    ids = [t["id"] for t in cust.get("/api/v1/tickets").json()["items"]]
    assert mine["id"] in ids and theirs["id"] not in ids
    assert cust.get(f"/api/v1/tickets/{theirs['id']}").status_code == 404
    # Customers can't post internal notes
    assert cust.post(f"/api/v1/tickets/{mine['id']}/comments", json={"body": "x", "internal": True}).status_code == 403


def test_search_filter_and_similar(org_team):
    tech = org_team["tech"]
    a = _new_ticket(tech, subject="VPN client disconnects every 10 minutes", description="GlobalProtect VPN keeps dropping")
    _new_ticket(tech, subject="Printer jam on floor 2", description="HP LaserJet paper jam")
    b = _new_ticket(tech, subject="VPN disconnects after sleep", description="VPN drops after laptop sleeps")
    res = tech.get("/api/v1/tickets", params={"q": "vpn"}).json()
    assert {a["id"], b["id"]} <= {t["id"] for t in res["items"]}
    assert all("Printer" not in t["subject"] for t in res["items"])
    sim = tech.get(f"/api/v1/tickets/{a['id']}/similar").json()["items"]
    assert b["id"] in [s["id"] for s in sim]
    by_number = tech.get("/api/v1/tickets", params={"q": f"#{a['number']}"}).json()
    assert by_number["items"][0]["id"] == a["id"]


def test_idempotent_create(org_team):
    tech = org_team["tech"]
    key = uuid.uuid4().hex
    r1 = tech.post("/api/v1/tickets", json={"subject": "Idem test", "description": ""}, headers={"Idempotency-Key": key})
    r2 = tech.post("/api/v1/tickets", json={"subject": "Idem test", "description": ""}, headers={"Idempotency-Key": key})
    assert r1.json()["id"] == r2.json()["id"]


def test_attachment_upload_validation(org_team):
    tech = org_team["tech"]
    t = _new_ticket(tech)
    ok = tech.post(f"/api/v1/tickets/{t['id']}/attachments", files={"file": ("log.txt", b"Faulting module: olmapi32.dll", "text/plain")})
    assert ok.status_code == 201
    exe = tech.post(f"/api/v1/tickets/{t['id']}/attachments", files={"file": ("evil.txt", b"MZ\x90\x00binary", "text/plain")})
    assert exe.status_code == 400 and exe.json()["error"]["code"] == "upload_rejected"
    fake_pdf = tech.post(f"/api/v1/tickets/{t['id']}/attachments", files={"file": ("x.pdf", b"not a pdf", "application/pdf")})
    assert fake_pdf.status_code == 400
    bad_ext = tech.post(f"/api/v1/tickets/{t['id']}/attachments", files={"file": ("run.ps1", b"Get-Process", "text/plain")})
    assert bad_ext.status_code == 400
    dl = tech.get(f"/api/v1/tickets/{t['id']}/attachments/{ok.json()['id']}")
    assert dl.status_code == 200 and dl.content == b"Faulting module: olmapi32.dll"


def test_cross_tenant_ticket_isolation(app, new_client, org_team):
    """A user from another organization can never see, guess or modify this org's tickets — enforced by RLS."""
    t = _new_ticket(org_team["tech"])
    attacker = register(new_client(), "Attacker Org")
    assert attacker.get(f"/api/v1/tickets/{t['id']}").status_code == 404
    assert attacker.post(f"/api/v1/tickets/{t['id']}/comments", json={"body": "hi"}).status_code == 404
    assert t["id"] not in [x["id"] for x in attacker.get("/api/v1/tickets").json()["items"]]
    assert attacker.get(f"/api/v1/tickets/{t['id']}/attachments/{uuid.uuid4()}").status_code == 404
