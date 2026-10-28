from __future__ import annotations

import re
import uuid
from datetime import timedelta

import pyotp
from fastapi import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..core.errors import AppError, Conflict, Unauthorized
from ..db import set_tenant
from ..deps import CSRF_COOKIE, SESSION_COOKIE
from ..models import (
    AIBudget,
    AuthSession,
    Membership,
    OneTimeToken,
    Organization,
    PolicyRule,
    SlaPolicy,
    Subscription,
    TicketCounter,
    User,
    utcnow,
)
from ..models.enums import Role
from ..security.crypto import decrypt_secret, encrypt_secret, hash_password, new_token, sha256_hex, verify_password
from . import audit
from .email import send_email

LOCKOUT_THRESHOLD = 8
LOCKOUT_MINUTES = 15


def validate_password(pw: str) -> None:
    if len(pw) < 12:
        raise AppError("Password must be at least 12 characters", code="weak_password")
    if pw.lower() in {"password1234", "123456789012", "qwertyuiop12"} or len(set(pw)) < 5:
        raise AppError("Password is too easy to guess", code="weak_password")


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "org"
    return f"{s}-{uuid.uuid4().hex[:6]}"


def create_session(db: Session, user: User, response: Response | None, *, org_id: uuid.UUID | None,
                   ip: str | None = None, ua: str | None = None, mfa_passed: bool = False,
                   auth_method: str = "password", oidc_provider_id: uuid.UUID | None = None,
                   ttl_seconds: int | None = None) -> tuple[str, AuthSession]:
    s = get_settings()
    token = new_token()
    now = utcnow()
    sess = AuthSession(
        user_id=user.id, token_hash=sha256_hex(token), csrf_token=new_token(24), active_org_id=org_id,
        expires_at=now + (timedelta(seconds=ttl_seconds) if ttl_seconds else timedelta(minutes=s.session_ttl_minutes)),
        last_seen_at=now, ip=ip, user_agent=(ua or "")[:400], mfa_passed=mfa_passed,
        auth_method=auth_method, oidc_provider_id=oidc_provider_id,
    )
    db.add(sess)
    db.flush()
    if response is not None:
        set_session_cookies(response, token, sess.csrf_token, max_age_seconds=ttl_seconds)
    return token, sess


def set_session_cookies(response: Response, token: str, csrf: str, *, max_age_seconds: int | None = None) -> None:
    s = get_settings()
    max_age = max_age_seconds or s.session_ttl_minutes * 60
    response.set_cookie(SESSION_COOKIE, token, max_age=max_age, httponly=True, secure=s.cookie_secure,
                        samesite="lax", domain=s.cookie_domain, path="/")
    # CSRF cookie is readable by JS so the SPA can echo it in X-CSRF-Token.
    response.set_cookie(CSRF_COOKIE, csrf, max_age=max_age, httponly=False, secure=s.cookie_secure,
                        samesite="lax", domain=s.cookie_domain, path="/")


def clear_cookies(response: Response) -> None:
    s = get_settings()
    for c in (SESSION_COOKIE, CSRF_COOKIE):
        response.delete_cookie(c, domain=s.cookie_domain, path="/")


def default_org_setup(db: Session, org: Organization) -> None:
    """Seed tenant defaults: SLA policies, safe policy rules, AI budget, trial subscription."""
    set_tenant(db, org.id)
    db.add(TicketCounter(org_id=org.id, next_number=1000))
    for prio, fr, res in (("urgent", 30, 240), ("high", 60, 480), ("normal", 240, 1440), ("low", 480, 4320)):
        db.add(SlaPolicy(org_id=org.id, name=f"Default {prio}", priority=prio, first_response_minutes=fr, resolution_minutes=res))
    db.add(PolicyRule(org_id=org.id, name="Destructive actions are denied by default", priority=10, effect="deny",
                      match={"risk_at_least": "DESTRUCTIVE"}))
    db.add(PolicyRule(org_id=org.id, name="Privileged actions need technician approval", priority=20, effect="require_approval",
                      match={"risk_at_least": "PRIVILEGED"}, approver_roles=["technician", "admin", "org_owner"]))
    db.add(PolicyRule(org_id=org.id, name="Sensitive actions need customer or technician approval", priority=30,
                      effect="require_approval", match={"risk_at_least": "SENSITIVE"},
                      approver_roles=["customer", "technician", "admin", "org_owner"]))
    db.add(PolicyRule(org_id=org.id, name="Read-only and low-risk actions allowed within consent", priority=100,
                      effect="allow", match={"risk_at_most": "LOW_RISK"}))
    db.add(AIBudget(org_id=org.id, routing={}))
    db.add(Subscription(org_id=org.id, plan="trial", status="trialing", trial_ends_at=utcnow() + timedelta(days=14)))
    org.settings = {
        "external_research_enabled": True,
        "auto_close_on_verified": False,
        "require_verification_to_resolve": True,
        "retention_days": {"session_events": 90, "screenshots": 30, "audit": 2555},
        "recording_default": False,
        **(org.settings or {}),
    }
    org.branding = org.branding or {"product_name": get_settings().product_name}


