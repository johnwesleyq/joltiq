"""AI chat + agent runs + research + approvals + verifications."""
from __future__ import annotations

import json
import uuid
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..core.errors import AppError, Forbidden, NotFound
from ..deps import Ctx, get_ctx, perm
from ..models import (
    AgentMessage,
    AgentRun,
    Approval,
    KnowledgeAnswerFeedback,
    ResearchFinding,
    ResearchQuery,
    ResearchSource,
    SupportSession,
    Ticket,
    VerificationEvidence,
    VerificationRun,
)
from ..models.enums import TERMINAL_RUN_STATES, ApprovalStatus
from ..services import agent as svc
from ..services import approvals as approval_svc
from ..services import ask_chat, audit
from ..services import research as research_svc
from ..services import tickets as ticket_svc
from .common import page_params, paginate
from .tickets import ticket_for

router = APIRouter(tags=["agents"])


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)
    ticket_id: uuid.UUID | None = None
    subject: str | None = Field(default=None, max_length=300)
    attachment_id: uuid.UUID | None = None
    mode: Literal["chat", "troubleshoot"] = "chat"


class AnswerFeedbackIn(BaseModel):
    helpful: bool
    comment: str = Field(default="", max_length=2000)


@router.get("/agents/ai-status")
def ai_status_endpoint(ctx: Ctx = Depends(perm("agents.run"))):
    """Is a live chat model connected for Ask? Never returns keys."""
    from ..config import get_settings

    st = ask_chat.status(ctx.db, ctx.org_id)
    return {"live_chat": st["connected"], "provider": st["provider"], "model": st["model"], "source": st["source"],
            "notice": st["notice"], "can_connect": ctx.p.can("models.manage"),
            "platform_key_available": bool(get_settings().openai_api_key)}


@router.put("/agents/messages/{message_id}/feedback")
def answer_feedback(message_id: uuid.UUID, body: AnswerFeedbackIn, ctx: Ctx = Depends(perm("agents.run"))):
    """Create or correct the caller's rating for one accessible assistant answer."""
    message = ctx.db.get(AgentMessage, message_id)
    if not message:
        raise NotFound("Answer not found")
    _run_for(ctx, message.run_id)
    if message.role != "assistant":
        raise AppError("Only assistant answers can be rated", code="validation_error")
    feedback = ctx.db.execute(
        select(KnowledgeAnswerFeedback).where(
            KnowledgeAnswerFeedback.message_id == message.id,
            KnowledgeAnswerFeedback.user_id == ctx.p.user_id,
        )
    ).scalar_one_or_none()
    if feedback is None:
        feedback = KnowledgeAnswerFeedback(
            org_id=ctx.org_id,
            run_id=message.run_id,
            message_id=message.id,
            user_id=ctx.p.user_id,
            helpful=body.helpful,
            comment=body.comment.strip(),
        )
        ctx.db.add(feedback)
    else:
        feedback.helpful = body.helpful
        feedback.comment = body.comment.strip()
    ctx.db.flush()
    audit.record(
        ctx.db,
        org_id=ctx.org_id,
        actor_kind="user",
        actor_id=ctx.p.user_id,
        action="knowledge.answer_feedback",
        resource_type="agent_message",
        resource_id=message.id,
        data={"helpful": body.helpful, "has_comment": bool(feedback.comment)},
    )
    return {
        "id": str(feedback.id),
        "message_id": str(feedback.message_id),
        "helpful": feedback.helpful,
        "comment": feedback.comment,
        "updated_at": feedback.updated_at.isoformat(),
    }


def _chat_ticket(ctx: Ctx, body: ChatIn) -> Ticket:
    if body.ticket_id:
        return ticket_for(ctx, body.ticket_id)
    subject = body.subject or body.message.split("\n")[0][:120]
    return ticket_svc.create(ctx.db, ctx.org_id, subject=subject if len(subject) >= 3 else "Support request", description=body.message,
                             source="chat", customer_id=ctx.p.customer_id if ctx.p.is_customer else None, requester_id=ctx.p.user_id)


def _ask_prepare(ctx: Ctx, body: ChatIn) -> tuple[Ticket, AgentRun]:
    """Ask chat: store the user's message on the ticket's chat run. No state machine, computer-use or approvals."""
    t = _chat_ticket(ctx, body)
    run = ask_chat.chat_run(ctx.db, ctx.org_id, t, ctx.p.user_id)
    if body.attachment_id:
        svc.select_attachment_for_run(ctx.db, run, t, body.attachment_id)
    svc.add_message(ctx.db, run, "user", body.message, author_id=ctx.p.user_id)
    return t, run


