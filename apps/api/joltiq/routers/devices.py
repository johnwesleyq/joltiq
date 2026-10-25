"""Device management (staff/customer) and the Windows companion protocol endpoints (/agent/v1/*)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..config import get_settings
from ..core.errors import AppError, Forbidden, NotFound
from ..db import get_raw_session
from ..deps import Ctx, get_ctx, perm
from ..models import (
    AgentRun,
    ComputerUseRun,
    Device,
    DeviceCommand,
    DeviceSupportRequest,
    Membership,
    SupportSession,
    Ticket,
    User,
)
from ..models.enums import TERMINAL_RUN_STATES
from ..services import agent as agent_svc
from ..services import audit
from ..services import devices as svc
from ..services import sessions as session_svc
from ..services import tickets as ticket_svc
from ..services.actions.registry import ACTIONS
from .common import page_params, paginate

router = APIRouter(tags=["devices"])


@router.get("/devices")
def list_devices(ctx: Ctx = Depends(get_ctx), pp=Depends(page_params), status: str | None = None):
    stmt = select(Device).order_by(Device.hostname)
    if not ctx.p.can("devices.manage"):
        stmt = stmt.where((Device.owner_user_id == ctx.p.user_id) | (Device.customer_id == ctx.p.customer_id))
    if status:
        stmt = stmt.where(Device.status == status)
    return paginate(ctx.db, stmt, pp, svc.ser)


def device_for(ctx: Ctx, did: uuid.UUID) -> Device:
    d = ctx.db.get(Device, did)
    if not d:
        raise NotFound("Device not found")
    if not ctx.p.can("devices.manage") and d.owner_user_id != ctx.p.user_id and (not ctx.p.customer_id or d.customer_id != ctx.p.customer_id):
        raise NotFound("Device not found")
    return d


@router.get("/devices/{did}")
def get_device(did: uuid.UUID, ctx: Ctx = Depends(get_ctx)):
    d = device_for(ctx, did)
    cmds = ctx.db.execute(select(DeviceCommand).where(DeviceCommand.device_id == d.id).order_by(DeviceCommand.created_at.desc()).limit(50)).scalars()
    return {**svc.ser(d), "recent_commands": [{"id": str(c.id), "action": c.action, "status": c.status, "params": c.params,
                                               "created_at": c.created_at.isoformat()} for c in cmds]}


class EnrollCodeIn(BaseModel):
    customer_id: uuid.UUID | None = None


@router.post("/devices/enrollment-codes", status_code=201)
def enrollment_code(body: EnrollCodeIn, ctx: Ctx = Depends(perm("devices.enroll"))):
    cust = ctx.p.customer_id if ctx.p.is_customer else body.customer_id
    code, e = svc.create_enrollment(ctx.db, ctx.org_id, created_by=ctx.p.user_id, customer_id=cust)
    return {"code": code, "expires_at": e.expires_at.isoformat(), "server_url": str(ctx.request.base_url).rstrip("/"),
            "instructions": "Install the JoltIQ companion on the Windows PC, choose 'Enroll this device', and enter this code. "
                            "The code works once and expires in 15 minutes."}


@router.post("/devices/{did}/revoke")
def revoke(did: uuid.UUID, ctx: Ctx = Depends(get_ctx)):
    d = device_for(ctx, did)
    if not ctx.p.can("devices.manage") and d.owner_user_id != ctx.p.user_id:
        raise Forbidden("Not allowed")
    svc.revoke(ctx.db, d, ctx.p.user_id)
    return svc.ser(d)


@router.get("/devices/actions/catalog")
def catalog(ctx: Ctx = Depends(get_ctx)):
    return [{"id": a.id, "channel": a.channel, "risk": a.risk, "description": a.description, "params": a.params, "effects": a.effects,
             "requires_admin": a.requires_admin} for a in ACTIONS.values()]


# ================= Windows companion protocol =================
agent_router = APIRouter(prefix="/agent/v1", tags=["agent-protocol"])


class EnrollIn(BaseModel):
    code: str = Field(min_length=8, max_length=20)
    public_key_pem: str = Field(max_length=2000)
    hostname: str = Field(max_length=255)
    os_name: str = Field(max_length=120)
    os_version: str = Field(max_length=120)
    agent_version: str = Field(max_length=40)
    capabilities: list[str] = Field(default_factory=list, max_length=50)


@agent_router.post("/enroll")
def enroll(body: EnrollIn, request: Request, db: Session = Depends(get_raw_session)):
    from ..security.ratelimit import hit

    hit(db, f"enroll:{request.client.host if request.client else 'x'}", get_settings().rate_limit_device_enroll_per_hour, 3600)
    d = svc.enroll(db, code=body.code, public_key_pem=body.public_key_pem, hostname=body.hostname, os_name=body.os_name,
                   os_version=body.os_version, agent_version=body.agent_version, capabilities=body.capabilities)
    return {"device_id": str(d.id), "org_id": str(d.org_id), "server_public_key_pem": svc.server_public_pem(), "protocol": svc.PROTOCOL_VERSION,
            "poll_interval_seconds": 3, "capabilities_accepted": d.capabilities}


async def device_auth(request: Request, db: Session = Depends(get_raw_session), x_jq_device: str = Header(...), x_jq_timestamp: str = Header(...),
                      x_jq_nonce: str = Header(...), x_jq_counter: str = Header(...), x_jq_signature: str = Header(...)) -> tuple[Session, Device]:
    body = await request.body()
    d = svc.authenticate_device(db, device_id=x_jq_device, ts=x_jq_timestamp, nonce=x_jq_nonce, counter=x_jq_counter, signature=x_jq_signature,
                                method=request.method, path=request.url.path, body=body)
    return db, d


class DeviceSupportMessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=20_000)


class DeviceSupportRequestIn(DeviceSupportMessageIn):
    client_request_id: uuid.UUID


class DeviceSupportCreatedOut(BaseModel):
    ticket_id: uuid.UUID
    ticket_number: int
    run_id: uuid.UUID
    state: str


class DeviceSupportMessageOut(BaseModel):
    ticket_id: uuid.UUID
    ticket_number: int
    run_id: uuid.UUID
    state: str


def _support_owner(db: Session, device: Device) -> User:
    if not device.owner_user_id:
        raise Forbidden("This device is not assigned to an active user. Contact your administrator.")
    owner = db.get(User, device.owner_user_id)
    membership = db.execute(
        select(Membership).where(Membership.user_id == device.owner_user_id, Membership.org_id == device.org_id)
    ).scalar_one_or_none()
    if not owner or owner.disabled or not membership:
        raise Forbidden("This device owner is no longer active. Contact your administrator.")
    return owner


def _device_support_run(db: Session, device: Device, run_id: uuid.UUID) -> tuple[AgentRun, Ticket]:
    run = db.get(AgentRun, run_id)
    ticket = db.get(Ticket, run.ticket_id) if run and run.ticket_id else None
    if not run or not ticket or ticket.device_id != device.id:
        raise NotFound("Support request not found")
    return run, ticket


@agent_router.post("/support-requests", status_code=201, response_model=DeviceSupportCreatedOut)
def create_device_support_request(body: DeviceSupportRequestIn, dd=Depends(device_auth)):
    db, device = dd
    owner = _support_owner(db, device)
    db.execute(
        text("select pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"device-support-request:{device.id}:{body.client_request_id}"},
    )
    existing = db.execute(
        select(DeviceSupportRequest).where(
            DeviceSupportRequest.device_id == device.id,
            DeviceSupportRequest.client_request_id == body.client_request_id,
        )
    ).scalar_one_or_none()
    if existing:
        return existing.response
    subject = body.message.split("\n", 1)[0][:120].strip()
    ticket = ticket_svc.create(
        db,
        device.org_id,
        subject=subject if len(subject) >= 3 else "Support request",
        description=body.message,
        source="device_chat",
        customer_id=device.customer_id,
        requester_id=owner.id,
        device_id=device.id,
        actor_kind="device",
    )
    run = agent_svc.start_run(db, device.org_id, ticket=ticket, started_by=owner.id, first_message=body.message)
    audit.record(
        db,
        org_id=device.org_id,
        actor_kind="device",
        actor_id=device.id,
        action="support_request.created",
        resource_type="agent_run",
        resource_id=run.id,
        data={"ticket_id": str(ticket.id)},
    )
    response = {"ticket_id": str(ticket.id), "ticket_number": ticket.number, "run_id": str(run.id), "state": run.state}
    db.add(
        DeviceSupportRequest(
            org_id=device.org_id,
            device_id=device.id,
            client_request_id=body.client_request_id,
            ticket_id=ticket.id,
            run_id=run.id,
            response=response,
        )
    )
    db.flush()
    return response


@agent_router.post("/support-requests/{run_id}/messages", response_model=DeviceSupportMessageOut)
def continue_device_support_request(run_id: uuid.UUID, body: DeviceSupportMessageIn, dd=Depends(device_auth)):
    db, device = dd
    owner = _support_owner(db, device)
    run, ticket = _device_support_run(db, device, run_id)
    if run.state in TERMINAL_RUN_STATES:
        run = agent_svc.start_run(db, device.org_id, ticket=ticket, started_by=owner.id, first_message=body.message)
    else:
        agent_svc.customer_message(db, run, body.message, owner.id)
        ticket_svc.comment(db, ticket, body.message, author_id=owner.id, author_kind="device")
    audit.record(
        db,
        org_id=device.org_id,
        actor_kind="device",
        actor_id=device.id,
        action="support_request.message",
        resource_type="agent_run",
        resource_id=run.id,
        data={"ticket_id": str(ticket.id)},
    )
    return {"ticket_id": ticket.id, "ticket_number": ticket.number, "run_id": run.id, "state": run.state}


@agent_router.get("/support-requests/{run_id}")
def get_device_support_request(run_id: uuid.UUID, dd=Depends(device_auth)):
    db, device = dd
    _support_owner(db, device)
    run, _ = _device_support_run(db, device, run_id)
    return agent_svc.device_run_detail(db, run)


class DirectComputerUseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = Field(pattern="^(guide|fix)$")


def _direct_computer_use_response(db: Session, session: SupportSession, mode: str) -> dict:
    cu = db.execute(
        select(ComputerUseRun)
        .where(ComputerUseRun.session_id == session.id)
        .order_by(ComputerUseRun.created_at.desc())
    ).scalars().first()
    return {
        "session_id": str(session.id),
        "computer_use_run_id": str(cu.id) if cu else None,
        "mode": cu.mode if cu else mode,
        "state": cu.state if cu else "waiting_session_consent",
    }


@agent_router.post("/support-requests/{run_id}/computer-use")
def start_direct_computer_use(run_id: uuid.UUID, body: DirectComputerUseIn, dd=Depends(device_auth)):
    """Turn this device's own support request into Guide/Fix without weakening either native consent gate."""
    db, device = dd
    owner = _support_owner(db, device)
    _run, ticket = _device_support_run(db, device, run_id)
    active = db.execute(
        select(SupportSession).where(
            SupportSession.ticket_id == ticket.id,
            SupportSession.device_id == device.id,
            SupportSession.kind == "desktop",
            SupportSession.state.in_(["pending_consent", "active", "paused"]),
        ).order_by(SupportSession.created_at.desc())
    ).scalars().all()
    for existing in active:
        details = existing.request_details or {}
        if details.get("direct_device_request") and details.get("computer_use_mode") == body.mode:
            return JSONResponse(_direct_computer_use_response(db, existing, body.mode), status_code=200)

    from ..services.ai.computer import SUPPORTED_PROVIDERS
    from ..services.ai.router import available_providers

    if not [candidate for candidate in available_providers(db, device.org_id, "computer_use") if candidate[0] in SUPPORTED_PROVIDERS]:
        raise AppError(
            "Fix it for me needs the company's approved local computer-use model gateway.",
            code="computer_use_not_configured",
        )
    goal = (ticket.description or ticket.subject).strip()
    session = session_svc.start(
        db,
        device.org_id,
        kind="desktop",
        owner_id=owner.id,
        ticket_id=ticket.id,
        device_id=device.id,
        run_id=run_id,
        request_details={
            "direct_device_request": True,
            "computer_use_mode": body.mode,
            "computer_use_goal": goal,
        },
    )
    session_svc.give_consent(
        db,
        session,
        user_id=owner.id,
        customer_id=device.customer_id,
        is_staff=True,
        actions=[],
        recording=False,
    )
    audit.record(
        db,
        org_id=device.org_id,
        actor_kind="device",
        actor_id=device.id,
        action="support_request.computer_use_requested",
        resource_type="support_session",
        resource_id=session.id,
        data={"ticket_id": str(ticket.id), "mode": body.mode},
    )
    return JSONResponse(_direct_computer_use_response(db, session, body.mode), status_code=201)


