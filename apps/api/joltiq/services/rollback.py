"""Server-derived rollback plans and execution safety helpers.

Rollback instructions are derived only from typed registry actions. Browser values are
captured immediately before mutation. Desktop restore commands identify a companion-local
journal entry; the server never sends a prior registry value back to the device.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ActionExecution, AgentPlan, AgentRun, Approval, SupportSession, utcnow
from ..models.enums import ApprovalStatus, RiskLevel
from ..security.crypto import canonical_json, sha256_hex
from . import audit, browser_client, policy, sessions
from .actions.registry import ACTIONS, validate_params

PLAN_HASH_VERSION = "joltiq-plan-rollback-v1"
INVERTIBLE = {"browser.type": "text", "browser.select": "value"}
REPLAYABLE = {"browser.navigate", "browser.submit"}
SKIPPABLE = {"browser.read"}
DEVICE_INVERTIBLE = {"office.addin.set_load_behavior": "office.addin.restore_load_behavior"}
DEVICE_REPLAYABLE = {"app.restart"}

AVAILABLE = "AVAILABLE"
NOT_NEEDED = "NOT_NEEDED"
ROLLED_BACK = "ROLLED_BACK"
ROLLBACK_UNVERIFIED = "ROLLBACK_UNVERIFIED"
ROLLBACK_FAILED = "ROLLBACK_FAILED"
ROLLBACK_BLOCKED = "ROLLBACK_BLOCKED"
NO_ROLLBACK_AVAILABLE = "NO_ROLLBACK_AVAILABLE"


def _changes_state(action: str) -> bool:
    spec = ACTIONS.get(action)
    if spec is None:
        return False
    return spec.risk != RiskLevel.READ_ONLY and action not in ("file.inspect", "config.read")


def derive_browser_rollback(steps: list[dict]) -> list[dict]:
    """Return a deterministic inverse, or no inverse when any mutation is unsupported."""
    out: list[dict] = []
    url: str | None = None
    if not any(step["action"] in INVERTIBLE for step in steps):
        return []
    for ordinal, step in enumerate(steps):
        action = step["action"]
        params = step.get("params", {})
        if action in SKIPPABLE:
            continue
        if action == "browser.navigate":
            url = params["url"]
            out.append({"action": action, "params": {"url": url}})
        elif action in INVERTIBLE:
            if not url:
                return []
            field = INVERTIBLE[action]
            key = f"s{ordinal}"
            out.append({
                "action": action,
                "params": {"selector": params["selector"], field: {"$pre": key}},
                "pre": {"key": key, "url": url, "selector": params["selector"]},
            })
        elif action == "browser.submit":
            out.append({"action": action, "params": {"selector": params["selector"]}})
        else:
            return []
    return out


def derive_device_rollback(steps: list[dict]) -> list[dict]:
    """Derive journal-referencing desktop inverses without accepting old values."""
    changes = [(i, step) for i, step in enumerate(steps) if _changes_state(step["action"])]
    if not any(step["action"] in DEVICE_INVERTIBLE for _, step in changes):
        return []
    if any(
        step["action"] not in DEVICE_INVERTIBLE and step["action"] not in DEVICE_REPLAYABLE
        for _, step in changes
    ):
        return []
    out: list[dict] = []
    for ordinal, step in reversed(changes):
        if step["action"] not in DEVICE_INVERTIBLE:
            continue
        key = f"s{ordinal}"
        reference = {"$cmd": key}
        out.append({
            "action": DEVICE_INVERTIBLE[step["action"]],
            "params": {"source_command_id": reference},
            "rationale": "Restore the setting this PC journaled right before the approved change",
            "postconditions": [{
                "type": "device_addin_restore_matches",
                "params": {"source_command_id": reference},
            }],
            "pre": {
                "key": key,
                "source": "companion_journal",
                "action": step["action"],
                "params": step.get("params", {}),
            },
        })
    for _, step in changes:
        if step["action"] in DEVICE_REPLAYABLE:
            params = step.get("params", {})
            out.append({
                "action": step["action"],
                "params": params,
                "rationale": "Safely restart so the restored setting takes effect",
                "postconditions": [{
                    "type": "device_process_responding",
                    "params": {"process": params.get("process")},
                }],
            })
    return out


def availability(steps: list[dict], rollback_steps: list[dict], channel: str) -> dict[str, Any]:
    changes = [step["action"] for step in steps if _changes_state(step["action"])]
    if rollback_steps:
        return {"status": AVAILABLE, "channel": channel, "steps": len(rollback_steps)}
    if not changes or all(action in DEVICE_REPLAYABLE for action in changes):
        return {"status": NOT_NEEDED, "channel": channel}
    blocking = sorted({
        action
        for action in changes
        if action not in DEVICE_INVERTIBLE and action not in INVERTIBLE and action not in REPLAYABLE
    }) or sorted(set(changes))
    return {
        "status": NO_ROLLBACK_AVAILABLE,
        "channel": channel,
        "actions": blocking,
        "reason": (
            f"No safe server-derivable inverse for: {', '.join(blocking)}. "
            "A failed change will be escalated, not undone."
        ),
    }


def derive(steps: list[dict], channel: str) -> tuple[list[dict], dict[str, Any]]:
    if channel == "browser":
        rollback_steps = derive_browser_rollback(steps)
    elif channel == "desktop":
        rollback_steps = derive_device_rollback(steps)
    else:
        rollback_steps = []
    return rollback_steps, availability(steps, rollback_steps, channel)


def plan_hash(base_hash: str, rollback_steps: list[dict], avail: dict | None = None) -> str:
    """Bind the exact inverse—or its explicit absence—into the approval hash."""
    if avail and avail.get("status") == NO_ROLLBACK_AVAILABLE:
        return sha256_hex(canonical_json({
            "v": PLAN_HASH_VERSION,
            "steps": base_hash,
            "rollback": [],
            "no_rollback": {
                "actions": avail.get("actions", []),
                "reason": avail.get("reason", ""),
            },
        }))
    if not rollback_steps:
        return base_hash
    return sha256_hex(canonical_json({
        "v": PLAN_HASH_VERSION,
        "steps": base_hash,
        "rollback": rollback_steps,
    }))


def partial_report(*, changed: list[str], restored: list[str], failed: dict[str, str] | None = None) -> dict[str, Any]:
    """Record the complete partial-rollback state without rewriting action results."""
    changed_items = list(dict.fromkeys(changed))
    restored_items = [item for item in dict.fromkeys(restored) if item in changed_items]
    unresolved = [item for item in changed_items if item not in restored_items]
    return {
        "status": ROLLED_BACK if not unresolved else ROLLBACK_FAILED,
        "changed": changed_items,
        "restored": restored_items,
        "unresolved": unresolved,
        "failures": dict(failed or {}),
    }


def remember(run: AgentRun, plan: AgentPlan, base_hash: str) -> None:
    context = dict(run.context or {})
    context["rollback_base_hashes"] = {
        **context.get("rollback_base_hashes", {}),
        str(plan.id): base_hash,
    }
    run.context = context


def capture_targets(plan: AgentPlan) -> dict[int, dict]:
    return {
        int(step["pre"]["key"][1:]): step["pre"]
        for step in (plan.rollback_steps or [])
        if step.get("pre") and step["pre"]["key"].startswith("s")
    }


def capture_pre_value(
    db: Session,
    run: AgentRun,
    plan: AgentPlan,
    ordinal: int,
    session: SupportSession | None,
) -> None:
    """Capture a browser field's persisted value before its first mutation."""
    del db
    target = capture_targets(plan).get(ordinal)
    if not target or session is None or not session.worker_session_ref:
        return
    pre = dict((run.context or {}).get("rollback_pre", {}))
    if target["key"] in pre:
        return
    try:
        readback = browser_client.readback(
            session.worker_session_ref,
            target["url"],
            target["selector"],
        )
    except Exception:  # noqa: BLE001
        return
    if (
        not readback.get("ok")
        or (readback.get("auth") or {}).get("auth_required")
        or readback.get("value") is None
    ):
        return
    pre[target["key"]] = readback["value"]
    run.context = {**run.context, "rollback_pre": pre}