class FixIn(BaseModel):
    message: str = Field(min_length=5, max_length=2000)
    ticket_id: uuid.UUID | None = None
    url: str | None = Field(default=None, max_length=500)
    device_id: uuid.UUID | None = None


def _kb_role(ctx: Ctx) -> str:
    return "platform_owner" if ctx.p.is_platform else (ctx.p.role or "customer")


@router.get("/agents/windows-catalog")
def windows_catalog(ctx: Ctx = Depends(perm("agents.run"))):
    """v14: the Windows technician action catalog (what JoltIQ can DO on a PC), with risk, params, verify and undo."""
    from ..services.actions.windows_catalog import catalog

    return catalog()


@router.get("/agents/fix/targets")
def fix_targets(ctx: Ctx = Depends(perm("agents.run"))):
    """Where Fix it for me can act: the org's allowlisted sites and the caller's enrolled computers."""
    from ..models import Device
    from ..services import fix as fix_svc

    q = select(Device).where(Device.org_id == ctx.org_id, Device.status == "active")
    if ctx.p.is_customer:
        q = q.where(Device.owner_user_id == ctx.p.user_id)
    devices = [{"id": str(d.id), "name": d.hostname,
                "screen": "screen.capture" in (d.capabilities or [])} for d in ctx.db.execute(q).scalars()]
    return {"sites": fix_svc.sites(ctx.db, ctx.org_id), "devices": devices}


@router.post("/agents/fix", status_code=201)
def fix_it(body: FixIn, ctx: Ctx = Depends(perm("agents.run"))):
    """Fix it for me: create the ticket, an isolated browser session on an allowlisted site (or a desktop session on
    an enrolled PC), and the computer-use run. Nothing runs until the customer approves ONCE on the session page."""
    from ..services import browser_fix
    from ..services import fix as fix_svc
    from ..services import sessions as session_svc

    t = _chat_ticket(ctx, ChatIn(message=body.message, ticket_id=body.ticket_id))
    if body.device_id:
        from ..services import windows_autoplan, windows_plan

        d = fix_svc.device_ok(ctx.db, ctx.org_id, body.device_id)
        planned = windows_autoplan.build(ctx.db, ctx.org_id, body.message, set(d.capabilities or []))
        plan = planned.items
        provenance = {
            "planner": planned.planner,
            "planner_model": planned.model,
            "planner_live": planned.live,
            "planner_rationale": planned.rationale,
            "planner_fallback_reason": planned.fallback_reason,
        }
        s = session_svc.start(ctx.db, ctx.org_id, kind="desktop", owner_id=ctx.p.user_id, ticket_id=t.id, device_id=d.id,
                              request_details={"full_fix": {"goal": body.message[:2000], "device_name": d.hostname,
                                                            "windows_actions": plan, "high_risk": windows_plan.needs_ack(plan),
                                                            **provenance}})
        audit.record(ctx.db, org_id=ctx.org_id, actor_kind="user", actor_id=ctx.p.user_id, action="fix.requested",
                     resource_type="support_session", resource_id=s.id,
                     data={"channel": "desktop", "ticket_id": str(t.id),
                           "windows_actions": [{"action": i["action"], "params": i["params"]} for i in plan], **provenance})
        return {"ticket_id": str(t.id), "ticket_number": t.number, "session_id": str(s.id), "run_id": None, "channel": "desktop"}
    site = fix_svc.pick_site(ctx.db, ctx.org_id, body.url, body.message)
    host = (urlsplit(site["url"]).hostname or "").lower()
    s = session_svc.start(ctx.db, ctx.org_id, kind="browser", owner_id=ctx.p.user_id, ticket_id=t.id, allowed_domains=[host],
                          request_details={"full_fix": {"goal": body.message[:2000], "site": site["name"]}, "start_url": site["url"]})
    run = browser_fix.start(ctx.db, s, goal=body.message, user_id=ctx.p.user_id)
    audit.record(ctx.db, org_id=ctx.org_id, actor_kind="user", actor_id=ctx.p.user_id, action="fix.requested",
                 resource_type="support_session", resource_id=s.id, data={"channel": "browser", "site": site["name"], "ticket_id": str(t.id)})
    return {"ticket_id": str(t.id), "ticket_number": t.number, "session_id": str(s.id), "run_id": str(run.id), "channel": "browser",
            "site": site}