class HeartbeatIn(BaseModel):
    agent_version: str = Field(max_length=40)
    facts: dict = Field(default_factory=dict)
    capabilities: list[str] = Field(default_factory=list)


@agent_router.post("/heartbeat")
def heartbeat(body: HeartbeatIn, dd=Depends(device_auth)):
    db, d = dd
    d.agent_version = body.agent_version[:40]
    d.facts = {k: str(v)[:200] for k, v in list(body.facts.items())[:30]}
    if body.capabilities:
        d.capabilities = [c for c in body.capabilities if c in svc.accepted_desktop_actions()]
    latest = "1.0.0"
    return {"ok": True, "server_time": int(__import__("time").time()), "update": {"latest_version": latest, "required": svc._ver(d.agent_version) < svc._ver(svc.MIN_AGENT_VERSION)}}


@agent_router.get("/poll")
def poll(dd=Depends(device_auth)):
    db, d = dd
    return {"envelopes": svc.pending_for(db, d)}


class LocalConsentIn(BaseModel):
    accepted: bool
    user_display_name: str = Field(default="", max_length=200)


@agent_router.post("/sessions/{sid}/local-consent")
def local_consent(sid: uuid.UUID, body: LocalConsentIn, dd=Depends(device_auth)):
    """The person at the Windows PC accepted/declined the session in the companion's consent dialog."""
    db, d = dd
    s = db.get(SupportSession, sid)
    if not s or s.device_id != d.id:
        raise NotFound("session not found")
    s.consent = {**(s.consent or {}), "device_local_consent": bool(body.accepted), "device_local_declined": not body.accepted,
                 "device_local_user": body.user_display_name[:200]}
    session_svc.event(db, s, "device_local_consent", actor_kind="device", data={"accepted": body.accepted, "user": body.user_display_name[:80]})
    audit.record(db, org_id=d.org_id, actor_kind="device", actor_id=d.id, action="session.device_local_consent", resource_type="support_session",
                 resource_id=s.id, data={"accepted": body.accepted})
    if not body.accepted:
        session_svc.terminate(db, s, by_kind="device", reason="declined on device")
    elif (s.request_details or {}).get("direct_device_request"):
        from ..services import computer_use as computer_use_svc

        existing = db.execute(select(ComputerUseRun.id).where(ComputerUseRun.session_id == s.id)).first()
        if not existing:
            owner = _support_owner(db, d)
            computer_use_svc.start(
                db,
                s,
                goal=str(s.request_details.get("computer_use_goal") or "Help with this support request"),
                user_id=owner.id,
                approved_by_customer=True,
                mode=str(s.request_details.get("computer_use_mode") or "fix"),
            )
    if s.run_id:
        run = db.get(AgentRun, s.run_id)
        if run:
            agent_svc.resume(db, run, "device consent", ("device_consent", "consent", "approval", "diagnostic_device_consent"))
    return {"ok": True}


