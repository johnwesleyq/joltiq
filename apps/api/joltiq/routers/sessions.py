"""Live support sessions: consent, viewer frames, control lease, human input relay (incl. protected secret entry),
pause/resume/handback, emergency stop."""
from __future__ import annotations

import base64
import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..core.errors import AppError, Conflict, Forbidden, NotFound
from ..deps import Ctx, get_ctx, perm
from ..models import AgentRun, SessionEvent, SupportSession, Ticket
from ..models.enums import RiskLevel
from ..services import agent as agent_svc
from ..services import browser_client
from ..services import sessions as svc
from .common import page_params, paginate
from .tickets import ticket_for

router = APIRouter(prefix="/sessions", tags=["sessions"])


def session_for(ctx: Ctx, sid: uuid.UUID) -> SupportSession:
    s = svc.get(ctx.db, sid)
    if ctx.p.can("sessions.read_all"):
        return s
    if s.owner_id == ctx.p.user_id:
        return s
    if s.ticket_id:
        ticket_for(ctx, s.ticket_id)
        return s
    raise NotFound("Session not found")


def _role_kind(ctx: Ctx, s: SupportSession) -> str:
    return "customer" if (ctx.p.is_customer or s.owner_id == ctx.p.user_id) and not ctx.p.is_staff else "technician"


@router.get("")
def list_sessions(ctx: Ctx = Depends(get_ctx), pp=Depends(page_params), state: str | None = None, ticket_id: uuid.UUID | None = None):
    stmt = select(SupportSession).order_by(SupportSession.created_at.desc())
    if not ctx.p.can("sessions.read_all"):
        mine = select(Ticket.id).where((Ticket.requester_id == ctx.p.user_id) | (Ticket.customer_id == ctx.p.customer_id))
        stmt = stmt.where((SupportSession.owner_id == ctx.p.user_id) | SupportSession.ticket_id.in_(mine))
    if state:
        stmt = stmt.where(SupportSession.state == state)
    if ticket_id:
        stmt = stmt.where(SupportSession.ticket_id == ticket_id)
    return paginate(ctx.db, stmt, pp, lambda s: svc.ser(s, svc.active_lease(ctx.db, s)))


class StartIn(BaseModel):
    kind: str = Field(pattern="^(chat|browser|desktop|technician_assisted)$")
    ticket_id: uuid.UUID | None = None
    device_id: uuid.UUID | None = None
    allowed_domains: list[str] = Field(default_factory=list, max_length=20)


@router.post("", status_code=201)
def start(body: StartIn, ctx: Ctx = Depends(perm("sessions.start"))):
    if body.ticket_id:
        ticket_for(ctx, body.ticket_id)
    s = svc.start(ctx.db, ctx.org_id, kind=body.kind, owner_id=ctx.p.user_id, ticket_id=body.ticket_id, device_id=body.device_id,
                  allowed_domains=body.allowed_domains)
    return svc.ser(s)


@router.get("/{sid}")
def get(sid: uuid.UUID, ctx: Ctx = Depends(get_ctx)):
    s = session_for(ctx, sid)
    out = svc.ser(s, svc.active_lease(ctx.db, s))
    out["can_consent"] = s.state == "pending_consent" and svc.consent_holder_ok(ctx.db, s, ctx.p.user_id, ctx.p.customer_id, ctx.p.is_staff)
    out["you_hold_control"] = bool(out["control"] and out["control"]["holder_user_id"] == str(ctx.p.user_id))
    if s.kind == "browser" and s.worker_session_ref and s.state not in ("terminated", "expired"):
        try:
            st = browser_client.state(s.worker_session_ref)
            out["browser"] = {"url": st.get("url"), "title": st.get("title"), "auth_required": st.get("auth_required"), "auth_kind": st.get("kind"),
                              "blocked_requests": st.get("blocked_requests", [])}
        except Exception as e:  # noqa: BLE001
            out["browser"] = {"error": str(e)[:200]}
    return out


