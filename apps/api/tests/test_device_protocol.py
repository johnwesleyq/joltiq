"""Windows companion protocol: enrollment + request authentication attacks (signature, replay, revocation)."""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from conftest import ApiClient


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
