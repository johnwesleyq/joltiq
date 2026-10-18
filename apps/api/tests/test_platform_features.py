"""Integrations, email, webhooks, notifications, billing/Stripe webhooks, entitlements, metering, branding, analytics,
policies, model config, audit, platform admin. External services are replaced by LOCAL FIXTURES (marked `mocked`):
a fake Freshdesk HTTP transport, a local SMTP server (aiosmtpd) and a local webhook receiver. Stripe webhook signatures
are generated with Stripe's documented scheme and verified by the real stripe SDK."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx
import pytest

from conftest import invite_and_join, register

pytestmark = pytest.mark.mocked


def drain():
    from joltiq.services.jobs import drain as d

    return d()


# ---------------- Freshdesk (fake transport) ----------------
class FakeFreshdesk:
    """In-memory stand-in for the Freshdesk v2 API. LOCAL TEST FIXTURE."""

    def __init__(self):
        self.tickets = {101: {"id": 101, "subject": "Printer offline in front office", "description_text": "HP printer shows offline",
                              "status": 2, "priority": 3, "updated_at": "2026-10-17T10:00:00Z"}}
        self.notes, self.status_updates = [], []

    def handler(self, req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"].startswith("Basic ")
        p = req.url.path
        if p == "/api/v2/agents/me":
            return httpx.Response(200, json={"id": 7, "contact": {"email": "agent@acme.test"}})
        if p == "/api/v2/tickets" and req.method == "GET":
            return httpx.Response(200, json=list(self.tickets.values()))
        if p.endswith("/notes"):
            self.notes.append(json.loads(req.content))
            return httpx.Response(201, json={"id": len(self.notes)})
        if req.method == "GET" and p.startswith("/api/v2/tickets/"):
            return httpx.Response(200, json=self.tickets[int(p.rsplit("/", 1)[1])])
        if req.method == "PUT":
            body = json.loads(req.content)
            self.status_updates.append(body)
            return httpx.Response(200, json={"id": 101, **body})
        return httpx.Response(404)


def test_freshdesk_connect_test_pull_push_and_idempotency(owner):
    from joltiq.services import integrations

    fake = FakeFreshdesk()
    integrations._TRANSPORT_OVERRIDE["freshdesk"] = httpx.MockTransport(fake.handler)
    try:
        r = owner.post("/api/v1/integrations", json={"kind": "freshdesk", "name": "Acme FD", "config": {"domain": "acme"}, "secret": "fd_api_key_123"})
        assert r.status_code == 201, r.text
        i = r.json()
        assert i["credential"]["fingerprint"] and "fd_api_key_123" not in json.dumps(i)
        assert owner.post(f"/api/v1/integrations/{i['id']}/test").json()["result"]["ok"] is True
        assert owner.post(f"/api/v1/integrations/{i['id']}/sync").json()["queued"] is True
        drain()
        tickets = owner.get("/api/v1/tickets", params={"q": "printer"}).json()["items"]
        assert len(tickets) == 1 and tickets[0]["external_ref"] == "freshdesk:101"
        # Second pull does not duplicate.
        from joltiq.db import session_scope
        from joltiq.models import Integration

        with session_scope(uuid.UUID(owner.org_id)) as db:
            integrations._pull_freshdesk(db, db.get(Integration, uuid.UUID(i["id"])), integrations.freshdesk_for(db, db.get(Integration, uuid.UUID(i["id"]))))
        assert len(owner.get("/api/v1/tickets", params={"q": "printer"}).json()["items"]) == 1
        # Push a comment back as a note (idempotent per comment).
        owner.post(f"/api/v1/tickets/{tickets[0]['id']}/comments", json={"body": "Restarted the spooler remotely."})
        drain()
        assert fake.notes and fake.notes[-1]["body"] == "Restarted the spooler remotely."
        hist = owner.get(f"/api/v1/integrations/{i['id']}").json()["history"]
        assert any(h["entity"] == "note" and h["status"] == "ok" for h in hist)
        # Rotate + revoke.
        assert owner.post(f"/api/v1/integrations/{i['id']}/rotate", json={"secret": "new_fd_key_456"}).json()["status"] == "disconnected"
        assert owner.post(f"/api/v1/integrations/{i['id']}/revoke").json()["status"] == "revoked"
    finally:
        integrations._TRANSPORT_OVERRIDE.clear()


def test_extension_points_are_not_faked(owner):
    cat = owner.get("/api/v1/integrations/catalog").json()
    assert "zendesk" in [e["kind"] for e in cat["extension_points"]]
    r = owner.post("/api/v1/integrations", json={"kind": "zendesk", "name": "zd", "config": {}})
    assert r.status_code == 501 and r.json()["error"]["code"] == "integration_not_implemented"


def test_generic_rest_inbound_with_secret(app, owner):
    from conftest import ApiClient

    r = owner.post("/api/v1/integrations", json={"kind": "generic_rest", "name": "RMM alerts"}).json()
    secret = r["generated_secret"]
    anon = ApiClient(app).c
    url = f"/api/v1/integrations/inbound/{r['id']}/tickets"
    assert anon.post(url, json={"subject": "Disk almost full on SRV1"}, headers={"authorization": "Bearer wrong"}).status_code == 401
    a = anon.post(url, json={"subject": "Disk almost full on SRV1", "external_ref": "alert-9"}, headers={"authorization": f"Bearer {secret}"})
    b = anon.post(url, json={"subject": "Disk almost full on SRV1", "external_ref": "alert-9"}, headers={"authorization": f"Bearer {secret}"})
    assert a.status_code == 201 and b.json()["duplicate"] is True and a.json()["id"] == b.json()["id"]


def test_email_intake_creates_ticket_and_threads_reply(app, owner):
    from conftest import ApiClient

    cust_acct = owner.post("/api/v1/customers", json={"name": "Bayside Plumbing"}).json()
    cust = invite_and_join(app, owner, "customer", cust_acct["id"])
    integ = owner.post("/api/v1/integrations", json={"kind": "email_intake", "name": "support inbox", "secret": "inbox-secret-123"}).json()
    anon = ApiClient(app).c
    raw = (f"From: Pat <{cust.email}>\r\nTo: help@acme.test\r\nSubject: QuickBooks won't open\r\nContent-Type: text/plain\r\n\r\n"
           "QuickBooks shows error H202 when opening the company file.\r\n").encode()
    r = anon.post(f"/api/v1/integrations/email/inbound/{integ['id']}", content=raw, headers={"authorization": "Bearer inbox-secret-123"})
    assert r.status_code == 200 and r.json()["action"] == "created" and r.json()["known_sender"] is True
    num = r.json()["number"]
    reply = (f"From: {cust.email}\r\nSubject: Re: [#{num}] QuickBooks won't open\r\n\r\nIt happens on two PCs.\r\n\r\nOn Mon someone wrote:\r\n> old\r\n").encode()
    r2 = anon.post(f"/api/v1/integrations/email/inbound/{integ['id']}", content=reply, headers={"authorization": "Bearer inbox-secret-123"})
    assert r2.json()["action"] == "comment"
    t = cust.get(f"/api/v1/tickets/{r.json()['ticket_id']}").json()
    assert t["comments"][-1]["body"] == "It happens on two PCs."


# ---------------- SMTP (local server) ----------------
@pytest.fixture(scope="module")
def smtp():
    from aiosmtpd.controller import Controller

    class H:
        def __init__(self):
            self.messages = []

        async def handle_DATA(self, server, session, envelope):  # noqa: N802
            self.messages.append(envelope)
            return "250 OK"

    h = H()
    c = Controller(h, hostname="127.0.0.1", port=int(os.environ["JOLTIQ_SMTP_PORT"]))
    c.start()
    yield h
    c.stop()


def test_invitation_email_delivered_via_smtp_with_tenant_branding(smtp, owner):
    owner.patch("/api/v1/admin/branding", json={"product_name": "Bayside IT Help", "support_email": "help@bayside.test"})
    owner.post("/api/v1/users/invitations", json={"email": "newtech@example.com", "role": "technician"})
    drain()
    msgs = [m for m in smtp.messages if "newtech@example.com" in m.rcpt_tos]
    assert msgs
    body = msgs[-1].content.decode()
    assert "Bayside IT Help" in body and "accept-invite?token=" in body


# ---------------- Webhooks (local receiver) ----------------
def test_outbound_webhook_signed_and_retried(owner):
    received = []
    fail_first = {"n": 1}

    class R(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = self.rfile.read(int(self.headers["content-length"]))
            if fail_first["n"]:
                fail_first["n"] -= 1
                self.send_response(500)
                self.end_headers()
                return
            received.append((dict(self.headers), body))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), R)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        hook = owner.post("/api/v1/admin/webhooks", json={"url": f"http://127.0.0.1:{srv.server_port}/hook", "categories": ["ticket"]}).json()
        owner.post("/api/v1/tickets", json={"subject": "Webhook test ticket"})
        drain()  # first attempt fails (500) → retried later
        from joltiq.db import session_scope
        from sqlalchemy import text

        with session_scope(None) as db:
            db.execute(text("update jobs set run_after = now() where kind = 'webhook.deliver' and status = 'queued'"))
        drain()
        assert received, "webhook not delivered after retry"
        headers, body = received[-1]
        sig = hmac.new(hook["signing_secret"].encode(), f"{headers['x-joltiq-timestamp']}.{body.decode()}".encode(), hashlib.sha256).hexdigest()
        assert headers["x-joltiq-signature"] == f"v1={sig}"
    finally:
        srv.shutdown()


def test_webhook_ssrf_blocked_in_production_style_check(owner):
    r = owner.post("/api/v1/integrations", json={"kind": "generic_webhook", "name": "bad", "config": {"url": "http://169.254.169.254/latest/meta-data"}})
    assert r.status_code == 400 and r.json()["error"]["code"] == "url_blocked"


# ---------------- Notifications ----------------
def test_notifications_and_preferences(app, owner):
    tech = invite_and_join(app, owner, "technician")
    owner.post("/api/v1/tickets", json={"subject": "Notify me"})
    n = tech.get("/api/v1/notifications").json()
    assert n["unread_count"] >= 1 and any("Notify me" in i["title"] for i in n["items"])
    assert tech.post("/api/v1/notifications/read", json={}).json()["marked"] >= 1
    assert tech.put("/api/v1/notifications/preferences", json={"category": "ticket", "in_app": False, "email": False}).status_code == 200
    owner.post("/api/v1/tickets", json={"subject": "Silent one"})
    assert not any("Silent one" in i["title"] for i in tech.get("/api/v1/notifications").json()["items"])
    assert tech.put("/api/v1/notifications/preferences", json={"category": "security", "email": False}).status_code == 400


# ---------------- Billing / Stripe ----------------
def _stripe_sig(payload: str, secret: str) -> str:
    t = int(time.time())
    v1 = hmac.new(secret.encode(), f"{t}.{payload}".encode(), hashlib.sha256).hexdigest()
    return f"t={t},v1={v1}"


def test_stripe_webhook_signature_idempotency_and_plan_change(owner, monkeypatch):
    from joltiq.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "stripe_webhook_secret", "whsec_test_local")
    from conftest import ApiClient

    anon = ApiClient(owner.c.app).c
    ev = {"id": f"evt_{uuid.uuid4().hex}", "type": "customer.subscription.updated", "livemode": False, "object": "event",
          "data": {"object": {"id": "sub_123", "object": "subscription", "status": "active", "cancel_at_period_end": False,
                              "current_period_end": int(time.time()) + 86400 * 30, "metadata": {"org_id": owner.org_id, "plan": "growth"},
                              "items": {"data": [{"price": {"lookup_key": "joltiq_growth_month"}}]}}}}
    payload = json.dumps(ev)
    bad = anon.post("/api/v1/billing/webhook", content=payload, headers={"Stripe-Signature": "t=1,v1=deadbeef"})
    assert bad.status_code == 400
    ok = anon.post("/api/v1/billing/webhook", content=payload, headers={"Stripe-Signature": _stripe_sig(payload, "whsec_test_local")})
    assert ok.status_code == 200 and ok.json()["result"] == "processed"
    dup = anon.post("/api/v1/billing/webhook", content=payload, headers={"Stripe-Signature": _stripe_sig(payload, "whsec_test_local")})
    assert dup.json()["result"] == "duplicate"
    b = owner.get("/api/v1/billing").json()
    assert b["subscription"]["plan"] == "growth" and b["subscription"]["status"] == "active" and b["limits"]["technicians"] == 10


def test_stripe_webhook_retry_after_failure(owner, monkeypatch):
    """A processing failure leaves the event unprocessed (Stripe retries); the retry succeeds exactly once."""
    from joltiq.config import get_settings
    from joltiq.services import billing

    monkeypatch.setattr(get_settings(), "stripe_webhook_secret", "whsec_test_local")
    from conftest import ApiClient

    anon = ApiClient(owner.c.app).c
    ev = {"id": f"evt_{uuid.uuid4().hex}", "type": "invoice.payment_failed", "livemode": False, "object": "event",
          "data": {"object": {"id": "in_1", "object": "invoice", "metadata": {"org_id": owner.org_id}}}}
    payload = json.dumps(ev)
    real = billing.process_event
    calls = {"n": 0}

    def flaky(db, event):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("transient DB error")
        return real(db, event)

    monkeypatch.setattr(billing, "process_event", flaky)
    r1 = anon.post("/api/v1/billing/webhook", content=payload, headers={"Stripe-Signature": _stripe_sig(payload, "whsec_test_local")})
    assert r1.status_code == 500  # Stripe will retry
    r2 = anon.post("/api/v1/billing/webhook", content=payload, headers={"Stripe-Signature": _stripe_sig(payload, "whsec_test_local")})
    assert r2.json()["result"] == "processed"
    assert owner.get("/api/v1/billing").json()["subscription"]["status"] == "past_due"


def test_checkout_reports_unconfigured_stripe_honestly(owner):
    r = owner.post("/api/v1/billing/checkout", json={"plan": "starter"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "billing_not_configured"


def test_entitlements_enforced_and_metering_idempotent(owner):
    from joltiq.db import session_scope
    from joltiq.services import billing

    org = uuid.UUID(owner.org_id)
    with session_scope(org) as db:
        assert billing.meter(db, org, "research_requests", 1, "k1") is True
        assert billing.meter(db, org, "research_requests", 1, "k1") is False  # duplicate never double-charges
        assert billing.usage_total(db, org, "research_requests") == 1
    # Trial allows 3 technicians: the 3rd invite (owner counts? no — technicians+admins) beyond limit is refused.
    codes = [owner.post("/api/v1/users/invitations", json={"email": f"t{i}-{uuid.uuid4().hex[:4]}@x.com", "role": "technician"}).status_code for i in range(3)]
    assert codes == [201, 201, 201]
    for i in range(3):
        from conftest import PASSWORD, ApiClient  # noqa: F401
    # Accepted memberships are what count; simulate by direct role grant.
    with session_scope(org) as db:
        from joltiq.models import Membership, User

        for i in range(3):
            u = User(email=f"seat{i}-{uuid.uuid4().hex[:6]}@x.com", name="s")
            db.add(u)
            db.flush()
            db.add(Membership(user_id=u.id, org_id=org, role="technician"))
    r = owner.post("/api/v1/users/invitations", json={"email": "one-too-many@x.com", "role": "technician"})
    assert r.status_code == 402 and r.json()["error"]["code"] == "entitlement_exceeded"


# ---------------- Admin: models / policies / branding / analytics / audit ----------------
def test_model_keys_encrypted_never_returned_and_unapproved_providers_unused(owner):
    r = owner.patch("/api/v1/admin/models/openai", json={"enabled": True, "api_key": "sk-test-secret-key-0000000000", "approved_for_tenant_data": False})
    assert r.status_code == 200 and "sk-test" not in r.text and r.json()["key_fingerprint"]
    from joltiq.db import session_scope
    from joltiq.services.ai.router import available_providers

    with session_scope(uuid.UUID(owner.org_id)) as db:
        assert [p[0] for p in available_providers(db, uuid.UUID(owner.org_id), "chat")] == ["local_fixture"]
    owner.patch("/api/v1/admin/models/openai", json={"approved_for_tenant_data": True})
    with session_scope(uuid.UUID(owner.org_id)) as db:
        assert available_providers(db, uuid.UUID(owner.org_id), "chat")[0][0] == "openai"


def test_policy_crud_simulate_and_guardrails(owner):
    sim = owner.post("/api/v1/admin/policies/simulate", json={"action": "service.restart", "params": {"name": "Spooler"}}).json()
    assert sim["effect"] == "require_approval" and sim["risk"] == "PRIVILEGED"
    assert owner.post("/api/v1/admin/policies/simulate", json={"action": "delete.anything", "params": {"target": "x"}}).json()["effect"] == "deny"
    r = owner.post("/api/v1/admin/policies", json={"name": "never restart spooler", "priority": 1, "effect": "deny", "match": {"action": "service.restart"}})
    assert r.status_code == 201
    assert owner.post("/api/v1/admin/policies/simulate", json={"action": "service.restart", "params": {"name": "Spooler"}}).json()["effect"] == "deny"
    bad = owner.post("/api/v1/admin/policies", json={"name": "yolo", "effect": "allow", "match": {"risk_at_least": "DESTRUCTIVE"}})
    assert bad.status_code == 400
    assert owner.post("/api/v1/admin/policies/simulate", json={"action": "shell.exec", "params": {}}).json()["effect"] == "deny"


def test_branding_public_lookup_and_validation(app, owner):
    from conftest import ApiClient

    dom = f"help{uuid.uuid4().hex[:6]}.example.com"
    r = owner.patch("/api/v1/admin/branding", json={"product_name": "Acme Assist", "primary_color": "#0055ff", "custom_domain": dom})
    assert r.status_code == 200 and r.json()["custom_domain_dns"]
    pub = ApiClient(app).get("/api/v1/branding/public", params={"host": dom}).json()
    assert pub["product_name"] == "Acme Assist"
    assert owner.patch("/api/v1/admin/branding", json={"logo_url": "javascript:alert(1)"}).status_code == 400
    assert ApiClient(app).get("/api/v1/branding/public").json()["product_name"] == "JoltIQ"


def test_analytics_and_audit_endpoints(app, owner):
    tech = invite_and_join(app, owner, "technician")
    d = owner.get("/api/v1/analytics/dashboard").json()
    assert "verified_resolution_rate" in d and "cost_per_verified_resolution_usd" in d
    assert owner.get("/api/v1/audit/verify").json()["ok"] is True
    assert owner.get("/api/v1/audit").json()["total"] >= 1
    assert tech.get("/api/v1/audit").status_code == 403
    exp = owner.get("/api/v1/audit/export")
    assert exp.status_code == 200 and exp.text.strip().splitlines()[0].startswith("{")


def test_platform_admin_requires_platform_owner_and_suspension(app, owner, new_client):
    assert owner.get("/api/v1/admin/platform/tenants").status_code == 403
    from joltiq.db import session_scope
    from joltiq.models import User
    from sqlalchemy import select

    p = register(new_client(), "Platform Co")
    with session_scope(None) as db:
        db.execute(select(User).where(User.email == p.email)).scalar_one().is_platform_owner = True
    t = p.get("/api/v1/admin/platform/tenants", params={"page_size": 200}).json()
    assert any(x["id"] == owner.org_id for x in t["items"])
    assert "queue" in p.get("/api/v1/admin/platform/health").json()
    assert p.patch(f"/api/v1/admin/platform/tenants/{owner.org_id}", json={"status": "suspended"}).status_code == 200
    r = owner.get("/api/v1/tickets")
    assert r.status_code == 403 and r.json()["error"]["code"] == "org_suspended"
    p.patch(f"/api/v1/admin/platform/tenants/{owner.org_id}", json={"status": "active"})
    assert owner.get("/api/v1/tickets").status_code == 200


def test_knowledge_ingest_search_permissions_and_url_ssrf(app, owner):
    tech = invite_and_join(app, owner, "technician")
    acct = owner.post("/api/v1/customers", json={"name": "C"}).json()
    cust = invite_and_join(app, owner, "customer", acct["id"])
    md = b"# VPN drops\n\nGlobalProtect disconnects every 10 minutes. Set the keepalive to 30 seconds in the portal config.\n"
    r = tech.post("/api/v1/knowledge/documents", files={"file": ("vpn.md", md, "text/markdown")}, data={"title": "VPN runbook"})
    assert r.status_code == 201
    internal = tech.post("/api/v1/knowledge/documents", files={"file": ("secret.md", b"# Admin passwords rotation\n\nRotate the domain admin quarterly.", "text/markdown")})
    drain()
    hits = tech.post("/api/v1/knowledge/search", json={"query": "vpn keeps disconnecting"}).json()
    assert hits["results"] and hits["results"][0]["title"] == "VPN runbook" and "§ VPN drops" in hits["results"][0]["citation"]
    # Customers only see customer-visible documents.
    assert cust.post("/api/v1/knowledge/search", json={"query": "domain admin rotation"}).json()["results"] == []
    tech.patch(f"/api/v1/knowledge/documents/{r.json()['id']}", json={"visibility": ["customer", "technician"]})
    assert cust.post("/api/v1/knowledge/search", json={"query": "vpn disconnect"}).json()["results"]
    # SSRF: internal/metadata URLs refused at ingestion.
    for u in ("http://169.254.169.254/latest/meta-data/", "http://localhost:8000/", "file:///etc/passwd", "http://10.0.0.5/admin"):
        assert tech.post("/api/v1/knowledge/documents/url", json={"url": u}).status_code in (400, 422), u
    # Delete removes chunks from retrieval.
    tech.delete(f"/api/v1/knowledge/documents/{r.json()['id']}")
    assert all(h["title"] != "VPN runbook" for h in tech.post("/api/v1/knowledge/search", json={"query": "vpn"}).json()["results"])
    _ = internal
