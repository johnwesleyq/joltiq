import uuid

from conftest import PASSWORD, ApiClient, invite_and_join, register


def test_register_login_logout_and_me(app, new_client):
    c = register(new_client(), "Acme MSP")
    me = c.get("/api/v1/auth/me").json()
    assert me["active_organization"]["role"] == "org_owner"
    assert me["user"]["email_verified"] is False

    c2 = new_client()
    r = c2.post("/api/v1/auth/login", json={"email": c.email, "password": PASSWORD})
    assert r.status_code == 200
    assert c2.get("/api/v1/auth/me").status_code == 200
    assert c2.post("/api/v1/auth/logout").status_code == 200
    assert c2.get("/api/v1/auth/me").status_code == 401


def test_bad_password_and_lockout(app, new_client):
    c = register(new_client(), "Lock Co")
    bad = new_client()
    codes = [bad.post("/api/v1/auth/login", json={"email": c.email, "password": "wrong-password-xx"}).json()["error"]["code"] for _ in range(9)]
    assert "invalid_credentials" in codes
    assert codes[-1] in ("locked", "rate_limited")
    # Even the right password is refused while locked.
    r = bad.post("/api/v1/auth/login", json={"email": c.email, "password": PASSWORD})
    assert r.status_code in (401, 429)


def test_weak_password_rejected(new_client):
    r = new_client().post("/api/v1/auth/register", json={"email": f"{uuid.uuid4().hex}@x.com", "password": "short", "name": "x", "organization_name": "xx"})
    assert r.status_code == 422


def test_csrf_required_for_cookie_auth(app, new_client):
    c = register(new_client(), "CSRF Co")
    r = c.c.post("/api/v1/customers", json={"name": "No CSRF"})  # raw client, no header
    assert r.status_code == 403 and r.json()["error"]["code"] == "csrf_failed"
    assert c.post("/api/v1/customers", json={"name": "With CSRF"}).status_code == 201


def test_password_reset_flow_revokes_sessions(app, new_client):
    from joltiq.db import session_scope
    from joltiq.models import User
    from joltiq.services import auth as svc
    from sqlalchemy import select

    c = register(new_client(), "Reset Co")
    with session_scope(None) as db:
        user = db.execute(select(User).where(User.email == c.email)).scalar_one()
        token = svc.issue_email_token(db, user, "reset_password")
    anon = new_client()
    assert anon.post("/api/v1/auth/password/reset", json={"token": token, "password": "a-brand-new-password-1"}).status_code == 200
    assert c.get("/api/v1/auth/me").status_code == 401  # old session revoked
    assert anon.post("/api/v1/auth/password/reset", json={"token": token, "password": "another-new-password-2"}).status_code == 400  # single use
    assert new_client().post("/api/v1/auth/login", json={"email": c.email, "password": "a-brand-new-password-1"}).status_code == 200


def test_email_verification(app, new_client):
    from joltiq.db import session_scope
    from joltiq.models import User
    from joltiq.services import auth as svc
    from sqlalchemy import select

    c = register(new_client(), "Verify Co")
    with session_scope(None) as db:
        token = svc.issue_email_token(db, db.execute(select(User).where(User.email == c.email)).scalar_one(), "verify_email")
    assert c.post("/api/v1/auth/verify-email", json={"token": token}).status_code == 200
    assert c.get("/api/v1/auth/me").json()["user"]["email_verified"] is True


def test_mfa_totp_enrollment_and_login(app, new_client):
    import pyotp

    c = register(new_client(), "MFA Co")
    secret = c.post("/api/v1/auth/mfa/setup").json()["secret"]
    assert c.post("/api/v1/auth/mfa/enable", json={"code": pyotp.TOTP(secret).now()}).status_code == 200
    c2 = new_client()
    r = c2.post("/api/v1/auth/login", json={"email": c.email, "password": PASSWORD})
    assert r.status_code == 401 and r.json()["error"]["code"] == "mfa_required"
    r = c2.post("/api/v1/auth/login", json={"email": c.email, "password": PASSWORD, "totp": pyotp.TOTP(secret).now()})
    assert r.status_code == 200


def test_invitation_roles_and_rbac(app, owner):
    tech = invite_and_join(app, owner, "technician")
    assert tech.get("/api/v1/auth/me").json()["active_organization"]["role"] == "technician"
    # Technicians cannot manage users or invite admins.
    assert tech.get("/api/v1/users").status_code == 403
    assert tech.post("/api/v1/users/invitations", json={"email": "x@example.com", "role": "admin"}).status_code == 403
    # Admins cannot mint owners.
    admin = invite_and_join(app, owner, "admin")
    assert admin.post("/api/v1/users/invitations", json={"email": "y@example.com", "role": "org_owner"}).status_code == 403
    users = owner.get("/api/v1/users").json()
    assert users["total"] == 3


def test_organization_switching(app, new_client):
    c = register(new_client(), "First Org")
    first = c.org_id
    second = c.post("/api/v1/organizations", json={"name": "Second Org"}).json()["id"]
    me = c.post("/api/v1/auth/switch-organization", json={"organization_id": second}).json()
    assert me["active_organization"]["id"] == second
    other = register(new_client(), "Someone Else")
    r = other.post("/api/v1/auth/switch-organization", json={"organization_id": first})
    assert r.status_code == 403


def test_request_id_and_error_envelope(app):
    c = ApiClient(app)
    r = c.get("/api/v1/auth/me", headers={"x-request-id": "abc123"})
    assert r.status_code == 401
    assert r.headers["x-request-id"] == "abc123"
    assert r.json()["error"]["request_id"] == "abc123"
