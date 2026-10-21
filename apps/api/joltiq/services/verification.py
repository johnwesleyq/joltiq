"""Independent outcome verification.

The verifier never trusts the executor's own report ("click succeeded"). For every executed step it evaluates typed
postconditions by observing the target system through a SEPARATE read path:
  * browser  → a fresh page in the session context re-loads the URL from the server (persisted state)
  * desktop  → separate read-only device commands (service.status, process.list, config.read)
  * internal → database re-fetch
  * http     → SSRF-guarded HTTP request
Result per check: VERIFIED | FAILED | UNKNOWN. Aggregate: all VERIFIED → VERIFIED; any FAILED & some VERIFIED → PARTIAL;
any FAILED → FAILED; otherwise UNKNOWN. UNKNOWN is never treated as success.
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import (
    ActionExecution,
    AgentPlan,
    Device,
    DeviceCommand,
    PlanStep,
    SupportSession,
    TicketComment,
    VerificationEvidence,
    VerificationRun,
    utcnow,
)
from ..models.enums import VerificationStatus as V
from ..security.redaction import redact_obj
from . import browser_client
from .realtime import publish

DEVICE_CHECKS = {
    "device_service_running": ("service.status", lambda p: {"name": p["name"]}),
    "device_process_responding": ("process.list", lambda p: {"name_filter": p["process"]}),
    "device_config_equals": ("config.read", lambda p: _addin_key(p)),
    "device_addin_restore_matches": ("office.addin.verify_restored", lambda p: {"source_command_id": p["source_command_id"]}),
}
SUPPORTED = {"browser_readback", "http_ok", "ticket_comment_exists", "ticket_field_equals", *DEVICE_CHECKS}


def _addin_key(p: dict) -> dict:
    if "addin_progid" in p:
        return {"key": f"HKCU\\Software\\Microsoft\\Office\\{p['app']}\\Addins\\{p['addin_progid']}", "value": "LoadBehavior"}
    return {"key": p["key"], "value": p["value"]}


def aggregate(statuses: list[str]) -> str:
    if not statuses:
        return V.UNKNOWN
    if all(s == V.VERIFIED for s in statuses):
        return V.VERIFIED
    if any(s == V.FAILED for s in statuses):
        return V.PARTIAL if any(s == V.VERIFIED for s in statuses) else V.FAILED
    return V.PARTIAL if any(s == V.VERIFIED for s in statuses) else V.UNKNOWN


def restore_readback_verified(details: list[dict]) -> bool:
    """A restore is proven only by its separate local-journal/live-registry comparison."""
    return any(
        detail.get("type") == "device_addin_restore_matches" and detail.get("status") == V.VERIFIED
        for detail in details
    )


def capture_pre_state(db: Session, ex: ActionExecution, postconditions: list[dict], session: SupportSession | None) -> dict[str, Any]:
    pre: dict[str, Any] = {}
    for i, pc in enumerate(postconditions):
        if pc.get("type") == "browser_readback" and session and session.worker_session_ref:
            p = pc.get("params", {})
            try:
                pre[str(i)] = browser_client.readback(session.worker_session_ref, p["url"], p.get("selector"))
            except Exception as e:  # noqa: BLE001
                pre[str(i)] = {"error": str(e)[:200]}
    return redact_obj(pre)


def start(db: Session, ex: ActionExecution, postconditions: list[dict], pre_state: dict, session: SupportSession | None,
          device: Device | None) -> VerificationRun:
    vr = VerificationRun(org_id=ex.org_id, execution_id=ex.id, run_id=ex.run_id, postconditions=postconditions, pre_state=pre_state,
                         status=V.UNKNOWN, details=[])
    db.add(vr)
    db.flush()
    details: list[dict] = []
    for i, pc in enumerate(postconditions):
        t, p = pc.get("type"), pc.get("params", {}) or {}
        d: dict[str, Any] = {"index": i, "type": t, "description": pc.get("description", ""), "status": V.UNKNOWN}
        if t not in SUPPORTED:
            d["reason"] = "unsupported postcondition type — cannot be verified"
        elif t == "browser_readback":
            d.update(_check_browser(db, vr, session, p))
        elif t == "http_ok":
            d.update(_check_http(db, vr, p))
        elif t == "ticket_comment_exists":
            d.update(_check_comment(db, vr, ex))
        elif t == "ticket_field_equals":
            d.update(_check_ticket_field(db, vr, p, ex))
        elif t in DEVICE_CHECKS:
            if device is None:
                d["reason"] = "no device"
            else:
                d.update(_queue_device_check(db, vr, device, session, t, p))
        details.append(d)
    vr.details = details
    _finalize_if_complete(db, vr)
    return vr


def _evidence(db, vr, kind, summary, data):
    db.add(VerificationEvidence(org_id=vr.org_id, verification_id=vr.id, kind=kind, summary=summary[:2000], data=redact_obj(data)))


def _check_browser(db, vr, session, p) -> dict:
    if not session or not session.worker_session_ref:
        return {"status": V.UNKNOWN, "reason": "no browser session for read-back"}
    try:
        rb = browser_client.readback(session.worker_session_ref, p["url"], p.get("selector"), p.get("expect_text"), p.get("expect_value"))
    except Exception as e:  # noqa: BLE001
        return {"status": V.UNKNOWN, "reason": f"read-back unavailable: {str(e)[:200]}"}
    _evidence(db, vr, "readback", f"Fresh page load of {p['url']}: matched={rb.get('matched')}", rb)
    if not rb.get("ok"):
        return {"status": V.UNKNOWN, "reason": rb.get("error", "read-back failed")}
    if (rb.get("auth") or {}).get("auth_required"):
        return {"status": V.UNKNOWN, "reason": "read-back hit a sign-in page; cannot observe state"}
    observed = rb.get("value") if rb.get("value") is not None else rb.get("text")
    return {"status": V.VERIFIED if rb.get("matched") else V.FAILED, "observed": (observed or "")[:300],
            "expected": p.get("expect_value") or p.get("expect_text")}


def _check_http(db, vr, p) -> dict:
    from ..security.ssrf import SSRFBlocked, safe_fetch

    try:
        r = safe_fetch(p["url"], allow_private=get_settings().env != "production", timeout=10, max_bytes=200_000)
    except (SSRFBlocked, Exception) as e:  # noqa: BLE001
        return {"status": V.UNKNOWN, "reason": f"request failed: {str(e)[:200]}"}
    ok = 200 <= r.status < 400 and (not p.get("contains") or p["contains"].encode() in r.body)
    _evidence(db, vr, "http", f"GET {p['url']} → {r.status}", {"status": r.status})
    return {"status": V.VERIFIED if ok else V.FAILED, "observed": r.status}


def _check_comment(db, vr, ex) -> dict:
    cid = (ex.result or {}).get("comment_id")
    c = db.get(TicketComment, uuid.UUID(cid)) if cid else None  # re-fetch from DB
    _evidence(db, vr, "db_refetch", f"ticket comment {cid} exists={bool(c)}", {"comment_id": cid})
    return {"status": V.VERIFIED if c else V.FAILED}


def _check_ticket_field(db, vr, p, ex) -> dict:
    from ..models import Ticket

    db.expire_all()
    t = db.get(Ticket, uuid.UUID(p["ticket_id"])) if p.get("ticket_id") else None
    if not t:
        return {"status": V.UNKNOWN, "reason": "ticket not found"}
    val = getattr(t, p["field"], None)
    _evidence(db, vr, "db_refetch", f"ticket.{p['field']} = {val}", {})
    return {"status": V.VERIFIED if str(val) == str(p["equals"]) else V.FAILED, "observed": str(val)}


def _queue_device_check(db, vr, device, session, t, p) -> dict:
    from .devices import queue_command

    action, mk = DEVICE_CHECKS[t]
    try:
        execution = db.get(ActionExecution, vr.execution_id)
        step = db.get(PlanStep, execution.step_id) if execution and execution.step_id else None
        plan = db.get(AgentPlan, step.plan_id) if step else None
        cmd = queue_command(db, device, action=action, params=mk(p), session=session,
                            procedure_hash=plan.procedure_hash if plan else None)
    except Exception as e:  # noqa: BLE001
        return {"status": V.UNKNOWN, "reason": f"could not queue device read-back: {str(e)[:200]}"}
    cmd.envelope = {**cmd.envelope}
    return {"status": "PENDING", "command_id": str(cmd.id), "params": p, "deadline": (utcnow() + timedelta(seconds=120)).isoformat()}


def evaluate_device_result(t: str, params: dict, result: dict) -> tuple[str, str]:
    r = result or {}
    if t == "device_service_running":
        st = str(r.get("status", "")).lower()
        return (V.VERIFIED if st == "running" else V.FAILED), f"service status {r.get('status')}"
    if t == "device_process_responding":
        procs = r.get("processes", [])
        ok = any(pr.get("main_window", False) and pr.get("responding", False) for pr in procs)
        return (V.VERIFIED if ok else V.FAILED), f"{len(procs)} matching processes, responding main window={ok}"
    if t == "device_config_equals":
        val = r.get("data")
        if "enabled" in params:
            expected = 3 if params["enabled"] else 2
            ok = str(val) == str(expected)
            return (V.VERIFIED if ok else V.FAILED), f"LoadBehavior={val} (expected {expected})"
        ok = str(val) == str(params.get("equals"))
        return (V.VERIFIED if ok else V.FAILED), f"value={val}"
    if t == "device_addin_restore_matches":
        ok = r.get("matches_local_journal") is True
        return (V.VERIFIED if ok else V.FAILED), f"live setting matches local pre-state={ok}"
    return V.UNKNOWN, "unknown check"


def on_device_result(db: Session, cmd: DeviceCommand) -> VerificationRun | None:
    """Called when a device read-back command completes; returns the run if it became final."""
    for vr in db.execute(select(VerificationRun).where(VerificationRun.status == V.UNKNOWN, VerificationRun.run_id.isnot(None))
                         .order_by(VerificationRun.created_at.desc()).limit(50)).scalars():
        changed = False
        details = [dict(d) for d in vr.details]
        for d in details:
            if d.get("command_id") == str(cmd.id) and d.get("status") == "PENDING":
                if cmd.status != "done":
                    d["status"], d["reason"] = V.UNKNOWN, f"device read-back {cmd.status}: {(cmd.result or {}).get('error', '')}"[:300]
                else:
                    d["status"], d["observed"] = evaluate_device_result(d["type"], d.get("params", {}), cmd.result or {})
                _evidence(db, vr, "device_result", f"{cmd.action} → {cmd.status}", cmd.result or {})
                changed = True
        if changed:
            vr.details = details
            _finalize_if_complete(db, vr)
            return vr
    return None


def expire_pending(db: Session, vr: VerificationRun) -> None:
    details = [dict(d) for d in vr.details]
    for d in details:
        if d.get("status") == "PENDING":
            d["status"], d["reason"] = V.UNKNOWN, "device did not answer the read-back in time"
    vr.details = details
    _finalize_if_complete(db, vr)


def _finalize_if_complete(db: Session, vr: VerificationRun) -> None:
    sts = [d.get("status") for d in vr.details]
    if any(s == "PENDING" for s in sts):
        return
    vr.status = aggregate([s for s in sts if s])
    vr.post_state = {"checks": [{k: v for k, v in d.items() if k in ("type", "status", "observed", "reason")} for d in vr.details]}
    db.flush()
    publish(db, vr.org_id, "verification.completed", {"verification_id": str(vr.id), "run_id": str(vr.run_id) if vr.run_id else None, "status": vr.status})


def is_pending(vr: VerificationRun) -> bool:
    return any(d.get("status") == "PENDING" for d in vr.details)
