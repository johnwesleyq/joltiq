"""Capability-aware AI planner for direct Windows Fix requests.

The model may choose only typed catalog actions. The server validates every parameter,
owns all risk metadata, and falls back atomically to the deterministic v14 parser.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from ..core.errors import AppError
from . import windows_plan
from .actions.registry import ActionValidationError, validate_params
from .actions.windows_catalog import BY_ID, TOOL_ACTIONS, as_dict
from .ai.router import available_providers
from .ai.router import call as _call
from .ai.schemas import WindowsRepairPlan

log = logging.getLogger("joltiq.windows_autoplan")


@dataclass(frozen=True)
class PlannedWindowsRepair:
    items: list[dict[str, Any]]
    planner: str
    model: str | None
    live: bool
    rationale: str
    fallback_reason: str | None = None


def _has_live_provider(db: Session, org_id: uuid.UUID) -> bool:
    return any(provider != "local_fixture" for provider, *_rest in available_providers(db, org_id, "planning"))


def _key(value: dict[str, Any]) -> str:
    return value["action"] + ":" + json.dumps(value["params"], sort_keys=True, separators=(",", ":"))


def _filter_capabilities(items: list[dict[str, Any]], capabilities: set[str]) -> tuple[list[dict[str, Any]], list[str]]:
    accepted: list[dict[str, Any]] = []
    omitted: list[str] = []
    for value in items:
        if value["action"] not in capabilities:
            omitted.append(f"{value['action']} was not advertised by the selected device")
        else:
            accepted.append(value)
    return accepted, omitted


def _deterministic(goal: str, capabilities: set[str], *reasons: str) -> PlannedWindowsRepair:
    items, omitted = _filter_capabilities(windows_plan.parse(goal), capabilities)
    why = [reason for reason in reasons if reason] + omitted
    return PlannedWindowsRepair(
        items=items,
        planner="deterministic",
        model=None,
        live=False,
        rationale="Used the built-in typed Windows rules for this request.",
        fallback_reason="; ".join(why) or None,
    )


def _catalog_for(capabilities: set[str]) -> list[dict[str, Any]]:
    return [as_dict(BY_ID[action]) for action in TOOL_ACTIONS if action in capabilities]


def build(db: Session, org_id: uuid.UUID, goal: str, capabilities: set[str] | list[str]) -> PlannedWindowsRepair:
    """Return a fully validated plan, or a capability-filtered deterministic fallback."""
    advertised = set(capabilities or [])
    deterministic = windows_plan.parse(goal)
    deterministic_high_risk = {_key(value) for value in deterministic if value.get("high_risk")}

    if not _has_live_provider(db, org_id):
        return _deterministic(goal, advertised, "No live planning model is configured and approved.")

    system = (
        "You plan Windows troubleshooting using only the supplied typed action catalog. "
        "Prefer read-only diagnosis before changes and the fewest actions needed. Never invent an action, parameter, "
        "risk level, shell command, script, executable path, or download URL. High-risk changes are allowed only when "
        "the user's words explicitly request that exact change. Set escalate_instead when no supplied action is safe."
    )
    messages = [{
        "role": "user",
        "content": "REQUEST:\n" + (goal or "") + "\n\nADVERTISED_ACTIONS_JSON:\n" + json.dumps(_catalog_for(advertised)),
    }]
    try:
        proposal, model_call = _call(
            db,
            org_id,
            "planning",
            system=system,
            messages=messages,
            output=WindowsRepairPlan,
            max_tokens=1800,
        )
    except AppError as exc:
        log.info("Windows AI planning fell back to deterministic rules: %s", exc)
        return _deterministic(goal, advertised, str(exc))

    if proposal.escalate_instead or not proposal.steps:
        reason = proposal.escalation_reason or "The model did not produce a safe typed action plan."
        return _deterministic(goal, advertised, reason)

    accepted: list[dict[str, Any]] = []
    notes: list[str] = []
    seen: set[str] = set()
    for step in proposal.steps:
        if step.action not in TOOL_ACTIONS:
            return _deterministic(goal, advertised, f"Model plan was invalid: unknown action {step.action!r}.")
        if step.action not in advertised:
            notes.append(f"{step.action} was not advertised by the selected device")
            continue
        try:
            params = validate_params(step.action, step.params)
        except ActionValidationError as exc:
            return _deterministic(goal, advertised, f"Model plan had invalid parameters: {exc}")
        value = windows_plan.item(step.action, params)
        key = _key(value)
        if value.get("high_risk") and key not in deterministic_high_risk:
            return _deterministic(goal, advertised, f"Model proposed high-risk action {step.action} without an exact user request.")
        if key not in seen:
            seen.add(key)
            accepted.append(value)

    # Exact actions named by the customer are authoritative even if the model accidentally omits them.
    # The live planner may add useful read-only checks, but it must never silently drop a typed action
    # that the deterministic parser can prove the customer explicitly requested (for example,
    # "Flush my DNS and check my network").
    for value in deterministic:
        key = _key(value)
        if value["action"] in advertised and key not in seen:
            seen.add(key)
            accepted.append(value)

    if not accepted:
        return _deterministic(goal, advertised, *(notes or ["The model produced no device-supported actions."]))

    accepted.sort(key=lambda value: value["action"] in ("system.sleep", "system.restart", "system.shutdown"))
    model_name = f"{model_call.provider}/{model_call.model}" if model_call else None
    return PlannedWindowsRepair(
        items=accepted,
        planner="live_ai",
        model=model_name,
        live=bool(getattr(model_call, "live", True)),
        rationale=proposal.rationale,
        fallback_reason="; ".join(notes) or None,
    )
