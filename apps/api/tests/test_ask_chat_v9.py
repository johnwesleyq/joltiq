"""V9 Ask chat integration contracts."""
from __future__ import annotations

import uuid

from test_device_protocol import FakeDevice, _code

FAKE_KEY = "sk-test-v9-not-a-real-key"


class _Result:
    text = "1. Turn Wi-Fi off and back on.\n2. Reconnect to the office network."
    model = "gpt-4.1-mini"
    input_tokens = 10
    output_tokens = 12
    latency_ms = 3


def _connect(owner):
    response = owner.patch(
        "/api/v1/admin/models/openai",
        json={"enabled": True, "approved_for_tenant_data": True, "api_key": FAKE_KEY},
    )
    assert response.status_code == 200, response.text


def test_plain_ask_uses_live_model_without_creating_approval(owner, monkeypatch):
    from joltiq.services import ask_chat

    calls = []

    class Client:
        def complete(self, **kwargs):
            calls.append(kwargs)
            return _Result()

    monkeypatch.setattr(ask_chat, "build", lambda provider, key, timeout=60: Client())
    monkeypatch.setattr(ask_chat.get_settings(), "openai_api_key", None)
    _connect(owner)

    response = owner.post("/api/v1/agents/chat", json={"message": "My laptop cannot connect to office Wi-Fi"})

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["mode"] == "chat"
    assert body["reply"]["content"] == _Result.text
    assert body["setup_required"] is False
    assert owner.get("/api/v1/approvals").json()["items"] == []
    assert calls[-1]["messages"][-1]["content"].startswith("My laptop")


def test_plain_ask_without_live_model_returns_setup_notice(owner, monkeypatch):
    from joltiq.config import get_settings

    monkeypatch.setattr(get_settings(), "openai_api_key", None)
    response = owner.post("/api/v1/agents/chat", json={"message": "My printer is offline"})

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["reply"] is None
    assert body["setup_required"] is True
    assert "Connect OpenAI" in body["ai_notice"]

    status = owner.get("/api/v1/agents/ai-status")
    assert status.status_code == 200
    assert status.json()["live_chat"] is False


def test_explicit_troubleshoot_mode_keeps_durable_pipeline(owner):
    response = owner.post(
        "/api/v1/agents/chat",
        json={"mode": "troubleshoot", "message": "My printer shows offline"},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["mode"] == "troubleshoot"
    run = owner.get(f"/api/v1/agents/runs/{body['run_id']}").json()
    assert run["paused_for"] != "chat"


def test_windows_ask_receives_immediate_reply_without_local_approval(app, owner, monkeypatch):
    from joltiq.services import ask_chat

    class Client:
        def complete(self, **kwargs):
            return _Result()

    monkeypatch.setattr(ask_chat, "build", lambda provider, key, timeout=60: Client())
    monkeypatch.setattr(ask_chat.get_settings(), "openai_api_key", None)
    _connect(owner)
    device = FakeDevice(app)
    assert device.enroll(_code(owner), caps=["system.info"]).status_code == 200

    created = device.post(
        "/support-requests",
        {"client_request_id": str(uuid.uuid4()), "message": "Wi-Fi keeps dropping"},
    )

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["reply"]["content"] == _Result.text
    assert body["setup_required"] is False
    status = device.get(f"/support-requests/{body['run_id']}").json()
    assert status["state"] == "CHAT"
    assert status["paused_for"] is None


def test_stream_sends_deltas_saves_reply_and_never_returns_key(owner, monkeypatch):
    from joltiq.services import ask_chat

    monkeypatch.setattr(ask_chat.get_settings(), "openai_api_key", None)
    _connect(owner)

    def fake_stream(plan):
        assert plan["key"] == FAKE_KEY
        yield "delta", {"text": "Try "}
        yield "delta", {"text": "reconnecting Wi-Fi."}
        yield "usage", {"in": 3, "out": 4, "ms": 2, "model": "gpt-4.1-mini"}

    monkeypatch.setattr(ask_chat, "stream_tokens", fake_stream)
    response = owner.post("/api/v1/agents/chat/stream", json={"message": "Wi-Fi is down"})

    assert response.status_code == 200
    assert "event: meta" in response.text
    assert response.text.count("event: delta") == 2
    assert "event: done" in response.text
    assert FAKE_KEY not in response.text
