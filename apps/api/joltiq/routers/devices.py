"""Device management (staff/customer) and the Windows companion protocol endpoints (/agent/v1/*)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..config import get_settings
from ..core.errors import AppError, Conflict, Forbidden, NotFound
from ..db import get_raw_session
from ..deps import Ctx, get_ctx, perm
from ..models import (
    AgentMessage,
    AgentRun,
    ComputerUseRun,
    Device,
    DeviceCommand,
    DeviceSupportRequest,
    Membership,
    RemoteAccessSession,
    SupportSession,
    Ticket,
    User,
)
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


class RemoteAccessBindingIn(BaseModel):
    node_id: str = Field(min_length=3, max_length=300, pattern=r"^[^\x00-\x1f\x7f]+$")


class RemoteAccessStartIn(BaseModel):
    reason: str = Field(min_length=5, max_length=500)


@router.get("/devices/{did}/remote-access")
def remote_access_status(did: uuid.UUID, ctx: Ctx = Depends(perm("devices.manage"))):
    from ..services import remote_access

    d = device_for(ctx, did)
    binding = remote_access.binding_for(ctx.db, d.id)
    active = ctx.db.execute(
        select(RemoteAccessSession).where(
            RemoteAccessSession.device_id == d.id, RemoteAccessSession.state == "active"
        ).order_by(RemoteAccessSession.created_at.desc())
    ).scalars().first()
    return {
        "enabled": bool(get_settings().meshcentral_url),
        "configured": binding is not None,
        "provider": binding.provider if binding else "meshcentral",
        "node_id": binding.node_id if binding else None,
        "active_session": remote_access.serialize(ctx.db, active) if active else None,
    }


@router.put("/devices/{did}/remote-access")
def bind_remote_access(did: uuid.UUID, body: RemoteAccessBindingIn, ctx: Ctx = Depends(perm("devices.manage"))):
    from ..services import remote_access

    d = device_for(ctx, did)
    binding = remote_access.bind(ctx.db, d, node_id=body.node_id.strip(), actor_id=ctx.p.user_id)
    return {"enabled": True, "configured": True, "provider": binding.provider, "node_id": binding.node_id}


@router.post("/devices/{did}/remote-access/sessions")
def start_remote_access(did: uuid.UUID, body: RemoteAccessStartIn, ctx: Ctx = Depends(perm("devices.manage"))):
    from ..services import remote_access

    d = device_for(ctx, did)
    session, created = remote_access.start(ctx.db, d, reason=body.reason.strip(), operator_id=ctx.p.user_id)
    return JSONResponse(remote_access.serialize(ctx.db, session), status_code=201 if created else 200)


@router.post("/devices/{did}/remote-access/sessions/{sid}/end")
def end_remote_access(did: uuid.UUID, sid: uuid.UUID, ctx: Ctx = Depends(perm("devices.manage"))):
    from ..services import remote_access

    d = device_for(ctx, did)
    session = ctx.db.get(RemoteAccessSession, sid)
    if session is None or session.device_id != d.id:
        raise NotFound("Remote access session not found")
    return remote_access.serialize(ctx.db, remote_access.end(ctx.db, session, actor_id=ctx.p.user_id))


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
    installation_id: uuid.UUID | None = None
    capabilities: list[str] = Field(default_factory=list, max_length=100)


@agent_router.post("/enroll")
def enroll(body: EnrollIn, request: Request, db: Session = Depends(get_raw_session)):
    from ..security.ratelimit import hit

    hit(db, f"enroll:{request.client.host if request.client else 'x'}", get_settings().rate_limit_device_enroll_per_hour, 3600)
    d = svc.enroll(db, code=body.code, public_key_pem=body.public_key_pem, hostname=body.hostname, os_name=body.os_name,
                   os_version=body.os_version, agent_version=body.agent_version, installation_id=body.installation_id,
                   capabilities=body.capabilities)
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
    ai_notice: str | None = None
    reply: dict | None = None
    setup_required: bool = False


class DeviceSupportMessageOut(BaseModel):
    ticket_id: uuid.UUID
    ticket_number: int
    run_id: uuid.UUID
    state: str
    reply: dict | None = None
    ai_notice: str | None = None
    setup_required: bool = False


def _ask_reply(db: Session, device: Device, run) -> tuple[dict | None, str | None, bool]:
    """Instant technician chat reply for the PC Ask window (no computer-use / approvals)."""
    from ..core.errors import AppError
    from ..services import ask_chat

    try:
        return ask_chat.reply(db, device.org_id, run), None, False
    except ask_chat.AskNotConnected as e:
        return None, e.message, True
    except AppError as e:
        return {"id": None, "content": f"Sorry, the AI model didn't answer: {e.message}", "live_model": False}, None, False


def _owner_role(db: Session, device: Device) -> str:
    m = db.execute(select(Membership).where(Membership.user_id == device.owner_user_id, Membership.org_id == device.org_id)).scalar_one_or_none()
    return m.role if m else "end_user"


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


def _active_direct_computer_use(db: Session, device: Device, run: AgentRun) -> ComputerUseRun | None:
    """Return the active Fix/Guide run that owns this native conversation, if any."""
    from ..services import computer_use

    return db.execute(
        select(ComputerUseRun)
        .join(SupportSession, SupportSession.id == ComputerUseRun.session_id)
        .where(
            SupportSession.run_id == run.id,
            SupportSession.device_id == device.id,
            SupportSession.request_details["direct_device_request"].as_boolean().is_(True),
            ComputerUseRun.state.in_(computer_use.ACTIVE),
        )
        .order_by(ComputerUseRun.created_at.desc())
    ).scalars().first()


def _native_computer_use_prompt(run: ComputerUseRun) -> str | None:
    waiting = run.waiting_for or {}
    text = waiting.get("text")
    if not text:
        return None
    kind = waiting.get("kind")
    if kind == "approval":
        return f"{text}\n\nReply APPROVE or DENY. Your reply applies only to this exact action."
    if kind == "secure_input":
        return f"{text}\n\nComplete it yourself, then reply DONE, or reply CANCEL."
    if kind == "guide_action":
        return f"{text}\n\nReply DONE when you finish, or SWITCH TO FIX."
    return text


def _native_computer_use_response(body: DeviceSupportMessageIn, run: ComputerUseRun) -> dict:
    waiting = run.waiting_for or {}
    kind = waiting.get("kind")
    response = body.message.strip()
    if kind == "approval":
        choice = response.upper()
        if choice not in ("APPROVE", "DENY"):
            raise AppError("Reply exactly APPROVE or DENY for this action", code="validation_error")
        return {"approved": choice == "APPROVE"}
    if kind == "secure_input":
        choice = response.upper()
        if choice not in ("DONE", "CANCEL"):
            raise AppError("Reply exactly DONE or CANCEL", code="validation_error")
        return {"done": choice == "DONE", "approved": False if choice == "CANCEL" else None}
    if kind == "guide_action":
        choice = response.upper()
        if choice not in ("DONE", "SWITCH TO FIX"):
            raise AppError("Reply exactly DONE or SWITCH TO FIX", code="validation_error")
        return {"done": choice == "DONE", "switch_to_fix": choice == "SWITCH TO FIX"}
    return {"answer": response}


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
    from ..services import ask_chat

    run = ask_chat.chat_run(db, device.org_id, ticket, owner.id)
    agent_svc.add_message(db, run, "user", body.message, author_id=owner.id)
    reply, notice, setup_required = _ask_reply(db, device, run)
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
    response = {"ticket_id": str(ticket.id), "ticket_number": ticket.number, "run_id": str(run.id), "state": run.state,
                "ai_notice": notice, "reply": reply, "setup_required": setup_required}
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
    reply = None
    direct_run = _active_direct_computer_use(db, device, run)
    answering_fix = bool(direct_run and direct_run.state == "waiting_customer" and direct_run.waiting_for)
    if answering_fix:
        from ..services import computer_use

        assert direct_run is not None
        response = _native_computer_use_response(body, direct_run)
        computer_use.respond(db, direct_run, user_id=owner.id, via="device", **response)
        state = "AI_INVESTIGATING"
    notice, setup_required = None, False
    if not answering_fix:
        # Ordinary Ask message: plain technician chat, instant reply. Never resumes the troubleshooting state machine.
        from ..services import ask_chat

        run = ask_chat.chat_run(db, device.org_id, ticket, owner.id)
        agent_svc.add_message(db, run, "user", body.message, author_id=owner.id)
        state = run.state
        reply, notice, setup_required = _ask_reply(db, device, run)
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
    return {"ticket_id": ticket.id, "ticket_number": ticket.number, "run_id": run.id, "state": state, "reply": reply,
            "ai_notice": notice, "setup_required": setup_required}


@agent_router.get("/support-requests/{run_id}")
def get_device_support_request(run_id: uuid.UUID, dd=Depends(device_auth)):
    db, device = dd
    _support_owner(db, device)
    run, _ = _device_support_run(db, device, run_id)
    detail = agent_svc.run_detail(db, run, customer_view=True)
    direct_run = _active_direct_computer_use(db, device, run)
    messages = detail["messages"]
    if direct_run:
        # The ticket agent may have escalated because no approved procedure matched. Once the customer
        # explicitly starts Fix/Guide, that separate run owns the native conversation until it ends.
        messages = [m for m in messages if not (m.get("data") or {}).get("escalation_reason")]
        prompt = _native_computer_use_prompt(direct_run)
        if prompt:
            messages = [
                *messages,
                {
                    "id": f"computer-use-{direct_run.id}-{direct_run.step_count}",
                    "role": "assistant",
                    "content": prompt,
                    "at": direct_run.updated_at.isoformat(),
                },
            ]
    from ..services import ask_chat
    from ..services.ai.router import ai_status

    status = ai_status(db, device.org_id)
    chat_mode = (run.context or {}).get("mode") == "chat" and run.paused_for == "chat"
    return {
        "ai_notice": ask_chat.status(db, device.org_id)["notice"],
        "computer_use": _computer_use_status(db, device, run, status),
        "id": detail["id"],
        "ticket_id": detail["ticket_id"],
        "state": "AI_INVESTIGATING" if direct_run else ("CHAT" if chat_mode else detail["state"]),
        "paused_for": "customer" if direct_run and direct_run.state == "waiting_customer" else (None if chat_mode else detail["paused_for"]),
        "outcome_stage": detail["outcome_stage"],
        "messages": [
            {key: message[key] for key in ("id", "role", "content", "at")}
            for message in messages
        ],
    }


CU_STATUS_TEXT = {
    "waiting_session_consent": ("Waiting for you to allow JoltIQ on this PC",
                                "A 'JoltIQ — permission requested' window should be open on this PC. Click Allow. "
                                "If you don't see it, click 'Approve on this PC' to ask again."),
    "waiting_local_consent": ("Waiting for you to allow Fix it for me on this PC",
                              "Click Allow in the 'JoltIQ Fix it for me' window. If you don't see it, click 'Approve on this PC'."),
    "pending_approval": ("Waiting for the goal to be approved", "Approve the goal in the JoltIQ window or the web portal."),
    "running": ("JoltIQ is working on this PC", "Watch the red banner; press STOP at any time."),
    "waiting_device": ("JoltIQ is waiting for this PC to finish a step", "Keep the JoltIQ companion running."),
    "waiting_customer": ("JoltIQ needs your answer", "Answer the question shown in the conversation."),
}


def _computer_use_status(db: Session, device: Device, run: AgentRun, ai: dict) -> dict | None:
    """What Guide/Fix is doing for this request, in plain English, so the tray never shows a stale status forever."""
    sess = db.execute(
        select(SupportSession).where(SupportSession.run_id == run.id, SupportSession.device_id == device.id,
                                     SupportSession.request_details["direct_device_request"].as_boolean().is_(True))
        .order_by(SupportSession.created_at.desc())
    ).scalars().first()
    if not sess:
        return None
    cu = db.execute(select(ComputerUseRun).where(ComputerUseRun.session_id == sess.id)
                    .order_by(ComputerUseRun.created_at.desc())).scalars().first()
    if cu is not None:
        key = cu.state
    elif sess.state == "active" and not (sess.consent or {}).get("device_local_consent"):
        key = "waiting_session_consent"
    else:
        key = sess.state
    if key in CU_STATUS_TEXT:
        label, next_step = CU_STATUS_TEXT[key]
        needs_local = key in ("waiting_session_consent", "waiting_local_consent")
    elif cu is not None and cu.state == "completed" and cu.success is True:
        reason = cu.summary or "The fix completed successfully."
        label, next_step, needs_local = f"Completed: {reason}"[:300], "You can start another request whenever you need help.", False
    else:
        reason = (cu.summary if cu is not None else sess.pause_reason) or key
        label, next_step, needs_local = f"Stopped: {reason}"[:300], "Click 'Fix it for me' or 'Guide me' to try again.", False
    return {"state": key, "label": label, "next_step": next_step, "needs_local_approval": needs_local,
            "session_id": str(sess.id), "computer_use_run_id": str(cu.id) if cu is not None else None,
            "model_notice": ai["computer_use_notice"]}


class DirectComputerUseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: str = Field(pattern="^(guide|fix)$")


def _direct_computer_use_goal(db: Session, run: AgentRun, ticket: Ticket) -> str:
    """Use the request the customer most recently asked JoltIQ to perform."""
    messages = db.execute(
        select(AgentMessage.role, AgentMessage.content)
        .where(AgentMessage.run_id == run.id, AgentMessage.role == "user")
        .order_by(AgentMessage.created_at.desc(), AgentMessage.id.desc())
        .limit(8)
    ).all()
    latest = str(messages[0].content).strip() if messages else ""
    # A short answer to a platform clarification is context, not a replacement goal.
    # Without this, "uninstall Adobe Genuine Service" followed by "Windows" created
    # an empty approved plan whose goal was merely "windows".
    platform = latest.casefold().strip(" .!?")
    if platform in {
        "windows", "windows 10", "windows 11", "win10", "win11", "pc",
        "mac", "macos", "mac os", "linux",
    }:
        previous = next((str(row.content).strip() for row in messages[1:] if str(row.content).strip()), "")
        if previous:
            # This endpoint is authenticated by an enrolled Windows companion, so
            # the platform answer adds no execution detail. Keep the actionable
            # request verbatim so strict parsers do not mistake the clarification
            # for part of an application name.
            return previous[:2000]
    return str(latest or ticket.description or ticket.subject).strip()[:2000]


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


def _direct_computer_use_details(db: Session, device: Device, goal: str, mode: str) -> dict:
    """Build the same approved typed-action plan used by the web Fix flow.

    Ask JoltIQ starts computer use from the companion itself, so it does not pass through
    POST /agents/fix. Persisting the plan here keeps the one local Fix approval authoritative
    for typed Windows actions as well as screen actions.
    """
    details = {
        "direct_device_request": True,
        "computer_use_mode": mode,
        "computer_use_goal": goal,
    }
    if mode != "fix":
        return details
    from ..services import windows_autoplan, windows_plan

    planned = windows_autoplan.build(db, device.org_id, goal, set(device.capabilities or []))
    plan = planned.items
    details["full_fix"] = {
        "goal": goal,
        "device_name": device.hostname,
        "windows_actions": plan,
        "high_risk": windows_plan.needs_ack(plan),
        "planner": planned.planner,
        "planner_model": planned.model,
        "planner_live": planned.live,
        "planner_rationale": planned.rationale,
        "planner_fallback_reason": planned.fallback_reason,
    }
    return details


@agent_router.post("/support-requests/{run_id}/computer-use")
def start_direct_computer_use(run_id: uuid.UUID, body: DirectComputerUseIn, dd=Depends(device_auth)):
    """Turn this device's own support request into Guide/Fix without weakening either native consent gate."""
    db, device = dd
    owner = _support_owner(db, device)
    run, ticket = _device_support_run(db, device, run_id)
    goal = _direct_computer_use_goal(db, run, ticket)
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
        if details.get("direct_device_request"):
            response = _direct_computer_use_response(db, existing, body.mode)
            from ..services import computer_use as computer_use_svc

            if response["computer_use_run_id"] and response["state"] in computer_use_svc.ACTIVE:
                return JSONResponse(response, status_code=200)
            if existing.state == "active" and (existing.consent or {}).get("device_local_consent"):
                existing.request_details = {
                    **(existing.request_details or {}),
                    **_direct_computer_use_details(db, device, goal, body.mode),
                }
                computer_use_svc.start(
                    db,
                    existing,
                    goal=goal,
                    user_id=owner.id,
                    approved_by_customer=True,
                    mode=body.mode,
                )
                return JSONResponse(_direct_computer_use_response(db, existing, body.mode), status_code=201)
            return JSONResponse(response, status_code=200)

    from ..services.ai.computer import SUPPORTED_PROVIDERS
    from ..services.ai.router import available_providers

    if not [candidate for candidate in available_providers(db, device.org_id, "computer_use") if candidate[0] in SUPPORTED_PROVIDERS]:
        raise AppError(
            "Fix it for me needs an admin-approved computer-use provider that may receive computer screens.",
            code="computer_use_not_configured",
        )
    session = session_svc.start(
        db,
        device.org_id,
        kind="desktop",
        owner_id=owner.id,
        ticket_id=ticket.id,
        device_id=device.id,
        run_id=run_id,
        request_details=_direct_computer_use_details(db, device, goal, body.mode),
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
    installation_id: uuid.UUID | None = None
    facts: dict = Field(default_factory=dict)
    capabilities: list[str] = Field(default_factory=list)


@agent_router.post("/heartbeat")
def heartbeat(body: HeartbeatIn, dd=Depends(device_auth)):
    db, d = dd
    if body.installation_id is not None:
        conflicting = db.execute(
            select(Device.id).where(
                Device.installation_id == body.installation_id,
                Device.status == "active",
                Device.id != d.id,
            )
        ).scalar_one_or_none()
        if conflicting is not None or (d.installation_id is not None and d.installation_id != body.installation_id):
            audit.security_event(db, "device_installation_identity_conflict", org_id=d.org_id, severity="warning",
                                 data={"device_id": str(d.id)})
            raise Conflict("Installation identity is already linked to another device", code="installation_identity_conflict")
        if d.installation_id is None:
            d.installation_id = body.installation_id
            audit.record(db, org_id=d.org_id, actor_kind="device", actor_id=d.id,
                         action="device.installation_identity_linked", resource_type="device", resource_id=d.id)
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
    authorization_id: uuid.UUID | None = None
    action_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
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
                                        switch_to_fix=body.switch_to_fix, via="device",
                                        authorization_id=str(body.authorization_id) if body.authorization_id else None,
                                        action_hash=body.action_hash)
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