@router.post("/agents/chat", status_code=201)
def chat(body: ChatIn, ctx: Ctx = Depends(perm("agents.run"))):
    """Ask JoltIQ. mode="chat" (default): instant technician chat on a live model; refuses with a setup prompt when no
    model is connected. mode="troubleshoot": the durable troubleshooting run (state machine / approvals)."""
    if body.mode == "troubleshoot":
        return _troubleshoot(body, ctx)
    t, run = _ask_prepare(ctx, body)
    out = {"ticket_id": str(t.id), "ticket_number": t.number, "run_id": str(run.id), "state": run.state, "mode": "chat"}
    try:
        reply = ask_chat.reply(ctx.db, ctx.org_id, run, role=_kb_role(ctx))
    except ask_chat.AskNotConnected as e:
        return out | {"reply": None, "setup_required": True, "ai_notice": e.message}
    except AppError as e:
        return out | {"reply": None, "setup_required": False, "error": e.message, "ai_notice": None}
    return out | {"reply": reply, "setup_required": False, "ai_notice": None}


@router.post("/agents/chat/stream")
def chat_stream(body: ChatIn, ctx: Ctx = Depends(perm("agents.run"))):
    """Same as /agents/chat (mode=chat) but streams the answer as Server-Sent Events:
    meta → delta* → done | setup_required | error."""
    from fastapi.responses import StreamingResponse

    from ..db import session_scope

    t, run = _ask_prepare(ctx, body)
    meta = {"ticket_id": str(t.id), "ticket_number": t.number, "run_id": str(run.id), "state": run.state, "mode": "chat"}
    plan = ask_chat.stream_plan(ctx.db, ctx.org_id, run, role=_kb_role(ctx))
    ctx.db.commit()
    org_id = ctx.org_id

    def ev(name: str, data: dict) -> str:
        return f"event: {name}\ndata: {json.dumps(data)}\n\n"

    def gen():
        yield ev("meta", meta)
        if plan is None:
            yield ev("setup_required", {"ai_notice": ask_chat.SETUP_NOTICE})
            return
        parts: list[str] = []
        usage: dict = {}
        try:
            for kind, data in ask_chat.stream_tokens(plan):
                if kind == "delta":
                    parts.append(data["text"])
                    yield ev("delta", data)
                else:
                    usage = data
        except Exception as e:  # noqa: BLE001 - surfaced to the user in plain English, never a hang
            yield ev("error", {"message": f"The AI model didn't answer ({type(e).__name__}). Please try again."})
            return
        with session_scope(org_id) as db:
            saved = ask_chat.finish_stream(db, plan, "".join(parts), usage)
        yield ev("done", saved)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"cache-control": "no-cache", "x-accel-buffering": "no"})


def _troubleshoot(body: ChatIn, ctx: Ctx) -> dict:
    """Legacy/explicit: start or continue the durable troubleshooting run."""
    t = _chat_ticket(ctx, body)
    run = ctx.db.execute(select(AgentRun).where(AgentRun.ticket_id == t.id).order_by(AgentRun.created_at.desc())).scalars().first()
    if run is not None and run.state not in TERMINAL_RUN_STATES and (run.context or {}).get("mode") == "chat":
        run.paused_for = None
        run.state = "NEW"
        run.context = {**run.context, "mode": "troubleshoot"}
        svc.add_message(ctx.db, run, "user", body.message, author_id=ctx.p.user_id)
        svc.enqueue(ctx.db, "agent.advance", {"run_id": str(run.id)}, org_id=run.org_id)
        resumed = True
    elif run is None or run.state in TERMINAL_RUN_STATES:
        if body.ticket_id and run is not None:
            ticket_svc.comment(ctx.db, t, body.message, author_id=ctx.p.user_id)
        run = svc.start_run(ctx.db, ctx.org_id, ticket=t, started_by=ctx.p.user_id, first_message=body.message,
                            attachment_id=body.attachment_id)
        resumed = True
    else:
        if body.attachment_id:
            svc.select_attachment_for_run(ctx.db, run, t, body.attachment_id)
        resumed = svc.customer_message(ctx.db, run, body.message, ctx.p.user_id)["resumed"]
        ticket_svc.comment(ctx.db, t, body.message, author_id=ctx.p.user_id)
    reply = None
    if not resumed:
        reply = _quick_reply(ctx, run, body.message)
    from ..services.ai.router import ai_status

    notice = (reply or {}).get("ai_notice") or ai_status(ctx.db, ctx.org_id)["notice"]
    return {"ticket_id": str(t.id), "ticket_number": t.number, "run_id": str(run.id), "state": run.state, "reply": reply,
            "ai_notice": notice, "mode": "troubleshoot"}


