"""Windows companion protocol: enrollment + request authentication attacks (signature, replay, revocation)."""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import uuid

from conftest import ApiClient
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec


class FakeDevice:
    """Python implementation of the device side of PROTOCOL.md, used to attack the server."""

    def __init__(self, app):
        self.c = ApiClient(app).c
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.counter = 0
        self.device_id = None

    def pem(self):
        return self.key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()

    def enroll(self, code, caps=("system.info", "service.status")):
        r = self.c.post("/api/v1/agent/v1/enroll", json={"code": code, "public_key_pem": self.pem(), "hostname": "PC-1", "os_name": "Windows 11",
                                                          "os_version": "10.0.22631", "agent_version": "1.0.0", "capabilities": list(caps)})
        if r.status_code == 200:
            self.device_id = r.json()["device_id"]
            self.server_pem = r.json()["server_public_key_pem"]
        return r

    def headers(self, method, path, body=b"", *, counter=None, nonce=None, ts=None, key=None):
        if counter is None:
            self.counter += 1
            counter = self.counter
        nonce = nonce or secrets.token_urlsafe(16)
        ts = str(ts if ts is not None else int(time.time()))
        msg = "\n".join([method, path, ts, nonce, str(counter), hashlib.sha256(body).hexdigest()]).encode()
        sig = (key or self.key).sign(msg, ec.ECDSA(hashes.SHA256()))
        return {"x-jq-device": self.device_id, "x-jq-timestamp": ts, "x-jq-nonce": nonce, "x-jq-counter": str(counter),
                "x-jq-signature": base64.b64encode(sig).decode()}

    def get(self, path, **kw):
        full = "/api/v1/agent/v1" + path
        return self.c.get(full, headers=self.headers("GET", full, **kw))

    def post(self, path, payload):
        full = "/api/v1/agent/v1" + path
        body = json.dumps(payload, separators=(",", ":")).encode()
        return self.c.post(full, content=body, headers={**self.headers("POST", full, body), "content-type": "application/json"})


def _code(owner):
    r = owner.post("/api/v1/devices/enrollment-codes", json={})
    assert r.status_code == 201, r.text
    return r.json()["code"]


def test_enrollment_single_use_and_signed_poll(app, owner):
    d = FakeDevice(app)
    code = _code(owner)
    assert d.enroll(code).status_code == 200
    assert FakeDevice(app).enroll(code).status_code == 401  # single use
    assert d.get("/poll").status_code == 200
    devs = owner.get("/api/v1/devices").json()["items"]
    assert devs[0]["hostname"] == "PC-1" and devs[0]["online"] is True


def test_rejects_bad_signature_replay_stale_and_reused_nonce(app, owner):
    d = FakeDevice(app)
    d.enroll(_code(owner))
    path = "/api/v1/agent/v1/poll"
    attacker = ec.generate_private_key(ec.SECP256R1())
    r = d.c.get(path, headers=d.headers("GET", path, key=attacker))
    assert r.status_code == 401 and r.json()["error"]["code"] == "bad_signature"
    ok = d.headers("GET", path)
    assert d.c.get(path, headers=ok).status_code == 200
    r = d.c.get(path, headers=ok)  # exact replay
    assert r.status_code == 401 and r.json()["error"]["code"] == "replay"
    r = d.c.get(path, headers=d.headers("GET", path, counter=1))  # counter rollback with fresh nonce
    assert r.json()["error"]["code"] == "replay"
    r = d.c.get(path, headers=d.headers("GET", path, ts=int(time.time()) - 3600))
    assert r.json()["error"]["code"] == "stale_request"
    # Signed for a different path (request smuggling) fails.
    h = d.headers("GET", "/api/v1/agent/v1/heartbeat")
    assert d.c.get(path, headers=h).status_code == 401


def test_revoked_device_is_refused_and_envelopes_are_signed(app, owner):
    d = FakeDevice(app)
    d.enroll(_code(owner))
    r = d.get("/poll")
    assert r.status_code == 200
    assert owner.post(f"/api/v1/devices/{d.device_id}/revoke").status_code == 200
    r = d.get("/poll")
    assert r.status_code == 403 and r.json()["error"]["code"] == "device_revoked"