@agent_router.post("/sessions/{sid}/stop")
def device_stop(sid: uuid.UUID, dd=Depends(device_auth)):
    """Kill switch pressed on the device (tray menu / hotkey)."""
    db, d = dd
    s = db.get(SupportSession, sid)
    if not s or s.device_id != d.id:
        raise NotFound("session not found")
    s.emergency_stopped = True
    session_svc.terminate(db, s, by_kind="device", reason="stopped from device kill switch")
    if s.run_id:
        run = db.get(AgentRun, s.run_id)
        if run and run.state not in ("RESOLVED", "ESCALATED", "FAILED", "CANCELLED", "PARTIALLY_RESOLVED"):
            agent_svc._escalate(db, run, "User stopped the session from their computer")
    return {"ok": True}


class ResultIn(BaseModel):
    status: str = Field(pattern="^(done|failed|rejected)$")
    result: dict = Field(default_factory=dict)


@agent_router.post("/commands/{cid}/result")
def command_result(cid: uuid.UUID, body: ResultIn, dd=Depends(device_auth)):
    db, d = dd
    cmd, idempotent, late, conflict = svc.record_result(db, d, cid, body.status, body.result)
    if conflict:
        return JSONResponse(status_code=409, content={"error": {"code": "command_result_conflict",
                                                                  "message": "terminal command result conflicts with the first report"}})
    return {"ok": True, "status": cmd.status, "late": late, "idempotent": idempotent}