def _quick_reply(ctx: Ctx, run: AgentRun, message: str) -> dict | None:
    """While a run is busy, answer the user's question from knowledge they are allowed to see (with citations)."""
    if ctx.p.role is None:
        raise Forbidden("Active organization role is required")
    return svc.quick_reply(ctx.db, ctx.org_id, run, message, role=ctx.p.role)


def _run_for(ctx: Ctx, run_id: uuid.UUID) -> AgentRun:
    run = ctx.db.get(AgentRun, run_id)
    if not run:
        raise NotFound("Run not found")
    if run.ticket_id:
        ticket_for(ctx, run.ticket_id)  # access check
    elif not ctx.p.can("agents.read_all"):
        raise NotFound("Run not found")
    return run


@router.get("/agents/runs")
def list_runs(ctx: Ctx = Depends(perm("agents.read_all")), pp=Depends(page_params), state: str | None = None):
    stmt = select(AgentRun).order_by(AgentRun.updated_at.desc())
    if state:
        stmt = stmt.where(AgentRun.state == state)
    return paginate(ctx.db, stmt, pp, lambda r: {"id": str(r.id), "ticket_id": str(r.ticket_id) if r.ticket_id else None, "state": r.state,
                                                 "paused_for": r.paused_for, "retries": r.retries, "cost_usd": round(r.cost_usd or 0, 4),
                                                 "updated_at": r.updated_at.isoformat()})


@router.get("/agents/runs/{run_id}")
def get_run(run_id: uuid.UUID, ctx: Ctx = Depends(get_ctx)):
    run = _run_for(ctx, run_id)
    detail = svc.run_detail(ctx.db, run, customer_view=not ctx.p.can("agents.read_all"))
    ratings = {
        str(row.message_id): row.helpful
        for row in ctx.db.execute(
            select(KnowledgeAnswerFeedback).where(
                KnowledgeAnswerFeedback.run_id == run.id,
                KnowledgeAnswerFeedback.user_id == ctx.p.user_id,
            )
        ).scalars()
    }
    for message in detail["messages"]:
        message["feedback"] = ratings.get(message["id"])
    return detail


@router.get("/agents/runs/{run_id}/verification-evidence")
def verification_evidence(run_id: uuid.UUID, ctx: Ctx = Depends(perm("agents.read_all"))):
    """Recompute every verification/rollback evidence hash for the run and compare it with the stored hash and the
    hash sealed into the tamper-evident audit chain (ADR 0017)."""
    from ..services import verification as vsvc

    run = _run_for(ctx, run_id)
    vrs = ctx.db.execute(select(VerificationRun).where(VerificationRun.run_id == run.id)
                         .order_by(VerificationRun.created_at)).scalars().all()
    items = [vsvc.recompute_evidence(ctx.db, vr) for vr in vrs]
    return {"run_id": str(run.id), "ok": all(i["ok"] for i in items) if items else None, "items": items,
            "rollback": (run.context or {}).get("rollback")}


@router.get("/tickets/{ticket_id}/runs")
def runs_for_ticket(ticket_id: uuid.UUID, ctx: Ctx = Depends(get_ctx)):
    ticket_for(ctx, ticket_id)
    rows = ctx.db.execute(select(AgentRun).where(AgentRun.ticket_id == ticket_id).order_by(AgentRun.created_at.desc())).scalars()
    return [svc.run_detail(ctx.db, r, customer_view=not ctx.p.can("agents.read_all")) for r in rows]


class StartIn(BaseModel):
    ticket_id: uuid.UUID
    attachment_id: uuid.UUID | None = None


@router.post("/agents/runs", status_code=201)
def start(body: StartIn, ctx: Ctx = Depends(perm("agents.run"))):
    t = ticket_for(ctx, body.ticket_id)
    run = svc.start_run(ctx.db, ctx.org_id, ticket=t, started_by=ctx.p.user_id, attachment_id=body.attachment_id)
    return {"run_id": str(run.id), "state": run.state}


class SymptomCheckIn(BaseModel):
    result: Literal["pdf_opened", "still_crashes", "cannot_test"]


