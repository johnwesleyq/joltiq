"""Windows companion protocol v1 (server side). Spec: packages/agent-protocol/PROTOCOL.md

Trust model
  * Device → server: every request is signed with the device's ECDSA P-256 private key (generated on-device,
    stored with Windows DPAPI/CNG, never leaves the machine). Server verifies signature, timestamp skew (±120 s),
    single-use nonce, and a strictly increasing counter (replay protection).
  * Server → device: every action envelope is signed with the server's ECDSA P-256 key. The companion verifies the
    signature with the server public key pinned at enrollment, checks expiry/nonce, and re-validates the action
    against its LOCAL allowlist/policy before doing anything.
  * Transport: outbound HTTPS only (the device polls); no inbound ports on the customer machine.
"""
from __future__ import annotations

import base64
import hashlib
import uuid
from datetime import timedelta
from functools import lru_cache
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from ..config import get_settings
from ..core.errors import AppError, Conflict, Forbidden, NotFound, Unauthorized
from ..db import set_tenant
from ..models import Device, DeviceCommand, DeviceEnrollment, DeviceNonce, SupportSession, utcnow
from ..security.crypto import canonical_json, new_token, sha256_hex
from . import audit
from .actions.registry import ACTIONS, validate_params

PROTOCOL_VERSION = "1"
MAX_SKEW = 120
ENROLL_TTL_MIN = 15
COMMAND_TTL_S = 120
DESKTOP_ACTIONS = [a.id for a in ACTIONS.values() if a.channel == "desktop" and not a.computer_use]
_TEST_DESKTOP_ACTIONS = [a.id for a in ACTIONS.values() if a.channel == "desktop"]
MIN_AGENT_VERSION = "1.0.0"


def accepted_desktop_actions() -> list[str]:
    """Production MVP excludes screen-driving; tests retain coverage of the dormant implementation."""
    return _TEST_DESKTOP_ACTIONS if get_settings().env == "test" else DESKTOP_ACTIONS


@lru_cache
def server_key() -> ec.EllipticCurvePrivateKey:
    s = get_settings()
    import os

    pem = os.environ.get("JOLTIQ_AGENT_SIGNING_KEY_PEM")
    if pem:
        k = serialization.load_pem_private_key(pem.encode(), password=None)
        assert isinstance(k, ec.EllipticCurvePrivateKey)
        return k
    if s.env == "production":
        raise RuntimeError("JOLTIQ_AGENT_SIGNING_KEY_PEM is required in production")
    # Dev/test: deterministic key derived from the secret so restarts keep enrolled devices valid.
    n = int.from_bytes(hashlib.sha256(("agent-sign:" + s.secret_key).encode()).digest(), "big")
    order = int("FFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551", 16)
    return ec.derive_private_key(n % (order - 1) + 1, ec.SECP256R1())


def server_public_pem() -> str:
    return server_key().public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def sign_envelope(body: dict[str, Any]) -> dict[str, Any]:
    msg = canonical_json(body).encode()
    sig = server_key().sign(msg, ec.ECDSA(hashes.SHA256()))
    # `payload` carries the exact signed bytes so verifiers never re-serialize JSON (no canonicalization drift).
    return {"protocol": PROTOCOL_VERSION, "payload": base64.b64encode(msg).decode(), "body": body,
            "signature": base64.b64encode(sig).decode(), "alg": "ES256-DER"}


def request_string(method: str, path: str, ts: str, nonce: str, counter: str, body: bytes) -> bytes:
    return "\n".join([method.upper(), path, ts, nonce, counter, hashlib.sha256(body).hexdigest()]).encode()


# ---------------- enrollment ----------------
def create_enrollment(db: Session, org_id: uuid.UUID, *, created_by, customer_id=None) -> tuple[str, DeviceEnrollment]:
    from .billing import check_entitlement

    check_entitlement(db, org_id, "devices", adding=1)
    # 12 chars from an unambiguous alphabet ≈ 60 bits, single use, 15 minutes.
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    raw = "".join(alphabet[b % len(alphabet)] for b in new_token(24).encode()[:12])
    code = f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"
    e = DeviceEnrollment(org_id=org_id, created_by=created_by, code_hash=sha256_hex(code.replace("-", "")),
                         expires_at=utcnow() + timedelta(minutes=ENROLL_TTL_MIN), customer_id=customer_id)
    db.add(e)
    db.flush()
    audit.record(db, org_id=org_id, actor_kind="user", actor_id=created_by, action="device.enrollment_code_created", resource_type="device_enrollment", resource_id=e.id)
    return code, e


