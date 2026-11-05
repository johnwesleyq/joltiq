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
    computer_use: bool = False  # low-level screen/input primitive, only issued by a computer-use run (never by the planner)
    run_bound: bool = False  # v13 Windows Fix: only inside the approved Fix run whose plan listed it (never by the planner)


_XY = {"x": {"type": "integer", "min": 0, "max": 10000}, "y": {"type": "integer", "min": 0, "max": 10000}}
_FRAME_BINDING = {
    "frame_generation": {"type": "integer", "min": 1, "max": 2**63 - 1, "required": True},
    "layout_hash": {"type": "string", "pattern": r"^[a-f0-9]{64}$", "required": True},
    "frame_sha256": {"type": "string", "pattern": r"^[a-f0-9]{64}$", "required": True},
    "captured_at": {"type": "integer", "min": 1, "max": 2**63 - 1, "required": True},
    "focused_target_hash": {"type": "string", "pattern": r"^[a-f0-9]{64}$", "required": True},
}
_COMBO = r"^[A-Za-z0-9_+\- ]{1,60}$"
def _CU_SPECS(rows: list[tuple[str, R, str, dict[str, dict[str, Any]], str]]) -> list[tuple[str, R, str, dict[str, dict[str, Any]], str]]:  # noqa: N802
    return rows


