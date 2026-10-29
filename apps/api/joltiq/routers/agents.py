"""AI chat + agent runs + research + approvals + verifications."""
from __future__ import annotations

import json
import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..core.errors import AppError, Forbidden, NotFound
from ..deps import Ctx, get_ctx, perm
from ..models import (
    AgentPlan,
    AgentRun,
    Approval,
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
from ..services import ask_chat
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


@router.get("/agents/ai-status")
def ai_status_endpoint(ctx: Ctx = Depends(perm("agents.run"))):
    result = ask_chat.status(ctx.db, ctx.org_id)
    return {"live_chat": result["connected"], "provider": result["provider"], "model": result["model"],
            "source": result["source"], "notice": result["notice"], "can_connect": ctx.p.can("models.manage")}


def _chat_ticket(ctx: Ctx, body: ChatIn) -> Ticket:
    if body.ticket_id:
        return ticket_for(ctx, body.ticket_id)
    subject = body.subject or body.message.split("\n")[0][:120]
    return ticket_svc.create(ctx.db, ctx.org_id, subject=subject if len(subject) >= 3 else "Support request",
                             description=body.message, source="chat",
                             customer_id=ctx.p.customer_id if ctx.p.is_customer else None, requester_id=ctx.p.user_id)


def _ask_prepare(ctx: Ctx, body: ChatIn) -> tuple[Ticket, AgentRun]:
    ticket = _chat_ticket(ctx, body)
    run = ask_chat.chat_run(ctx.db, ctx.org_id, ticket, ctx.p.user_id)
    if body.attachment_id:
        svc.select_attachment_for_run(ctx.db, run, ticket, body.attachment_id)
    svc.add_message(ctx.db, run, "user", body.message, author_id=ctx.p.user_id)
    return ticket, run


@router.post("/agents/chat", status_code=201)
def chat(body: ChatIn, ctx: Ctx = Depends(perm("agents.run"))):
    if body.mode == "troubleshoot":
        return _troubleshoot(body, ctx)
    ticket, run = _ask_prepare(ctx, body)
    result = {"ticket_id": str(ticket.id), "ticket_number": ticket.number, "run_id": str(run.id),
              "state": run.state, "mode": "chat"}
    try:
        answer = ask_chat.reply(ctx.db, ctx.org_id, run)
    except ask_chat.AskNotConnected as exc:
        return result | {"reply": None, "setup_required": True, "ai_notice": exc.message}
    except AppError as exc:
        return result | {"reply": None, "setup_required": False, "ai_notice": None, "error": exc.message}
    return result | {"reply": answer, "setup_required": False, "ai_notice": None}


@router.post("/agents/chat/stream")
def chat_stream(body: ChatIn, ctx: Ctx = Depends(perm("agents.run"))):
    from fastapi.responses import StreamingResponse
    from ..db import session_scope

    if body.mode != "chat":
        raise AppError("Streaming is available only for Ask chat", code="validation_error")
    ticket, run = _ask_prepare(ctx, body)
    meta = {"ticket_id": str(ticket.id), "ticket_number": ticket.number, "run_id": str(run.id),
            "state": run.state, "mode": "chat"}
    plan = ask_chat.stream_plan(ctx.db, ctx.org_id, run)
    ctx.db.commit()
    org_id = ctx.org_id

    def event(name: str, data: dict) -> str:
        return f"event: {name}\ndata: {json.dumps(data)}\n\n"

    def generate():
        yield event("meta", meta)
        if plan is None:
            yield event("setup_required", {"ai_notice": ask_chat.SETUP_NOTICE})
            return
        parts, usage = [], {}
        try:
            for kind, data in ask_chat.stream_tokens(plan):
                if kind == "delta":
                    parts.append(data["text"])
                    yield event("delta", data)
                else:
                    usage = data
        except Exception as exc:
            yield event("error", {"message": f"The AI model did not answer ({type(exc).__name__}). Please try again."})
            return
        with session_scope(org_id) as db:
            saved = ask_chat.finish_stream(db, plan, "".join(parts), usage)
        yield event("done", saved)

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"cache-control": "no-cache", "x-accel-buffering": "no"})


