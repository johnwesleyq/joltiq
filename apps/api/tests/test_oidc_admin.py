"""OIDC administration, secret handling and neutral domain discovery."""
from __future__ import annotations

import uuid

from sqlalchemy import select


def _enable_mfa(client) -> None:
    from joltiq.db import session_scope
    from joltiq.models import AuthSession, User

    with session_scope(uuid.UUID(client.org_id)) as db:
        user = db.get(User, uuid.UUID(client.me["user"]["id"]))
        user.mfa_enabled = True
        session = db.execute(
            select(AuthSession).where(AuthSession.user_id == user.id).order_by(AuthSession.created_at.desc())
        ).scalars().first()
        session.mfa_passed = True


def _config(domain: str, **overrides):
    value = {
        "display_name": "Company Microsoft",
        "kind": "entra",
        "issuer": "https://login.microsoftonline.com/tenant/v2.0",
        "client_id": "client-id",
        "client_secret": "client-secret-value",
        "scopes": "openid email profile",
        "domains": [domain],
        "default_role": "technician",
        "role_mapping": {},
        "jit_provisioning": False,
        "enforce_sso": False,
        "enabled": True,
    }
    value.update(overrides)
    return value


def _mock_discovery(monkeypatch):
    from joltiq.services import oidc

    monkeypatch.setattr(
        oidc,
        "discover_provider",
        lambda http, issuer, allowed_hosts: oidc.OidcMetadata(
            issuer=issuer,
            authorization_endpoint=issuer + "/authorize",
            token_endpoint=issuer + "/token",
            jwks_uri=issuer + "/keys",
            code_challenge_methods_supported=["S256"],
        ),
    )


def test_oidc_admin_requires_mfa_and_test_proof(owner, monkeypatch):
    _mock_discovery(monkeypatch)
    body = _config(f"corp-{uuid.uuid4().hex}.example")
    denied = owner.post("/api/v1/admin/security/oidc/test", json=body)
    assert denied.status_code == 403

    _enable_mfa(owner)
    tested = owner.post("/api/v1/admin/security/oidc/test", json=body)
    assert tested.status_code == 200, tested.text
    proof = tested.json()["test_proof"]

    bad = owner.put("/api/v1/admin/security/oidc", json={**body, "test_proof": proof, "client_id": "changed"})
    assert bad.status_code == 409
    saved = owner.put("/api/v1/admin/security/oidc", json={**body, "test_proof": proof})
    assert saved.status_code == 200, saved.text
    assert saved.json()["client_secret_set"] is True
    assert "client_secret" not in saved.json()


def test_oidc_secret_is_encrypted_and_get_is_redacted(owner, monkeypatch):
    from joltiq.db import session_scope
    from joltiq.models import OidcProvider

    _mock_discovery(monkeypatch)
    _enable_mfa(owner)
    body = _config(f"secret-{uuid.uuid4().hex}.example")
    proof = owner.post("/api/v1/admin/security/oidc/test", json=body).json()["test_proof"]
    assert owner.put("/api/v1/admin/security/oidc", json={**body, "test_proof": proof}).status_code == 200

    with session_scope(uuid.UUID(owner.org_id)) as db:
        provider = db.execute(select(OidcProvider)).scalar_one()
        assert provider.client_secret_enc != body["client_secret"]
        assert body["client_secret"] not in provider.client_secret_enc
    fetched = owner.get("/api/v1/admin/security/oidc")
    assert fetched.status_code == 200
    assert body["client_secret"] not in fetched.text
    assert "client_secret" not in fetched.json()
    assert fetched.json()["client_secret_set"] is True


def test_oidc_domain_cannot_be_claimed_by_another_org(app, owner, new_client, monkeypatch):
    from conftest import register

    _mock_discovery(monkeypatch)
    domain = f"unique-{uuid.uuid4().hex}.example"
    for client in (owner, register(new_client(), "Second OIDC org")):
        _enable_mfa(client)
        body = _config(domain)
        proof = client.post("/api/v1/admin/security/oidc/test", json=body).json()["test_proof"]
        response = client.put("/api/v1/admin/security/oidc", json={**body, "test_proof": proof})
        if client is owner:
            assert response.status_code == 200
        else:
            assert response.status_code == 409


def test_sso_lookup_is_neutral_and_returns_only_opaque_continuation(owner, new_client, monkeypatch):
    _mock_discovery(monkeypatch)
    _enable_mfa(owner)
    domain = f"lookup-{uuid.uuid4().hex}.example"
    body = _config(domain)
    proof = owner.post("/api/v1/admin/security/oidc/test", json=body).json()["test_proof"]
    owner.put("/api/v1/admin/security/oidc", json={**body, "test_proof": proof})

    anon = new_client()
    known = anon.post("/api/v1/auth/sso/lookup", json={"email": f"nobody@{domain}"})
    unknown = anon.post("/api/v1/auth/sso/lookup", json={"email": "nobody@not-configured.example"})
    assert known.status_code == unknown.status_code == 200
    assert set(known.json()) == set(unknown.json()) == {"available", "continuation", "message"}
    assert known.json()["available"] is True
    assert known.json()["continuation"] and len(known.json()["continuation"]) >= 32
    assert owner.org_id not in known.text
    assert unknown.json() == {"available": False, "continuation": None, "message": known.json()["message"]}