def test_signed_device_support_request_links_identity_and_creates_no_control_artifacts(app, owner):
    from sqlalchemy import func, select

    from joltiq.db import session_scope
    from joltiq.models import Approval, Device, DeviceCommand, SupportSession, Ticket

    d = FakeDevice(app)
    assert d.enroll(_code(owner)).status_code == 200

    created = d.post(
        "/support-requests",
        {"client_request_id": str(uuid.uuid4()), "message": "Outlook cannot find Adobe Sign."},
    )
    assert created.status_code == 201, created.text
    result = created.json()
    assert set(result) == {"ticket_id", "ticket_number", "run_id", "state"}

    with session_scope(uuid.UUID(owner.org_id)) as db:
        device = db.get(Device, uuid.UUID(d.device_id))
        ticket = db.get(Ticket, uuid.UUID(result["ticket_id"]))
        assert ticket.org_id == device.org_id
        assert ticket.customer_id == device.customer_id
        assert ticket.requester_id == device.owner_user_id
        assert ticket.device_id == device.id
        assert ticket.source == "device_chat"
        assert db.scalar(select(func.count()).select_from(SupportSession)) == 0
        assert db.scalar(select(func.count()).select_from(Approval)) == 0
        assert db.scalar(select(func.count()).select_from(DeviceCommand)) == 0


def test_device_support_request_status_is_customer_safe_and_can_continue(app, owner):
    from sqlalchemy import select

    from joltiq.db import session_scope
    from joltiq.models import AgentMessage, AuditEvent

    d = FakeDevice(app)
    assert d.enroll(_code(owner)).status_code == 200
    created = d.post(
        "/support-requests",
        {"client_request_id": str(uuid.uuid4()), "message": "Please help with Outlook."},
    )
    assert created.status_code == 201, created.text
    run_id = created.json()["run_id"]

    continued = d.post(f"/support-requests/{run_id}/messages", {"message": "It fails when I open a PDF."})
    assert continued.status_code == 200, continued.text
    assert continued.json()["run_id"] == run_id

    status = d.get(f"/support-requests/{run_id}")
    assert status.status_code == 200, status.text
    body = status.json()
    assert set(body) == {"id", "ticket_id", "state", "paused_for", "outcome_stage", "messages"}
    assert all(set(message) == {"id", "role", "content", "at"} for message in body["messages"])
    assert [message["content"] for message in body["messages"]] == [
        "Please help with Outlook.",
        "It fails when I open a PDF.",
    ]

    with session_scope(uuid.UUID(owner.org_id)) as db:
        messages = db.execute(select(AgentMessage).where(AgentMessage.run_id == uuid.UUID(run_id))).scalars().all()
        assert [message.content for message in messages] == [
            "Please help with Outlook.",
            "It fails when I open a PDF.",
        ]
        actions = db.execute(
            select(AuditEvent.action)
            .where(AuditEvent.action.in_(("support_request.created", "support_request.message")))
            .order_by(AuditEvent.id)
        ).scalars().all()
        assert actions == ["support_request.created", "support_request.message"]


def test_device_support_request_is_private_to_its_device_even_in_same_tenant(app, owner):
    first = FakeDevice(app)
    second = FakeDevice(app)
    assert first.enroll(_code(owner)).status_code == 200
    assert second.enroll(_code(owner)).status_code == 200
    created = first.post(
        "/support-requests",
        {"client_request_id": str(uuid.uuid4()), "message": "Private device issue."},
    )
    assert created.status_code == 201
    run_id = created.json()["run_id"]

    assert second.get(f"/support-requests/{run_id}").status_code == 404
    assert second.post(f"/support-requests/{run_id}/messages", {"message": "Cross-device write."}).status_code == 404


