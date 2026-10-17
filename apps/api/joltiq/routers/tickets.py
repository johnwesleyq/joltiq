from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Header, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from ..core.errors import AppError, Forbidden, NotFound
from ..deps import Ctx, get_ctx, perm
from ..models import Customer, Device, Ticket, TicketAttachment, TicketComment, TicketEvent, User
from ..models.enums import TicketStatus
from ..services import storage
from ..services import tickets as svc
from ..services.billing import check_entitlement, meter
from ..services.idempotency import idempotent
from .common import page_params, paginate

router = APIRouter(prefix="/tickets", tags=["tickets"])


def ticket_for(ctx: Ctx, ticket_id: uuid.UUID) -> Ticket:
    t = svc.get(ctx.db, ticket_id)
    if ctx.p.can("tickets.read_all"):
        return t
    if ctx.p.can("tickets.read_own") and (t.requester_id == ctx.p.user_id or (ctx.p.customer_id and t.customer_id == ctx.p.customer_id)):
        return t
    raise NotFound("Ticket not found")  # don't reveal existence


def ser(t: Ticket, users: dict | None = None) -> dict:
    users = users or {}
    return {
        "id": str(t.id), "number": t.number, "subject": t.subject, "description": t.description, "status": t.status,
        "priority": t.priority, "category": t.category, "source": t.source,
        "customer_id": str(t.customer_id) if t.customer_id else None,
        "requester": users.get(t.requester_id), "assignee": users.get(t.assignee_id),
        "requester_id": str(t.requester_id) if t.requester_id else None, "assignee_id": str(t.assignee_id) if t.assignee_id else None,
        "device_id": str(t.device_id) if t.device_id else None, "tags": t.tags or [],
        "first_response_due": t.first_response_due.isoformat() if t.first_response_due else None,
        "resolution_due": t.resolution_due.isoformat() if t.resolution_due else None,
        "first_responded_at": t.first_responded_at.isoformat() if t.first_responded_at else None,
        "resolved_at": t.resolved_at.isoformat() if t.resolved_at else None,
        "closed_at": t.closed_at.isoformat() if t.closed_at else None,
        "resolution_verified": t.resolution_verified, "ai_summary": t.ai_summary, "external_ref": t.external_ref,
        "created_at": t.created_at.isoformat(), "updated_at": t.updated_at.isoformat(),
    }


def _users(ctx: Ctx, ids) -> dict:
    ids = [i for i in ids if i]
    if not ids:
        return {}
    return {u.id: {"id": str(u.id), "name": u.name or u.email, "email": u.email}
            for u in ctx.db.execute(select(User).where(User.id.in_(ids))).scalars()}


@router.get("")
def list_tickets(ctx: Ctx = Depends(get_ctx), pp=Depends(page_params), status: list[str] | None = Query(None),
                 priority: str | None = None, assignee: str | None = None, customer_id: uuid.UUID | None = None,
                 q: str | None = Query(None, max_length=200), sort: str = "-updated_at"):
    stmt = select(Ticket)
    if not ctx.p.can("tickets.read_all"):
        ctx.p.require("tickets.read_own")
        conds = [Ticket.requester_id == ctx.p.user_id]
        if ctx.p.customer_id:
            conds.append(Ticket.customer_id == ctx.p.customer_id)
        stmt = stmt.where(or_(*conds))
    if status:
        stmt = stmt.where(Ticket.status.in_(status))
    if priority:
        stmt = stmt.where(Ticket.priority == priority)
    if assignee == "me":
        stmt = stmt.where(Ticket.assignee_id == ctx.p.user_id)
    elif assignee == "unassigned":
        stmt = stmt.where(Ticket.assignee_id.is_(None))
    elif assignee:
        stmt = stmt.where(Ticket.assignee_id == uuid.UUID(assignee))
    if customer_id:
        stmt = stmt.where(Ticket.customer_id == customer_id)
    if q:
        if q.lstrip("#").isdigit():
            stmt = stmt.where(Ticket.number == int(q.lstrip("#")))
        else:
            stmt = stmt.where(Ticket.search_tsv.op("@@")(func.websearch_to_tsquery("english", q)))
    col = {"updated_at": Ticket.updated_at, "created_at": Ticket.created_at, "priority": Ticket.priority,
           "number": Ticket.number, "resolution_due": Ticket.resolution_due}.get(sort.lstrip("-"), Ticket.updated_at)
    stmt = stmt.order_by(col.desc().nullslast() if sort.startswith("-") else col.asc().nullslast())
    page = paginate(ctx.db, stmt, pp, lambda t: t)
    users = _users(ctx, {u for t in page["items"] for u in (t.requester_id, t.assignee_id)})
    page["items"] = [ser(t, users) for t in page["items"]]
    return page