def enroll(db: Session, *, code: str, public_key_pem: str, hostname: str, os_name: str, os_version: str, agent_version: str,
           capabilities: list[str]) -> Device:
    set_tenant(db, None, platform=True)  # lookup by unguessable code hash only
    e = db.execute(select(DeviceEnrollment).where(DeviceEnrollment.code_hash == sha256_hex(code.replace("-", "").strip().upper()))
                   .with_for_update()).scalar_one_or_none()
    if not e or e.used_at or e.expires_at < utcnow():
        raise Unauthorized("Enrollment code is invalid, used or expired", code="enrollment_invalid")
    try:
        pk = serialization.load_pem_public_key(public_key_pem.encode())
    except ValueError as ex:
        raise AppError("Invalid device public key", code="invalid_key") from ex
    if not isinstance(pk, ec.EllipticCurvePublicKey) or pk.curve.name != "secp256r1":
        raise AppError("Device key must be ECDSA P-256", code="invalid_key")
    if _ver(agent_version) < _ver(MIN_AGENT_VERSION):
        raise AppError(f"Agent version {agent_version} is too old; minimum {MIN_AGENT_VERSION}", code="agent_outdated")
    set_tenant(db, e.org_id)
    caps = [c for c in capabilities if c in accepted_desktop_actions()]
    d = Device(org_id=e.org_id, customer_id=e.customer_id, owner_user_id=e.created_by, hostname=hostname[:255], os_name=os_name[:120],
               os_version=os_version[:120], agent_version=agent_version[:40], status="active", public_key_pem=public_key_pem,
               last_seen_at=utcnow(), capabilities=caps, last_counter=0)
    db.add(d)
    db.flush()
    e.used_at, e.device_id = utcnow(), d.id
    audit.record(db, org_id=e.org_id, actor_kind="device", actor_id=d.id, action="device.enrolled", resource_type="device", resource_id=d.id,
                 data={"hostname": hostname, "os": f"{os_name} {os_version}", "agent_version": agent_version, "capabilities": caps})
    from .notifications import notify

    notify(db, e.org_id, [e.created_by], "device", f"Device {hostname} enrolled", f"{os_name} {os_version}, agent {agent_version}", f"/admin/devices/{d.id}", email=False)
    return d


def _ver(v: str) -> tuple[int, ...]:
    try:
        return tuple(int(x) for x in v.split(".")[:3])
    except ValueError:
        return (0,)


# ---------------- request authentication ----------------
def authenticate_device(db: Session, *, device_id: str, ts: str, nonce: str, counter: str, signature: str, method: str, path: str,
                        body: bytes) -> Device:
    set_tenant(db, None, platform=True)
    try:
        did = uuid.UUID(device_id)
    except ValueError as e:
        raise Unauthorized("bad device id") from e
    d = db.get(Device, did)
    if not d or not d.public_key_pem:
        raise Unauthorized("unknown device")
    set_tenant(db, d.org_id)
    if d.status != "active" or d.revoked_at:
        raise Forbidden("Device has been revoked", code="device_revoked")
    now = int(utcnow().timestamp())
    try:
        t = int(ts)
        ctr = int(counter)
    except ValueError as e:
        raise Unauthorized("bad timestamp/counter") from e
    if abs(now - t) > MAX_SKEW:
        audit.security_event(db, "device_clock_skew_or_replay", org_id=d.org_id, severity="warn", data={"device": str(d.id), "skew": now - t})
        raise Unauthorized("timestamp outside allowed window", code="stale_request")
    if not (8 <= len(nonce) <= 64):
        raise Unauthorized("bad nonce")
    pk = serialization.load_pem_public_key(d.public_key_pem.encode())
    if not isinstance(pk, ec.EllipticCurvePublicKey):
        raise Unauthorized("device key type not allowed", code="bad_signature")
    try:
        pk.verify(base64.b64decode(signature), request_string(method, path, ts, nonce, counter, body), ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, ValueError) as e:
        audit.security_event(db, "device_bad_signature", org_id=d.org_id, severity="critical", data={"device": str(d.id)})
        raise Unauthorized("invalid signature", code="bad_signature") from e
    if ctr <= d.last_counter:
        audit.security_event(db, "device_replay", org_id=d.org_id, severity="critical", data={"device": str(d.id), "counter": ctr, "last": d.last_counter})
        raise Unauthorized("replayed or out-of-order request", code="replay")
    res = db.execute(insert(DeviceNonce).values(org_id=d.org_id, device_id=d.id, nonce=nonce, seen_at=utcnow())
                     .on_conflict_do_nothing(index_elements=["device_id", "nonce"]).returning(DeviceNonce.nonce))
    if res.scalar() is None:
        audit.security_event(db, "device_replay", org_id=d.org_id, severity="critical", data={"device": str(d.id), "nonce": nonce})
        raise Unauthorized("nonce reused", code="replay")
    d.last_counter = ctr
    d.last_seen_at = utcnow()
    return d


