"""Default V7 paths must not contact optional connector or control endpoints."""
from __future__ import annotations

import uuid


def test_registration_ticket_approval_device_and_oidc_have_zero_optional_egress(
    app, new_client, monkeypatch
):
    from conftest import register
    from test_approval_notifications import _request
    from test_device_protocol import FakeDevice, _code
    from test_oidc_login import _begin, _mock_protocol, _seed_provider, _set_owner_email

    from joltiq.services import browser_client, integrations, slack, teams

    calls = {"slack": 0, "teams": 0, "freshdesk": 0, "headless": 0}

    def counted(name):
        def record(*args, **kwargs):
            calls[name] += 1
            raise AssertionError(f"disabled {name} endpoint was contacted")
        return record

    monkeypatch.setattr(slack, "post", counted("slack"))
    monkeypatch.setattr(teams, "post", counted("teams"))
    monkeypatch.setattr(integrations, "Freshdesk", counted("freshdesk"))
    monkeypatch.setattr(browser_client, "_req", counted("headless"))

    owner = register(new_client(), "No optional egress")
    ticket = owner.post(
        "/api/v1/tickets",
        json={"subject": "Laptop cannot connect", "description": "Local-only test"},
    )
    assert ticket.status_code == 201
    approval_id, _ = _request(owner)
    assert approval_id
    capabilities = [
        "system.info", "screen.capture", "input.click", "input.type", "input.key",
        "input.scroll", "input.move", "url.open", "guide.highlight", "guide.clear",
    ]
    device = FakeDevice(app)
    assert device.enroll(_code(owner), caps=capabilities).status_code == 200
    created = device.post(
        "/support-requests",
        {"client_request_id": str(uuid.uuid4()), "message": "Please diagnose this PC and guide me."},
    )
    assert created.status_code == 201, created.text
    started = device.post(
        f"/support-requests/{created.json()['run_id']}/computer-use", {"mode": "guide"}
    )
    assert started.status_code == 201, started.text

    domain = f"no-egress-{uuid.uuid4().hex}.example"
    email = _set_owner_email(owner, domain)
    _seed_provider(owner, domain=domain)
    _mock_protocol(monkeypatch, email=email)
    oidc_client = new_client()
    state = _begin(oidc_client, email)
    callback = oidc_client.c.get(
        "/api/v1/auth/sso/callback",
        params={"state": state, "code": "no-egress-code"},
        follow_redirects=False,
    )
    assert callback.status_code == 302
    assert calls == {"slack": 0, "teams": 0, "freshdesk": 0, "headless": 0}
