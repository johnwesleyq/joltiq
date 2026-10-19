"""Device side of the JoltIQ protocol: ECDSA P-256 device key, signed requests (timestamp, nonce, counter),
pinned server key, signed-envelope verification with expiry + nonce replay protection."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import stat
import time
from pathlib import Path
from typing import Any

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

API = "/api/v1/agent/v1"


class ProtocolError(Exception):
    pass


def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


class DeviceClient:
    """State lives in `state_dir` (default ~/.joltiq-agent): device.key (0600), state.json (device id, server key, counter).
    On Windows the .NET companion uses DPAPI; this cross-platform companion relies on the user-only file permission."""

    def __init__(self, server: str, state_dir: str | os.PathLike | None = None, http: httpx.Client | None = None):
        self.server = server.rstrip("/")
        self.dir = Path(state_dir or Path.home() / ".joltiq-agent")
        self.http = http or httpx.Client(base_url=self.server, timeout=30)
        self.state: dict[str, Any] = {}
        sp = self.dir / "state.json"
        if sp.exists():
            self.state = json.loads(sp.read_text())
        kp = self.dir / "device.key"
        if kp.exists():
            self.key = serialization.load_pem_private_key(kp.read_bytes(), password=None)
        else:
            self.key = ec.generate_private_key(ec.SECP256R1())
            _write_private(kp, self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        self.seen_nonces: dict[str, float] = {}

    # ---- state ----
    @property
    def enrolled(self) -> bool:
        return bool(self.state.get("device_id"))

    def _save(self) -> None:
        _write_private(self.dir / "state.json", json.dumps(self.state).encode())

    def _next_counter(self) -> int:
        # Persist before use so a crash can never reuse a counter value.
        c = int(self.state.get("counter", 0)) + 1
        self.state["counter"] = c
        self._save()
        return c

    # ---- enrollment ----
    def enroll(self, code: str, *, hostname: str, os_name: str, os_version: str, agent_version: str, capabilities: list[str]) -> dict:
        pem = self.key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        r = self.http.post(f"/api/v1/agent/v1/enroll", json={"code": code, "public_key_pem": pem, "hostname": hostname, "os_name": os_name,
                                                             "os_version": os_version, "agent_version": agent_version,
                                                             "capabilities": capabilities})
        if r.status_code != 200:
            raise ProtocolError(f"enrollment failed: {r.status_code} {r.text[:300]}")
        j = r.json()
        self.state = {"device_id": j["device_id"], "org_id": j["org_id"], "server_public_key_pem": j["server_public_key_pem"],
                      "server": self.server, "counter": 0, "capabilities": j.get("capabilities_accepted", [])}
        self._save()
        return j

    # ---- signed requests ----
    def _headers(self, method: str, path: str, body: bytes) -> dict[str, str]:
        ts, nonce, ctr = str(int(time.time())), secrets.token_urlsafe(18), str(self._next_counter())
        msg = "\n".join([method, path, ts, nonce, ctr, hashlib.sha256(body).hexdigest()]).encode()
        sig = self.key.sign(msg, ec.ECDSA(hashes.SHA256()))
        return {"x-jq-device": self.state["device_id"], "x-jq-timestamp": ts, "x-jq-nonce": nonce, "x-jq-counter": ctr,
                "x-jq-signature": base64.b64encode(sig).decode()}

    def request(self, method: str, path: str, *, json_body: Any = None, raw: bytes | None = None, content_type: str | None = None) -> httpx.Response:
        full = API + path
        body = raw if raw is not None else (json.dumps(json_body).encode() if json_body is not None else b"")
        h = self._headers(method, full, body)
        if json_body is not None:
            h["content-type"] = "application/json"
        elif content_type:
            h["content-type"] = content_type
        return self.http.request(method, full, content=body, headers=h)

    # ---- envelopes ----
    def verify(self, env: dict) -> dict:
        pub = serialization.load_pem_public_key(self.state["server_public_key_pem"].encode())
        payload = base64.b64decode(env["payload"])
        try:
            pub.verify(base64.b64decode(env["signature"]), payload, ec.ECDSA(hashes.SHA256()))
        except (InvalidSignature, ValueError) as e:
            raise ProtocolError("envelope signature invalid") from e
        body = json.loads(payload)  # parse the signed bytes, never the unsigned copy
        now = time.time()
        if body.get("expires_at") and body["expires_at"] < now:
            raise ProtocolError("envelope expired")
        if body.get("device_id") and body["device_id"] != self.state["device_id"]:
            raise ProtocolError("envelope for another device")
        n = body.get("nonce")
        if n:
            if n in self.seen_nonces and body.get("type") == "action":
                raise ProtocolError("envelope nonce replayed")
            self.seen_nonces[n] = now
            for k, t in list(self.seen_nonces.items()):
                if now - t > 900:
                    del self.seen_nonces[k]
        return body