# ---------------- commands ----------------
def queue_command(db: Session, device: Device, *, action: str, params: dict, session: SupportSession | None, execution_id=None,
                  procedure_hash: str | None = None) -> DeviceCommand:
    if ACTIONS.get(action) is None or (ACTIONS[action].computer_use and get_settings().env != "test"):
        raise AppError("Windows MVP does not dispatch screen-control actions", code="unsupported_action")
    clean = validate_params(action, params)
    if action not in (device.capabilities or []):
        raise AppError(f"Device agent does not support {action}", code="unsupported_action")
    if session is not None and not (session.consent or {}).get("device_local_consent"):
        raise Conflict("Waiting for the user to accept the session on their computer", code="device_consent_pending")
    cmd = DeviceCommand(org_id=device.org_id, device_id=device.id, session_id=session.id if session else None, execution_id=execution_id,
                        action=action, params=clean, status="queued", expires_at=utcnow() + timedelta(seconds=COMMAND_TTL_S))
    db.add(cmd)
    db.flush()
    payload = {"type": "action", "command_id": str(cmd.id), "device_id": str(device.id),
               "session_id": str(session.id) if session else None, "action": action, "params": clean,
               "issued_at": int(utcnow().timestamp()), "expires_at": int(cmd.expires_at.timestamp()),
               "nonce": new_token(12), "risk": ACTIONS[action].risk}
    if procedure_hash:
        payload["procedure_hash"] = procedure_hash
    cmd.envelope = sign_envelope(payload)
    return cmd


def session_begin_envelope(device: Device, s: SupportSession) -> dict:
    return sign_envelope({"type": "session.begin", "session_id": str(s.id), "device_id": str(device.id),
                          "requested_by": "JoltIQ support session", "consent": s.consent or {},
                          "issued_at": int(utcnow().timestamp()), "expires_at": int(s.expires_at.timestamp()) if s.expires_at else None,
                          "nonce": new_token(12)})


def pending_for(db: Session, device: Device) -> list[dict]:
    out = []
    now = utcnow()
    # Sessions waiting for local consent on this device.
    for s in db.execute(select(SupportSession).where(SupportSession.device_id == device.id, SupportSession.state == "active")).scalars():
        c = s.consent or {}
        if not c.get("device_local_consent") and not c.get("device_local_declined"):
            out.append(session_begin_envelope(device, s))
    for s in db.execute(select(SupportSession).where(SupportSession.device_id == device.id, SupportSession.state == "terminated",
                                                     SupportSession.terminated_at > now - timedelta(minutes=5))).scalars():
        out.append(sign_envelope({"type": "session.end", "session_id": str(s.id), "issued_at": int(now.timestamp()), "nonce": new_token(12)}))
    for cmd in db.execute(select(DeviceCommand).where(DeviceCommand.device_id == device.id, DeviceCommand.status == "queued")
                          .order_by(DeviceCommand.created_at).limit(10)).scalars():
        if cmd.expires_at < now:
            cmd.status = "expired"
            continue
        cmd.status, cmd.delivered_at = "delivered", now
        out.append(cmd.envelope)
    return out


