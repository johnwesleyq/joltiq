"""OIDC browser flow, identity linking, JIT and MFA preservation."""
from __future__ import annotations

import uuid
from urllib.parse import parse_qs, urlsplit

import pyotp
from sqlalchemy import select


def _seed_provider(owner, *, domain: str, jit: bool = False, role: str = "technician"):
    from joltiq.db import session_scope
    from joltiq.models import OidcProvider, OidcProviderDomain, User, utcnow
    from joltiq.security.crypto import encrypt_secret

    org_id = uuid.UUID(owner.org_id)
    with session_scope(org_id) as db:
        user = db.get(User, uuid.UUID(owner.me["user"]["id"]))
        user.email_verified_at = utcnow()
        provider = OidcProvider(
            org_id=org_id,
            display_name="Company Microsoft",
            kind="entra",
            issuer="https://login.microsoftonline.com/tenant/v2.0",
            client_id="client-id",
            client_secret_enc=encrypt_secret("client-secret"),
            scopes="openid email profile",
            default_role=role,
            jit_provisioning=jit,
            enabled=True,
            tested_at=utcnow(),
            tested_fingerprint="f" * 64,
        )
        db.add(provider)
        db.flush()
        db.add(OidcProviderDomain(org_id=org_id, provider_id=provider.id, domain=domain))
        return provider.id


def _set_owner_email(owner, domain: str) -> str:
    from joltiq.db import session_scope
    from joltiq.models import User, utcnow

    email = f"owner@{domain}"
    with session_scope(uuid.UUID(owner.org_id)) as db:
        user = db.get(User, uuid.UUID(owner.me["user"]["id"]))
        user.email = email
        user.email_verified_at = utcnow()
    owner.email = email
    owner.me["user"]["email"] = email
    return email


def _mock_protocol(monkeypatch, *, email: str, subject: str = "subject-1"):
    from joltiq.services import oidc

    metadata = oidc.OidcMetadata(
        issuer="https://login.microsoftonline.com/tenant/v2.0",
        authorization_endpoint="https://login.microsoftonline.com/tenant/v2.0/authorize",
        token_endpoint="https://login.microsoftonline.com/tenant/v2.0/token",
        jwks_uri="https://login.microsoftonline.com/tenant/v2.0/keys",
        code_challenge_methods_supported=["S256"],
    )
    monkeypatch.setattr(oidc, "discover_provider", lambda http, issuer, allowed_hosts: metadata)
    monkeypatch.setattr(oidc, "exchange_code", lambda *args, **kwargs: {"id_token": "validated-by-fixture"})
    monkeypatch.setattr(
        oidc,
        "fetch_and_validate_id_token",
        lambda *args, **kwargs: (
            oidc.ValidatedIdentity(
                issuer=metadata.issuer,
                subject=subject,
                email=email,
                email_verified=True,
                claims={"iss": metadata.issuer, "sub": subject, "email": email, "email_verified": True},
            ),
            {},
        ),
    )


def _begin(anon, email: str):
    lookup = anon.post("/api/v1/auth/sso/lookup", json={"email": email})
    assert lookup.status_code == 200 and lookup.json()["available"] is True
    start = anon.c.get(
        "/api/v1/auth/sso/start",
        params={"continuation": lookup.json()["continuation"]},
        follow_redirects=False,
    )
    assert start.status_code == 302, start.text
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    return state


