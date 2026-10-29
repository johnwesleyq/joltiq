"""Default V7 paths must not contact optional connector or control endpoints."""
from __future__ import annotations


def test_registration_ticket_approval_device_and_oidc_have_zero_optional_egress(
    app, new_client, monkeypatch
):
    from conftest import register
    from test_approval_notifications import _request

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
    assert owner.post("/api/v1/devices/enrollment-codes", json={}).status_code == 201

    lookup = new_client().post(
        "/api/v1/auth/sso/lookup", json={"email": owner.email}
    )
    assert lookup.status_code == 200
    assert lookup.json()["available"] is False
    assert calls == {"slack": 0, "teams": 0, "freshdesk": 0, "headless": 0}