@router.post("/agents/runs/{run_id}/symptom-check")
def symptom_check(run_id: uuid.UUID, body: SymptomCheckIn, ctx: Ctx = Depends(get_ctx)):
    run = _run_for(ctx, run_id)
    ticket = ctx.db.get(Ticket, run.ticket_id) if run.ticket_id else None
    session = ctx.db.execute(select(SupportSession).where(
        SupportSession.run_id == run.id, SupportSession.kind == "desktop",
    )).scalars().first()
    owns_ticket = bool(ticket and (
        ticket.requester_id == ctx.p.user_id or (ctx.p.customer_id and ticket.customer_id == ctx.p.customer_id)
    ))
    if not ctx.p.is_customer or not owns_ticket or not session or session.owner_id != ctx.p.user_id:
        raise Forbidden("Only the customer who approved this desktop session can submit the symptom check")
    return svc.record_symptom_response(ctx.db, run, body.result, ctx.p.user_id)


@router.post("/agents/runs/{run_id}/cancel")
def cancel(run_id: uuid.UUID, ctx: Ctx = Depends(get_ctx)):
    run = _run_for(ctx, run_id)
    if run.state in TERMINAL_RUN_STATES:
        return {"state": run.state}
    from ..models.enums import RunState

    svc.transition(ctx.db, run, RunState.CANCELLED, f"cancelled by {ctx.p.role}")
    run.paused_for = None
    svc._release_ai_control(ctx.db, run)
    for a in ctx.db.execute(select(Approval).where(Approval.run_id == run.id, Approval.status == ApprovalStatus.PENDING)).scalars():
        a.status = ApprovalStatus.CANCELLED
    return {"state": run.state}


class EscalateIn(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)


@router.post("/agents/runs/{run_id}/escalate")
def escalate(run_id: uuid.UUID, body: EscalateIn, ctx: Ctx = Depends(get_ctx)):
    run = _run_for(ctx, run_id)
    if run.state in TERMINAL_RUN_STATES:
        raise AppError("Run already finished", code="invalid_state")
    svc._escalate(ctx.db, run, f"Manual escalation by {ctx.p.role}: {body.reason}")
    return {"state": run.state}


# ---------------- Approvals ----------------
def _approval_rollback(a: Approval) -> dict | None:
    from sqlalchemy.orm import object_session

    from ..models import AgentPlan

    db = object_session(a)
    plan = db.get(AgentPlan, a.plan_id) if db is not None and a.plan_id else None
    if plan is None:
        return None
    av = dict(plan.rollback_availability or {})
    if not av:
        return None
    av["steps"] = [r["action"] for r in (plan.rollback_steps or [])]
    return av


def ser_approval(a: Approval) -> dict:
    return {"rollback": _approval_rollback(a), "id": str(a.id), "status": a.status, "risk": a.risk, "action": a.action, "target": a.target, "params": a.params,
            "plan_id": str(a.plan_id) if a.plan_id else None, "step_id": str(a.step_id) if a.step_id else None,
            "reason": a.reason, "effects": a.effects, "approver_roles": a.approver_roles, "run_id": str(a.run_id) if a.run_id else None,
            "ticket_id": str(a.ticket_id) if a.ticket_id else None, "session_id": str(a.session_id) if a.session_id else None,
            "expires_at": a.expires_at.isoformat(), "decided_at": a.decided_at.isoformat() if a.decided_at else None,
            "decided_by": str(a.decided_by) if a.decided_by else None,
            "requested_for_user_id": str(a.requested_for_user_id) if a.requested_for_user_id else None, "decision_comment": a.decision_comment,
            "created_at": a.created_at.isoformat()}


@router.get("/approvals")
def list_approvals(ctx: Ctx = Depends(get_ctx), pp=Depends(page_params), status: str | None = "PENDING"):
    stmt = select(Approval).order_by(Approval.created_at.desc())
    if status:
        stmt = stmt.where(Approval.status == status)
    if ctx.p.is_customer:
        mine = select(Ticket.id).where((Ticket.requester_id == ctx.p.user_id) | (Ticket.customer_id == ctx.p.customer_id))
        stmt = stmt.where(Approval.ticket_id.in_(mine), Approval.approver_roles.contains(["customer"]))
    else:
        ctx.p.require("tickets.read_all")
    return paginate(ctx.db, stmt, pp, ser_approval)