def test_sso_links_existing_verified_email_and_state_is_single_use(owner, new_client, monkeypatch):
    domain = f"existing-{uuid.uuid4().hex}.example"
    _set_owner_email(owner, domain)
    provider_id = _seed_provider(owner, domain=domain)
    _mock_protocol(monkeypatch, email=owner.email)
    anon = new_client()
    state = _begin(anon, owner.email)

    callback = anon.c.get(
        "/api/v1/auth/sso/callback",
        params={"state": state, "code": "code-1"},
        follow_redirects=False,
    )
    assert callback.status_code == 302
    assert callback.headers["location"] == "/portal"
    me = anon.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["email"] == owner.email

    from joltiq.db import session_scope
    from joltiq.models import AuthSession, UserIdentity

    with session_scope(uuid.UUID(owner.org_id)) as db:
        identity = db.execute(select(UserIdentity).where(UserIdentity.provider_id == provider_id)).scalar_one()
        assert identity.linked_via == "verified-email"
        session = db.execute(
            select(AuthSession).where(AuthSession.user_id == identity.user_id).order_by(AuthSession.created_at.desc())
        ).scalars().first()
        assert session.auth_method == "oidc"
        assert session.oidc_provider_id == provider_id

    replay = anon.c.get(
        "/api/v1/auth/sso/callback",
        params={"state": state, "code": "code-1"},
        follow_redirects=False,
    )
    assert replay.status_code == 302
    assert "sso_error=" in replay.headers["location"]


def test_sso_safe_jit_creates_technician_membership(owner, new_client, monkeypatch):
    domain = f"jit-{uuid.uuid4().hex}.example"
    _seed_provider(owner, domain=domain, jit=True, role="technician")
    email = f"tech@{domain}"
    _mock_protocol(monkeypatch, email=email, subject="jit-subject")
    anon = new_client()
    state = _begin(anon, email)
    callback = anon.c.get(
        "/api/v1/auth/sso/callback", params={"state": state, "code": "code-2"}, follow_redirects=False
    )
    assert callback.status_code == 302 and callback.headers["location"] == "/portal"

    from joltiq.db import session_scope
    from joltiq.models import Membership, User

    with session_scope(uuid.UUID(owner.org_id)) as db:
        user = db.execute(select(User).where(User.email == email)).scalar_one()
        membership = db.execute(
            select(Membership).where(Membership.user_id == user.id, Membership.org_id == uuid.UUID(owner.org_id))
        ).scalar_one()
        assert membership.role == "technician"


def test_sso_customer_jit_requires_existing_customer_mapping(owner, new_client, monkeypatch):
    domain = f"customer-{uuid.uuid4().hex}.example"
    _seed_provider(owner, domain=domain, jit=True, role="customer")
    email = f"person@{domain}"
    _mock_protocol(monkeypatch, email=email)
    anon = new_client()
    state = _begin(anon, email)
    callback = anon.c.get(
        "/api/v1/auth/sso/callback", params={"state": state, "code": "code-3"}, follow_redirects=False
    )
    assert callback.status_code == 302
    assert "sso_error=" in callback.headers["location"]


def test_sso_preserves_totp_second_factor(owner, new_client, monkeypatch):
    from joltiq.db import session_scope
    from joltiq.models import User
    from joltiq.security.crypto import encrypt_secret

    domain = f"mfa-{uuid.uuid4().hex}.example"
    _set_owner_email(owner, domain)
    _seed_provider(owner, domain=domain)
    secret = pyotp.random_base32()
    with session_scope(uuid.UUID(owner.org_id)) as db:
        user = db.get(User, uuid.UUID(owner.me["user"]["id"]))
        user.mfa_secret_enc = encrypt_secret(secret)
        user.mfa_enabled = True
    _mock_protocol(monkeypatch, email=owner.email)
    anon = new_client()
    state = _begin(anon, owner.email)
    callback = anon.c.get(
        "/api/v1/auth/sso/callback", params={"state": state, "code": "code-4"}, follow_redirects=False
    )
    assert callback.status_code == 302 and callback.headers["location"] == "/login?sso_mfa=1"
    assert anon.get("/api/v1/auth/me").status_code == 401

    verified = anon.post("/api/v1/auth/sso/mfa", json={"code": pyotp.TOTP(secret).now()})
    assert verified.status_code == 200, verified.text
    assert verified.json()["user"]["email"] == owner.email
