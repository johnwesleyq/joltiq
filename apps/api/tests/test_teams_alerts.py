"""Microsoft Teams approval alerts (ADR 0022). Teams is a labelled in-process MOCK transport: nothing is ever sent
to real Teams."""
import json
import uuid

import httpx
import pytest
from conftest import invite_and_join
from test_approval_notifications import _audit, _request, _uid

HOOK = "https://contoso.webhook.office.com/webhookb2/mock-0000/IncomingWebhook/mockmockmock/mock"
FLOW = "https://prod-00.westus.logic.azure.com/workflows/mockflow/triggers/manual/paths/invoke?sig=mocksigmocksig"


class MockTeams:
    """MOCK Teams (test only): records requests, answers like a connector webhook."""

    def __init__(self):
        self.calls: list[dict] = []
        self.fail = False

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.calls.append({"url": str(req.url), "body": json.loads(req.content)})
        return httpx.Response(500, text="mock_failure") if self.fail else httpx.Response(200, text="1")


@pytest.fixture()
def teams():
    from joltiq.services import integrations

    m = MockTeams()
    integrations._TRANSPORT_OVERRIDE["teams"] = httpx.MockTransport(m)
    yield m
    integrations._TRANSPORT_OVERRIDE.pop("teams", None)


def _drain():
    from joltiq.services.jobs import drain

    drain(["teams.deliver"])


def _connect(c, **cfg):
    return c.post("/api/v1/integrations", json={"kind": "teams", "name": "Teams", "config": cfg})


def _card(call):
    (att,) = call["body"]["attachments"]
    assert call["body"]["type"] == "message" and att["contentType"] == "application/vnd.microsoft.card.adaptive"
    return att["content"]


def test_teams_url_allowlist():
    from joltiq.core.errors import AppError
    from joltiq.services.teams import validate_webhook_url

    for ok in (HOOK, FLOW, "https://outlook.office.com/webhook/abc/IncomingWebhook/x/y",
               "https://default0a1b.0a.environment.api.powerplatform.com:443/powerautomate/automations/direct/workflows/x/triggers/manual/paths/invoke"):
        validate_webhook_url(ok)
    for bad in ("http://contoso.webhook.office.com/webhookb2/x", "https://evil.example.com/webhookb2/x", "https://webhook.office.com.evil.io/webhookb2/x",
                "https://contoso.webhook.office.com/other/x", "https://user:pw@contoso.webhook.office.com/webhookb2/x",
                "https://evil.logic.azure.com.example.com/workflows/x", "http://mock-teams:8610/webhook/mock", ""):
        with pytest.raises(AppError):
            validate_webhook_url(bad)


def test_connect_test_settings_and_no_secret_leak(app, owner, teams):
    assert _connect(owner, webhook_url="https://evil.example.com/webhookb2/x").status_code in (400, 422)
    assert _connect(owner, webhook_url=HOOK, use_mock=True).status_code in (400, 422)  # no MOCK base configured here
    assert _connect(owner).status_code in (400, 422)
    assert _connect(owner, webhook_url=HOOK, events=["nope"]).status_code in (400, 422)
    r = _connect(owner, webhook_url=FLOW)
    assert r.status_code == 201, r.text
    i = r.json()
    assert i["config"]["flavour"] == "power_automate" and len(i["config"]["events"]) == 3
    dumped = json.dumps(owner.get(f"/api/v1/integrations/{i['id']}").json()) + json.dumps(owner.get("/api/v1/integrations").json())
    assert "logic.azure.com" not in dumped and "mocksig" not in dumped
    t = owner.post(f"/api/v1/integrations/{i['id']}/test").json()
    assert t["result"]["ok"] and t["integration"]["status"] == "connected" and teams.calls[-1]["url"] == FLOW
    assert "connected" in json.dumps(_card(teams.calls[-1]))
    assert owner.post(f"/api/v1/integrations/{i['id']}/rotate", json={"secret": "x"}).status_code in (400, 422)
    assert owner.patch(f"/api/v1/integrations/{i['id']}/teams", json={"events": ["bad"]}).status_code in (400, 422)
    r = owner.patch(f"/api/v1/integrations/{i['id']}/teams", json={"events": ["approval_outcome"]})
    assert r.status_code == 200 and r.json()["config"]["events"] == ["approval_outcome"]
    assert _audit(owner.org_id, "integration.teams_settings_updated") and _audit(owner.org_id, "teams.test")
    teams.fail = True
    t = owner.post(f"/api/v1/integrations/{i['id']}/test").json()
    assert not t["result"]["ok"] and "500" in t["result"]["detail"] and "logic.azure" not in t["result"]["detail"]
    assert owner.post(f"/api/v1/integrations/{i['id']}/revoke").json()["status"] == "revoked"
    from sqlalchemy import select

    from joltiq.db import session_scope
    from joltiq.models import AuditEvent, Job

    with session_scope(uuid.UUID(owner.org_id)) as db:
        blob = json.dumps([e.data for e in db.execute(select(AuditEvent)).scalars()], default=str)
    with session_scope(None) as db:
        blob += json.dumps([j.payload for j in db.execute(select(Job)).scalars()], default=str)
    assert "mocksig" not in blob and "logic.azure.com" not in blob


