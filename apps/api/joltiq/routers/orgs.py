"""Organizations, users/memberships, invitations, customers & contacts."""
from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select

from ..config import get_settings
from ..core.errors import AppError, Conflict, Forbidden, NotFound
from ..deps import Ctx, get_ctx, perm
from ..models import Contact, Customer, Invitation, Membership, Organization, User, utcnow
from ..models.enums import Role
from ..security.crypto import new_token, sha256_hex
from ..security.rbac import can_grant_role
from ..services import audit
from ..services.email import send_email
from .common import page_params, paginate

router = APIRouter(tags=["organizations"])


@router.get("/organizations/current")
def current_org(ctx: Ctx = Depends(get_ctx)):
    org = ctx.db.get(Organization, ctx.org_id)
    assert org
    return {"id": str(org.id), "name": org.name, "slug": org.slug, "plan": org.plan, "settings": org.settings,
            "branding": org.branding, "custom_domain": org.custom_domain}


class OrgUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    settings: dict | None = None


ALLOWED_SETTINGS = {"external_research_enabled", "auto_close_on_verified", "require_verification_to_resolve",
                    "retention_days", "recording_default", "research_allowed_domains", "approval_ttl_minutes",
                    "max_agent_steps", "default_session_minutes", "email_intake_address"}


@router.patch("/organizations/current")
def update_org(body: OrgUpdate, ctx: Ctx = Depends(perm("settings.manage"))):
    org = ctx.db.get(Organization, ctx.org_id)
    assert org
    if body.name:
        org.name = body.name
    if body.settings is not None:
        unknown = set(body.settings) - ALLOWED_SETTINGS
        if unknown:
            raise AppError(f"Unknown settings: {sorted(unknown)}", code="validation_error")
        org.settings = {**(org.settings or {}), **body.settings}
    audit.record(ctx.db, org_id=org.id, actor_kind="user", actor_id=ctx.p.user_id, action="org.updated", resource_type="organization", resource_id=org.id, data=body.model_dump(exclude_none=True))
    return current_org(ctx)


@router.post("/organizations", status_code=201)
def create_additional_org(body: OrgUpdate, ctx: Ctx = Depends(get_ctx)):
    """A user may create another organization (e.g. MSP with multiple practices)."""
    from ..services.auth import default_org_setup, slugify

    if not body.name:
        raise AppError("name required", code="validation_error")
    org = Organization(name=body.name, slug=slugify(body.name))
    ctx.db.add(org)
    ctx.db.flush()
    ctx.db.add(Membership(user_id=ctx.p.user_id, org_id=org.id, role=Role.ORG_OWNER))
    default_org_setup(ctx.db, org)
    audit.record(ctx.db, org_id=org.id, actor_kind="user", actor_id=ctx.p.user_id, action="org.created", resource_type="organization", resource_id=org.id)
    return {"id": str(org.id), "name": org.name}


# ---------------- Users ----------------
@router.get("/users")
def list_users(ctx: Ctx = Depends(perm("users.manage")), pp=Depends(page_params)):
    q = (select(Membership, User).join(User, User.id == Membership.user_id).where(Membership.org_id == ctx.org_id)
         .order_by(User.email))
    return paginate(ctx.db, q, pp, lambda r: {
        "user_id": str(r[1].id), "email": r[1].email, "name": r[1].name, "role": r[0].role,
        "customer_id": str(r[0].customer_id) if r[0].customer_id else None, "disabled": r[1].disabled,
        "mfa_enabled": r[1].mfa_enabled, "membership_id": str(r[0].id),
    })


@router.get("/users/technicians")
def list_technicians(ctx: Ctx = Depends(perm("tickets.assign"))):
    rows = ctx.db.execute(select(User).join(Membership, Membership.user_id == User.id).where(
        Membership.org_id == ctx.org_id, Membership.role.in_([Role.TECHNICIAN, Role.ADMIN, Role.ORG_OWNER]))).scalars()
    return [{"id": str(u.id), "name": u.name or u.email, "email": u.email} for u in rows]


class RoleChange(BaseModel):
    role: Role


@router.patch("/users/{user_id}/role")
def change_role(user_id: uuid.UUID, body: RoleChange, ctx: Ctx = Depends(perm("users.manage"))):
    m = ctx.db.execute(select(Membership).where(Membership.org_id == ctx.org_id, Membership.user_id == user_id)).scalar_one_or_none()
    if not m:
        raise NotFound("User is not a member of this organization")
    if not can_grant_role(ctx.p.role or "", body.role) or (m.role == Role.ORG_OWNER and ctx.p.role != Role.ORG_OWNER):
        raise Forbidden("You cannot grant or change that role")
    if m.role == Role.ORG_OWNER and body.role != Role.ORG_OWNER:
        owners = ctx.db.execute(select(func.count()).select_from(Membership).where(Membership.org_id == ctx.org_id, Membership.role == Role.ORG_OWNER)).scalar() or 0
        if owners <= 1:
            raise Conflict("An organization must keep at least one owner")
    old = m.role
    m.role = body.role
    audit.record(ctx.db, org_id=ctx.org_id, actor_kind="user", actor_id=ctx.p.user_id, action="user.role_changed", resource_type="user", resource_id=user_id, data={"from": old, "to": body.role})
    return {"ok": True}


@router.delete("/users/{user_id}")
def remove_user(user_id: uuid.UUID, ctx: Ctx = Depends(perm("users.manage"))):
    m = ctx.db.execute(select(Membership).where(Membership.org_id == ctx.org_id, Membership.user_id == user_id)).scalar_one_or_none()
    if not m:
        raise NotFound("User is not a member")
    if m.role == Role.ORG_OWNER:
        raise Conflict("Transfer ownership before removing an owner")
    ctx.db.delete(m)
    audit.record(ctx.db, org_id=ctx.org_id, actor_kind="user", actor_id=ctx.p.user_id, action="user.removed", resource_type="user", resource_id=user_id)
    return {"ok": True}