class TicketIn(BaseModel):
    subject: str = Field(min_length=3, max_length=300)
    description: str = Field(default="", max_length=50_000)
    priority: str = Field(default="normal", pattern="^(low|normal|high|urgent)$")
    category: str | None = Field(default=None, max_length=80)
    customer_id: uuid.UUID | None = None
    device_id: uuid.UUID | None = None
    requester_id: uuid.UUID | None = None


@router.post("", status_code=201)
def create_ticket(body: TicketIn, ctx: Ctx = Depends(perm("tickets.create")),
                  idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    def _do():
        customer_id, requester = body.customer_id, ctx.p.user_id
        if ctx.p.is_customer:
            customer_id = ctx.p.customer_id  # customers can't file on behalf of other accounts
        else:
            requester = body.requester_id or ctx.p.user_id
        if customer_id and not ctx.db.get(Customer, customer_id):
            raise AppError("Unknown customer", code="validation_error")
        if body.device_id and not ctx.db.get(Device, body.device_id):
            raise AppError("Unknown device", code="validation_error")
        t = svc.create(ctx.db, ctx.org_id, subject=body.subject, description=body.description, priority=body.priority,
                       category=body.category, source="api" if not ctx.p.via_cookie else "portal", customer_id=customer_id,
                       requester_id=requester, device_id=body.device_id)
        return ser(t, _users(ctx, [t.requester_id]))

    return idempotent(ctx, idempotency_key, "tickets.create", _do)


@router.get("/{ticket_id}")
def get_ticket(ticket_id: uuid.UUID, ctx: Ctx = Depends(get_ctx)):
    t = ticket_for(ctx, ticket_id)
    staff = ctx.p.can("tickets.read_all")
    cq = select(TicketComment).where(TicketComment.ticket_id == t.id).order_by(TicketComment.created_at)
    if not staff:
        cq = cq.where(TicketComment.internal.is_(False))
    comments = ctx.db.execute(cq).scalars().all()
    eq = select(TicketEvent).where(TicketEvent.ticket_id == t.id).order_by(TicketEvent.created_at)
    events = ctx.db.execute(eq).scalars().all()
    if not staff:
        events = [e for e in events if e.kind in svc.CUSTOMER_VISIBLE_EVENTS]
    atts = ctx.db.execute(select(TicketAttachment).where(TicketAttachment.ticket_id == t.id)).scalars().all()
    users = _users(ctx, {t.requester_id, t.assignee_id, *[c.author_id for c in comments]})
    return {
        **ser(t, users),
        "comments": [{"id": str(c.id), "body": c.body, "internal": c.internal, "author_kind": c.author_kind,
                      "author": users.get(c.author_id), "created_at": c.created_at.isoformat()} for c in comments],
        "timeline": [{"id": str(e.id), "kind": e.kind, "actor_kind": e.actor_kind, "data": e.data, "at": e.created_at.isoformat()} for e in events],
        "attachments": [{"id": str(a.id), "filename": a.filename, "content_type": a.content_type, "size_bytes": a.size_bytes,
                         "created_at": a.created_at.isoformat()} for a in atts],
        "allowed_transitions": sorted(svc.TRANSITIONS.get(TicketStatus(t.status), set())) if staff else
                               (["REOPENED"] if t.status in ("RESOLVED", "CLOSED") else (["CANCELLED"] if t.status not in ("CANCELLED",) else [])),
    }


class TicketPatch(BaseModel):
    subject: str | None = Field(default=None, max_length=300)
    priority: str | None = Field(default=None, pattern="^(low|normal|high|urgent)$")
    category: str | None = Field(default=None, max_length=80)
    tags: list[str] | None = None
    device_id: uuid.UUID | None = None


@router.patch("/{ticket_id}")
def update_ticket(ticket_id: uuid.UUID, body: TicketPatch, ctx: Ctx = Depends(perm("tickets.update"))):
    t = ticket_for(ctx, ticket_id)
    changes = body.model_dump(exclude_none=True)
    for k, v in changes.items():
        setattr(t, k, v)
    if "priority" in changes:
        svc._apply_sla(ctx.db, t)
    svc.add_event(ctx.db, t, "updated", actor_kind="user", actor_id=ctx.p.user_id, data={k: str(v) for k, v in changes.items()})
    return ser(t, _users(ctx, [t.requester_id, t.assignee_id]))


class AssignIn(BaseModel):
    assignee_id: uuid.UUID | None


@router.post("/{ticket_id}/assign")
def assign(ticket_id: uuid.UUID, body: AssignIn, ctx: Ctx = Depends(perm("tickets.assign"))):
    t = ticket_for(ctx, ticket_id)
    if body.assignee_id:
        from ..models import Membership

        m = ctx.db.execute(select(Membership).where(Membership.org_id == ctx.org_id, Membership.user_id == body.assignee_id)).scalar_one_or_none()
        if not m or m.role not in ("technician", "admin", "org_owner"):
            raise AppError("Assignee must be a technician in this organization", code="validation_error")
    svc.assign(ctx.db, t, body.assignee_id, actor_id=ctx.p.user_id)
    return {"ok": True}


class StatusIn(BaseModel):
    status: TicketStatus
    reason: str = Field(default="", max_length=2000)
    resolution_note: str | None = Field(default=None, max_length=5000)


@router.post("/{ticket_id}/status")
def set_status(ticket_id: uuid.UUID, body: StatusIn, ctx: Ctx = Depends(get_ctx)):
    t = ticket_for(ctx, ticket_id)
    if not ctx.p.can("tickets.update"):
        # Customers may only reopen their resolved/closed ticket or cancel their own.
        if body.status not in (TicketStatus.REOPENED, TicketStatus.CANCELLED):
            raise Forbidden("Customers can only reopen or cancel tickets")
    svc.transition(ctx.db, t, body.status, actor_kind="user", actor_id=ctx.p.user_id, reason=body.reason,
                   force_close_reason=body.resolution_note)
    return ser(t)


class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=50_000)
    internal: bool = False