def _resolve(params: dict, pre: dict) -> dict:
    resolved = {}
    for key, value in params.items():
        if isinstance(value, dict) and "$pre" in value:
            if value["$pre"] not in pre:
                raise KeyError(value["$pre"])
            resolved[key] = pre[value["$pre"]]
        else:
            resolved[key] = value
    return resolved


def authorized(db: Session, run: AgentRun, plan: AgentPlan) -> tuple[bool, str, Approval | None]:
    base_hash = (run.context or {}).get("rollback_base_hashes", {}).get(str(plan.id))
    if not base_hash or plan_hash(base_hash, plan.rollback_steps or [], plan.rollback_availability or {}) != plan.plan_hash:
        return False, "rollback steps do not match the approved plan hash", None
    approvals = db.execute(select(Approval).where(Approval.plan_id == plan.id)).scalars().all()
    if not approvals:
        return False, "the plan had no approval covering its rollback", None
    if any(
        approval.status != ApprovalStatus.APPROVED or approval.plan_hash != plan.plan_hash
        for approval in approvals
    ):
        return False, "not every approval is APPROVED for the current plan hash", None
    return True, "", approvals[0]


def resolve_device_steps(
    db: Session,
    run: AgentRun,
    plan: AgentPlan,
) -> tuple[list[dict], dict[str, Any], str]:
    """Bind journal placeholders to completed authenticated commands, never to server values."""
    from ..models import PlanStep

    del run
    by_ordinal = {
        step.ordinal: step
        for step in db.execute(select(PlanStep).where(PlanStep.plan_id == plan.id)).scalars()
    }
    resolved: list[dict] = []
    snapshot: dict[str, Any] = {}
    for rollback_step in plan.rollback_steps or []:
        pre = rollback_step.get("pre") or {}
        key = pre.get("key")
        command_id = None
        if key:
            source_step = by_ordinal.get(int(key[1:]))
            execution = (
                db.execute(
                    select(ActionExecution)
                    .where(
                        ActionExecution.step_id == source_step.id,
                        ActionExecution.status == "succeeded",
                    )
                    .order_by(ActionExecution.created_at.desc())
                ).scalars().first()
                if source_step
                else None
            )
            if execution is None:
                continue
            result = execution.result or {}
            command_id = result.get("command_id")
            device = result.get("device") or {}
            if not command_id or (
                device.get("source_command_id")
                and device["source_command_id"] != command_id
            ):
                return [], snapshot, "the authenticated change command ID is unavailable or inconsistent"
            enrolled_sid = device.get("enrolled_user_sid")
            interactive_sid = device.get("interactive_user_sid")
            if enrolled_sid and interactive_sid and enrolled_sid.casefold() != interactive_sid.casefold():
                return [], snapshot, "the logged-in Windows user no longer matches the enrolled user"
            if device.get("before") is None:
                return [], snapshot, "the pre-change state was not read back by the device"
            snapshot[key] = {
                "command_id": command_id,
                "before": device.get("before"),
                "after": device.get("after"),
            }

        def bind(value: Any, bound_command_id: Any = command_id) -> Any:
            if isinstance(value, dict) and "$cmd" in value:
                return str(bound_command_id)
            if isinstance(value, dict):
                return {name: bind(item) for name, item in value.items()}
            if isinstance(value, list):
                return [bind(item) for item in value]
            return value

        resolved.append({
            "action": rollback_step["action"],
            "params": validate_params(rollback_step["action"], bind(rollback_step["params"])),
            "rationale": rollback_step.get("rationale", ""),
            "postconditions": bind(rollback_step.get("postconditions", [])),
        })
    if not any(step["action"] in DEVICE_INVERTIBLE.values() for step in resolved):
        return [], snapshot, "no completed change needs restoring"
    return resolved, snapshot, ""