class InviteIn(BaseModel):
    email: EmailStr
    role: Role
    customer_id: uuid.UUID | None = None


@router.post("/users/invitations", status_code=201)
def invite(body: InviteIn, ctx: Ctx = Depends(get_ctx)):
    # Technicians may invite customers; admins may invite staff.
    if body.role == Role.CUSTOMER:
        ctx.p.require("customers.manage")
        if not body.customer_id or not ctx.db.get(Customer, body.customer_id):
            raise AppError("Customer users must be linked to a customer account", code="validation_error")
    else:
        ctx.p.require("users.manage")
        if not can_grant_role(ctx.p.role or "", body.role):
            raise Forbidden("You cannot invite that role")
    from ..services.billing import check_entitlement

    if body.role in (Role.TECHNICIAN, Role.ADMIN):
        check_entitlement(ctx.db, ctx.org_id, "technicians", adding=1)
    token = new_token()
    inv = Invitation(org_id=ctx.org_id, email=body.email.lower(), role=body.role, token_hash=sha256_hex(token),
                     invited_by=ctx.p.user_id, customer_id=body.customer_id, expires_at=utcnow() + timedelta(days=7))
    ctx.db.add(inv)
    ctx.db.flush()
    org = ctx.db.get(Organization, ctx.org_id)
    link = f"{get_settings().web_origin}/accept-invite?token={token}"
    send_email(ctx.db, org_id=ctx.org_id, to=inv.email, subject=f"You're invited to {org.name if org else ''}",
               body=f"{ctx.p.user.name or ctx.p.user.email} invited you to join {org.name if org else ''} as {body.role}.\n\nAccept: {link}\n\nThis invitation expires in 7 days.")
    audit.record(ctx.db, org_id=ctx.org_id, actor_kind="user", actor_id=ctx.p.user_id, action="invitation.created", resource_type="invitation", resource_id=inv.id, data={"email": inv.email, "role": body.role})
    out = {"id": str(inv.id), "email": inv.email, "role": inv.role, "expires_at": inv.expires_at.isoformat()}
    if get_settings().env in ("development", "test"):
        out["dev_accept_link"] = link  # dev convenience only; never returned in production
    return out


@router.get("/users/invitations")
def list_invites(ctx: Ctx = Depends(perm("users.manage"))):
    rows = ctx.db.execute(select(Invitation).where(Invitation.accepted_at.is_(None)).order_by(Invitation.created_at.desc())).scalars()
    return [{"id": str(i.id), "email": i.email, "role": i.role, "expires_at": i.expires_at.isoformat(),
             "expired": i.expires_at < utcnow()} for i in rows]


@router.delete("/users/invitations/{inv_id}")
def revoke_invite(inv_id: uuid.UUID, ctx: Ctx = Depends(perm("users.manage"))):
    inv = ctx.db.get(Invitation, inv_id)
    if not inv:
        raise NotFound("Invitation not found")
    ctx.db.delete(inv)
    return {"ok": True}


# ---------------- Customers ----------------
class CustomerIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    domain: str | None = Field(default=None, max_length=253)
    notes: str = ""


def _cust(c: Customer) -> dict:
    return {"id": str(c.id), "name": c.name, "domain": c.domain, "notes": c.notes, "created_at": c.created_at.isoformat()}


@router.get("/customers")
def list_customers(ctx: Ctx = Depends(perm("customers.manage")), pp=Depends(page_params), q: str | None = None):
    stmt = select(Customer).order_by(Customer.name)
    if q:
        stmt = stmt.where(Customer.name.ilike(f"%{q}%"))
    return paginate(ctx.db, stmt, pp, _cust)


@router.post("/customers", status_code=201)
def create_customer(body: CustomerIn, ctx: Ctx = Depends(perm("customers.manage"))):
    c = Customer(org_id=ctx.org_id, **body.model_dump())
    ctx.db.add(c)
    ctx.db.flush()
    audit.record(ctx.db, org_id=ctx.org_id, actor_kind="user", actor_id=ctx.p.user_id, action="customer.created", resource_type="customer", resource_id=c.id)
    return _cust(c)


@router.get("/customers/{cid}")
def get_customer(cid: uuid.UUID, ctx: Ctx = Depends(perm("customers.manage"))):
    c = ctx.db.get(Customer, cid)
    if not c:
        raise NotFound("Customer not found")
    contacts = ctx.db.execute(select(Contact).where(Contact.customer_id == cid)).scalars()
    return {**_cust(c), "contacts": [{"id": str(x.id), "name": x.name, "email": x.email, "phone": x.phone} for x in contacts]}


@router.patch("/customers/{cid}")
def update_customer(cid: uuid.UUID, body: CustomerIn, ctx: Ctx = Depends(perm("customers.manage"))):
    c = ctx.db.get(Customer, cid)
    if not c:
        raise NotFound("Customer not found")
    for k, v in body.model_dump().items():
        setattr(c, k, v)
    return _cust(c)


class ContactIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    phone: str | None = Field(default=None, max_length=40)


@router.post("/customers/{cid}/contacts", status_code=201)
def add_contact(cid: uuid.UUID, body: ContactIn, ctx: Ctx = Depends(perm("customers.manage"))):
    if not ctx.db.get(Customer, cid):
        raise NotFound("Customer not found")
    x = Contact(org_id=ctx.org_id, customer_id=cid, **body.model_dump())
    ctx.db.add(x)
    ctx.db.flush()
    return {"id": str(x.id), "name": x.name, "email": x.email, "phone": x.phone}
