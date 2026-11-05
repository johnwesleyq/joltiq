"""Ask JoltIQ on the PC must never sit on "Waiting for approval" forever, and must say when no AI model is connected."""
from __future__ import annotations

import base64
import json
import uuid
from datetime import timedelta

from test_device_protocol import FakeDevice, _code

CAPS = ["system.info", "screen.capture", "input.click", "input.type", "input.key", "input.scroll", "input.move",
        "url.open", "guide.highlight", "guide.clear"]


def _types(device):
    return [json.loads(base64.b64decode(e["payload"]))["type"] for e in device.get("/poll").json()["envelopes"]]


def _start_fix(app, owner):
    device = FakeDevice(app)
    assert device.enroll(_code(owner), caps=CAPS).status_code == 200
    created = device.post("/support-requests", {"client_request_id": str(uuid.uuid4()), "message": "You fix my wifi"}).json()
    started = device.post(f"/support-requests/{created['run_id']}/computer-use", {"mode": "fix"}).json()
    return device, created, started


def test_status_explains_the_local_approval_step_in_plain_english(app, owner):
    device, created, started = _start_fix(app, owner)
    cu = device.get(f"/support-requests/{created['run_id']}").json()["computer_use"]
    assert cu["state"] == "waiting_session_consent" and cu["needs_local_approval"] is True
    assert "Allow" in cu["next_step"] and "Approve on this PC" in cu["next_step"]
    assert cu["session_id"] == started["session_id"]


def test_expired_session_stops_resending_grants_and_frees_the_user(app, owner):
    from joltiq.db import session_scope
    from joltiq.models import ComputerUseRun, SupportSession
    from joltiq.models.base import utcnow

    device, created, started = _start_fix(app, owner)
    sid = started["session_id"]
    assert device.post(f"/sessions/{sid}/local-consent", {"accepted": True, "user_display_name": "John"}).status_code == 200
    assert "computer_use.begin" in _types(device)
    with session_scope(uuid.UUID(owner.org_id)) as db:
        s = db.get(SupportSession, uuid.UUID(sid))
        s.expires_at = utcnow() - timedelta(minutes=1)
    types = _types(device)
    assert "session.begin" not in types and "computer_use.begin" not in types
    assert "session.end" in types and "computer_use.end" in types
    with session_scope(uuid.UUID(owner.org_id)) as db:
        assert db.get(SupportSession, uuid.UUID(sid)).state == "terminated"
        run = db.query(ComputerUseRun).filter(ComputerUseRun.session_id == uuid.UUID(sid)).one()
        assert run.state == "stopped"
    cu = device.get(f"/support-requests/{created['run_id']}").json()["computer_use"]
    assert cu["needs_local_approval"] is False and cu["label"].startswith("Stopped")
    again = device.post(f"/support-requests/{created['run_id']}/computer-use", {"mode": "fix"})
    assert again.status_code == 201 and again.json()["session_id"] != sid


def test_successful_direct_fix_is_reported_as_completed(app, owner):
    from joltiq.db import session_scope
    from joltiq.models import ComputerUseRun

    device, created, started = _start_fix(app, owner)
    sid = started["session_id"]
    assert device.post(f"/sessions/{sid}/local-consent", {"accepted": True, "user_display_name": "John"}).status_code == 200
    _types(device)
    with session_scope(uuid.UUID(owner.org_id)) as db:
        run = db.query(ComputerUseRun).filter(ComputerUseRun.session_id == uuid.UUID(sid)).one()
        run.state = "completed"
        run.success = True
        run.summary = "DNS was flushed and the network is reachable."

    cu = device.get(f"/support-requests/{created['run_id']}").json()["computer_use"]
    assert cu["label"] == "Completed: DNS was flushed and the network is reachable."
    assert cu["needs_local_approval"] is False
    assert "another request" in cu["next_step"]


def test_unanswered_local_consent_times_out_after_ten_minutes(app, owner):
    from joltiq.db import session_scope
    from joltiq.models import SupportSession
    from joltiq.models.base import utcnow

    device, created, started = _start_fix(app, owner)
    with session_scope(uuid.UUID(owner.org_id)) as db:
        db.get(SupportSession, uuid.UUID(started["session_id"])).consented_at = utcnow() - timedelta(minutes=11)
    assert "session.begin" not in _types(device)
    cu = device.get(f"/support-requests/{created['run_id']}").json()["computer_use"]
    assert "10 minutes" in cu["label"]


def test_ask_answers_and_flags_missing_ai_key(app, owner):
    device = FakeDevice(app)
    assert device.enroll(_code(owner), caps=CAPS).status_code == 200
    created = device.post("/support-requests", {"client_request_id": str(uuid.uuid4()), "message": "My wifi is slow"}).json()
    assert "Admin → Models" in created["ai_notice"]
    r = device.post(f"/support-requests/{created['run_id']}/messages", {"message": "How do I restart my router?"}).json()
    # No silent offline-rules answer: refuse with a clear "Connect OpenAI" setup prompt.
    assert r["reply"] is None and r["setup_required"] is True
    assert "Connect OpenAI" in r["ai_notice"]


def test_web_ask_reports_missing_ai_model(owner):
    st = owner.get("/api/v1/agents/ai-status").json()
    assert st["live_chat"] is False and "Admin → Models" in st["notice"]
    r = owner.post("/api/v1/agents/chat", json={"message": "My printer shows offline"}).json()
    assert "Admin → Models" in r["ai_notice"]