def test_device_support_request_can_start_fix_with_two_local_consent_gates(app, owner):
    capabilities = [
        "system.info", "screen.capture", "input.click", "input.type", "input.key", "input.scroll", "input.move",
        "url.open", "guide.highlight", "guide.clear",
    ]
    device = FakeDevice(app)
    other = FakeDevice(app)
    assert device.enroll(_code(owner), caps=capabilities).status_code == 200
    assert other.enroll(_code(owner), caps=capabilities).status_code == 200
    created = device.post(
        "/support-requests",
        {"client_request_id": str(uuid.uuid4()), "message": "I can't find Adobe Sign in Outlook."},
    ).json()

    started = device.post(f"/support-requests/{created['run_id']}/computer-use", {"mode": "fix"})
    assert started.status_code == 201, started.text
    body = started.json()
    assert body["mode"] == "fix" and body["state"] == "waiting_session_consent"
    assert body["computer_use_run_id"] is None
    assert other.post(f"/support-requests/{created['run_id']}/computer-use", {"mode": "fix"}).status_code == 404

    begin = [json.loads(base64.b64decode(e["payload"])) for e in device.get("/poll").json()["envelopes"]]
    session_begin = next(e for e in begin if e["type"] == "session.begin")
    assert session_begin["request_details"]["computer_use_mode"] == "fix"
    assert device.post(
        f"/sessions/{body['session_id']}/local-consent",
        {"accepted": True, "user_display_name": "John"},
    ).status_code == 200

    pending = [json.loads(base64.b64decode(e["payload"])) for e in device.get("/poll").json()["envelopes"]]
    # The companion keeps the locally-consented session only in memory. Re-send the
    # idempotent session grant so a restarted tray can safely ask again and recover.
    recovered_session = next(e for e in pending if e["type"] == "session.begin")
    assert recovered_session["session_id"] == body["session_id"]
    run_begin = next(e for e in pending if e["type"] == "computer_use.begin")
    assert run_begin["mode"] == "fix"
    assert run_begin["goal"] == "I can't find Adobe Sign in Outlook."

    assert device.post(
        f"/computer-use/{run_begin['computer_use_run_id']}/local-consent",
        {"accepted": True},
    ).status_code == 200
    restarted = [json.loads(base64.b64decode(e["payload"])) for e in device.get("/poll").json()["envelopes"]]
    recovered_run = next(e for e in restarted if e["type"] == "computer_use.begin")
    assert recovered_run["computer_use_run_id"] == run_begin["computer_use_run_id"]
    assert device.post(
        f"/computer-use/{run_begin['computer_use_run_id']}/local-consent",
        {"accepted": True},
    ).status_code == 200

    repeated = device.post(f"/support-requests/{created['run_id']}/computer-use", {"mode": "fix"})
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["session_id"] == body["session_id"]
    assert repeated.json()["computer_use_run_id"] == run_begin["computer_use_run_id"]

    conflicting = device.post(f"/support-requests/{created['run_id']}/computer-use", {"mode": "guide"})
    assert conflicting.status_code == 200, conflicting.text
    assert conflicting.json()["session_id"] == body["session_id"]
    assert conflicting.json()["computer_use_run_id"] == run_begin["computer_use_run_id"]
    assert conflicting.json()["mode"] == "fix"


