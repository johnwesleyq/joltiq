"""Action gateway: the single path through which any AI-proposed action reaches a real system.

Order of checks for every step, at execution time (not just at planning time):
  1. registry validation + policy re-evaluation (consent may have expired / been narrowed / session stopped)
  2. approval verification + consumption (signature, step hash, expiry, single use)
  3. exclusive control lease for the AI (a human holding control blocks the AI)
  4. idempotent execution record, dispatch to channel, result capture, audit
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.errors import Conflict, Unavailable
from ..models import ActionExecution, AgentPlan, AgentRun, Approval, Device, PlanStep, SupportSession, Ticket, utcnow
from ..models.enums import ApprovalStatus
from . import approvals, audit, browser_client, policy, sessions
from . import tickets as ticket_svc
from .actions.registry import ACTIONS
from .devices import queue_command


@dataclass
class Outcome:
    status: str  # succeeded|failed|pending_device|auth_required|blocked|human_control
    detail: str = ""
    execution: ActionExecution | None = None
    data: dict[str, Any] | None = None


def execute_step(db: Session, run: AgentRun, plan: AgentPlan, step: PlanStep) -> Outcome:
    s = db.get(SupportSession, uuid_or_none(step.target.get("session_id"))) if step.target.get("session_id") else None
    device = db.get(Device, uuid_or_none(step.target.get("device_id"))) if step.target.get("device_id") else None
    dec = policy.evaluate(db, run.org_id, step.action, step.params, session=s, device=device, for_execution=True)
    if dec.effect == "deny":
        return _blocked(db, run, step, s, dec, "; ".join(dec.reasons))
    approval = None
    if dec.effect == "require_approval":
        approval = db.execute(select(Approval).where(Approval.step_id == step.id, Approval.status == ApprovalStatus.APPROVED)
                              .order_by(Approval.created_at.desc())).scalars().first()
        if approval is None:
            return _blocked(db, run, step, s, dec, "no approval for this step")
        ok, why = approvals.verify_and_consume(db, approval, step, plan.plan_hash)
        if not ok:
            audit.security_event(db, "approval_bypass_blocked", org_id=run.org_id, severity="warn", data={"step": str(step.id), "why": why})
            return _blocked(db, run, step, s, dec, f"approval check failed: {why}")
    spec_channel = ACTIONS[step.action].channel
    if s is not None and spec_channel in ("browser", "desktop"):
        try:
            sessions.acquire_lease(db, s, "ai", reason=f"executing {step.action}")
        except Conflict as e:
            return Outcome("human_control", str(e.message))
    key = f"{run.id}:{step.id}:{run.retries}"
    ex = db.execute(select(ActionExecution).where(ActionExecution.idempotency_key == key)).scalar_one_or_none()
    if ex and ex.status in ("succeeded", "running"):
        return Outcome("succeeded" if ex.status == "succeeded" else "pending_device", "already executed (idempotent)", ex)
    if ex is None:
        ex = ActionExecution(org_id=run.org_id, run_id=run.id, step_id=step.id, approval_id=approval.id if approval else None,
                             session_id=s.id if s else None, action=step.action, target=step.target, params=dec.params, risk=dec.risk,
                             policy_decision=dec.as_dict(), status="running", idempotency_key=key, started_at=utcnow())
        db.add(ex)
        db.flush()
    try:
        if step.action == "ticket.comment":
            t = db.get(Ticket, run.ticket_id) if run.ticket_id else None
            if t is None:
                return _done(db, run, ex, s, "failed", "run has no ticket")
            c = ticket_svc.comment(db, t, dec.params["body"], author_kind="ai")
            ex.result = {"comment_id": str(c.id)}
            return _done(db, run, ex, s, "succeeded")
        if spec_channel == "browser":
            if s is None or not s.worker_session_ref:  # policy already denies this; defensive
                return _done(db, run, ex, s, "failed", "no running browser session")
            res = browser_client.act(s.worker_session_ref, step.action, dec.params, actor="ai")
            ex.result = {k: v for k, v in res.items() if k != "page"} | {"page": {"url": (res.get("page") or {}).get("url")}}
            if res.get("auth_required"):
                sessions.awaiting_user_auth(db, s, res.get("kind") or "login")
                ex.status = "pending"
                return Outcome("auth_required", res.get("kind") or "login", ex)
            if res.get("secret_field"):
                sessions.awaiting_user_auth(db, s, "secret_entry")
                ex.status = "pending"
                return Outcome("auth_required", "secret_entry", ex)
            sessions.event(db, s, "action", actor_kind="ai", data={"action": step.action, "params": dec.params, "ok": res.get("ok")})
            return _done(db, run, ex, s, "succeeded" if res.get("ok") else "failed", res.get("error", ""))
        if spec_channel == "desktop":
            if s is None or device is None:  # policy already denies this; defensive
                return _done(db, run, ex, s, "failed", "no desktop session/device")
            cmd = queue_command(db, device, action=step.action, params=dec.params, session=s, execution_id=ex.id,
                                procedure_hash=plan.procedure_hash)
            ex.result = {"command_id": str(cmd.id)}
            sessions.event(db, s, "action_dispatched", actor_kind="ai", data={"action": step.action, "params": dec.params})
            return Outcome("pending_device", "waiting for device", ex)
        return _done(db, run, ex, s, "failed", f"no executor for {step.action}")
    except Unavailable as e:
        return _done(db, run, ex, s, "failed", e.message)
    except Conflict as e:
        ex.status = "pending"
        return Outcome("blocked", e.message, ex)


def _done(db, run, ex: ActionExecution, s, status: str, error: str = "") -> Outcome:
    ex.status = status
    ex.error = error or None
    ex.finished_at = utcnow()
    audit.record(db, org_id=run.org_id, actor_kind="ai", actor_id=run.id, action=f"action.{status}", resource_type="action_execution",
                 resource_id=ex.id, data={"action": ex.action, "params": ex.params, "risk": ex.risk, "error": error[:300] if error else None})
    return Outcome(status, error, ex)


def _blocked(db, run, step, s, dec, why: str) -> Outcome:
    audit.record(db, org_id=run.org_id, actor_kind="system", actor_id=None, action="action.blocked", resource_type="plan_step",
                 resource_id=step.id, data={"action": step.action, "reason": why, "policy": dec.as_dict()})
    if s is not None:
        sessions.event(db, s, "action_blocked", actor_kind="system", data={"action": step.action, "reason": why})
    return Outcome("blocked", why)


def uuid_or_none(v):
    import uuid

    try:
        return uuid.UUID(str(v)) if v else None
    except ValueError:
        return None
