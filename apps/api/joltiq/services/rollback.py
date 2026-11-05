"""Server-computed rollback for browser plans (ADR 0017).

The inverse steps are DERIVED BY THE SERVER from the typed plan, never proposed by the model, and they are folded
into the plan hash, so the person who approves the plan also approves exactly this rollback. Values to restore are
not known at approval time; they are read back from the application (fresh page load) immediately before each
field is changed and referenced by key (`{"$pre": "s<ordinal>"}`).

On a FAILED/PARTIAL verification the run executes the inverse steps through policy + the AI control lease, then
verifies the restore with the same independent read-back verifier (VerificationRun.kind = "rollback", evidence
hashed into the audit chain). A rollback is never a fix: the run always escalates afterwards.

Device plans (ADR 0019): the inverse of `office.addin.set_load_behavior` is `office.addin.restore_load_behavior`
bound to the change command (`{"$cmd": "s<ordinal>"}`); the pre-change value is the read-back the companion journals
right before writing (reported back as `device.before`), so the server never supplies registry state. A plan
containing any change with no safe inverse (service.restart, ui.invoke, ui.set_text, ...) gets NO inverse and is
marked NO_ROLLBACK_AVAILABLE up front; that marker is part of the plan hash and is shown on the approval card.
If the plan's approvals are no longer valid for the hash, the companion-journal recovery (`agent.offer_rollback`)
falls back to asking for its own approval.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ActionExecution, AgentPlan, AgentRun, Approval, SupportSession, utcnow
from ..models.enums import ApprovalStatus
from ..security.crypto import canonical_json, sha256_hex
from . import audit, browser_client, policy, sessions
from .actions.registry import validate_params

PLAN_HASH_VERSION = "joltiq-plan-rollback-v1"
INVERTIBLE = {"browser.type": "text", "browser.select": "value"}
REPLAYABLE = {"browser.navigate", "browser.submit"}
SKIPPABLE = {"browser.read"}
# Device actions: inverse via the companion's local journal; app.restart is replayed to apply the restore.
DEVICE_INVERTIBLE = {"office.addin.set_load_behavior": "office.addin.restore_load_behavior"}
DEVICE_REPLAYABLE = {"app.restart"}
AVAILABLE = "AVAILABLE"
NOT_NEEDED = "NOT_NEEDED"

# Rollback outcomes
ROLLED_BACK = "ROLLED_BACK"
ROLLBACK_UNVERIFIED = "ROLLBACK_UNVERIFIED"
ROLLBACK_FAILED = "ROLLBACK_FAILED"
ROLLBACK_BLOCKED = "ROLLBACK_BLOCKED"
NO_ROLLBACK_AVAILABLE = "NO_ROLLBACK_AVAILABLE"


def derive_browser_rollback(steps: list[dict]) -> list[dict]:
    """Inverse of a browser plan, or [] when any step has no known inverse (click, upload, download, …)."""
    out: list[dict] = []
    url: str | None = None
    if not any(s["action"] in INVERTIBLE for s in steps):
        return []
    for i, s in enumerate(steps):
        a, p = s["action"], s.get("params", {})
        if a in SKIPPABLE:
            continue
        if a == "browser.navigate":
            url = p["url"]
            out.append({"action": a, "params": {"url": url}})
        elif a in INVERTIBLE:
            if not url:
                return []
            field = INVERTIBLE[a]
            out.append({"action": a, "params": {"selector": p["selector"], field: {"$pre": f"s{i}"}},
                        "pre": {"key": f"s{i}", "url": url, "selector": p["selector"]}})
        elif a == "browser.submit":
            out.append({"action": a, "params": {"selector": p["selector"]}})
        else:
            return []
    return out


def _changes_state(action: str) -> bool:
    from ..models.enums import RiskLevel
    from .actions.registry import ACTIONS

    spec = ACTIONS.get(action)
    return spec is not None and spec.risk != RiskLevel.READ_ONLY and action not in ("file.inspect", "config.read")


def derive_device_rollback(steps: list[dict]) -> list[dict]:
    """Inverse of a desktop plan, or [] when any state-changing step has no safe inverse."""
    changes = [(i, s) for i, s in enumerate(steps) if _changes_state(s["action"])]
    if not any(s["action"] in DEVICE_INVERTIBLE for _, s in changes):
        return []
    if any(s["action"] not in DEVICE_INVERTIBLE and s["action"] not in DEVICE_REPLAYABLE for _, s in changes):
        return []
    out: list[dict] = []
    for i, s in reversed(changes):
        if s["action"] in DEVICE_INVERTIBLE:
            out.append({"action": DEVICE_INVERTIBLE[s["action"]], "params": {"source_command_id": {"$cmd": f"s{i}"}},
                        "rationale": "Restore the setting this PC journaled right before the approved change",
                        "postconditions": [{"type": "device_addin_restore_matches", "params": {"source_command_id": {"$cmd": f"s{i}"}}}],
                        "pre": {"key": f"s{i}", "source": "companion_journal", "action": s["action"], "params": s.get("params", {})}})
    for _, s in changes:
        if s["action"] in DEVICE_REPLAYABLE:
            p = s.get("params", {})
            out.append({"action": s["action"], "params": p, "rationale": "Safely restart so the restored setting takes effect",
                        "postconditions": [{"type": "device_process_responding", "params": {"process": p.get("process")}}]})
    return out


def availability(steps: list[dict], rollback_steps: list[dict], channel: str) -> dict[str, Any]:
    """Up-front rollback marker shown on the approval card and folded into the plan hash."""
    changes = [s["action"] for s in steps if _changes_state(s["action"])]
    if rollback_steps:
        return {"status": AVAILABLE, "channel": channel, "steps": len(rollback_steps)}
    if not changes or all(a in DEVICE_REPLAYABLE for a in changes):
        return {"status": NOT_NEEDED, "channel": channel}
    blocking = sorted({a for a in changes if a not in DEVICE_INVERTIBLE and a not in INVERTIBLE and a not in REPLAYABLE}) or sorted(set(changes))
    return {"status": NO_ROLLBACK_AVAILABLE, "channel": channel, "actions": blocking,
            "reason": f"No safe server-derivable inverse for: {', '.join(blocking)}. A failed change will be escalated, not undone."}


def derive(steps: list[dict], channel: str) -> tuple[list[dict], dict[str, Any]]:
    if channel == "browser":
        rb = derive_browser_rollback(steps)
    elif channel == "desktop":
        rb = derive_device_rollback(steps)
    else:
        rb = []
    return rb, availability(steps, rb, channel)


def plan_hash(base_hash: str, rollback_steps: list[dict], avail: dict | None = None) -> str:
    if avail and avail.get("status") == NO_ROLLBACK_AVAILABLE:
        return sha256_hex(canonical_json({"v": PLAN_HASH_VERSION, "steps": base_hash, "rollback": [],
                                          "no_rollback": {"actions": avail.get("actions", []), "reason": avail.get("reason", "")}}))
    if not rollback_steps:
        return base_hash
    return sha256_hex(canonical_json({"v": PLAN_HASH_VERSION, "steps": base_hash, "rollback": rollback_steps}))


def remember(run: AgentRun, plan: AgentPlan, base_hash: str) -> None:
    ctx = dict(run.context or {})
    ctx["rollback_base_hashes"] = {**ctx.get("rollback_base_hashes", {}), str(plan.id): base_hash}
    run.context = ctx


def capture_targets(plan: AgentPlan) -> dict[int, dict]:
    return {int(s["pre"]["key"][1:]): s["pre"] for s in (plan.rollback_steps or []) if s.get("pre")}


def capture_pre_value(db: Session, run: AgentRun, plan: AgentPlan, ordinal: int, session: SupportSession | None) -> None:
    """Read the field's current persisted value right before the AI changes it (only the first time)."""
    target = capture_targets(plan).get(ordinal)
    if not target or session is None or not session.worker_session_ref:
        return
    pre = dict((run.context or {}).get("rollback_pre", {}))
    if target["key"] in pre:
        return
    try:
        rb = browser_client.readback(session.worker_session_ref, target["url"], target["selector"])
    except Exception:  # noqa: BLE001
        return
    if not rb.get("ok") or (rb.get("auth") or {}).get("auth_required") or rb.get("value") is None:
        return
    pre[target["key"]] = rb["value"]
    run.context = {**run.context, "rollback_pre": pre}