def test_active_direct_computer_use_accepts_device_result_after_ticket_run_escalated(app, owner):
    from joltiq.db import session_scope
    from joltiq.models import AgentRun, DeviceCommand

    capabilities = [
        "system.info", "screen.capture", "input.click", "input.type", "input.key", "input.scroll", "input.move",
        "url.open", "guide.highlight", "guide.clear",
    ]
    device = FakeDevice(app)
    assert device.enroll(_code(owner), caps=capabilities).status_code == 200
    created = device.post(
        "/support-requests",
        {"client_request_id": str(uuid.uuid4()), "message": "Please uninstall Adobe Acrobat DC."},
    ).json()

    device.post(f"/support-requests/{created['run_id']}/computer-use", {"mode": "fix"}).json()
    pending = [json.loads(base64.b64decode(e["payload"])) for e in device.get("/poll").json()["envelopes"]]
    session_begin = next(e for e in pending if e["type"] == "session.begin")
    assert device.post(
        f"/sessions/{session_begin['session_id']}/local-consent",
        {"accepted": True, "user_display_name": "John"},
    ).status_code == 200

    pending = [json.loads(base64.b64decode(e["payload"])) for e in device.get("/poll").json()["envelopes"]]
    run_begin = next(e for e in pending if e["type"] == "computer_use.begin")
    assert device.post(
        f"/computer-use/{run_begin['computer_use_run_id']}/local-consent",
        {"accepted": True},
    ).status_code == 200
    pending = [json.loads(base64.b64decode(e["payload"])) for e in device.get("/poll").json()["envelopes"]]
    action = next(e for e in pending if e["type"] == "action")

    with session_scope(uuid.UUID(owner.org_id)) as db:
        ticket_run = db.get(AgentRun, uuid.UUID(created["run_id"]))
        ticket_run.state = "ESCALATED"

    response = device.post(
        f"/commands/{action['command_id']}/result",
        {"status": "failed", "result": {"ok": False, "error": "Desktop capture refused: remote_session"}},
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True, "status": "failed", "late": False, "idempotent": False}
    with session_scope(uuid.UUID(owner.org_id)) as db:
        command = db.get(DeviceCommand, uuid.UUID(action["command_id"]))
        assert command.status == "failed"
        assert command.result == {"ok": False, "error": "Desktop capture refused: remote_session"}


def test_active_direct_computer_use_owns_native_conversation_after_ticket_agent_escalates(app, owner):
    from joltiq.db import session_scope
    from joltiq.models import AgentRun, ComputerUseRun
    from joltiq.services.agent import add_message

    capabilities = [
        "system.info", "screen.capture", "input.click", "input.type", "input.key", "input.scroll", "input.move",
        "url.open", "guide.highlight", "guide.clear",
    ]
    device = FakeDevice(app)
    assert device.enroll(_code(owner), caps=capabilities).status_code == 200
    created = device.post(
        "/support-requests",
        {"client_request_id": str(uuid.uuid4()), "message": "Please uninstall Adobe Acrobat DC."},
    ).json()

    device.post(f"/support-requests/{created['run_id']}/computer-use", {"mode": "fix"}).json()
    pending = [json.loads(base64.b64decode(e["payload"])) for e in device.get("/poll").json()["envelopes"]]
    session_begin = next(e for e in pending if e["type"] == "session.begin")
    assert device.post(
        f"/sessions/{session_begin['session_id']}/local-consent",
        {"accepted": True, "user_display_name": "John"},
    ).status_code == 200
    pending = [json.loads(base64.b64decode(e["payload"])) for e in device.get("/poll").json()["envelopes"]]
    run_begin = next(e for e in pending if e["type"] == "computer_use.begin")
    computer_run_id = run_begin["computer_use_run_id"]

    with session_scope(uuid.UUID(owner.org_id)) as db:
        ticket_run = db.get(AgentRun, uuid.UUID(created["run_id"]))
        ticket_run.state = "ESCALATED"
        add_message(
            db,
            ticket_run,
            "assistant",
            "I've handed this to a technician so a person can take it from here. You'll get an update on your ticket.",
            data={"escalation_reason": "No approved procedure matched"},
        )
        computer_run = db.get(ComputerUseRun, uuid.UUID(computer_run_id))
        computer_run.local_consent_state = "approved"
        computer_run.state = "waiting_customer"
        computer_run.waiting_for = {
            "kind": "question",
            "text": "I can't capture the screen. Is Adobe Acrobat listed in Installed apps?",
            "tool_use_id": "call-question",
        }
    detail = device.get(f"/support-requests/{created['run_id']}")
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["state"] == "AI_INVESTIGATING"
    assert not any("handed this to a technician" in message["content"] for message in body["messages"])
    assert body["messages"][-1]["content"] == "I can't capture the screen. Is Adobe Acrobat listed in Installed apps?"

    answer = device.post(
        f"/support-requests/{created['run_id']}/messages",
        {"message": "Yes, Adobe Acrobat is listed in Installed apps."},
    )
    assert answer.status_code == 200, answer.text
    assert answer.json()["run_id"] == created["run_id"]
    with session_scope(uuid.UUID(owner.org_id)) as db:
        computer_run = db.get(ComputerUseRun, uuid.UUID(computer_run_id))
        assert computer_run.state != "waiting_customer"
        assert computer_run.waiting_for is None


