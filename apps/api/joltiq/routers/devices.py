"""Device management (staff/customer) and the Windows companion protocol endpoints (/agent/v1/*)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.errors import AppError, Forbidden, NotFound
from ..db import get_raw_session
from ..deps import Ctx, get_ctx, perm
from ..models import AgentRun, Device, DeviceCommand, SupportSession
from ..services import agent as agent_svc
from ..services import audit
from ..services import devices as svc
from ..services import sessions as session_svc
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

    hit(db, f"enroll:{request.client.host if request.client else 'x'}", 20, 3600)
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
        d.capabilities = [c for c in body.capabilities if c in svc.DESKTOP_ACTIONS]
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
    if s.run_id:
        run = db.get(AgentRun, s.run_id)
        if run:
            agent_svc.resume(db, run, "device consent", ("device_consent", "consent", "approval"))
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
    cmd = svc.record_result(db, d, cid, body.status, body.result)
    return {"ok": True, "status": cmd.status}


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
    if run.state == "pending_approval":
        computer_use.approve_goal(db, run, user_id=None, approved=bool(body.approved))
    else:
        computer_use.respond(db, run, user_id=None, approved=body.approved, answer=body.answer, done=body.done, via="device")
    return computer_use.ser(run)


@agent_router.post("/unenroll")
def unenroll(dd=Depends(device_auth)):
    db, d = dd
    svc.revoke(db, d, actor_id=None)
    return {"ok": True}


_ = AppError
