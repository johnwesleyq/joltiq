"""Slack approval alerts + approval notification settings (ADR 0021). Slack is a labelled in-process MOCK transport:
nothing is ever sent to real Slack."""
import json
import uuid

import httpx
import pytest
from conftest import invite_and_join
from sqlalchemy import select
from test_approval_notifications import _audit, _emails, _notes, _request, _uid

HOOK = "https://hooks.slack.com/services/T000MOCK/B000MOCK/mockmockmockmock"
TOKEN = "xoxb-mock-0001-not-a-real-token"


class MockSlack:
    """MOCK Slack (test only): records requests, answers like Slack."""

    def __init__(self):
        self.calls: list[dict] = []
        self.fail = False

    def __call__(self, req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        self.calls.append({"url": str(req.url), "auth": req.headers.get("authorization"), "body": body})
        if self.fail:
            return httpx.Response(500, text="mock_failure")
        if req.url.path == "/api/chat.postMessage":
            ok = req.headers.get("authorization") == f"Bearer {TOKEN}"
            return httpx.Response(200, json={"ok": ok, **({} if ok else {"error": "invalid_auth"})})
        return httpx.Response(200, text="ok")


@pytest.fixture()
def slack():
    from joltiq.services import integrations

    m = MockSlack()
    integrations._TRANSPORT_OVERRIDE["slack"] = httpx.MockTransport(m)
    yield m
    integrations._TRANSPORT_OVERRIDE.pop("slack", None)


def _drain():
    from joltiq.services.jobs import drain

    drain(["slack.deliver"])


def _connect(c, **cfg):
    return c.post("/api/v1/integrations", json={"kind": "slack", "name": "Slack", "config": cfg})


def test_connect_validates_and_never_returns_secrets(app, owner, slack):
    assert _connect(owner, webhook_url="https://evil.example.com/services/x").status_code in (400, 422)
    assert _connect(owner, webhook_url="http://hooks.slack.com/services/x").status_code in (400, 422)
    assert _connect(owner, bot_token="not-a-token", channel="#ops").status_code in (400, 422)
    assert _connect(owner, bot_token=TOKEN).status_code in (400, 422)  # channel required
    assert _connect(owner, webhook_url=HOOK, use_mock=True).status_code in (400, 422)  # no MOCK base configured here
    assert _connect(owner).status_code in (400, 422)
    r = _connect(owner, webhook_url=HOOK, bot_token=TOKEN, channel="#it-approvals", events=["approval_requested", "nope"])
    assert r.status_code in (400, 422)
    r = _connect(owner, webhook_url=HOOK, bot_token=TOKEN, channel="#it-approvals")
    assert r.status_code == 201, r.text
    i = r.json()
    assert i["config"]["mode"] == "bot" and i["config"]["events"] == ["approval_requested", "approval_reminder", "approval_outcome"]
    dumped = json.dumps(owner.get(f"/api/v1/integrations/{i['id']}").json()) + json.dumps(owner.get("/api/v1/integrations").json())
    assert TOKEN not in dumped and "hooks.slack.com" not in dumped and i.get("generated_secret") in (None, "")
    t = owner.post(f"/api/v1/integrations/{i['id']}/test").json()
    assert t["result"]["ok"] and t["integration"]["status"] == "connected"
    assert slack.calls[-1]["url"] == "https://slack.com/api/chat.postMessage" and slack.calls[-1]["body"]["channel"] == "#it-approvals"
    assert TOKEN not in json.dumps(t)
    assert owner.post(f"/api/v1/integrations/{i['id']}/rotate", json={"secret": "xoxb-other-token"}).status_code in (400, 422)
    # Settings: channel/events only.
    assert owner.patch(f"/api/v1/integrations/{i['id']}/slack", json={"channel": "bad channel!"}).status_code in (400, 422)
    r = owner.patch(f"/api/v1/integrations/{i['id']}/slack", json={"channel": "#ops", "events": ["approval_outcome"]})
    assert r.status_code == 200 and r.json()["config"]["events"] == ["approval_outcome"]
    assert _audit(owner.org_id, "integration.slack_settings_updated")
    assert owner.post(f"/api/v1/integrations/{i['id']}/revoke").json()["status"] == "revoked"
    from joltiq.db import session_scope
    from joltiq.models import AuditEvent, Job

    with session_scope(uuid.UUID(owner.org_id)) as db:
        blob = json.dumps([e.data for e in db.execute(select(AuditEvent)).scalars()], default=str)
    with session_scope(None) as db:
        blob += json.dumps([j.payload for j in db.execute(select(Job)).scalars()], default=str)
    assert TOKEN not in blob and "mockmockmock" not in blob


def test_webhook_test_failure_is_sanitised(app, owner, slack):
    i = _connect(owner, webhook_url=HOOK).json()
    slack.fail = True
    t = owner.post(f"/api/v1/integrations/{i['id']}/test").json()
    assert not t["result"]["ok"] and "500" in t["result"]["detail"] and "hooks.slack.com" not in t["result"]["detail"]
    assert t["integration"]["status"] == "error"


def test_approval_alerts_block_kit_dedupe_events_and_audit(app, owner, slack):
    from joltiq.db import session_scope
    from joltiq.models import Approval
    from joltiq.services import approval_notify

    i = _connect(owner, webhook_url=HOOK, events=["approval_requested", "approval_outcome"]).json()
    tech = invite_and_join(app, owner, "technician")
    aid, _ = _request(owner, started_by=_uid(tech))
    with session_scope(uuid.UUID(owner.org_id)) as db:
        approval_notify.on_requested(db, db.get(Approval, aid))  # retried event → still one Slack message
    _drain()
    msgs = [c for c in slack.calls if c["url"] == HOOK]
    assert len(msgs) == 1
    blocks = msgs[0]["body"]["blocks"]
    flat = json.dumps(blocks)
    assert blocks[0]["type"] == "header" and "Approval needed" in flat and "SENSITIVE" in flat and "browser.submit" in flat and "UTC" in flat
    btn = [b for b in blocks if b["type"] == "actions"][0]["elements"][0]
    assert btn["url"].endswith(f"/approvals/{aid}") and btn["url"].startswith("http")
    assert tech.email not in flat and owner.email not in flat and "save the form" not in flat  # no PII / reason
    assert [d["status"] for d in _audit(owner.org_id, "notification.slack.approval_requested")] == ["queued"]
    assert _audit(owner.org_id, "slack.sent")
    # Reminder not subscribed → nothing; outcome subscribed.
    with session_scope(uuid.UUID(owner.org_id)) as db:
        a = db.get(Approval, aid)
        from datetime import timedelta

        from joltiq.models import utcnow

        a.expires_at, a.created_at = utcnow() + timedelta(minutes=5), utcnow() - timedelta(minutes=55)
    with session_scope(uuid.UUID(owner.org_id)) as db:
        approval_notify.send_reminders(db)
    assert owner.post(f"/api/v1/approvals/{aid}/decision", json={"decision": "deny", "comment": "no"}).status_code == 200
    _drain()
    texts = [c["body"]["text"] for c in slack.calls if c["url"] == HOOK]
    assert len(texts) == 2 and "denied" in texts[1] and not any("Reminder" in t for t in texts)
    assert i["id"]


def test_slack_rate_limit_and_failure_retry(app, owner, slack):
    from joltiq.db import session_scope
    from joltiq.models import Organization

    _connect(owner, webhook_url=HOOK)
    with session_scope(uuid.UUID(owner.org_id)) as db:
        org = db.get(Organization, uuid.UUID(owner.org_id))
        org.settings = {**(org.settings or {}), "slack_rate_per_hour": 1}
    _request(owner)
    _request(owner)
    _drain()
    assert len(slack.calls) == 1
    assert [d["status"] for d in _audit(owner.org_id, "notification.slack.approval_requested")] == ["queued", "rate_limited"]
    with session_scope(uuid.UUID(owner.org_id)) as db:
        org = db.get(Organization, uuid.UUID(owner.org_id))
        org.settings = {**(org.settings or {}), "slack_rate_per_hour": 30}
    slack.fail = True
    _request(owner)
    _drain()
    assert _audit(owner.org_id, "slack.failed")


def test_approval_notification_settings_validation_toggles_and_audit(app, owner, slack):
    tech = invite_and_join(app, owner, "technician")
    g = owner.get("/api/v1/admin/approval-notifications").json()
    assert g["reminder_minutes"] == 15 and g["email_rate_per_hour"] == 20 and g["channels"]["approval_reminder"]["slack"] is True
    assert tech.get("/api/v1/admin/approval-notifications").status_code == 403
    ch = {k: {"in_app": True, "email": True, "slack": True} for k in ("approval_requested", "approval_reminder", "approval_outcome")}
    bad = [{"reminder_minutes": 0, "email_rate_per_hour": 20, "slack_rate_per_hour": 30, "channels": ch},
           {"reminder_minutes": 15, "email_rate_per_hour": 0, "slack_rate_per_hour": 30, "channels": ch},
           {"reminder_minutes": 15, "email_rate_per_hour": 20, "slack_rate_per_hour": 30, "channels": {"approval_requested": ch["approval_requested"]}},
           {"reminder_minutes": 15, "email_rate_per_hour": 20, "slack_rate_per_hour": 30,
            "channels": {**ch, "approval_requested": {"in_app": False, "email": False, "slack": True}}}]
    for b in bad:
        assert owner.put("/api/v1/admin/approval-notifications", json=b).status_code in (400, 422), b
    ch["approval_requested"] = {"in_app": True, "email": False, "slack": False}
    r = owner.put("/api/v1/admin/approval-notifications", json={"reminder_minutes": 30, "email_rate_per_hour": 5, "slack_rate_per_hour": 10, "channels": ch})
    assert r.status_code == 200 and r.json()["reminder_minutes"] == 30
    (ev,) = _audit(owner.org_id, "settings.approval_notifications_updated")
    assert ev["changes"]["reminder_minutes"] == {"from": 15, "to": 30} and "channels" in ev["changes"]
    _connect(owner, webhook_url=HOOK)
    aid, _ = _request(owner)
    _drain()
    assert _notes(owner.org_id, _uid(owner), aid)  # in-app still on
    assert not [m for m in _emails(owner.org_id, owner.email) if "Approval needed" in m[0]]  # email off org-wide
    assert not slack.calls  # Slack off for requests
    assert any("email:org_setting" in d["suppressed"] for d in _audit(owner.org_id, "notification.approval_requested"))


def test_email_deep_link_uses_configured_web_origin(app, owner, monkeypatch):
    from joltiq.config import get_settings

    monkeypatch.setattr(get_settings(), "web_origin", "http://localhost:13000/")
    aid, _ = _request(owner)
    (_, body), = [m for m in _emails(owner.org_id, owner.email) if "Approval needed" in m[0]]
    assert f"Open: http://localhost:13000/approvals/{aid}" in body