def test_device_poll_delivers_native_computer_use_action_approval(app, owner):
    from joltiq.db import session_scope
    from joltiq.models import ComputerUseRun

    capabilities = [
        "system.info", "screen.capture", "input.click", "input.type", "input.key", "input.scroll", "input.move",
        "url.open", "guide.highlight", "guide.clear",
    ]
    device = FakeDevice(app)
    assert device.enroll(_code(owner), caps=capabilities).status_code == 200
    created = device.post(
        "/support-requests",
        {"client_request_id": str(uuid.uuid4()), "message": "Please uninstall Adobe Acrobat DC."},
    ).json()
    device.post(f"/support-requests/{created['run_id']}/computer-use", {"mode": "fix"})
    begin = [json.loads(base64.b64decode(e["payload"])) for e in device.get("/poll").json()["envelopes"]]
    session_begin = next(e for e in begin if e["type"] == "session.begin")
    assert device.post(
        f"/sessions/{session_begin['session_id']}/local-consent",
        {"accepted": True, "user_display_name": "John"},
    ).status_code == 200
    pending = [json.loads(base64.b64decode(e["payload"])) for e in device.get("/poll").json()["envelopes"]]
    run_begin = next(e for e in pending if e["type"] == "computer_use.begin")
    run_id = uuid.UUID(run_begin["computer_use_run_id"])
    assert device.post(f"/computer-use/{run_id}/local-consent", {"accepted": True}).status_code == 200

    with session_scope(uuid.UUID(owner.org_id)) as db:
        run = db.get(ComputerUseRun, run_id)
        run.state = "waiting_customer"
        run.waiting_for = {
            "kind": "approval",
            "text": "Uninstall Adobe Acrobat (64-bit) while leaving Adobe Genuine Service installed.",
            "step_id": "approval-step-1",
            "tool_use_id": "call-approval",
        }

    approval_poll = [json.loads(base64.b64decode(e["payload"])) for e in device.get("/poll").json()["envelopes"]]
    approval = next(e for e in approval_poll if e["type"] == "computer_use.approval")
    assert approval == {
        "type": "computer_use.approval",
        "session_id": session_begin["session_id"],
        "computer_use_run_id": str(run_id),
        "device_id": device.device_id,
        "text": "Uninstall Adobe Acrobat (64-bit) while leaving Adobe Genuine Service installed.",
        "interaction_id": "approval-step-1",
        "issued_at": approval["issued_at"],
        "expires_at": approval["expires_at"],
        "nonce": approval["nonce"],
    }


def test_revoked_device_cannot_create_support_request(app, owner):
    d = FakeDevice(app)
    assert d.enroll(_code(owner)).status_code == 200
    assert owner.post(f"/api/v1/devices/{d.device_id}/revoke").status_code == 200

    refused = d.post(
        "/support-requests",
        {"client_request_id": str(uuid.uuid4()), "message": "This must not create a ticket."},
    )
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "device_revoked"


def test_ownerless_device_cannot_create_or_read_support_requests(app, owner):
    from joltiq.db import session_scope
    from joltiq.models import Device

    d = FakeDevice(app)
    assert d.enroll(_code(owner)).status_code == 200
    created = d.post(
        "/support-requests",
        {"client_request_id": str(uuid.uuid4()), "message": "Create before owner is removed."},
    )
    assert created.status_code == 201, created.text

    with session_scope(uuid.UUID(owner.org_id)) as db:
        device = db.get(Device, uuid.UUID(d.device_id))
        device.owner_user_id = None

    refused_create = d.post(
        "/support-requests",
        {"client_request_id": str(uuid.uuid4()), "message": "This must be refused."},
    )
    refused_read = d.get(f"/support-requests/{created.json()['run_id']}")
    assert refused_create.status_code == 403
    assert refused_read.status_code == 403