@router.get("/approvals/{aid}")
def get_approval(aid: uuid.UUID, ctx: Ctx = Depends(get_ctx)):
    a = ctx.db.get(Approval, aid)
    if not a:
        raise NotFound("Approval not found")
    if a.ticket_id:
        ticket_for(ctx, a.ticket_id)
    out = ser_approval(a)
    out["can_decide"] = approval_svc.can_decide(ctx.db, a, user_id=ctx.p.user_id, role=ctx.p.role, customer_id=ctx.p.customer_id)[0]
    return out


class DecideIn(BaseModel):
    decision: str = Field(pattern="^(approve|deny)$")
    comment: str = Field(default="", max_length=2000)


@router.post("/approvals/{aid}/decision")
def decide(aid: uuid.UUID, body: DecideIn, ctx: Ctx = Depends(perm("approvals.decide"))):
    a = ctx.db.get(Approval, aid)
    if not a:
        raise NotFound("Approval not found")
    if a.ticket_id:
        ticket_for(ctx, a.ticket_id)
    a = approval_svc.decide(ctx.db, aid, approve=body.decision == "approve", user_id=ctx.p.user_id, role=ctx.p.role,
                            customer_id=ctx.p.customer_id, comment=body.comment)
    if a.run_id:
        run = ctx.db.get(AgentRun, a.run_id)
        if run:
            svc.resume(ctx.db, run, f"approval {a.status}", ("approval",))
    return ser_approval(a)


# ---------------- Research ----------------
class ResearchIn(BaseModel):
    query: str = Field(min_length=3, max_length=1000)
    ticket_id: uuid.UUID | None = None


def ser_research(ctx: Ctx, rq: ResearchQuery) -> dict:
    srcs = ctx.db.execute(select(ResearchSource).where(ResearchSource.query_id == rq.id)).scalars().all()
    finds = ctx.db.execute(select(ResearchFinding).where(ResearchFinding.query_id == rq.id)).scalars().all()
    return {"id": str(rq.id), "query": rq.redacted_query, "provider": rq.provider, "live": rq.live, "status": rq.status, "error": rq.error,
            "redactions": rq.redactions, "created_at": rq.created_at.isoformat(),
            "sources": [{"id": str(s.id), "url": s.url, "domain": s.domain, "title": s.title, "retrieved_at": s.retrieved_at.isoformat(),
                         "excerpt": s.excerpt, "source_kind": s.source_kind, "reliability": s.reliability, "relevance": s.relevance,
                         "applicable_versions": s.applicable_versions, "fetched": s.fetched, "flags": s.injection_flags} for s in srcs],
            "findings": [{"kind": f.kind, "statement": f.statement, "source_ids": f.source_ids, "confidence": f.confidence} for f in finds]}


@router.post("/research", status_code=201)
def run_research(body: ResearchIn, ctx: Ctx = Depends(perm("research.run"))):
    t = ticket_for(ctx, body.ticket_id) if body.ticket_id else None
    rq = research_svc.run(ctx.db, ctx.org_id, body.query, ticket=t, actor_id=ctx.p.user_id)
    return ser_research(ctx, rq)


@router.get("/research/status")
def research_status(ctx: Ctx = Depends(perm("research.run"))):
    return research_svc.provider_status()


@router.get("/research/{qid}")
def get_research(qid: uuid.UUID, ctx: Ctx = Depends(perm("research.run"))):
    rq = ctx.db.get(ResearchQuery, qid)
    if not rq:
        raise NotFound("Research not found")
    if rq.ticket_id:
        ticket_for(ctx, rq.ticket_id)
    elif not ctx.p.is_staff:
        raise Forbidden("Not allowed")
    return ser_research(ctx, rq)


# ---------------- Verifications ----------------
@router.get("/verifications/{vid}")
def get_verification(vid: uuid.UUID, ctx: Ctx = Depends(get_ctx)):
    v = ctx.db.get(VerificationRun, vid)
    if not v:
        raise NotFound("Verification not found")
    if v.run_id:
        _run_for(ctx, v.run_id)
    ev = ctx.db.execute(select(VerificationEvidence).where(VerificationEvidence.verification_id == v.id)).scalars()
    return {"id": str(v.id), "status": v.status, "postconditions": v.postconditions, "details": v.details, "pre_state": v.pre_state if ctx.p.is_staff else {},
            "post_state": v.post_state, "evidence": [{"kind": e.kind, "summary": e.summary, "data": e.data if ctx.p.is_staff else {}} for e in ev]}