SERVICE_NAME = r"^[A-Za-z0-9_.\- ]{1,80}$"
PROCESS_NAME = r"^[A-Za-z0-9_.\- ]{1,80}(\.exe)?$"
COMMAND_ID = r"^[A-Za-z0-9_-]{1,100}$"

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
    ActionSpec("outlook.pdf_crash.inspect", "desktop", R.READ_ONLY,
               "Inspect the fixed Outlook/PDF add-in prerequisites and matching crash evidence without arbitrary targets.",
               {"addin_progid": {"type": "string", "pattern": r"^[A-Za-z0-9_.\-]{1,120}$", "required": True},
                "faulting_module": {"type": "string", "pattern": r"^[A-Za-z0-9_.\-]{1,180}$", "required": True},
                "lookback_hours": {"type": "integer", "min": 1, "max": 168, "required": True},
                "since": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,7})?(?:Z|[+\-]\d{2}:\d{2})$"}},
               "Reads fixed Outlook installation, add-in policy, interactive-session, and Application crash-log facts."),
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
    ActionSpec("office.addin.restore_load_behavior", "desktop", R.SENSITIVE,
               "Restore the exact typed add-in state saved locally for an earlier approved change.",
               {"source_command_id": {"type": "string", "pattern": COMMAND_ID, "required": True}},
               "Restores only the local journal state for the named change command; no registry state is accepted from the server.",
               default_postconditions=({"type": "device_addin_restore_matches", "params_from": {"source_command_id": "source_command_id"}},)),
    ActionSpec("office.addin.verify_restored", "desktop", R.READ_ONLY,
               "Compare the live add-in setting with the original state retained in the PC's local journal.",
               {"source_command_id": {"type": "string", "pattern": COMMAND_ID, "required": True}},
               "Reads the fixed local journal target and registry value; no saved registry state leaves the PC."),
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
    # ---------------- v13 Windows Fix (typed, run-bound; each must be listed on the plan card the customer approved) -------
    ActionSpec("printer.list", "desktop", R.READ_ONLY, "List installed printers and their drivers.", {}, "None (read-only)."),
    ActionSpec("app.uninstall", "desktop", R.PRIVILEGED,
               "Uninstall the ONE installed program whose name matches exactly (registered uninstaller; admin via the broker or a UAC prompt).",
               {"name": {"type": "string", "pattern": r"^[\w .,+()&'\-]{2,120}$", "required": True}},
               "Removes the program from this PC. Not reversible by JoltIQ (reinstall it to undo).",
               reversible=False, requires_admin=True, run_bound=True),
    ActionSpec("system.restart", "desktop", R.PRIVILEGED,
               "Restart the PC after a visible 1-10 minute warning (cancellable with system.power_cancel / STOP).",
               {"delay_seconds": {"type": "integer", "min": 60, "max": 600, "required": True}},
               "Restarts Windows; unsaved work in open apps is lost. The JoltIQ session ends.", reversible=False, run_bound=True),
    ActionSpec("system.shutdown", "desktop", R.PRIVILEGED,
               "Shut down the PC after a visible 1-10 minute warning (cancellable with system.power_cancel / STOP).",
               {"delay_seconds": {"type": "integer", "min": 60, "max": 600, "required": True}},
               "Turns the PC off; unsaved work is lost; someone must switch it back on.", reversible=False, run_bound=True),
    ActionSpec("system.power_cancel", "desktop", R.LOW_RISK, "Cancel a pending JoltIQ restart/shutdown (shutdown /a).", {},
               "Aborts the pending restart or shutdown."),
    ActionSpec("driver.install", "desktop", R.PRIVILEGED,
               "Download a printer driver from the vendor's OFFICIAL domain (https), check its vendor signature, install it with admin rights.",
               {"vendor": {"type": "string", "enum": ["hp", "canon", "epson", "brother"], "required": True},
                "model": {"type": "string", "max": 80, "required": True},
                "url": {"type": "string", "max": 1000, "pattern": r"^https://[^\s]+$", "required": True},
                "sha256": {"type": "string", "pattern": r"^[a-f0-9]{64}$"},
                "silent_args": {"type": "string", "enum": ["", "/s", "/S", "/quiet", "-s", "/silent"]}},
               "Installs vendor driver software; may restart the print spooler. Uninstall it from Settings to undo.",
               reversible=False, requires_admin=True, run_bound=True),
    # ---------------- Computer use (screen-driving; issued only by a consented computer-use run) ----------------
    # Coordinates are in the pixel space of the most recent screenshot the device sent; the device maps them back.
    *[ActionSpec(i, "desktop", r, d, p, e, computer_use=True) for i, r, d, p, e in _CU_SPECS([
        ("screen.capture", R.READ_ONLY, "Capture the screen (scaled; secret fields are not masked — sent only to approved AI providers).",
         {"max_long_edge": {"type": "integer", "min": 640, "max": 1920}}, "None (read-only)."),
        ("input.click", R.SENSITIVE, "Click at a screen coordinate.",
         {**_XY, **_FRAME_BINDING, "button": {"type": "string", "enum": ["left", "right", "middle"]}, "count": {"type": "integer", "min": 1, "max": 3},
          "modifiers": {"type": "string", "pattern": _COMBO}}, "Clicks on the user's screen."),
        ("input.drag", R.SENSITIVE, "Press, drag and release the left mouse button.",
         {**_XY, **_FRAME_BINDING, "to_x": {"type": "integer", "min": 0, "max": 10000, "required": True},
          "to_y": {"type": "integer", "min": 0, "max": 10000, "required": True}}, "Drags on the user's screen."),
        ("input.move", R.LOW_RISK, "Move the mouse pointer.", {**_XY, **_FRAME_BINDING}, "Moves the pointer."),
        ("input.mouse", R.SENSITIVE, "Press or release the left mouse button.", {**_FRAME_BINDING, "state": {"type": "string", "enum": ["down", "up"], "required": True}},
         "Presses/releases the mouse button."),
        ("input.type", R.SENSITIVE, "Type non-secret text at the keyboard focus (secrets are refused server- and device-side).",
         {**_FRAME_BINDING, "text": {"type": "string", "max": 2000, "required": True}}, "Types text on the user's computer."),
        ("input.key", R.SENSITIVE, "Press a key or key combination (e.g. Return, ctrl+s).",
         {**_FRAME_BINDING, "combo": {"type": "string", "pattern": _COMBO, "required": True}, "repeat": {"type": "integer", "min": 1, "max": 50},
          "hold_seconds": {"type": "integer", "min": 0, "max": 10}}, "Presses keys on the user's computer."),
        ("input.scroll", R.LOW_RISK, "Scroll at a position.",
         {**_FRAME_BINDING, "x": {"type": "integer", "min": 0, "max": 10000}, "y": {"type": "integer", "min": 0, "max": 10000},
          "direction": {"type": "string", "enum": ["up", "down", "left", "right"], "required": True},
          "amount": {"type": "integer", "min": 1, "max": 30}}, "Scrolls."),
        ("input.wait", R.READ_ONLY, "Wait, then capture the screen.", {"seconds": {"type": "integer", "min": 1, "max": 30, "required": True}}, "None."),
        ("input.cursor", R.READ_ONLY, "Report the pointer position.", {}, "None."),
        ("url.open", R.SENSITIVE, "Open an approved public HTTPS URL in the user's default browser (where they are already signed in).",
         {"url": {"type": "string", "max": 2000, "pattern": r"^https://[^\s]+$", "required": True},
          "allowed_origins": {"type": "array", "min_items": 1, "max_items": 1, "items": {
              "type": "string", "max": 300, "pattern": r"^https://[A-Za-z0-9.-]+$"}, "required": True}},
         "Opens one approved public HTTPS origin on the user's computer."),
        ("guide.highlight", R.READ_ONLY, "Show a click-through highlight and instruction without controlling input.",
         {**_FRAME_BINDING,
          "x": {"type": "integer", "min": 0, "max": 10000, "required": True},
          "y": {"type": "integer", "min": 0, "max": 10000, "required": True},
          "width": {"type": "integer", "min": 1, "max": 10000, "required": True},
          "height": {"type": "integer", "min": 1, "max": 10000, "required": True},
          "frame_width": {"type": "integer", "min": 1, "max": 10000, "required": True},
          "frame_height": {"type": "integer", "min": 1, "max": 10000, "required": True},
          "instruction": {"type": "string", "max": 500, "required": True},
          "expected_control_label": {"type": "string", "max": 200, "required": True}},
         "Shows visual guidance only; it never clicks or types."),
        ("guide.clear", R.READ_ONLY, "Remove the Guide highlight.", {}, "Removes visual guidance only."),
    ])],
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


# v14 Windows technician catalog (windows_catalog.py): typed, run-bound desktop actions.
from . import windows_catalog as _wc  # noqa: E402

for _s in _wc.specs():
    ACTIONS.setdefault(_s.id, _s)


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
        elif t == "array":
            if not isinstance(v, list):
                raise ActionValidationError(f"{action_id}.{name} must be an array")
            if len(v) < rule.get("min_items", 0) or len(v) > rule.get("max_items", 100):
                raise ActionValidationError(f"{action_id}.{name} has invalid item count")
            item_rule = rule.get("items", {})
            for item in v:
                if item_rule.get("type", "string") != "string" or not isinstance(item, str):
                    raise ActionValidationError(f"{action_id}.{name} items must be strings")
                if len(item) > item_rule.get("max", 2000):
                    raise ActionValidationError(f"{action_id}.{name} item too long")
                if "pattern" in item_rule and not re.fullmatch(item_rule["pattern"], item):
                    raise ActionValidationError(f"{action_id}.{name} item has invalid format")
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
            for a in ACTIONS.values() if a.risk != R.DESTRUCTIVE and not a.computer_use and not a.run_bound]
