"""Organization-scoped OIDC persistence and isolation."""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from joltiq.models import utcnow


def _classes():
    import joltiq.models as models

    required = (
        "OidcProvider",
        "OidcProviderDomain",
        "UserIdentity",
        "OidcLoginState",
        "SsoMfaChallenge",
        "SsoRecoveryGrant",
    )
    missing = [name for name in required if not hasattr(models, name)]
    assert not missing, f"missing OIDC persistence models: {', '.join(missing)}"
    return tuple(getattr(models, name) for name in required)


def _provider(provider_cls, org_id: uuid.UUID, suffix: str):
    return provider_cls(
        org_id=org_id,
        display_name=f"Provider {suffix}",
        kind="entra",
        issuer=f"https://login.microsoftonline.com/{suffix}/v2.0",
        client_id=f"client-{suffix}",
        scopes="openid email profile",
        default_role="technician",
    )


def test_oidc_models_exist_and_are_disabled_by_default(app, owner):
    from joltiq.db import session_scope

    provider_cls, domain_cls, identity_cls, state_cls, challenge_cls, recovery_cls = _classes()
    org_id = uuid.UUID(owner.org_id)
    with session_scope(org_id) as db:
        provider = _provider(provider_cls, org_id, uuid.uuid4().hex)
        db.add(provider)
        db.flush()
        db.add(domain_cls(org_id=org_id, provider_id=provider.id, domain="example.invalid"))
        db.flush()

        assert provider.enabled is False
        assert provider.enforce_sso is False
        assert provider.jit_provisioning is False
        assert provider.client_secret_enc is None
        assert db.execute(select(domain_cls).where(domain_cls.provider_id == provider.id)).scalar_one().domain == "example.invalid"
        assert all(getattr(cls, "__tenant__", False) for cls in (provider_cls, domain_cls, identity_cls, state_cls, challenge_cls, recovery_cls))


def test_oidc_domain_is_globally_unique_across_organizations(app, owner, new_client):
    from conftest import register

    from joltiq.db import session_scope

    provider_cls, domain_cls, *_ = _classes()
    org_a = uuid.UUID(owner.org_id)
    other = register(new_client(), "Other OIDC tenant")
    org_b = uuid.UUID(other.org_id)
    domain = f"corp-{uuid.uuid4().hex}.example"

    with session_scope(org_a) as db:
        provider = _provider(provider_cls, org_a, uuid.uuid4().hex)
        db.add(provider)
        db.flush()
        db.add(domain_cls(org_id=org_a, provider_id=provider.id, domain=domain))

    with pytest.raises(IntegrityError):
        with session_scope(org_b) as db:
            provider = _provider(provider_cls, org_b, uuid.uuid4().hex)
            db.add(provider)
            db.flush()
            db.add(domain_cls(org_id=org_b, provider_id=provider.id, domain=domain))
            db.flush()


def test_oidc_transient_and_identity_uniqueness(app, owner):
    from joltiq.db import session_scope
    from joltiq.models import User

    provider_cls, _, identity_cls, state_cls, challenge_cls, recovery_cls = _classes()
    org_id = uuid.UUID(owner.org_id)
    now = utcnow()
    with session_scope(org_id) as db:
        provider = _provider(provider_cls, org_id, uuid.uuid4().hex)
        db.add(provider)
        db.flush()
        user = db.get(User, uuid.UUID(owner.me["user"]["id"]))
        identity = identity_cls(
            org_id=org_id,
            user_id=user.id,
            provider_id=provider.id,
            issuer=provider.issuer,
            subject="subject-1",
            email_at_link=user.email,
            linked_via="verified-email",
        )
        db.add(identity)
        db.add_all(
            [
                state_cls(
                    org_id=org_id,
                    provider_id=provider.id,
                    state_hash="a" * 64,
                    nonce="nonce",
                    code_verifier_enc="encrypted",
                    return_to="/portal",
                    expires_at=now + timedelta(minutes=10),
                ),
                challenge_cls(
                    org_id=org_id,
                    user_id=user.id,
                    provider_id=provider.id,
                    token_hash="b" * 64,
                    expires_at=now + timedelta(minutes=5),
                ),
                recovery_cls(
                    org_id=org_id,
                    user_id=user.id,
                    token_hash="c" * 64,
                    expires_at=now + timedelta(minutes=15),
                ),
            ]
        )
        db.flush()
        assert identity.last_login_at is None

        with pytest.raises(IntegrityError), db.begin_nested():
            db.add(
                identity_cls(
                    org_id=org_id,
                    user_id=user.id,
                    provider_id=provider.id,
                    issuer=provider.issuer,
                    subject="subject-1",
                    email_at_link=user.email,
                    linked_via="verified-email",
                )
            )
            db.flush()


def test_existing_sessions_default_to_password(app, owner):
    from joltiq.db import session_scope
    from joltiq.models import AuthSession

    with session_scope(uuid.UUID(owner.org_id)) as db:
        session = db.execute(
            select(AuthSession).where(AuthSession.user_id == uuid.UUID(owner.me["user"]["id"]))
        ).scalar_one()
        assert session.auth_method == "password"
        assert session.oidc_provider_id is None