def _resolve(params: dict, pre: dict) -> dict:
    out = {}
    for k, v in params.items():
        if isinstance(v, dict) and "$pre" in v:
            if v["$pre"] not in pre:
                raise KeyError(v["$pre"])
            out[k] = pre[v["$pre"]]
        else:
            out[k] = v
    return out


def authorized(db: Session, run: AgentRun, plan: AgentPlan) -> tuple[bool, str, Approval | None]:
    base = (run.context or {}).get("rollback_base_hashes", {}).get(str(plan.id))
    if not base or plan_hash(base, plan.rollback_steps or [], plan.rollback_availability or {}) != plan.plan_hash:
        return False, "rollback steps do not match the approved plan hash", None
    aps = db.execute(select(Approval).where(Approval.plan_id == plan.id)).scalars().all()
    if not aps:
        return False, "the plan had no approval covering its rollback", None
    if any(a.status != ApprovalStatus.APPROVED or a.plan_hash != plan.plan_hash for a in aps):
        return False, "not every approval of this plan is APPROVED for the current plan hash", None
    return True, "", aps[0]


def execute_browser(db: Session, run: AgentRun, plan: AgentPlan, session: SupportSession | None) -> dict[str, Any]:
    """Run the approved inverse steps. Returns {"status", "reason", "verification_id"}."""
    from . import verification

    def done(status: str, reason: str, vr=None) -> dict[str, Any]:
        out = {"status": status, "reason": reason, "verification_id": str(vr.id) if vr else None}
        audit.record(db, org_id=run.org_id, actor_kind="system", actor_id=None, action="rollback.result",
                     resource_type="agent_plan", resource_id=plan.id, data=out)
        run.context = {**run.context, "rollback": out}
        return out

    rb_steps = plan.rollback_steps or []
    if not rb_steps:
        return done(NO_ROLLBACK_AVAILABLE, "no server-derivable inverse for this plan")
    ok, why, approval = authorized(db, run, plan)
    if not ok:
        return done(ROLLBACK_BLOCKED, why)
    if session is None or session.emergency_stopped or session.state not in ("active", "awaiting_user_auth", "paused") \
            or not session.worker_session_ref:
        return done(ROLLBACK_BLOCKED, "browser session is no longer available")
    pre = (run.context or {}).get("rollback_pre", {})
    needed = [s["pre"]["key"] for s in rb_steps if s.get("pre")]
    if any(k not in pre for k in needed):
        return done(NO_ROLLBACK_AVAILABLE, "the pre-change value of a field could not be read back")
    audit.record(db, org_id=run.org_id, actor_kind="system", actor_id=None, action="rollback.started",
                 resource_type="agent_plan", resource_id=plan.id, data={"steps": len(rb_steps)})
    last_ex = None
    for i, st in enumerate(rb_steps):
        try:
            params = validate_params(st["action"], _resolve(st["params"], pre))
        except Exception as e:  # noqa: BLE001
            return done(ROLLBACK_FAILED, f"rollback step {i} invalid: {str(e)[:200]}")
        dec = policy.evaluate(db, run.org_id, st["action"], params, session=session, for_execution=True)
        if dec.effect == "deny":
            return done(ROLLBACK_BLOCKED, f"policy denied rollback step {st['action']}: {'; '.join(dec.reasons)[:200]}")
        try:
            sessions.acquire_lease(db, session, "ai", reason=f"rollback {st['action']}")
        except Exception as e:  # noqa: BLE001  (a human holds control → we never fight them)
            return done(ROLLBACK_BLOCKED, f"control lease unavailable: {getattr(e, 'message', str(e))[:200]}")
        ex = ActionExecution(org_id=run.org_id, run_id=run.id, step_id=None, approval_id=approval.id if approval else None,
                             session_id=session.id, action=st["action"], target={"session_id": str(session.id), "rollback": True},
                             params=params, risk=dec.risk, policy_decision=dec.as_dict(), status="running",
                             idempotency_key=f"{run.id}:rollback:{plan.id}:{i}"[:100], started_at=utcnow())
        db.add(ex)
        db.flush()
        try:
            res = browser_client.act(session.worker_session_ref, st["action"], params, actor="ai", epoch=session.lease_epoch)
        except Exception as e:  # noqa: BLE001
            res = {"ok": False, "error": getattr(e, "message", str(e))[:200]}
        ex.result = {k: v for k, v in res.items() if k not in ("page",)}
        ex.status, ex.finished_at = ("succeeded" if res.get("ok") else "failed"), utcnow()
        ex.error = None if res.get("ok") else str(res.get("error", ""))[:500]
        sessions.event(db, session, "rollback_action", actor_kind="ai", data={"action": st["action"], "ok": res.get("ok")})
        last_ex = ex
        if res.get("auth_required"):
            return done(ROLLBACK_BLOCKED, "the application asked for sign-in during rollback")
        if not res.get("ok"):
            return done(ROLLBACK_FAILED, f"rollback step {st['action']} failed: {ex.error}")
    pcs = [{"type": "browser_readback", "critical": True, "description": f"{s['pre']['selector']} restored to its pre-change value",
            "params": {"url": s["pre"]["url"], "selector": s["pre"]["selector"], "expect_value": pre[s["pre"]["key"]]}}
           for s in rb_steps if s.get("pre")]
    if last_ex is None:
        return done(ROLLBACK_FAILED, "rollback produced no action execution")
    vr = verification.start(db, last_ex, pcs, {"fields": {k: pre[k] for k in needed}}, session, None, kind="rollback")
    status = {"VERIFIED": ROLLED_BACK, "UNKNOWN": ROLLBACK_UNVERIFIED}.get(vr.status, ROLLBACK_FAILED)
    return done(status, f"rollback read-back {vr.status}", vr)


