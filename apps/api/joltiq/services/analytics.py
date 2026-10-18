"""Tenant analytics computed from source-of-truth tables (no separate warehouse)."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import AgentRun, Approval, ModelCall, SupportSession, Ticket, VerificationRun, WorkerHeartbeat, utcnow


def dashboard(db: Session, days: int = 30) -> dict:
    since = utcnow() - timedelta(days=days)
    runs = db.execute(select(AgentRun.state, func.count()).where(AgentRun.created_at >= since).group_by(AgentRun.state)).all()
    by_state = {s: n for s, n in runs}
    finished = sum(n for s, n in by_state.items() if s in ("RESOLVED", "ESCALATED", "FAILED", "PARTIALLY_RESOLVED", "CANCELLED"))
    resolved = by_state.get("RESOLVED", 0)
    vr = db.execute(select(VerificationRun.status, func.count()).where(VerificationRun.created_at >= since).group_by(VerificationRun.status)).all()
    lat = db.execute(select(func.avg(func.extract("epoch", Approval.decided_at - Approval.created_at)))
                     .where(Approval.decided_at.isnot(None), Approval.created_at >= since)).scalar()
    sess = db.execute(select(func.sum(func.extract("epoch", SupportSession.terminated_at - SupportSession.consented_at)), func.count())
                      .where(SupportSession.consented_at.isnot(None), SupportSession.terminated_at.isnot(None), SupportSession.created_at >= since)).one()
    cost = db.execute(select(ModelCall.provider, func.sum(ModelCall.cost_usd), func.sum(ModelCall.input_tokens + ModelCall.output_tokens),
                             func.count(), func.sum(func.cast(~ModelCall.ok, type_=_int())))
                      .where(ModelCall.created_at >= since).group_by(ModelCall.provider)).all()
    total_cost = float(sum(c[1] or 0 for c in cost))
    tickets = db.execute(select(Ticket.status, func.count()).where(Ticket.created_at >= since).group_by(Ticket.status)).all()
    breached = db.execute(select(func.count()).select_from(Ticket).where(Ticket.resolution_due < utcnow(),
                          Ticket.status.notin_(["RESOLVED", "CLOSED", "CANCELLED"]))).scalar()
    daily = db.execute(select(func.date_trunc("day", AgentRun.created_at).label("d"), AgentRun.state, func.count())
                       .where(AgentRun.created_at >= since).group_by("d", AgentRun.state).order_by("d")).all()
    series: dict[str, dict] = {}
    for d, s, n in daily:
        k = d.date().isoformat()
        series.setdefault(k, {"date": k, "resolved": 0, "escalated": 0, "other": 0})
        series[k]["resolved" if s == "RESOLVED" else "escalated" if s in ("ESCALATED", "PARTIALLY_RESOLVED") else "other"] += n
    return {
        "window_days": days,
        "runs": {"total": sum(by_state.values()), "by_state": by_state, "finished": finished},
        "verified_resolution_rate": round(resolved / finished, 4) if finished else None,
        "escalation_rate": round((by_state.get("ESCALATED", 0) + by_state.get("PARTIALLY_RESOLVED", 0)) / finished, 4) if finished else None,
        "verifications": {s: n for s, n in vr},
        "approval_latency_seconds_avg": round(float(lat), 1) if lat else None,
        "session_minutes_total": round(float(sess[0] or 0) / 60, 1), "sessions": sess[1],
        "model_costs": [{"provider": c[0], "cost_usd": round(float(c[1] or 0), 4), "tokens": int(c[2] or 0), "calls": c[3], "failures": int(c[4] or 0)} for c in cost],
        "cost_total_usd": round(total_cost, 4),
        "cost_per_verified_resolution_usd": round(total_cost / resolved, 4) if resolved else None,
        "tickets": {s: n for s, n in tickets}, "sla_breached_open": breached,
        "daily": list(series.values()),
    }


def worker_health(db: Session) -> list[dict]:
    rows = db.execute(select(WorkerHeartbeat).order_by(WorkerHeartbeat.kind)).scalars()
    now = utcnow()
    return [{"worker_id": w.worker_id, "kind": w.kind, "last_beat": w.last_beat.isoformat(), "healthy": (now - w.last_beat).total_seconds() < 60,
             "info": w.info} for w in rows]


def _int():
    from sqlalchemy import Integer

    return Integer()