@router.get("/{sid}/events")
def events(sid: uuid.UUID, ctx: Ctx = Depends(get_ctx), after: int = 0):
    s = session_for(ctx, sid)
    rows = ctx.db.execute(select(SessionEvent).where(SessionEvent.session_id == s.id, SessionEvent.seq > after).order_by(SessionEvent.seq).limit(500)).scalars()
    return [{"seq": e.seq, "kind": e.kind, "actor_kind": e.actor_kind, "data": e.data, "at": e.created_at.isoformat()} for e in rows]


class ConsentIn(BaseModel):
    minutes: int = Field(default=60, ge=5, le=240)
    max_risk: RiskLevel = RiskLevel.SENSITIVE
    recording: bool = False
    accept: bool = True


@router.post("/{sid}/consent")
def consent(sid: uuid.UUID, body: ConsentIn, ctx: Ctx = Depends(get_ctx)):
    s = session_for(ctx, sid)
    if not body.accept:
        if not svc.consent_holder_ok(ctx.db, s, ctx.p.user_id, ctx.p.customer_id, ctx.p.is_staff):
            raise Forbidden("Only the customer can decline")
        svc.terminate(ctx.db, s, by_kind="user", user_id=ctx.p.user_id, reason="consent declined")
        _resume_run(ctx, s, "consent declined")
        return svc.ser(s)
    svc.give_consent(ctx.db, s, user_id=ctx.p.user_id, customer_id=ctx.p.customer_id, is_staff=ctx.p.is_staff, minutes=body.minutes,
                     max_risk=body.max_risk, recording=body.recording)
    _resume_run(ctx, s, "consent granted")
    return svc.ser(s)


def _resume_run(ctx: Ctx, s: SupportSession, reason: str) -> None:
    if s.run_id:
        run = ctx.db.get(AgentRun, s.run_id)
        if run:
            agent_svc.resume(ctx.db, run, reason, ("consent", "approval", "user_auth", "human_control", "device_consent"))


@router.get("/{sid}/frame")
def frame(sid: uuid.UUID, ctx: Ctx = Depends(get_ctx)):
    """Latest masked screenshot for the live viewer (password/OTP/card fields are blacked out before capture)."""
    s = session_for(ctx, sid)
    if s.kind != "browser" or not s.worker_session_ref or s.state in ("terminated", "expired", "pending_consent"):
        raise Conflict("No live browser view for this session", code="no_view")
    shot = browser_client.screenshot(s.worker_session_ref)
    return Response(base64.b64decode(shot["image"]), media_type="image/jpeg", headers={"cache-control": "no-store", "x-page-url": shot["url"][:500]})


class LeaseIn(BaseModel):
    action: str = Field(pattern="^(take|release)$")


@router.post("/{sid}/control")
def control(sid: uuid.UUID, body: LeaseIn, ctx: Ctx = Depends(get_ctx)):
    s = session_for(ctx, sid)
    kind = _role_kind(ctx, s)
    if kind == "technician":
        ctx.p.require("sessions.takeover")
    if body.action == "take":
        svc.acquire_lease(ctx.db, s, kind, ctx.p.user_id, preempt=True, reason=f"{kind} takeover")
        if s.run_id:
            run = ctx.db.get(AgentRun, s.run_id)
            if run and run.state == "EXECUTING" and not run.paused_for:
                run.paused_for = "human_control"
    else:
        if not svc.holds_lease(ctx.db, s, kind, ctx.p.user_id):
            raise Conflict("You don't hold control", code="not_holder")
        svc.release_lease(ctx.db, s, f"released by {kind}")
    return svc.ser(s, svc.active_lease(ctx.db, s))


class HumanInput(BaseModel):
    kind: str = Field(pattern="^(click|type|key|scroll)$")
    x: float | None = Field(default=None, ge=0, le=4000)
    y: float | None = Field(default=None, ge=0, le=4000)
    text: str | None = Field(default=None, max_length=2000)
    key: str | None = Field(default=None, max_length=40)
    dy: float | None = None
    protected: bool = False