def resolve_device_steps(db: Session, run: AgentRun, plan: AgentPlan) -> tuple[list[dict], dict[str, Any], str]:
    """Bind `$cmd` placeholders to the authenticated change commands and collect the journaled pre-change snapshot.
    Returns (steps, snapshot, problem)."""
    from ..models import PlanStep

    by_ord = {st.ordinal: st for st in db.execute(select(PlanStep).where(PlanStep.plan_id == plan.id)).scalars()}
    out: list[dict] = []
    snapshot: dict[str, Any] = {}
    for rb in plan.rollback_steps or []:
        key = (rb.get("pre") or {}).get("key")
        cmd = None
        if key:
            st = by_ord.get(int(key[1:]))
            ex = db.execute(select(ActionExecution).where(ActionExecution.step_id == st.id, ActionExecution.status == "succeeded")
                            .order_by(ActionExecution.created_at.desc())).scalars().first() if st else None
            if ex is None:
                continue  # that change never happened → nothing to undo for it
            res = ex.result or {}
            cmd = res.get("command_id")
            dev = res.get("device") or {}
            if not cmd or (dev.get("source_command_id") and dev["source_command_id"] != cmd):
                return [], snapshot, "the authenticated change command ID is unavailable or inconsistent"
            if dev.get("before") is None:
                return [], snapshot, "the pre-change state was not read back by the device before the change"
            snapshot[key] = {"command_id": cmd, "before": dev.get("before"), "after": dev.get("after")}

        def bind(v: Any, cmd: Any = cmd) -> Any:
            if isinstance(v, dict) and "$cmd" in v:
                return str(cmd)
            if isinstance(v, dict):
                return {k: bind(x) for k, x in v.items()}
            if isinstance(v, list):
                return [bind(x) for x in v]
            return v
        out.append({"action": rb["action"], "params": validate_params(rb["action"], bind(rb["params"])),
                    "rationale": rb.get("rationale", ""), "postconditions": bind(rb.get("postconditions", []))})
    if not any(s["action"] in DEVICE_INVERTIBLE.values() for s in out):
        return [], snapshot, "no completed change needs restoring"
    return out, snapshot, ""


def uuid_or_none(v: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(v))
    except (TypeError, ValueError):
        return None
