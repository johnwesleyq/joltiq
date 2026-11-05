"""Ask JoltIQ is a plain technician chat: instant model reply, no computer-use / approvals, honest when not connected."""
from __future__ import annotations

import logging
import uuid

import pytest
from test_device_protocol import FakeDevice, _code

FAKE_KEY = "sk-test-ask-chat-0000000000000000"
WIFI_ANSWER = "Let's get you back online.\n1. Toggle Wi-Fi off and on.\n2. Forget the network and reconnect."


class _Res:
    text = WIFI_ANSWER
    model = "gpt-6-luna"
    input_tokens = 50
    output_tokens = 20
    latency_ms = 12


@pytest.fixture
def fake_openai(monkeypatch):
    from joltiq.services import ask_chat

    calls: list[dict] = []

    class Client:
        def complete(self, **kw):
            calls.append(kw)
            return _Res()

    monkeypatch.setattr(ask_chat, "build", lambda prov, key, timeout=60: Client())
    return calls


def _connect(owner):
    r = owner.patch("/api/v1/admin/models/openai", json={"enabled": True, "approved_for_tenant_data": True, "api_key": FAKE_KEY})
    assert r.status_code == 200, r.text


def _no_env_key(monkeypatch):
    from joltiq.config import get_settings

    monkeypatch.setattr(get_settings(), "openai_api_key", None)


