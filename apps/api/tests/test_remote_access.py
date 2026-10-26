from __future__ import annotations

from sqlalchemy import select


def _device(owner) -> str:
    from joltiq.db import session_scope
    from joltiq.models import Device, utcnow

    with session_scope(owner.org_id) as db:
        device = Device(
            org_id=owner.org_id,
            owner_user_id=owner.me["user"]["id"],
            hostname="WIN11-REMOTE",
            os_name="Windows",
            os_version="11",
            agent_version="1.0.0",
            status="active",
            last_seen_at=utcnow(),
        )
        db.add(device)
        db.flush()
        return str(device.id)


def test_remote_access_requires_deployment_configuration(owner, monkeypatch):
    from joltiq.config import get_settings

    monkeypatch.setattr(get_settings(), "meshcentral_url", None)
    did = _device(owner)
    response = owner.post(f"/api/v1/devices/{did}/remote-access/sessions", json={"reason": "Customer requested help"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "remote_access_not_configured"


def test_staff_can_bind_start_reuse_and_end_audited_session(app, org_team, monkeypatch):
    from joltiq.config import get_settings
    from joltiq.db import session_scope
    from joltiq.models import AuditEvent, RemoteAccessSession

    owner, tech = org_team["owner"], org_team["tech"]
    did = _device(owner)
    monkeypatch.setattr(get_settings(), "meshcentral_url", "https://support.example.test/base")

    bound = owner.put(f"/api/v1/devices/{did}/remote-access", json={"node_id": "node/abc+123"})
    assert bound.status_code == 200, bound.text
    assert bound.json()["configured"] is True
    assert bound.json()["node_id"] == "node/abc+123"

    started = tech.post(
        f"/api/v1/devices/{did}/remote-access/sessions",
        json={"reason": "Customer asked for visible troubleshooting"},
    )
    assert started.status_code == 201, started.text
    body = started.json()
    assert body["state"] == "active"
    assert body["workspace_path"] == f"/portal/devices/{did}/remote-support"
    assert body["transport_url"] == "https://support.example.test/base/?node=node%2Fabc%2B123&viewmode=11&hide=15"
    assert body["launch_url"] == "https://support.example.test/base/?node=node%2Fabc%2B123&viewmode=11&hide=15"
    serialized = str(body).lower()
    assert "token" not in serialized
    assert "password" not in serialized
    assert "credential" not in serialized

    reused = tech.post(
        f"/api/v1/devices/{did}/remote-access/sessions",
        json={"reason": "Customer asked for visible troubleshooting"},
    )
    assert reused.status_code == 200
    assert reused.json()["id"] == body["id"]

    ended = tech.post(f"/api/v1/devices/{did}/remote-access/sessions/{body['id']}/end")
    assert ended.status_code == 200
    assert ended.json()["state"] == "ended"

    with session_scope(owner.org_id) as db:
        session = db.get(RemoteAccessSession, body["id"])
        assert session and session.ended_at is not None
        actions = db.execute(
            select(AuditEvent.action).where(AuditEvent.resource_id == body["id"]).order_by(AuditEvent.id)
        ).scalars().all()
        assert actions == ["remote_access.started", "remote_access.ended"]


def test_customer_cannot_bind_or_start_remote_access(app, org_team, monkeypatch):
    from joltiq.config import get_settings

    owner, customer = org_team["owner"], org_team["customer"]
    did = _device(owner)
    monkeypatch.setattr(get_settings(), "meshcentral_url", "https://support.example.test")
    assert customer.put(f"/api/v1/devices/{did}/remote-access", json={"node_id": "node-1"}).status_code == 403
    assert customer.post(
        f"/api/v1/devices/{did}/remote-access/sessions", json={"reason": "Try to bypass staff"}
    ).status_code == 403


def test_remote_access_rejects_unsafe_node_id(owner, monkeypatch):
    from joltiq.config import get_settings

    did = _device(owner)
    monkeypatch.setattr(get_settings(), "meshcentral_url", "https://support.example.test")
    response = owner.put(f"/api/v1/devices/{did}/remote-access", json={"node_id": "bad\nnode"})
    assert response.status_code == 422
