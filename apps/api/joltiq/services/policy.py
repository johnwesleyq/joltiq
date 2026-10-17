"""Central policy engine. Evaluated OUTSIDE the LLM for every proposed and every executed action.

Inputs: tenant rules + action registry risk + session consent scope + device state + target/parameters.
Output: allow | require_approval | deny, with the effective risk and human-readable reasons.
Model output and retrieved web text are never inputs to the decision except as the *subject* being evaluated.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Device, PolicyRule, SupportSession, utcnow
from ..models.enums import RISK_ORDER, RiskLevel
from .actions.registry import ACTIONS, ActionValidationError, validate_params


@dataclass
class Decision:
    effect: str  # allow|require_approval|deny
    risk: RiskLevel
    reasons: list[str] = field(default_factory=list)
    rule: str | None = None
    approver_roles: list[str] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"effect": self.effect, "risk": self.risk, "reasons": self.reasons, "rule": self.rule, "approver_roles": self.approver_roles}


def _rank(r: str) -> int:
    return RISK_ORDER.index(RiskLevel(r))


def _domain_allowed(host: str, allowed: list[str]) -> bool:
    host = host.lower().rstrip(".")
    return any(host == d.lower() or host.endswith("." + d.lower()) for d in allowed)


def evaluate(db: Session, org_id: uuid.UUID, action: str, params: dict[str, Any], *, session: SupportSession | None = None,
             device: Device | None = None, actor_role: str | None = None, for_execution: bool = False) -> Decision:
    spec = ACTIONS.get(action)
    if spec is None:
        return Decision("deny", RiskLevel.DESTRUCTIVE, [f"Action {action!r} is not in the typed action registry"])
    try:
        clean = validate_params(action, params)
    except ActionValidationError as e:
        return Decision("deny", spec.risk, [f"Invalid parameters: {e}"])
    risk = spec.risk
    reasons: list[str] = []
    if spec.requires_admin and _rank(risk) < _rank(RiskLevel.PRIVILEGED):
        risk = RiskLevel.PRIVILEGED
        reasons.append("requires administrator rights on the device")

    # ---- Consent / scope checks (hard denies, not approvable) ----
    if spec.channel in ("browser", "desktop"):
        if session is None:
            return Decision("deny", risk, ["No consented live session for this action"], params=clean)
        if session.emergency_stopped:
            return Decision("deny", risk, ["Session is emergency-stopped"], params=clean)
        if for_execution and session.state != "active":
            return Decision("deny", risk, [f"Session is {session.state}; execution requires an active session"], params=clean)
        if session.state in ("terminated", "expired", "pending_consent"):
            return Decision("deny", risk, [f"Session is {session.state}"], params=clean)
        if session.expires_at and session.expires_at < utcnow():
            return Decision("deny", risk, ["Consent has expired"], params=clean)
        consent = session.consent or {}
        if consent.get("max_risk") and _rank(risk) > _rank(consent["max_risk"]):
            return Decision("deny", risk, [f"Customer consent covers up to {consent['max_risk']} actions only"], params=clean)
        if consent.get("actions") and action not in consent["actions"]:
            return Decision("deny", risk, ["Action not covered by the customer's consent"], params=clean)
    if spec.channel == "browser":
        if session is None or session.kind not in ("browser", "technician_assisted"):
            return Decision("deny", risk, ["Browser actions require a browser session"], params=clean)
        if action == "browser.navigate":
            host = urlsplit(clean["url"]).hostname or ""
            if urlsplit(clean["url"]).scheme not in ("http", "https"):
                return Decision("deny", risk, ["Only http(s) URLs are allowed"], params=clean)
            if not _domain_allowed(host, list(session.allowed_domains or [])):
                return Decision("deny", risk, [f"Domain {host} is outside the consented domains"], params=clean)
    if spec.channel == "desktop":
        if session is None or session.kind not in ("desktop", "technician_assisted"):
            return Decision("deny", risk, ["Desktop actions require a desktop session"], params=clean)
        if device is None or device.status != "active" or device.revoked_at:
            return Decision("deny", risk, ["Device is not enrolled/active"], params=clean)
        if session.device_id and device.id != session.device_id:
            return Decision("deny", risk, ["Action targets a different device than the consented session"], params=clean)
        if spec.id not in (device.capabilities or [spec.id]):
            return Decision("deny", risk, ["Device agent does not support this action"], params=clean)

    # ---- Tenant rules (ordered by priority; first match wins) ----
    rules = db.execute(select(PolicyRule).where(PolicyRule.enabled.is_(True)).order_by(PolicyRule.priority, PolicyRule.created_at)).scalars()
    for rule in rules:
        if _matches(rule.match or {}, action, risk, clean, spec.channel, actor_role, session, device):
            dec = Decision(rule.effect, risk, reasons + [f"policy: {rule.name}"], rule.name, list(rule.approver_roles or []), clean)
            if dec.effect == "require_approval" and not dec.approver_roles:
                dec.approver_roles = ["technician", "admin", "org_owner"]
            return dec
    # Default-deny for anything above LOW_RISK without an explicit rule.
    if _rank(risk) <= _rank(RiskLevel.LOW_RISK):
        return Decision("allow", risk, reasons + ["default: low risk within consent"], None, [], clean)
    if risk == RiskLevel.DESTRUCTIVE:
        return Decision("deny", risk, reasons + ["default: destructive actions are denied"], None, [], clean)
    return Decision("require_approval", risk, reasons + ["default: approval required"], None, ["technician", "admin", "org_owner"], clean)


def _matches(m: dict, action: str, risk: RiskLevel, params: dict, channel: str, role: str | None, session, device) -> bool:
    if "action" in m and m["action"] != action:
        return False
    if "actions" in m and action not in m["actions"]:
        return False
    if "channel" in m and m["channel"] != channel:
        return False
    if "risk_at_least" in m and _rank(risk) < _rank(m["risk_at_least"]):
        return False
    if "risk_at_most" in m and _rank(risk) > _rank(m["risk_at_most"]):
        return False
    if "device_id" in m and (device is None or str(device.id) != m["device_id"]):
        return False
    if "role" in m and role != m["role"]:
        return False
    if "param_equals" in m:
        for k, v in m["param_equals"].items():
            if params.get(k) != v:
                return False
    if "domain" in m:
        host = urlsplit(params.get("url", "")).hostname or ""
        if not _domain_allowed(host, [m["domain"]]):
            return False
    return True