def record_result(db: Session, device: Device, command_id: uuid.UUID, status: str,
                  result: dict) -> tuple[DeviceCommand, bool, bool, bool]:
    from ..security.redaction import redact_obj

    cmd = db.execute(select(DeviceCommand).where(DeviceCommand.id == command_id).with_for_update()).scalar_one_or_none()
    if not cmd or cmd.device_id != device.id:
        raise NotFound("command not found")
    if status not in ("done", "failed", "rejected"):
        raise AppError("bad status")
    clean = redact_obj(result)
    linked_run = None
    if cmd.execution_id:
        from ..models import ActionExecution, AgentRun

        execution = db.get(ActionExecution, cmd.execution_id)
        linked_run = db.get(AgentRun, execution.run_id) if execution and execution.run_id else None
    if linked_run is None and cmd.session_id:
        from ..models import AgentRun

        support_session = db.get(SupportSession, cmd.session_id)
        linked_run = db.get(AgentRun, support_session.run_id) if support_session and support_session.run_id else None
    linked_run_terminal = bool(linked_run and linked_run.state in
                               ("RESOLVED", "ESCALATED", "FAILED", "CANCELLED", "PARTIALLY_RESOLVED"))
    if cmd.status in ("done", "failed", "rejected"):
        if cmd.status == status and cmd.result == clean:
            return cmd, True, False, False
        audit.security_event(db, "device_command_result_conflict", org_id=device.org_id, severity="critical",
                             data={"device": str(device.id), "command": str(cmd.id), "stored_status": cmd.status,
                                   "reported_status": status})
        return cmd, False, False, True
    if cmd.status == "expired" or linked_run_terminal:
        late_evidence = {"late": True, "reported_status": status, "result": clean}
        if cmd.result is not None:
            if cmd.result == late_evidence:
                return cmd, True, True, False
            audit.security_event(db, "device_command_result_conflict", org_id=device.org_id, severity="critical",
                                 data={"device": str(device.id), "command": str(cmd.id), "stored_status": "expired",
                                       "reported_status": status})
            return cmd, False, True, True
        cmd.status, cmd.result, cmd.completed_at = "expired", late_evidence, utcnow()
        from .realtime import publish

        publish(db, device.org_id, "device.command_late_result", {"command_id": str(cmd.id), "reported_status": status,
                                                                   "action": cmd.action})
        return cmd, False, True, False
    if cmd.status not in ("delivered", "queued"):
        raise Conflict(f"command already {cmd.status}")
    cmd.status, cmd.result, cmd.completed_at = status, clean, utcnow()
    from .realtime import publish

    publish(db, device.org_id, "device.command_result", {"command_id": str(cmd.id), "status": status, "action": cmd.action,
                                                         "session_id": str(cmd.session_id) if cmd.session_id else None})
    if cmd.execution_id or cmd.session_id:
        from .jobs import enqueue

        enqueue(db, "agent.device_result", {"command_id": str(cmd.id)}, org_id=device.org_id)
    return cmd, False, False, False


def revoke(db: Session, device: Device, actor_id) -> None:
    device.status, device.revoked_at = "revoked", utcnow()
    for s in db.execute(select(SupportSession).where(SupportSession.device_id == device.id,
                                                     SupportSession.state.in_(["active", "paused", "pending_consent", "awaiting_user_auth"]))).scalars():
        from .sessions import terminate

        terminate(db, s, by_kind="system", reason="device revoked")
    for cmd in db.execute(select(DeviceCommand).where(DeviceCommand.device_id == device.id, DeviceCommand.status.in_(["queued", "delivered"]))).scalars():
        cmd.status = "expired"
    audit.record(db, org_id=device.org_id, actor_kind="user", actor_id=actor_id, action="device.revoked", resource_type="device", resource_id=device.id)


def ser(d: Device) -> dict[str, Any]:
    online = bool(d.last_seen_at and (utcnow() - d.last_seen_at).total_seconds() < 90)
    return {"id": str(d.id), "hostname": d.hostname, "os_name": d.os_name, "os_version": d.os_version, "agent_version": d.agent_version,
            "status": d.status, "online": online, "last_seen_at": d.last_seen_at.isoformat() if d.last_seen_at else None,
            "capabilities": d.capabilities, "customer_id": str(d.customer_id) if d.customer_id else None,
            "owner_user_id": str(d.owner_user_id) if d.owner_user_id else None, "facts": d.facts or {}}

