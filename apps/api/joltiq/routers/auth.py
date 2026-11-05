from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.errors import AppError, Forbidden, NotFound
from ..db import get_raw_session, set_tenant
from ..deps import Ctx, get_principal_optional, get_user_ctx
from ..models import AuthSession, Invitation, Membership, Organization, User, utcnow
from ..security.crypto import hash_password, sha256_hex
from ..security.ratelimit import hit
from ..services import audit
from ..services import auth as svc

router = APIRouter(prefix="/auth", tags=["auth"])


def _ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=256)
    name: str = Field(min_length=1, max_length=200)
    organization_name: str = Field(min_length=2, max_length=200)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=256)
    totp: str | None = Field(default=None, max_length=10)


def me_payload(db: Session, user: User, sess: AuthSession) -> dict:
    rows = db.execute(
        select(Membership, Organization).join(Organization, Organization.id == Membership.org_id).where(Membership.user_id == user.id)
    ).all()
    orgs = [{"id": str(o.id), "name": o.name, "slug": o.slug, "role": m.role, "branding": o.branding or {}} for m, o in rows]
    active = next((o for o in orgs if o["id"] == str(sess.active_org_id)), None)
    if active is None and user.is_platform_owner and sess.active_org_id:
        org = db.get(Organization, sess.active_org_id)
        if org:
            active = {"id": str(org.id), "name": org.name, "slug": org.slug, "role": "platform_owner", "branding": org.branding or {}}
    return {
        "user": {"id": str(user.id), "email": user.email, "name": user.name, "email_verified": bool(user.email_verified_at),
                 "mfa_enabled": user.mfa_enabled, "is_platform_owner": user.is_platform_owner},
        "organizations": orgs,
        "active_organization": active,
        "csrf_token": sess.csrf_token,
        "session_expires_at": sess.expires_at.isoformat(),
    }


@router.post("/register", status_code=201)
def register(body: RegisterIn, request: Request, response: Response, db: Session = Depends(get_raw_session)):
    from ..config import get_settings as _gs

    hit(db, f"register:{_ip(request)}", _gs().rate_limit_register_per_hour, 3600)
    user, org = svc.register(db, email=body.email, password=body.password, name=body.name, org_name=body.organization_name)
    _, sess = svc.create_session(db, user, response, org_id=org.id, ip=_ip(request), ua=request.headers.get("user-agent"), mfa_passed=True)
    return me_payload(db, user, sess)


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_raw_session)):
    from ..config import get_settings

    hit(db, f"login-ip:{_ip(request)}", get_settings().rate_limit_login_per_minute * 3, 60)
    hit(db, f"login-email:{sha256_hex(body.email.lower())}", get_settings().rate_limit_login_per_minute, 60)
    user = svc.authenticate(db, body.email, body.password, body.totp, _ip(request))
    token, sess = svc.create_session(db, user, response, org_id=svc.default_org_for(db, user), ip=_ip(request),
                                     ua=request.headers.get("user-agent"), mfa_passed=True)
    if sess.active_org_id:
        set_tenant(db, sess.active_org_id)
        audit.record(db, org_id=sess.active_org_id, actor_kind="user", actor_id=user.id, action="auth.login", resource_type="user", resource_id=user.id)
    out = me_payload(db, user, sess)
    # API clients (and the Windows companion's enrollment flow) may use the bearer token instead of cookies.
    if request.headers.get("x-joltiq-client") == "api":
        out["access_token"] = token
    return out


@router.post("/logout")
def logout(response: Response, ctx: Ctx = Depends(get_user_ctx)):
    ctx.p.session.revoked_at = utcnow()
    svc.clear_cookies(response)
    return {"ok": True}


@router.post("/logout-all")
def logout_all(response: Response, ctx: Ctx = Depends(get_user_ctx)):
    for s in ctx.db.execute(select(AuthSession).where(AuthSession.user_id == ctx.p.user_id, AuthSession.revoked_at.is_(None))).scalars():
        s.revoked_at = utcnow()
    svc.clear_cookies(response)
    return {"ok": True}