def test_adaptive_card_dedupe_events_rate_limit_and_retry(app, owner, teams):
    from joltiq.db import session_scope
    from joltiq.models import Approval, Organization
    from joltiq.services import approval_notify

    _connect(owner, webhook_url=HOOK, events=["approval_requested", "approval_outcome"])
    tech = invite_and_join(app, owner, "technician")
    aid, _ = _request(owner, started_by=_uid(tech))
    with session_scope(uuid.UUID(owner.org_id)) as db:
        approval_notify.on_requested(db, db.get(Approval, aid))  # retried event → still one message
    _drain()
    assert len(teams.calls) == 1
    card = _card(teams.calls[0])
    flat = json.dumps(card)
    assert card["type"] == "AdaptiveCard" and "Approval needed" in flat and "SENSITIVE" in flat and "browser.submit" in flat and "UTC" in flat
    (act,) = card["actions"]
    assert act["type"] == "Action.OpenUrl" and act["url"].endswith(f"/approvals/{aid}")
    assert tech.email not in flat and owner.email not in flat and "save the form" not in flat
    assert [d["status"] for d in _audit(owner.org_id, "notification.teams.approval_requested")] == ["queued"]
    assert _audit(owner.org_id, "teams.sent")
    assert owner.post(f"/api/v1/approvals/{aid}/decision", json={"decision": "deny", "comment": "no"}).status_code == 200
    _drain()
    assert len(teams.calls) == 2 and "denied" in json.dumps(_card(teams.calls[1]))
    with session_scope(uuid.UUID(owner.org_id)) as db:
        org = db.get(Organization, uuid.UUID(owner.org_id))
        org.settings = {**(org.settings or {}), "teams_rate_per_hour": 1}
    _request(owner)
    _drain()
    assert len(teams.calls) == 2
    assert "rate_limited" in [d["status"] for d in _audit(owner.org_id, "notification.teams.approval_requested")]
    with session_scope(uuid.UUID(owner.org_id)) as db:
        org = db.get(Organization, uuid.UUID(owner.org_id))
        org.settings = {**(org.settings or {}), "teams_rate_per_hour": 30}
    teams.fail = True
    _request(owner)
    _drain()
    assert _audit(owner.org_id, "teams.failed")


def test_settings_teams_toggle_and_cap(app, owner, teams):
    g = owner.get("/api/v1/admin/approval-notifications").json()
    assert g["teams_rate_per_hour"] == 30 and g["channels"]["approval_requested"]["teams"] is True and g["teams_connected"] is False
    ch = {k: {"in_app": True, "email": True, "slack": True, "teams": True} for k in ("approval_requested", "approval_reminder", "approval_outcome")}
    base = {"reminder_minutes": 15, "email_rate_per_hour": 20, "slack_rate_per_hour": 30, "channels": ch}
    assert owner.put("/api/v1/admin/approval-notifications", json={**base, "teams_rate_per_hour": 0}).status_code in (400, 422)
    assert owner.put("/api/v1/admin/approval-notifications", json={**base, "teams_rate_per_hour": 1001}).status_code in (400, 422)
    ch["approval_requested"]["teams"] = False
    r = owner.put("/api/v1/admin/approval-notifications", json={**base, "teams_rate_per_hour": 12})
    assert r.status_code == 200 and r.json()["teams_rate_per_hour"] == 12
    (ev,) = _audit(owner.org_id, "settings.approval_notifications_updated")
    assert ev["changes"]["teams_rate_per_hour"] == {"from": 30, "to": 12}
    r = owner.put("/api/v1/admin/approval-notifications", json=base)  # omitted cap is kept
    assert r.json()["teams_rate_per_hour"] == 12
    _connect(owner, webhook_url=HOOK)
    _request(owner)
    _drain()
    assert not teams.calls  # Teams off for requests org-wide