def _troubleshoot(body: ChatIn, ctx: Ctx) -> dict:
    ticket = _chat_ticket(ctx, body)
    run = ctx.db.execute(select(AgentRun).where(AgentRun.ticket_id == ticket.id).order_by(AgentRun.created_at.desc())).scalars().first()
    if run is not None and run.state not in TERMINAL_RUN_STATES and (run.context or {}).get("mode") == "chat":
        run.paused_for = None
        run.state = "NEW"
        run.context = {**(run.context or {}), "mode": "troubleshoot"}
        svc.add_message(ctx.db, run, "user", body.message, author_id=ctx.p.user_id)
        svc.enqueue(ctx.db, "agent.advance", {"run_id": str(run.id)}, org_id=run.org_id)
        resumed = True
    elif run is None or run.state in TERMINAL_RUN_STATES:
        if body.ticket_id and run is not None:
            ticket_svc.comment(ctx.db, ticket, body.message, author_id=ctx.p.user_id)
        run = svc.start_run(ctx.db, ctx.org_id, ticket=ticket, started_by=ctx.p.user_id,
                            first_message=body.message, attachment_id=body.attachment_id)
        resumed = True
    else:
        if body.attachment_id:
            svc.select_attachment_for_run(ctx.db, run, ticket, body.attachment_id)
        resumed = svc.customer_message(ctx.db, run, body.message, ctx.p.user_id)["resumed"]
        ticket_svc.comment(ctx.db, ticket, body.message, author_id=ctx.p.user_id)
    answer = None if resumed else _quick_reply(ctx, run, body.message)
    return {"ticket_id": str(ticket.id), "ticket_number": ticket.number, "run_id": str(run.id),
            "state": run.state, "reply": answer, "ai_notice": (answer or {}).get("ai_notice"), "mode": "troubleshoot"}


def _quick_reply(ctx: Ctx, run: AgentRun, message: str) -> dict | None:
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
    return svc.run_detail(ctx.db, run, customer_view=not ctx.p.can("agents.read_all"))


@router.get("/agents/runs/{run_id}/verification-evidence")
def get_run_verification_evidence(run_id: uuid.UUID, ctx: Ctx = Depends(perm("agents.read_all"))):
    from ..services import verification

    run = _run_for(ctx, run_id)
    rows = ctx.db.execute(
        select(VerificationRun)
        .where(VerificationRun.run_id == run.id)
        .order_by(VerificationRun.created_at)
    ).scalars().all()
    items = [verification.recompute_evidence(ctx.db, row) for row in rows]
    return {"ok": all(item["ok"] for item in items) if items else None, "items": items}


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
def ser_approval(a: Approval, ctx: Ctx) -> dict:
    plan = ctx.db.get(AgentPlan, a.plan_id) if a.plan_id else None
    rollback = dict(plan.rollback_availability or {}) if plan else {}
    if plan:
        rollback.setdefault("steps", len(plan.rollback_steps or []))
    can_decide = approval_svc.can_decide(
        ctx.db, a, user_id=ctx.p.user_id, role=ctx.p.role, customer_id=ctx.p.customer_id
    )[0]
    return {"id": str(a.id), "status": a.status, "risk": a.risk, "action": a.action, "target": a.target, "params": a.params,
            "plan_id": str(a.plan_id) if a.plan_id else None, "step_id": str(a.step_id) if a.step_id else None,
            "reason": a.reason, "effects": a.effects, "approver_roles": a.approver_roles, "run_id": str(a.run_id) if a.run_id else None,
            "ticket_id": str(a.ticket_id) if a.ticket_id else None, "session_id": str(a.session_id) if a.session_id else None,
            "expires_at": a.expires_at.isoformat(), "decided_at": a.decided_at.isoformat() if a.decided_at else None,
            "decided_by": str(a.decided_by) if a.decided_by else None, "decision_comment": a.decision_comment,
            "created_at": a.created_at.isoformat(), "rollback": rollback or None, "can_decide": can_decide}


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
    return paginate(ctx.db, stmt, pp, lambda approval: ser_approval(approval, ctx))


@router.get("/approvals/{aid}")
def get_approval(aid: uuid.UUID, ctx: Ctx = Depends(get_ctx)):
    a = ctx.db.get(Approval, aid)
    if not a:
        raise NotFound("Approval not found")
    if a.ticket_id:
        ticket_for(ctx, a.ticket_id)
    return ser_approval(a, ctx)


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
    return ser_approval(a, ctx)


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