@router.get("/me")
def me(ctx: Ctx = Depends(get_user_ctx)):
    return me_payload(ctx.db, ctx.p.user, ctx.p.session)


@router.get("/session")
def session_probe(p=Depends(get_principal_optional), db: Session = Depends(get_raw_session)):
    """Unauthenticated-safe probe used by the web app's middleware."""
    if not p:
        return {"authenticated": False}
    return {"authenticated": True, **me_payload(db, p.user, p.session)}


class SwitchIn(BaseModel):
    organization_id: uuid.UUID


@router.post("/switch-organization")
def switch_org(body: SwitchIn, ctx: Ctx = Depends(get_user_ctx)):
    m = ctx.db.execute(select(Membership).where(Membership.user_id == ctx.p.user_id, Membership.org_id == body.organization_id)).scalar_one_or_none()
    if not m and not ctx.p.is_platform:
        audit.security_event(ctx.db, "cross_tenant_denied", user_id=ctx.p.user_id, severity="warn", data={"attempted_org": str(body.organization_id)})
        raise Forbidden("You are not a member of that organization")
    if not ctx.db.get(Organization, body.organization_id):
        raise NotFound("Organization not found")
    ctx.p.session.active_org_id = body.organization_id
    return me_payload(ctx.db, ctx.p.user, ctx.p.session)


class TokenIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)


@router.post("/verify-email")
def verify_email(body: TokenIn, db: Session = Depends(get_raw_session)):
    user = svc.consume_token(db, body.token, "verify_email")
    user.email_verified_at = utcnow()
    return {"ok": True}


@router.post("/resend-verification")
def resend(ctx: Ctx = Depends(get_user_ctx)):
    hit(ctx.db, f"resend:{ctx.p.user_id}", 3, 3600)
    if ctx.p.user.email_verified_at:
        return {"ok": True, "already_verified": True}
    svc.issue_email_token(ctx.db, ctx.p.user, "verify_email")
    return {"ok": True}


class ForgotIn(BaseModel):
    email: EmailStr


@router.post("/password/forgot")
def forgot(body: ForgotIn, request: Request, db: Session = Depends(get_raw_session)):
    hit(db, f"forgot:{_ip(request)}", 5, 3600)
    user = db.execute(select(User).where(User.email == body.email.lower())).scalar_one_or_none()
    if user and not user.disabled:
        svc.issue_email_token(db, user, "reset_password")
    # Same response either way (no account enumeration).
    return {"ok": True}


class ResetIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    password: str = Field(min_length=12, max_length=256)


@router.post("/password/reset")
def reset(body: ResetIn, db: Session = Depends(get_raw_session)):
    svc.validate_password(body.password)
    user = svc.consume_token(db, body.token, "reset_password")
    user.password_hash = hash_password(body.password)
    user.failed_logins, user.locked_until = 0, None
    for s in db.execute(select(AuthSession).where(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None))).scalars():
        s.revoked_at = utcnow()  # all sessions invalidated on password change
    audit.security_event(db, "password_reset", user_id=user.id)
    return {"ok": True}


class ChangePwIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=12, max_length=256)


@router.post("/password/change")
def change_password(body: ChangePwIn, ctx: Ctx = Depends(get_user_ctx)):
    from ..security.crypto import verify_password

    if not verify_password(body.current_password, ctx.p.user.password_hash):
        raise AppError("Current password is incorrect", code="invalid_credentials")
    svc.validate_password(body.new_password)
    ctx.p.user.password_hash = hash_password(body.new_password)
    for s in ctx.db.execute(select(AuthSession).where(AuthSession.user_id == ctx.p.user_id, AuthSession.id != ctx.p.session.id)).scalars():
        s.revoked_at = s.revoked_at or utcnow()
    return {"ok": True}


