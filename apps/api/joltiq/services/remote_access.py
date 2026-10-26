from __future__ import annotations

import uuid
from urllib.parse import quote, urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..core.errors import AppError, Conflict, NotFound
from ..models import Device, RemoteAccessBinding, RemoteAccessSession, utcnow
from . import audit


def configured_base_url() -> str:
    raw = (get_settings().meshcentral_url or "").strip().rstrip("/")
    if not raw:
        raise Conflict("Technician remote access is not configured for this deployment", code="remote_access_not_configured")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise AppError("The deployment MeshCentral URL is invalid", code="remote_access_configuration_invalid", status=500)
    if get_settings().env == "production" and parsed.scheme != "https":
        raise AppError("MeshCentral must use HTTPS in production", code="remote_access_configuration_invalid", status=500)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def launch_url(node_id: str) -> str:
    return f"{configured_base_url()}/?node={quote(node_id, safe='')}&viewmode=11&hide=15"


def binding_for(db: Session, device_id: uuid.UUID) -> RemoteAccessBinding | None:
    return db.execute(select(RemoteAccessBinding).where(RemoteAccessBinding.device_id == device_id)).scalar_one_or_none()


def bind(db: Session, device: Device, *, node_id: str, actor_id: uuid.UUID) -> RemoteAccessBinding:
    configured_base_url()
    binding = binding_for(db, device.id)
    if binding is None:
        binding = RemoteAccessBinding(
            org_id=device.org_id, device_id=device.id, provider="meshcentral", node_id=node_id, created_by=actor_id
        )
        db.add(binding)
    else:
        binding.node_id = node_id
    db.flush()
    audit.record(
        db, org_id=device.org_id, actor_kind="user", actor_id=actor_id, action="remote_access.bound",
        resource_type="device", resource_id=device.id, data={"provider": "meshcentral"},
    )
    return binding


def start(db: Session, device: Device, *, reason: str, operator_id: uuid.UUID) -> tuple[RemoteAccessSession, bool]:
    configured_base_url()
    binding = binding_for(db, device.id)
    if binding is None:
        raise Conflict("This computer is not linked to the remote support service", code="remote_access_not_bound")
    existing = db.execute(
        select(RemoteAccessSession).where(
            RemoteAccessSession.device_id == device.id, RemoteAccessSession.state == "active"
        ).order_by(RemoteAccessSession.created_at.desc())
    ).scalars().first()
    if existing:
        return existing, False
    session = RemoteAccessSession(
        org_id=device.org_id, device_id=device.id, binding_id=binding.id,
        operator_id=operator_id, reason=reason, state="active",
    )
    db.add(session)
    db.flush()
    audit.record(
        db, org_id=device.org_id, actor_kind="user", actor_id=operator_id, action="remote_access.started",
        resource_type="remote_access_session", resource_id=session.id,
        data={"device_id": str(device.id), "provider": "meshcentral", "reason": reason},
    )
    return session, True


def end(db: Session, session: RemoteAccessSession, *, actor_id: uuid.UUID) -> RemoteAccessSession:
    if session.state != "ended":
        session.state, session.ended_at = "ended", utcnow()
        audit.record(
            db, org_id=session.org_id, actor_kind="user", actor_id=actor_id, action="remote_access.ended",
            resource_type="remote_access_session", resource_id=session.id,
            data={"device_id": str(session.device_id)},
        )
    return session


def serialize(db: Session, session: RemoteAccessSession) -> dict:
    binding = db.get(RemoteAccessBinding, session.binding_id)
    if binding is None:
        raise NotFound("Remote access binding not found")
    transport_url = launch_url(binding.node_id) if session.state == "active" else None
    return {
        "id": str(session.id), "device_id": str(session.device_id), "provider": binding.provider,
        "state": session.state, "reason": session.reason,
        "created_at": session.created_at.isoformat(),
        "ended_at": session.ended_at.isoformat() if session.ended_at else None,
        "workspace_path": f"/portal/devices/{session.device_id}/remote-support",
        "transport_url": transport_url,
        "launch_url": transport_url,
    }