@agent_router.post("/commands/{cid}/screenshot")
async def command_screenshot(cid: uuid.UUID, request: Request, dd=Depends(device_auth)):
    """Raw JPEG/PNG body (signed like every request). Upload before posting the command result."""
    from ..models import DeviceCommand
    from ..services import computer_use

    db, d = dd
    cmd = db.get(DeviceCommand, cid)
    if not cmd:
        raise NotFound("command not found")
    key = computer_use.attach_screenshot(db, d, cmd, await request.body())
    return {"ok": True, "stored": bool(key)}


class DeviceRespondIn(BaseModel):
    approved: bool | None = None
    answer: str | None = Field(default=None, max_length=1000)
    done: bool = False
    switch_to_fix: bool = False


class DeviceRunConsentIn(BaseModel):
    accepted: bool


@agent_router.get("/computer-use/active")
def device_cu_active(dd=Depends(device_auth)):
    """What the companion should show: the active run's goal, and any question/approval/secure-input pause."""
    from sqlalchemy import select

    from ..models import ComputerUseRun
    from ..services import computer_use

    db, d = dd
    sids = [s.id for s in db.execute(select(SupportSession).where(SupportSession.device_id == d.id, SupportSession.state == "active")).scalars()]
    runs = db.execute(select(ComputerUseRun).where(ComputerUseRun.session_id.in_(sids), ComputerUseRun.state.in_(computer_use.ACTIVE))).scalars().all() if sids else []
    return {"runs": [computer_use.ser(r) for r in runs]}


