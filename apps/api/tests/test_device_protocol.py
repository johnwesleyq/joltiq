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

    created = d.post("/support-requests", {"message": "Outlook cannot find Adobe Sign."})
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
    created = d.post("/support-requests", {"message": "Please help with Outlook."})
    assert created.status_code == 201, created.text
    run_id = created.json()["run_id"]

    continued = d.post(f"/support-requests/{run_id}/messages", {"message": "It fails when I open a PDF."})
    assert continued.status_code == 200, continued.text
    assert continued.json()["run_id"] == run_id

    status = d.get(f"/support-requests/{run_id}")
    assert status.status_code == 200, status.text
    body = status.json()
    assert "model_calls" not in body
    assert "observations" not in body
    assert "hypotheses" not in body
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
    created = first.post("/support-requests", {"message": "Private device issue."})
    assert created.status_code == 201
    run_id = created.json()["run_id"]

    assert second.get(f"/support-requests/{run_id}").status_code == 404
    assert second.post(f"/support-requests/{run_id}/messages", {"message": "Cross-device write."}).status_code == 404


def test_revoked_device_cannot_create_support_request(app, owner):
    d = FakeDevice(app)
    assert d.enroll(_code(owner)).status_code == 200
    assert owner.post(f"/api/v1/devices/{d.device_id}/revoke").status_code == 200

    refused = d.post("/support-requests", {"message": "This must not create a ticket."})
    assert refused.status_code == 403
    assert refused.json()["error"]["code"] == "device_revoked"


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
