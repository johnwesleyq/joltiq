"""Request authentication, tenant binding and permission dependencies."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .core.errors import Forbidden, Unauthorized
from .db import get_raw_session, set_tenant
from .models import AuthSession, Membership, User, utcnow
from .models.enums import Role
from .security.crypto import sha256_hex
from .security.rbac import has_permission

SESSION_COOKIE = "jq_session"
CSRF_COOKIE = "jq_csrf"
UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


@dataclass
class Principal:
    user: User
    session: AuthSession
    org_id: uuid.UUID | None
    role: str | None
    customer_id: uuid.UUID | None
    via_cookie: bool

    @property
    def user_id(self) -> uuid.UUID:
        return self.user.id

    @property
    def is_platform(self) -> bool:
        return bool(self.user.is_platform_owner)

    def can(self, perm: str) -> bool:
        if self.is_platform:
            return True
        return self.role is not None and has_permission(self.role, perm)

    def require(self, perm: str) -> None:
        if not self.can(perm):
            raise Forbidden(f"Missing permission: {perm}")

    @property
    def is_customer(self) -> bool:
        return self.role == Role.CUSTOMER and not self.is_platform

    @property
    def is_staff(self) -> bool:
        return self.is_platform or self.role in (Role.ORG_OWNER, Role.ADMIN, Role.TECHNICIAN)


def _extract_token(request: Request) -> tuple[str | None, bool]:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip(), False
    return request.cookies.get(SESSION_COOKIE), True


def resolve_principal(request: Request, db: Session) -> Principal | None:
    token, via_cookie = _extract_token(request)
    if not token:
        return None
    s = get_settings()
    sess = db.execute(select(AuthSession).where(AuthSession.token_hash == sha256_hex(token))).scalar_one_or_none()
    now = utcnow()
    if not sess or sess.revoked_at or sess.expires_at <= now or sess.last_seen_at < now - timedelta(minutes=s.session_idle_minutes):
        return None
    user = db.get(User, sess.user_id)
    if not user or user.disabled:
        return None
    if user.mfa_enabled and not sess.mfa_passed:
        return None
    if via_cookie and request.method in UNSAFE:
        # Double-submit CSRF: header must equal the per-session token.
        if request.headers.get("x-csrf-token") != sess.csrf_token:
            raise Forbidden("CSRF token missing or invalid", code="csrf_failed")
    # Touch at most once a minute to limit write load.
    if (now - sess.last_seen_at).total_seconds() > 60:
        sess.last_seen_at = now
    role = None
    customer_id = None
    org_id = sess.active_org_id
    if org_id:
        m = db.execute(
            select(Membership).where(Membership.user_id == user.id, Membership.org_id == org_id)
        ).scalar_one_or_none()
        if m:
            role, customer_id = m.role, m.customer_id
        elif user.is_platform_owner:
            role = Role.PLATFORM_OWNER
        else:
            org_id = None
    return Principal(user, sess, org_id, role, customer_id, via_cookie)


@dataclass
class Ctx:
    db: Session
    p: Principal
    request: Request

    @property
    def org_id(self) -> uuid.UUID:
        assert self.p.org_id is not None
        return self.p.org_id


def get_principal_optional(request: Request, db: Session = Depends(get_raw_session)) -> Principal | None:
    p = resolve_principal(request, db)
    request.state.principal = p
    return p


def get_user_ctx(request: Request, db: Session = Depends(get_raw_session)) -> Ctx:
    """Authenticated, no organization required (profile, org list, org switch)."""
    p = resolve_principal(request, db)
    if not p:
        raise Unauthorized("Authentication required")
    request.state.principal = p
    return Ctx(db, p, request)


def get_ctx(request: Request, db: Session = Depends(get_raw_session)) -> Ctx:
    """Authenticated + bound to an active organization. All tenant data access goes through this."""
    p = resolve_principal(request, db)
    if not p:
        raise Unauthorized("Authentication required")
    if not p.org_id:
        raise Forbidden("Select an organization first", code="no_active_org")
    set_tenant(db, p.org_id)  # RLS context for every query in this request
    from .models import Organization

    org = db.get(Organization, p.org_id)
    if org is not None and org.status == "suspended" and not p.is_platform:
        raise Forbidden("This organization is suspended. Contact support.", code="org_suspended")
    request.state.principal = p
    return Ctx(db, p, request)


def get_platform_ctx(request: Request, db: Session = Depends(get_raw_session)) -> Ctx:
    p = resolve_principal(request, db)
    if not p:
        raise Unauthorized("Authentication required")
    if not p.is_platform:
        raise Forbidden("Platform owner only")
    set_tenant(db, None, platform=True)
    return Ctx(db, p, request)


def perm(name: str):
    def _dep(ctx: Ctx = Depends(get_ctx)) -> Ctx:
        ctx.p.require(name)
        return ctx

    return _dep