def register(db: Session, *, email: str, password: str, name: str, org_name: str) -> tuple[User, Organization]:
    email = email.strip().lower()
    validate_password(password)
    if db.execute(select(User).where(User.email == email)).scalar_one_or_none():
        raise Conflict("An account with this email already exists", code="email_taken")
    user = User(email=email, name=name.strip(), password_hash=hash_password(password))
    org = Organization(name=org_name.strip(), slug=slugify(org_name))
    db.add_all([user, org])
    db.flush()
    db.add(Membership(user_id=user.id, org_id=org.id, role=Role.ORG_OWNER))
    default_org_setup(db, org)
    db.flush()
    audit.record(db, org_id=org.id, actor_kind="user", actor_id=user.id, action="org.created", resource_type="organization", resource_id=org.id)
    issue_email_token(db, user, "verify_email")
    return user, org


def issue_email_token(db: Session, user: User, purpose: str) -> str:
    token = new_token()
    ttl = timedelta(hours=24) if purpose == "verify_email" else timedelta(minutes=30)
    db.add(OneTimeToken(user_id=user.id, purpose=purpose, token_hash=sha256_hex(token), expires_at=utcnow() + ttl))
    s = get_settings()
    if purpose == "verify_email":
        link = f"{s.web_origin}/verify-email?token={token}"
        send_email(db, org_id=None, to=user.email, subject="Verify your email", body=f"Confirm your email address:\n{link}\n\nThis link expires in 24 hours.")
    else:
        link = f"{s.web_origin}/reset-password?token={token}"
        send_email(db, org_id=None, to=user.email, subject="Reset your password", body=f"Reset your password:\n{link}\n\nThis link expires in 30 minutes. If you didn't request it, ignore this email.")
    db.flush()
    return token


def consume_token(db: Session, token: str, purpose: str) -> User:
    t = db.execute(select(OneTimeToken).where(OneTimeToken.token_hash == sha256_hex(token), OneTimeToken.purpose == purpose)).scalar_one_or_none()
    if not t or t.used_at or t.expires_at < utcnow():
        raise AppError("This link is invalid or has expired", code="invalid_token")
    t.used_at = utcnow()
    user = db.get(User, t.user_id)
    assert user
    return user


def authenticate(db: Session, email: str, password: str, totp: str | None, ip: str | None) -> User:
    email = email.strip().lower()
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    now = utcnow()
    if user and user.locked_until and user.locked_until > now:
        audit.security_event(db, "login_locked", user_id=user.id, ip=ip, severity="warn")
        raise Unauthorized("Account temporarily locked after repeated failures. Try again later.", code="locked")
    if not user or user.disabled or not verify_password(password, user.password_hash if user else None):
        if user:
            user.failed_logins += 1
            if user.failed_logins >= LOCKOUT_THRESHOLD:
                user.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
                user.failed_logins = 0
                audit.security_event(db, "lockout", user_id=user.id, ip=ip, severity="warn")
        audit.security_event(db, "login_failed", ip=ip, data={"email_hash": sha256_hex(email)[:16]})
        db.commit()
        raise Unauthorized("Invalid email or password", code="invalid_credentials")
    if user.mfa_enabled:
        if not totp:
            raise Unauthorized("Enter your authenticator code", code="mfa_required")
        if not verify_totp(user, totp):
            user.failed_logins += 1
            audit.security_event(db, "mfa_failed", user_id=user.id, ip=ip, severity="warn")
            db.commit()
            raise Unauthorized("Invalid authenticator code", code="mfa_invalid")
    user.failed_logins = 0
    user.locked_until = None
    return user


def verify_totp(user: User, code: str) -> bool:
    if not user.mfa_secret_enc:
        return False
    return pyotp.TOTP(decrypt_secret(user.mfa_secret_enc)).verify(code.strip(), valid_window=1)


def mfa_begin(user: User) -> tuple[str, str]:
    secret = pyotp.random_base32()
    user.mfa_secret_enc = encrypt_secret(secret)
    user.mfa_enabled = False
    uri = pyotp.TOTP(secret).provisioning_uri(name=user.email, issuer_name=get_settings().product_name)
    return secret, uri


def default_org_for(db: Session, user: User) -> uuid.UUID | None:
    m = db.execute(select(Membership).where(Membership.user_id == user.id).order_by(Membership.created_at)).scalars().first()
    return m.org_id if m else None