def execute_browser(
    db: Session,
    run: AgentRun,
    plan: AgentPlan,
    session: SupportSession | None,
) -> dict[str, Any]:
    """Execute an approved browser inverse and independently verify restored fields."""
    from . import verification

    def finish(status: str, reason: str, verification_run=None) -> dict[str, Any]:
        result = {
            "status": status,
            "reason": reason,
            "verification_id": str(verification_run.id) if verification_run else None,
        }
        audit.record(
            db,
            org_id=run.org_id,
            actor_kind="system",
            actor_id=None,
            action="rollback.result",
            resource_type="agent_plan",
            resource_id=plan.id,
            data=result,
        )
        run.context = {**run.context, "rollback": result}
        return result

    rollback_steps = plan.rollback_steps or []
    if not rollback_steps:
        return finish(NO_ROLLBACK_AVAILABLE, "no server-derivable inverse for this plan")
    allowed, reason, approval = authorized(db, run, plan)
    if not allowed:
        return finish(ROLLBACK_BLOCKED, reason)
    if (
        session is None
        or session.emergency_stopped
        or session.state not in ("active", "awaiting_user_auth", "paused")
        or not session.worker_session_ref
    ):
        return finish(ROLLBACK_BLOCKED, "browser session is no longer available")
    pre = (run.context or {}).get("rollback_pre", {})
    needed = [step["pre"]["key"] for step in rollback_steps if step.get("pre")]
    if any(key not in pre for key in needed):
        return finish(NO_ROLLBACK_AVAILABLE, "a pre-change field value was not captured")

    changed: list[str] = list(needed)
    restored: list[str] = []
    failures: dict[str, str] = {}
    last_execution = None
    for ordinal, step in enumerate(rollback_steps):
        label = step.get("pre", {}).get("key") or f"rollback-step-{ordinal}"
        try:
            params = validate_params(step["action"], _resolve(step["params"], pre))
        except Exception as exc:  # noqa: BLE001
            failures[label] = f"invalid rollback step: {str(exc)[:200]}"
            break
        decision = policy.evaluate(
            db,
            run.org_id,
            step["action"],
            params,
            session=session,
            for_execution=True,
        )
        if decision.effect == "deny":
            failures[label] = "policy denied rollback step"
            break
        try:
            sessions.acquire_lease(db, session, "ai", reason=f"rollback {step['action']}")
        except Exception as exc:  # noqa: BLE001
            failures[label] = str(exc)[:200]
            break
        execution = ActionExecution(
            org_id=run.org_id,
            run_id=run.id,
            approval_id=approval.id if approval else None,
            session_id=session.id,
            action=step["action"],
            target={"session_id": str(session.id), "rollback": True},
            params=params,
            risk=decision.risk,
            policy_decision=decision.as_dict(),
            status="running",
            idempotency_key=f"{run.id}:rollback:{plan.id}:{ordinal}"[:100],
            started_at=utcnow(),
        )
        db.add(execution)
        db.flush()
        try:
            response = browser_client.act(
                session.worker_session_ref,
                step["action"],
                params,
                actor="ai",
                epoch=session.lease_epoch,
            )
        except Exception as exc:  # noqa: BLE001
            response = {"ok": False, "error": str(exc)[:200]}
        execution.result = {key: value for key, value in response.items() if key != "page"}
        execution.status = "succeeded" if response.get("ok") else "failed"
        execution.finished_at = utcnow()
        execution.error = None if response.get("ok") else str(response.get("error", ""))[:500]
        last_execution = execution
        if response.get("auth_required"):
            failures[label] = "application requested sign-in"
            break
        if not response.get("ok"):
            failures[label] = execution.error or "rollback action failed"
            break
        if step.get("pre"):
            restored.append(label)

    if failures or last_execution is None:
        report = partial_report(changed=changed, restored=restored, failed=failures)
        run.context = {**run.context, "rollback_partial": report}
        return finish(ROLLBACK_FAILED, "rollback stopped before every item was restored")

    postconditions = [
        {
            "type": "browser_readback",
            "critical": True,
            "description": f"{step['pre']['selector']} restored to its pre-change value",
            "params": {
                "url": step["pre"]["url"],
                "selector": step["pre"]["selector"],
                "expect_value": pre[step["pre"]["key"]],
            },
        }
        for step in rollback_steps
        if step.get("pre")
    ]
    result = verification.start(
        db,
        last_execution,
        postconditions,
        {"fields": {key: pre[key] for key in needed}},
        session,
        None,
        kind="rollback",
    )
    status = {
        "VERIFIED": ROLLED_BACK,
        "UNKNOWN": ROLLBACK_UNVERIFIED,
    }.get(result.status, ROLLBACK_FAILED)
    return finish(status, f"rollback read-back {result.status}", result)