def test_chat_answers_instantly_without_runs_or_approvals(owner, fake_openai, monkeypatch):
    _no_env_key(monkeypatch)
    _connect(owner)
    r = owner.post("/api/v1/agents/chat", json={"message": "My laptop won't connect to WiFi"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["mode"] == "chat" and body["setup_required"] is False
    assert body["reply"]["content"] == WIFI_ANSWER and body["reply"]["provider"] == "openai"
    # technician system prompt, conversation passed through
    assert "IT support technician" in fake_openai[0]["system"] and fake_openai[0]["messages"][-1]["content"].startswith("My laptop")
    run = owner.get(f"/api/v1/agents/runs/{body['run_id']}").json()
    assert run["paused_for"] == "chat"
    assert owner.get("/api/v1/approvals").json()["items"] == []
    # follow-up keeps history
    r2 = owner.post("/api/v1/agents/chat", json={"ticket_id": body["ticket_id"], "message": "Still nothing"}).json()
    assert r2["run_id"] == body["run_id"]
    assert [m["role"] for m in fake_openai[1]["messages"]] == ["user", "assistant", "user"]


def test_not_connected_refuses_with_setup_prompt_no_fake_answer(owner, fake_openai, monkeypatch):
    _no_env_key(monkeypatch)
    r = owner.post("/api/v1/agents/chat", json={"message": "WiFi is down"}).json()
    assert r["reply"] is None and r["setup_required"] is True and "Connect OpenAI" in r["ai_notice"]
    assert fake_openai == []
    st = owner.get("/api/v1/agents/ai-status").json()
    assert st["live_chat"] is False and st["can_connect"] is True and "Admin" in st["notice"]


def test_ai_status_reports_provider_and_model(owner, fake_openai, monkeypatch):
    _no_env_key(monkeypatch)
    _connect(owner)
    st = owner.get("/api/v1/agents/ai-status").json()
    assert st["live_chat"] is True and st["provider"] == "openai" and st["model"] == "gpt-6-luna" and st["source"] == "admin key"
    assert FAKE_KEY not in str(st)


def test_admin_can_enable_platform_openai_without_entering_a_tenant_key(owner, monkeypatch):
    from joltiq.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "dev_bootstrap_openai_from_env", False)
    monkeypatch.setattr(settings, "openai_api_key", "sk-platform-central-0000000000000000")
    # Create an explicit disabled tenant preference so DEV bootstrap cannot
    # silently enable it; tenant approval remains a required boundary.
    r = owner.patch("/api/v1/admin/models/openai", json={"enabled": False, "approved_for_tenant_data": False})
    assert r.status_code == 200

    before = owner.get("/api/v1/agents/ai-status").json()
    assert before["live_chat"] is False and before["platform_key_available"] is True

    enabled = owner.patch("/api/v1/admin/models/openai", json={"enabled": True, "approved_for_tenant_data": True})
    assert enabled.status_code == 200
    assert enabled.json()["key_fingerprint"] is None

    after = owner.get("/api/v1/agents/ai-status").json()
    assert after["live_chat"] is True and after["model"] == "gpt-6-luna" and after["source"] == "server key"
    assert "sk-platform" not in str(after)


def test_stream_endpoint_emits_deltas_and_saves_reply(owner, monkeypatch):
    from joltiq.services import ask_chat

    _no_env_key(monkeypatch)
    _connect(owner)

    def fake_stream(plan):
        assert plan["key"] == FAKE_KEY and plan["messages"][-1]["role"] == "user"
        yield "delta", {"text": "Try "}
        yield "delta", {"text": "toggling Wi-Fi."}
        yield "usage", {"in": 10, "out": 4, "ms": 5, "model": "gpt-4.1-mini"}

    monkeypatch.setattr(ask_chat, "stream_tokens", fake_stream)
    r = owner.post("/api/v1/agents/chat/stream", json={"message": "wifi?"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    text = r.text
    assert "event: meta" in text and text.count("event: delta") == 2 and "event: done" in text
    assert FAKE_KEY not in text
    run_id = next(line for line in text.splitlines() if line.startswith("data:") and "run_id" in line)
    import json

    rid = json.loads(run_id[5:])["run_id"]
    msgs = owner.get(f"/api/v1/agents/runs/{rid}").json()["messages"]
    assert msgs[-1]["role"] == "assistant" and msgs[-1]["content"] == "Try toggling Wi-Fi."


def test_stream_not_connected(owner, monkeypatch):
    _no_env_key(monkeypatch)
    text = owner.post("/api/v1/agents/chat/stream", json={"message": "wifi?"}).text
    assert "event: setup_required" in text and "event: delta" not in text


def test_pc_ask_gets_reply_without_approval(app, owner, fake_openai, monkeypatch):
    _no_env_key(monkeypatch)
    _connect(owner)
    device = FakeDevice(app)
    assert device.enroll(_code(owner), caps=["system.info"]).status_code == 200
    created = device.post("/support-requests", {"client_request_id": str(uuid.uuid4()), "message": "WiFi keeps dropping"}).json()
    assert created["reply"]["content"] == WIFI_ANSWER and created["setup_required"] is False
    status = device.get(f"/support-requests/{created['run_id']}").json()
    assert status["state"] == "CHAT" and status["paused_for"] is None
    assert not (status["computer_use"] or {}).get("needs_local_approval")
    more = device.post(f"/support-requests/{created['run_id']}/messages", {"message": "Still dropping"}).json()
    assert more["reply"]["content"] == WIFI_ANSWER


def test_pc_ask_not_connected_says_so(app, owner, monkeypatch):
    _no_env_key(monkeypatch)
    device = FakeDevice(app)
    assert device.enroll(_code(owner), caps=["system.info"]).status_code == 200
    created = device.post("/support-requests", {"client_request_id": str(uuid.uuid4()), "message": "WiFi?"}).json()
    assert created["reply"] is None and created["setup_required"] is True and "Connect OpenAI" in created["ai_notice"]


def test_fix_it_for_me_still_requires_local_approval(app, owner, fake_openai, monkeypatch):
    _no_env_key(monkeypatch)
    _connect(owner)
    device = FakeDevice(app)
    caps = ["system.info", "screen.capture", "input.click", "input.type", "input.key", "url.open", "guide.highlight", "guide.clear"]
    assert device.enroll(_code(owner), caps=caps).status_code == 200
    created = device.post("/support-requests", {"client_request_id": str(uuid.uuid4()), "message": "Fix my wifi"}).json()
    started = device.post(f"/support-requests/{created['run_id']}/computer-use", {"mode": "fix"})
    assert started.status_code == 201, started.text
    cu = device.get(f"/support-requests/{created['run_id']}").json()["computer_use"]
    assert cu["needs_local_approval"] is True


def test_env_bootstrap_only_first_org_and_never_logs_key(owner, monkeypatch, caplog):
    from sqlalchemy import select

    from joltiq.config import get_settings
    from joltiq.db import session_scope
    from joltiq.models import ModelProviderConfig, Organization
    from joltiq.services import ask_chat

    env_key = "sk-env-dev-bootstrap-1111111111"
    monkeypatch.setattr(get_settings(), "openai_api_key", env_key)
    caplog.set_level(logging.DEBUG)
    org_id = uuid.UUID(owner.org_id)
    with session_scope(org_id) as db:
        assert ask_chat.bootstrap_env_openai(db, org_id) is False  # not the first org
    with session_scope(org_id) as db:
        first = db.execute(select(Organization.id).order_by(Organization.created_at, Organization.id).limit(1)).scalar()
    with session_scope(first) as db:
        had = db.execute(select(ModelProviderConfig).where(ModelProviderConfig.provider == "openai")).scalar_one_or_none()
        changed = ask_chat.bootstrap_env_openai(db, first)
        assert changed is (had is None)
        st = ask_chat.status(db, first)
        if changed:
            assert st["connected"] and st["source"] == "server key"
            assert ask_chat.bootstrap_env_openai(db, first) is False  # idempotent / respects existing config
            db.delete(db.execute(select(ModelProviderConfig).where(ModelProviderConfig.provider == "openai")).scalar_one())
    monkeypatch.setattr(get_settings(), "env", "production")
    with session_scope(first) as db:
        assert ask_chat.bootstrap_env_openai(db, first) is False
    assert env_key not in caplog.text


def test_troubleshoot_mode_keeps_durable_pipeline(owner, monkeypatch):
    _no_env_key(monkeypatch)
    r = owner.post("/api/v1/agents/chat", json={"mode": "troubleshoot", "message": "My printer shows offline"}).json()
    assert r["mode"] == "troubleshoot"
    run = owner.get(f"/api/v1/agents/runs/{r['run_id']}").json()
    assert run["paused_for"] != "chat"