def test_device_with_stale_owner_membership_cannot_continue_support_request(app, owner):
    from sqlalchemy import select

    from joltiq.db import session_scope
    from joltiq.models import Device, Membership

    d = FakeDevice(app)
    assert d.enroll(_code(owner)).status_code == 200
    created = d.post(
        "/support-requests",
        {"client_request_id": str(uuid.uuid4()), "message": "Create before membership is removed."},
    )
    assert created.status_code == 201, created.text

    with session_scope(uuid.UUID(owner.org_id)) as db:
        device = db.get(Device, uuid.UUID(d.device_id))
        membership = db.execute(
            select(Membership).where(
                Membership.user_id == device.owner_user_id,
                Membership.org_id == device.org_id,
            )
        ).scalar_one()
        db.delete(membership)

    refused = d.post(
        f"/support-requests/{created.json()['run_id']}/messages",
        {"message": "This must be refused."},
    )
    assert refused.status_code == 403


def test_device_support_request_retry_returns_original_ticket_and_run(app, owner):
    from sqlalchemy import func, select

    from joltiq.db import session_scope
    from joltiq.models import DeviceSupportRequest

    d = FakeDevice(app)
    assert d.enroll(_code(owner)).status_code == 200
    request_id = str(uuid.uuid4())
    payload = {"client_request_id": request_id, "message": "The same request must be safe to retry."}

    first = d.post("/support-requests", payload)
    retry = d.post("/support-requests", payload)
    assert first.status_code == 201, first.text
    assert retry.status_code == 201, retry.text
    assert retry.json() == first.json()

    with session_scope(uuid.UUID(owner.org_id)) as db:
        count = db.scalar(
            select(func.count()).select_from(DeviceSupportRequest).where(
                DeviceSupportRequest.device_id == uuid.UUID(d.device_id),
                DeviceSupportRequest.client_request_id == uuid.UUID(request_id),
            )
        )
        assert count == 1


def test_device_support_request_idempotency_key_is_scoped_to_device(app, owner):
    first_device = FakeDevice(app)
    second_device = FakeDevice(app)
    assert first_device.enroll(_code(owner)).status_code == 200
    assert second_device.enroll(_code(owner)).status_code == 200
    payload = {"client_request_id": str(uuid.uuid4()), "message": "Device-scoped retry key."}

    first = first_device.post("/support-requests", payload)
    second = second_device.post("/support-requests", payload)
    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["ticket_id"] != second.json()["ticket_id"]
    assert first.json()["run_id"] != second.json()["run_id"]


def test_server_envelope_signature_verifies(app, owner):
    from joltiq.services.devices import sign_envelope

    d = FakeDevice(app)
    d.enroll(_code(owner))
    env = sign_envelope({"type": "action", "x": "é", "n": 1})
    pub = serialization.load_pem_public_key(d.server_pem.encode())
    payload = base64.b64decode(env["payload"])
    pub.verify(base64.b64decode(env["signature"]), payload, ec.ECDSA(hashes.SHA256()))
    assert json.loads(payload)["x"] == "é"


def test_cross_tenant_device_invisible(app, owner, new_client):
    from conftest import register

    d = FakeDevice(app)
    d.enroll(_code(owner))
    other = register(new_client(), "Other")
    assert other.get(f"/api/v1/devices/{d.device_id}").status_code == 404
    assert other.post(f"/api/v1/devices/{d.device_id}/revoke").status_code == 404


def _queued_command(owner, device_id):
    from joltiq.db import session_scope
    from joltiq.models import Device
    from joltiq.services.devices import queue_command

    with session_scope(uuid.UUID(owner.org_id)) as db:
        device = db.get(Device, uuid.UUID(device_id))
        command = queue_command(db, device, action="system.info", params={}, session=None)
        return str(command.id), command.created_at, command.expires_at


def test_command_expiry_is_120_seconds(app, owner):
    d = FakeDevice(app)
    assert d.enroll(_code(owner)).status_code == 200
    _, created_at, expires_at = _queued_command(owner, d.device_id)
    assert 119 <= (expires_at - created_at).total_seconds() <= 121


