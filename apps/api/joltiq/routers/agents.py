"""AI chat + agent runs + research + approvals + verifications."""
from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..core.errors import AppError, Forbidden, NotFound
from ..deps import Ctx, get_ctx, perm
from ..models import AgentRun, Approval, ResearchFinding, ResearchQuery, ResearchSource, Ticket, VerificationEvidence, VerificationRun
from ..models.enums import ApprovalStatus, TERMINAL_RUN_STATES
from ..services import agent as svc
from ..services import approvals as approval_svc
from ..services import research as research_svc
from ..services import tickets as ticket_svc
from ..services.ai import router as ai
from ..services.ai import schemas as S
from ..services.ai.fixture import MARK
from ..services.knowledge import service as kb
from .common import page_params, paginate
from .tickets import ticket_for

router = APIRouter(tags=["agents"])


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)
    ticket_id: uuid.UUID | None = None
    subject: str | None = Field(default=None, max_length=300)


@router.post("/agents/chat", status_code=201)
def chat(body: ChatIn, ctx: Ctx = Depends(perm("agents.run"))):
    """Customer (or technician) talks to the AI. Creates/links a ticket and starts or continues the durable run."""
    if body.ticket_id:
        t = ticket_for(ctx, body.ticket_id)
    else:
        subject = body.subject or body.message.split("\n")[0][:120]
        t = ticket_svc.create(ctx.db, ctx.org_id, subject=subject if len(subject) >= 3 else "Support request", description=body.message,
                              source="chat", customer_id=ctx.p.customer_id if ctx.p.is_customer else None, requester_id=ctx.p.user_id)
    run = ctx.db.execute(select(AgentRun).where(AgentRun.ticket_id == t.id).order_by(AgentRun.created_at.desc())).scalars().first()
    if run is None or run.state in TERMINAL_RUN_STATES:
        if body.ticket_id and run is not None:
            ticket_svc.comment(ctx.db, t, body.message, author_id=ctx.p.user_id)
        run = svc.start_run(ctx.db, ctx.org_id, ticket=t, started_by=ctx.p.user_id, first_message=body.message)
        resumed = True
    else:
        resumed = svc.customer_message(ctx.db, run, body.message, ctx.p.user_id)["resumed"]
        ticket_svc.comment(ctx.db, t, body.message, author_id=ctx.p.user_id)
    reply = None
    if not resumed:
        reply = _quick_reply(ctx, run, body.message)
    return {"ticket_id": str(t.id), "ticket_number": t.number, "run_id": str(run.id), "state": run.state, "reply": reply}


def _quick_reply(ctx: Ctx, run: AgentRun, message: str) -> dict | None:
    """While a run is busy, answer the user's question from knowledge they are allowed to see (with citations)."""
    hits = kb.search(ctx.db, ctx.org_id, message, role=ctx.p.role, limit=3)
    ev = [{"id": f"E{i + 1}", "citation": h["citation"], "text": h["text"][:800]} for i, h in enumerate(hits)]
    try:
        out, mc = ai.call(ctx.db, ctx.org_id, "chat", system=svc.SYSTEM_BASE, run_id=run.id, output=S.ChatReply,
                          messages=[{"role": "user", "content": f"<untrusted>\n{MARK} {json.dumps({'question': message, 'evidence': ev, 'state': run.state})}\n</untrusted>"}])
    except AppError as e:
        return {"content": f"(AI unavailable: {e.message})", "citations": []}
    m = svc.add_message(ctx.db, run, "assistant", out.reply, data={"citations": [e for e in ev if e["id"] in out.cited_evidence_ids], "live_model": mc.live})
    return {"id": str(m.id), "content": out.reply, "citations": m.data["citations"], "live_model": mc.live}


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


@router.get("/tickets/{ticket_id}/runs")
def runs_for_ticket(ticket_id: uuid.UUID, ctx: Ctx = Depends(get_ctx)):
    ticket_for(ctx, ticket_id)
    rows = ctx.db.execute(select(AgentRun).where(AgentRun.ticket_id == ticket_id).order_by(AgentRun.created_at.desc())).scalars()
    return [svc.run_detail(ctx.db, r, customer_view=not ctx.p.can("agents.read_all")) for r in rows]


class StartIn(BaseModel):
    ticket_id: uuid.UUID


@router.post("/agents/runs", status_code=201)
def start(body: StartIn, ctx: Ctx = Depends(perm("agents.run"))):
    t = ticket_for(ctx, body.ticket_id)
    run = svc.start_run(ctx.db, ctx.org_id, ticket=t, started_by=ctx.p.user_id)
    return {"run_id": str(run.id), "state": run.state}


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
def ser_approval(a: Approval) -> dict:
    return {"id": str(a.id), "status": a.status, "risk": a.risk, "action": a.action, "target": a.target, "params": a.params,
            "reason": a.reason, "effects": a.effects, "approver_roles": a.approver_roles, "run_id": str(a.run_id) if a.run_id else None,
            "ticket_id": str(a.ticket_id) if a.ticket_id else None, "session_id": str(a.session_id) if a.session_id else None,
            "expires_at": a.expires_at.isoformat(), "decided_at": a.decided_at.isoformat() if a.decided_at else None,
            "decided_by": str(a.decided_by) if a.decided_by else None, "decision_comment": a.decision_comment,
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