@router.post("/mfa/setup")
def mfa_setup(ctx: Ctx = Depends(get_user_ctx)):
    secret, uri = svc.mfa_begin(ctx.p.user)
    return {"secret": secret, "otpauth_uri": uri}


class CodeIn(BaseModel):
    code: str = Field(min_length=6, max_length=10)


@router.post("/mfa/enable")
def mfa_enable(body: CodeIn, ctx: Ctx = Depends(get_user_ctx)):
    if not svc.verify_totp(ctx.p.user, body.code):
        raise AppError("Code did not match; check your authenticator's clock", code="mfa_invalid")
    ctx.p.user.mfa_enabled = True
    ctx.p.session.mfa_passed = True
    audit.security_event(ctx.db, "mfa_enabled", user_id=ctx.p.user_id)
    return {"ok": True}


@router.post("/mfa/disable")
def mfa_disable(body: CodeIn, ctx: Ctx = Depends(get_user_ctx)):
    if not svc.verify_totp(ctx.p.user, body.code):
        raise AppError("Invalid code", code="mfa_invalid")
    ctx.p.user.mfa_enabled = False
    ctx.p.user.mfa_secret_enc = None
    audit.security_event(ctx.db, "mfa_disabled", user_id=ctx.p.user_id, severity="warn")
    return {"ok": True}


class AcceptInviteIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    name: str = Field(default="", max_length=200)
    password: str | None = Field(default=None, max_length=256)


@router.get("/invitations/{token}")
def invitation_info(token: str, db: Session = Depends(get_raw_session)):
    set_tenant(db, None, platform=True)  # lookup by unguessable token hash only
    inv = db.execute(select(Invitation).where(Invitation.token_hash == sha256_hex(token))).scalar_one_or_none()
    if not inv or inv.accepted_at or inv.expires_at < utcnow():
        raise NotFound("Invitation is invalid or expired")
    org = db.get(Organization, inv.org_id)
    exists = db.execute(select(User.id).where(User.email == inv.email)).scalar() is not None
    return {"email": inv.email, "role": inv.role, "organization": org.name if org else "", "account_exists": exists}


@router.post("/invitations/accept")
def accept_invite(body: AcceptInviteIn, request: Request, response: Response, db: Session = Depends(get_raw_session)):
    set_tenant(db, None, platform=True)
    inv = db.execute(select(Invitation).where(Invitation.token_hash == sha256_hex(body.token))).scalar_one_or_none()
    if not inv or inv.accepted_at or inv.expires_at < utcnow():
        raise NotFound("Invitation is invalid or expired")
    user = db.execute(select(User).where(User.email == inv.email)).scalar_one_or_none()
    if user is None:
        if not body.password:
            raise AppError("Choose a password to create your account", code="password_required")
        svc.validate_password(body.password)
        user = User(email=inv.email, name=body.name or inv.email.split("@")[0], password_hash=hash_password(body.password),
                    email_verified_at=utcnow())  # invitation link proves mailbox control
        db.add(user)
        db.flush()
    else:
        # Existing account must prove identity with its password.
        from ..security.crypto import verify_password

        if not body.password or not verify_password(body.password, user.password_hash):
            raise AppError("Enter your existing account password to accept", code="invalid_credentials")
    if not db.execute(select(Membership).where(Membership.user_id == user.id, Membership.org_id == inv.org_id)).scalar_one_or_none():
        db.add(Membership(user_id=user.id, org_id=inv.org_id, role=inv.role, customer_id=inv.customer_id))
    inv.accepted_at = utcnow()
    set_tenant(db, inv.org_id)
    audit.record(db, org_id=inv.org_id, actor_kind="user", actor_id=user.id, action="invitation.accepted", resource_type="user", resource_id=user.id, data={"role": inv.role})
    _, sess = svc.create_session(db, user, response, org_id=inv.org_id, ip=_ip(request), ua=request.headers.get("user-agent"), mfa_passed=not user.mfa_enabled)
    return me_payload(db, user, sess)


_ = timedelta