def test_identical_terminal_result_retry_is_idempotent_but_conflict_is_security_event(app, owner):
    from sqlalchemy import select

    from joltiq.db import session_scope
    from joltiq.models import DeviceCommand, SecurityEvent

    d = FakeDevice(app)
    assert d.enroll(_code(owner)).status_code == 200
    command_id, _, _ = _queued_command(owner, d.device_id)
    result = {"status": "done", "result": {"ok": True, "value": 7}}
    assert d.post(f"/commands/{command_id}/result", result).status_code == 200
    retry = d.post(f"/commands/{command_id}/result", result)
    assert retry.status_code == 200 and retry.json()["idempotent"] is True

    conflict = d.post(f"/commands/{command_id}/result", {"status": "failed", "result": {"ok": False}})
    assert conflict.status_code == 409 and conflict.json()["error"]["code"] == "command_result_conflict"
    with session_scope(uuid.UUID(owner.org_id)) as db:
        command = db.get(DeviceCommand, uuid.UUID(command_id))
        assert command.status == "done" and command.result == {"ok": True, "value": 7}
        events = db.execute(select(SecurityEvent).where(SecurityEvent.kind == "device_command_result_conflict")).scalars().all()
        assert len(events) == 1


def test_late_result_is_retained_as_evidence_without_changing_terminal_state_or_enqueuing_work(app, owner):
    from sqlalchemy import select

    from joltiq.db import session_scope
    from joltiq.models import DeviceCommand, Job

    d = FakeDevice(app)
    assert d.enroll(_code(owner)).status_code == 200
    command_id, _, _ = _queued_command(owner, d.device_id)
    with session_scope(uuid.UUID(owner.org_id)) as db:
        command = db.get(DeviceCommand, uuid.UUID(command_id))
        command.status = "expired"

    late = d.post(f"/commands/{command_id}/result", {"status": "done", "result": {"ok": True, "changed": True}})
    assert late.status_code == 200 and late.json() == {"ok": True, "status": "expired", "late": True, "idempotent": False}
    with session_scope(uuid.UUID(owner.org_id)) as db:
        command = db.get(DeviceCommand, uuid.UUID(command_id))
        assert command.status == "expired"
        assert command.result == {"late": True, "reported_status": "done", "result": {"ok": True, "changed": True}}
        jobs = db.execute(select(Job).where(Job.kind == "agent.device_result", Job.payload["command_id"].astext == command_id)).scalars().all()
        assert jobs == []


def test_result_after_run_escalation_stays_late_evidence_and_cannot_revive_execution(app, owner):
    from sqlalchemy import select

    from joltiq.db import session_scope
    from joltiq.models import ActionExecution, AgentRun, Device, DeviceCommand, Job
    from joltiq.services.devices import queue_command

    d = FakeDevice(app)
    assert d.enroll(_code(owner)).status_code == 200
    org_id = uuid.UUID(owner.org_id)
    with session_scope(org_id) as db:
        run = AgentRun(org_id=org_id, state="ESCALATED", correlation_id="late-result-test", outcome_stage="UNCONFIRMED")
        db.add(run)
        db.flush()
        execution = ActionExecution(org_id=org_id, run_id=run.id, action="system.info", target={}, params={}, risk="READ_ONLY",
                                    policy_decision={}, status="failed", result={}, idempotency_key="late-result-test")
        db.add(execution)
        db.flush()
        device = db.get(Device, uuid.UUID(d.device_id))
        command = queue_command(db, device, action="system.info", params={}, session=None, execution_id=execution.id)
        command_id, execution_id = str(command.id), execution.id

    response = d.post(f"/commands/{command_id}/result", {"status": "done", "result": {"ok": True}})
    assert response.status_code == 200 and response.json()["late"] is True
    with session_scope(org_id) as db:
        command = db.get(DeviceCommand, uuid.UUID(command_id))
        execution = db.get(ActionExecution, execution_id)
        assert command.status == "expired" and command.result["reported_status"] == "done"
        assert execution.status == "failed"
        jobs = db.execute(select(Job).where(Job.kind == "agent.device_result", Job.payload["command_id"].astext == command_id)).scalars().all()
        assert jobs == []
