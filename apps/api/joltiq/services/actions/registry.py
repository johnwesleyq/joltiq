"""Typed action registry: the ONLY operations the AI may propose. Risk is assigned here and by the policy
engine — never by the model. Parameter schemas are validated before policy evaluation and again by the executor
(and, for desktop actions, a third time by the Windows companion's local policy)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from ...models.enums import RiskLevel as R


@dataclass(frozen=True)
class ActionSpec:
    id: str
    channel: str  # desktop|browser|internal
    risk: R
    description: str
    params: dict[str, dict[str, Any]]  # name -> {type, required, pattern, enum, max}
    effects: str
    default_postconditions: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    reversible: bool = True
    requires_admin: bool = False


SERVICE_NAME = r"^[A-Za-z0-9_.\- ]{1,80}$"
PROCESS_NAME = r"^[A-Za-z0-9_.\- ]{1,80}(\.exe)?$"

ACTIONS: dict[str, ActionSpec] = {a.id: a for a in [
    # ---------------- Windows desktop (executed by the companion) ----------------
    ActionSpec("system.info", "desktop", R.READ_ONLY, "Read OS edition, version, uptime, CPU/RAM.", {}, "None (read-only)."),
    ActionSpec("process.list", "desktop", R.READ_ONLY, "List running processes (name, pid, memory, responding).",
               {"name_filter": {"type": "string", "pattern": PROCESS_NAME}}, "None (read-only)."),
    ActionSpec("app.info", "desktop", R.READ_ONLY, "Installed application name/version/publisher from Uninstall registry keys.",
               {"name_filter": {"type": "string", "max": 80, "required": True}}, "None (read-only)."),
    ActionSpec("disk.usage", "desktop", R.READ_ONLY, "Free/used space per fixed drive.", {}, "None (read-only)."),
    ActionSpec("network.diagnostics", "desktop", R.READ_ONLY, "Adapters, DNS servers, default gateway reachability, DNS resolution of a host.",
               {"resolve_host": {"type": "string", "pattern": r"^[A-Za-z0-9.-]{1,253}$"}}, "None (read-only)."),
    ActionSpec("eventlog.query", "desktop", R.READ_ONLY, "Recent entries from Application/System logs, filtered by provider/level.",
               {"log": {"type": "string", "enum": ["Application", "System"], "required": True},
                "provider": {"type": "string", "max": 120}, "level": {"type": "string", "enum": ["Error", "Warning", "Information"]},
                "hours": {"type": "integer", "min": 1, "max": 168}, "max_events": {"type": "integer", "min": 1, "max": 100}},
               "None (read-only). Messages are redacted on-device."),
    ActionSpec("service.status", "desktop", R.READ_ONLY, "Status and start type of a Windows service.",
               {"name": {"type": "string", "pattern": SERVICE_NAME, "required": True}}, "None (read-only)."),
    ActionSpec("file.inspect", "desktop", R.SENSITIVE, "Read metadata and first 64 KB of a file under an allowlisted path.",
               {"path": {"type": "string", "max": 260, "required": True}}, "Reads file content (redacted) from the device.",
               requires_admin=False),
    ActionSpec("config.read", "desktop", R.SENSITIVE, "Read an allowlisted registry value (HKCU\\Software\\Microsoft\\Office\\...).",
               {"key": {"type": "string", "max": 260, "required": True}, "value": {"type": "string", "max": 120, "required": True}},
               "Reads a configuration value."),
    ActionSpec("app.restart", "desktop", R.SENSITIVE, "Gracefully close (WM_CLOSE, 20s) then relaunch an allowlisted application.",
               {"process": {"type": "string", "pattern": PROCESS_NAME, "required": True},
                "args": {"type": "string", "enum": ["", "/safe"], "required": False}},
               "Closes the application (user may lose unsaved work if they ignore the save prompt) and starts it again.",
               default_postconditions=({"type": "device_process_responding", "params_from": {"process": "process"}},)),
    ActionSpec("office.addin.set_load_behavior", "desktop", R.SENSITIVE,
               "Enable/disable a per-user Office COM add-in via HKCU LoadBehavior (no admin needed).",
               {"app": {"type": "string", "enum": ["Outlook", "Excel", "Word"], "required": True},
                "addin_progid": {"type": "string", "pattern": r"^[A-Za-z0-9_.\-]{1,120}$", "required": True},
                "enabled": {"type": "boolean", "required": True}},
               "Changes whether an Office add-in loads at startup. Reversible.",
               default_postconditions=({"type": "device_config_equals", "params_from": {"app": "app", "addin_progid": "addin_progid", "enabled": "enabled"}},)),
    ActionSpec("service.restart", "desktop", R.PRIVILEGED, "Restart an allowlisted Windows service (via the separate privileged broker).",
               {"name": {"type": "string", "pattern": SERVICE_NAME, "required": True}},
               "Stops and starts the service; dependent apps may briefly disconnect.",
               default_postconditions=({"type": "device_service_running", "params_from": {"name": "name"}},), requires_admin=True),
    ActionSpec("ui.invoke", "desktop", R.SENSITIVE, "Invoke a UI Automation element (button/menu) in an allowlisted app window.",
               {"process": {"type": "string", "pattern": PROCESS_NAME, "required": True},
                "automation_id": {"type": "string", "max": 200}, "name": {"type": "string", "max": 200},
                "control_type": {"type": "string", "enum": ["Button", "MenuItem", "CheckBox", "TabItem", "Hyperlink", "ListItem"]}},
               "Clicks a control in the user's application while they watch."),
    ActionSpec("ui.set_text", "desktop", R.SENSITIVE, "Set non-secret text in a UI Automation edit control (password fields refused on-device).",
               {"process": {"type": "string", "pattern": PROCESS_NAME, "required": True},
                "automation_id": {"type": "string", "max": 200, "required": True}, "text": {"type": "string", "max": 500, "required": True}},
               "Types text into a field in the user's application."),
    # ---------------- Browser (executed by the isolated browser worker) ----------------
    ActionSpec("browser.navigate", "browser", R.LOW_RISK, "Open a URL in the isolated session (consented domains only).",
               {"url": {"type": "string", "max": 2000, "required": True}}, "Loads a page."),
    ActionSpec("browser.read", "browser", R.READ_ONLY, "Read text / accessibility snapshot of the current page or an element.",
               {"selector": {"type": "string", "max": 300}}, "None (read-only)."),
    ActionSpec("browser.click", "browser", R.LOW_RISK, "Click an element by deterministic selector (role/name/test id/CSS).",
               {"selector": {"type": "string", "max": 300, "required": True}}, "Clicks a page element."),
    ActionSpec("browser.type", "browser", R.LOW_RISK, "Type NON-secret text into a field (password/OTP fields are refused).",
               {"selector": {"type": "string", "max": 300, "required": True}, "text": {"type": "string", "max": 2000, "required": True}},
               "Fills in a form field."),
    ActionSpec("browser.select", "browser", R.LOW_RISK, "Choose an option in a <select>.",
               {"selector": {"type": "string", "max": 300, "required": True}, "value": {"type": "string", "max": 300, "required": True}},
               "Changes a selection."),
    ActionSpec("browser.submit", "browser", R.SENSITIVE, "Click a control that saves/submits data in the target application.",
               {"selector": {"type": "string", "max": 300, "required": True}},
               "Saves or submits changes in the customer's web application.", reversible=False),
    ActionSpec("browser.download", "browser", R.SENSITIVE, "Download a file into the session's quarantined download area.",
               {"selector": {"type": "string", "max": 300, "required": True}}, "Retrieves a file (scanned, never executed)."),
    ActionSpec("browser.upload", "browser", R.SENSITIVE, "Upload an approved ticket attachment into a file input.",
               {"selector": {"type": "string", "max": 300, "required": True}, "attachment_id": {"type": "string", "max": 40, "required": True}},
               "Sends a file to the customer's web application.", reversible=False),
    # ---------------- Internal ----------------
    ActionSpec("ticket.comment", "internal", R.LOW_RISK, "Post a customer-visible update on the ticket.",
               {"body": {"type": "string", "max": 5000, "required": True}}, "Adds a reply to the ticket.",
               default_postconditions=({"type": "ticket_comment_exists"},)),
    ActionSpec("delete.anything", "internal", R.DESTRUCTIVE, "Placeholder for any destructive operation; always denied by default policy.",
               {"target": {"type": "string", "max": 200, "required": True}}, "Irreversible deletion.", reversible=False),
]}


class ActionValidationError(ValueError):
    pass


def validate_params(action_id: str, params: dict[str, Any]) -> dict[str, Any]:
    spec = ACTIONS.get(action_id)
    if spec is None:
        raise ActionValidationError(f"unknown action {action_id!r}")
    clean: dict[str, Any] = {}
    for k in params:
        if k not in spec.params:
            raise ActionValidationError(f"{action_id}: unexpected parameter {k!r}")
    for name, rule in spec.params.items():
        if name not in params or params[name] is None:
            if rule.get("required"):
                raise ActionValidationError(f"{action_id}: missing parameter {name!r}")
            continue
        v = params[name]
        t = rule.get("type", "string")
        if t == "string":
            if not isinstance(v, str):
                raise ActionValidationError(f"{action_id}.{name} must be a string")
            if len(v) > rule.get("max", 2000):
                raise ActionValidationError(f"{action_id}.{name} too long")
            if "pattern" in rule and not re.fullmatch(rule["pattern"], v):
                raise ActionValidationError(f"{action_id}.{name} has invalid format")
            if "enum" in rule and v not in rule["enum"]:
                raise ActionValidationError(f"{action_id}.{name} must be one of {rule['enum']}")
            if any(c in v for c in ("\x00", "\r")):
                raise ActionValidationError(f"{action_id}.{name} contains control characters")
        elif t == "integer":
            if not isinstance(v, int) or isinstance(v, bool):
                raise ActionValidationError(f"{action_id}.{name} must be an integer")
            if v < rule.get("min", -(2**31)) or v > rule.get("max", 2**31):
                raise ActionValidationError(f"{action_id}.{name} out of range")
        elif t == "boolean":
            if not isinstance(v, bool):
                raise ActionValidationError(f"{action_id}.{name} must be a boolean")
        clean[name] = v
    return clean


def default_postconditions(action_id: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    spec = ACTIONS[action_id]
    out = []
    for pc in spec.default_postconditions:
        p = {k: params.get(src) for k, src in pc.get("params_from", {}).items()}
        out.append({"type": pc["type"], "params": p, "description": f"default check for {action_id}"})
    return out


def registry_for_prompt() -> list[dict[str, Any]]:
    return [{"id": a.id, "channel": a.channel, "description": a.description, "params": a.params, "effects": a.effects}
            for a in ACTIONS.values() if a.risk != R.DESTRUCTIVE]
