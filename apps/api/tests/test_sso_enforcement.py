"""SSO enforcement and CLI-issued break-glass recovery."""
from __future__ import annotations

import uuid

import pyotp
from conftest import PASSWORD


def _enforced_owner(owner):
    from joltiq.db import session_scope
    from joltiq.models import OidcProvider, User, utcnow
    from joltiq.security.crypto import encrypt_secret

    org_id = uuid.UUID(owner.org_id)
    secret = pyotp.random_base32()
    with session_scope(org_id) as db:
        user = db.get(User, uuid.UUID(owner.me["user"]["id"]))
        user.email_verified_at = utcnow()
        user.mfa_secret_enc = encrypt_secret(secret)
        user.mfa_enabled = True
        provider = OidcProvider(
            org_id=org_id,
            display_name="Enforced Microsoft",
            kind="entra",
            issuer="https://login.microsoftonline.com/tenant/v2.0",
            client_id="client-id",
            enabled=True,
            enforce_sso=True,
            tested_at=utcnow(),
            tested_fingerprint="f" * 64,
        )
        db.add(provider)
    return secret


def test_enforced_sso_blocks_new_password_login_but_not_existing_session(owner, new_client):
    _enforced_owner(owner)
    assert owner.get("/api/v1/auth/me").status_code == 200

    password_client = new_client()
    denied = password_client.post(
        "/api/v1/auth/login",
        json={"email": owner.email, "password": PASSWORD, "totp": pyotp.TOTP(
            _secret_for(owner)
        ).now()},
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "sso_required"


def _secret_for(owner):
    from joltiq.db import session_scope
    from joltiq.models import User
    from joltiq.security.crypto import decrypt_secret

    with session_scope(uuid.UUID(owner.org_id)) as db:
        user = db.get(User, uuid.UUID(owner.me["user"]["id"]))
        return decrypt_secret(user.mfa_secret_enc)


def test_recovery_grant_requires_verified_mfa_owner(owner):
    from joltiq.db import session_scope
    from joltiq.services.auth import issue_sso_recovery_grant

    with session_scope(uuid.UUID(owner.org_id)) as db:
        try:
            issue_sso_recovery_grant(db, uuid.UUID(owner.org_id), owner.email, ttl_minutes=15)
        except ValueError as exc:
            assert "verified" in str(exc) or "MFA" in str(exc)
        else:
            raise AssertionError("unverified owner unexpectedly received a recovery grant")


def test_recovery_grant_is_single_use_and_creates_short_session(owner, new_client):
    from joltiq.db import session_scope
    from joltiq.services.auth import issue_sso_recovery_grant

    secret = _enforced_owner(owner)
    with session_scope(uuid.UUID(owner.org_id)) as db:
        raw = issue_sso_recovery_grant(db, uuid.UUID(owner.org_id), owner.email, ttl_minutes=15)

    recovery = new_client()
    response = recovery.post(
        "/api/v1/auth/sso/recovery",
        json={"token": raw, "code": pyotp.TOTP(secret).now()},
    )
    assert response.status_code == 200, response.text
    assert response.json()["user"]["email"] == owner.email

    from sqlalchemy import select

    from joltiq.models import AuthSession

    with session_scope(uuid.UUID(owner.org_id)) as db:
        session = db.execute(
            select(AuthSession).where(AuthSession.user_id == uuid.UUID(owner.me["user"]["id"])).order_by(AuthSession.created_at.desc())
        ).scalars().first()
        assert session.auth_method == "recovery"
        assert (session.expires_at - session.created_at).total_seconds() <= 16 * 60

    replay = new_client().post(
        "/api/v1/auth/sso/recovery",
        json={"token": raw, "code": pyotp.TOTP(secret).now()},
    )
    assert replay.status_code == 401