@agent_router.post("/computer-use/{rid}/local-consent")
def device_cu_local_consent(rid: uuid.UUID, body: DeviceRunConsentIn, dd=Depends(device_auth)):
    """Record native consent for one exact computer-use goal and immutable Guide/Fix mode."""
    from ..models import ComputerUseRun
    from ..services import computer_use

    db, d = dd
    run = db.get(ComputerUseRun, rid)
    s = db.get(SupportSession, run.session_id) if run else None
    if not run or not s or s.device_id != d.id:
        raise NotFound("run not found")
    computer_use.record_local_consent(db, run, device_id=d.id, accepted=body.accepted)
    return computer_use.ser(run)


@agent_router.post("/computer-use/{rid}/respond")
def device_cu_respond(rid: uuid.UUID, body: DeviceRespondIn, dd=Depends(device_auth)):
    """The person at the computer answered in the companion (approve/deny, answer, or 'I finished typing')."""
    from ..models import ComputerUseRun
    from ..services import computer_use

    db, d = dd
    run = db.get(ComputerUseRun, rid)
    s = db.get(SupportSession, run.session_id) if run else None
    if not run or not s or s.device_id != d.id:
        raise NotFound("run not found")
    choices = int(body.approved is not None) + int(bool(body.answer)) + int(body.done) + int(body.switch_to_fix)
    if choices != 1:
        raise AppError("Provide exactly one response", code="validation_error")
    if run.state == "pending_approval":
        computer_use.approve_goal(db, run, user_id=None, approved=bool(body.approved))
        answered = run
    else:
        answered = computer_use.respond(db, run, user_id=None, approved=body.approved, answer=body.answer, done=body.done,
                                        switch_to_fix=body.switch_to_fix, via="device")
    return computer_use.ser(answered)


@agent_router.post("/computer-use/{rid}/stop")
def device_cu_stop(rid: uuid.UUID, dd=Depends(device_auth)):
    from ..models import ComputerUseRun
    from ..services import computer_use

    db, d = dd
    run = db.get(ComputerUseRun, rid)
    s = db.get(SupportSession, run.session_id) if run else None
    if not run or not s or s.device_id != d.id:
        raise NotFound("run not found")
    computer_use.stop(db, run, user_id=None, reason="Stopped on the customer computer")
    return computer_use.ser(run)


@agent_router.post("/unenroll")
def unenroll(dd=Depends(device_auth)):
    db, d = dd
    svc.revoke(db, d, actor_id=None)
    return {"ok": True}


_ = AppError