@router.post("/{ticket_id}/comments", status_code=201)
def add_comment(ticket_id: uuid.UUID, body: CommentIn, ctx: Ctx = Depends(perm("tickets.comment"))):
    t = ticket_for(ctx, ticket_id)
    if body.internal:
        ctx.p.require("tickets.internal_note")
    c = svc.comment(ctx.db, t, body.body, author_id=ctx.p.user_id, internal=body.internal)
    return {"id": str(c.id), "body": c.body, "internal": c.internal, "created_at": c.created_at.isoformat()}


@router.post("/{ticket_id}/attachments", status_code=201)
async def upload_attachment(ticket_id: uuid.UUID, file: UploadFile = File(...), ctx: Ctx = Depends(perm("tickets.comment"))):
    t = ticket_for(ctx, ticket_id)
    data = await file.read()
    try:
        ctype = storage.sniff(file.filename or "file", data)
    except storage.UploadRejected as e:
        raise AppError(str(e), code="upload_rejected") from e
    check_entitlement(ctx.db, ctx.org_id, "storage_bytes", adding=len(data))
    key, sha = storage.put(ctx.org_id, "attachments", file.filename or "file", data)
    a = TicketAttachment(org_id=ctx.org_id, ticket_id=t.id, filename=(file.filename or "file")[:255], content_type=ctype,
                         size_bytes=len(data), storage_key=key, sha256=sha, uploaded_by=ctx.p.user_id)
    ctx.db.add(a)
    ctx.db.flush()
    meter(ctx.db, ctx.org_id, "storage_bytes", len(data), f"attachment:{a.id}")
    svc.add_event(ctx.db, t, "attachment", actor_kind="user", actor_id=ctx.p.user_id, data={"filename": a.filename})
    return {"id": str(a.id), "filename": a.filename, "size_bytes": a.size_bytes, "content_type": ctype}


@router.get("/{ticket_id}/attachments/{att_id}")
def download_attachment(ticket_id: uuid.UUID, att_id: uuid.UUID, ctx: Ctx = Depends(get_ctx)):
    t = ticket_for(ctx, ticket_id)
    a = ctx.db.get(TicketAttachment, att_id)
    if not a or a.ticket_id != t.id:
        raise NotFound("Attachment not found")
    data = storage.get(ctx.org_id, a.storage_key)
    safe = a.filename.replace('"', "")
    return Response(data, media_type="application/octet-stream",
                    headers={"content-disposition": f'attachment; filename="{safe}"', "x-content-type-options": "nosniff"})


@router.get("/{ticket_id}/similar")
def similar(ticket_id: uuid.UUID, ctx: Ctx = Depends(perm("tickets.read_all"))):
    t = ticket_for(ctx, ticket_id)
    return {"items": svc.find_similar(ctx.db, t)}