@router.post("/{sid}/input")
def human_input(sid: uuid.UUID, body: HumanInput, ctx: Ctx = Depends(get_ctx)):
    """Relays the human's own input. `protected=true` text (passwords, MFA codes) is sent straight to the isolated
    browser and is never stored, logged, shown to the AI, or included in session events."""
    s = session_for(ctx, sid)
    if s.kind != "browser" or s.state not in ("active", "awaiting_user_auth", "paused"):
        raise Conflict("Session not accepting input", code="invalid_state")
    kind = _role_kind(ctx, s)
    if not svc.holds_lease(ctx.db, s, kind, ctx.p.user_id):
        svc.acquire_lease(ctx.db, s, kind, ctx.p.user_id, preempt=True, reason="input")
    res = browser_client.human_input(s.worker_session_ref, body.model_dump())
    svc.event(ctx.db, s, "human_input", actor_kind=kind, actor_id=ctx.p.user_id,
              data={"kind": body.kind, "protected": body.protected, **({} if body.protected or body.kind != "key" else {"key": body.key})})
    return {"ok": True, "page": {"url": (res.get("page") or {}).get("url"), "auth_required": (res.get("page") or {}).get("auth_required")}}


@router.post("/{sid}/handback")
def handback(sid: uuid.UUID, ctx: Ctx = Depends(get_ctx)):
    """Human finished (e.g. signed in) and returns control to the AI."""
    s = session_for(ctx, sid)
    kind = _role_kind(ctx, s)
    lease = svc.active_lease(ctx.db, s)
    if lease and lease.holder_kind != "ai" and not svc.holds_lease(ctx.db, s, kind, ctx.p.user_id) and kind != "customer":
        raise Conflict("Someone else holds control", code="lease_held")
    if lease and lease.holder_kind != "ai":
        svc.release_lease(ctx.db, s, f"handed back by {kind}")
    if s.state == "awaiting_user_auth":
        s.state = "active"
        svc.event(ctx.db, s, "auth_completed_by_user", actor_kind=kind, actor_id=ctx.p.user_id)
    _resume_run(ctx, s, "control handed back")
    return svc.ser(s, svc.active_lease(ctx.db, s))


@router.post("/{sid}/pause")
def pause(sid: uuid.UUID, ctx: Ctx = Depends(get_ctx)):
    s = session_for(ctx, sid)
    svc.pause(ctx.db, s, by_kind=_role_kind(ctx, s), user_id=ctx.p.user_id, reason=f"paused by {_role_kind(ctx, s)}")
    if s.run_id:
        run = ctx.db.get(AgentRun, s.run_id)
        if run and not run.paused_for and run.state not in ("RESOLVED", "ESCALATED", "FAILED", "CANCELLED", "PARTIALLY_RESOLVED"):
            run.paused_for = "human_control"
    return svc.ser(s)


@router.post("/{sid}/resume")
def resume(sid: uuid.UUID, ctx: Ctx = Depends(get_ctx)):
    s = session_for(ctx, sid)
    if not svc.consent_holder_ok(ctx.db, s, ctx.p.user_id, ctx.p.customer_id, ctx.p.is_staff) and not ctx.p.can("sessions.takeover"):
        raise Forbidden("Not allowed")
    svc.resume(ctx.db, s, user_id=ctx.p.user_id)
    _resume_run(ctx, s, "session resumed")
    return svc.ser(s)


@router.post("/{sid}/emergency-stop")
def emergency_stop(sid: uuid.UUID, ctx: Ctx = Depends(get_ctx)):
    s = session_for(ctx, sid)
    svc.emergency_stop(ctx.db, s, user_id=ctx.p.user_id)
    if s.run_id:
        run = ctx.db.get(AgentRun, s.run_id)
        if run and run.state not in ("RESOLVED", "ESCALATED", "FAILED", "CANCELLED", "PARTIALLY_RESOLVED"):
            agent_svc._escalate(ctx.db, run, "Customer pressed emergency stop")
    return svc.ser(s)


@router.post("/{sid}/terminate")
def terminate(sid: uuid.UUID, ctx: Ctx = Depends(get_ctx)):
    s = session_for(ctx, sid)
    svc.terminate(ctx.db, s, by_kind=_role_kind(ctx, s), user_id=ctx.p.user_id, reason="ended by user")
    return svc.ser(s)


_ = AppError
