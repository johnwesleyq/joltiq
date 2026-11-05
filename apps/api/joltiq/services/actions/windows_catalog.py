"""v14 Windows technician action catalog: what JoltIQ can actually DO on a Windows PC.

The language model already knows IT theory ("flush DNS when sites don't resolve"). This catalog is the separate,
typed, allowlisted list of things the companion can execute, each with a risk tier, parameters, how the result is
verified and how (or whether) it can be undone. ``windows_plan.parse`` maps a plain-English request onto these ids;
the plan card shows them; one Approve (plus the "I understand" tick for high-risk items) + the local Allow on the PC
covers exactly those. Read-only entries can also be called during any approved Fix to diagnose.

Mirrors apps/windows-agent/src/JoltIQ.Agent.Core/WindowsTechRules.cs (+ LocalPolicy). ``status``:
``live`` = implemented in the Windows companion (and faked in the simulator); ``stub`` = NOT_IMPLEMENTED.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...models.enums import RiskLevel as R

HOST = r"^(?=.{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?)*$"
ADAPTER = r"^[A-Za-z0-9 _().#\-]{1,80}$"
PRINTER = r"^[\w .,()#&+\-]{1,120}$"
# Same default allowlist as LocalPolicy.AllowedServices / the Broker.
SERVICES = ["Spooler", "wuauserv", "BITS", "Dnscache", "W32Time", "WSearch", "AudioSrv"]
SERVICE_LABELS = {"Spooler": "Print Spooler", "wuauserv": "Windows Update", "BITS": "Background Intelligent Transfer",
                  "Dnscache": "DNS Client", "W32Time": "Windows Time", "WSearch": "Windows Search", "AudioSrv": "Windows Audio"}
APP_VENDORS = ["microsoft", "google", "mozilla", "adobe", "zoom"]


@dataclass(frozen=True)
class Entry:
    id: str
    category: str
    risk: R
    title: str
    description: str
    params: dict[str, dict[str, Any]] = field(default_factory=dict)
    effects: str = "None (read-only)."
    verify: str = ""
    rollback: str = "Nothing to undo (read-only)."
    high_risk: bool = False
    requires_admin: bool = False
    reversible: bool = True
    status: str = "live"
    existing: bool = False  # spec already defined in registry.py (v8-v13)

    @property
    def read_only(self) -> bool:
        return self.risk == R.READ_ONLY


CATEGORIES = ["Network", "Services", "Printers", "Software", "Power", "Disk & cleanup", "Updates", "Accounts", "Event logs",
              "System", "Browser & SaaS"]

CATALOG: list[Entry] = [
    # ---------------- Network ----------------
    Entry("network.diagnostics", "Network", R.READ_ONLY, "Network check", "Adapters, gateway reachability and DNS resolution.",
          {"resolve_host": {"type": "string", "pattern": HOST}}, verify="Gateway/DNS results returned.", existing=True),
    Entry("network.wifi_report", "Network", R.READ_ONLY, "Wi-Fi report", "Wi-Fi adapter state, SSID, signal, radio and link rate "
          "(netsh wlan show interfaces; no BSSID/MAC).", verify="Fields returned from netsh."),
    Entry("network.ping", "Network", R.READ_ONLY, "Ping a host", "Ping a host (1-10 echoes) and report loss and latency.",
          {"host": {"type": "string", "pattern": HOST, "required": True}, "count": {"type": "integer", "min": 1, "max": 10}},
          verify="Replies and loss % returned."),
    Entry("network.traceroute", "Network", R.READ_ONLY, "Traceroute", "tracert -d -h 15 to a host.",
          {"host": {"type": "string", "pattern": HOST, "required": True}}, verify="Hop list returned."),
    Entry("network.proxy_check", "Network", R.READ_ONLY, "Proxy check", "User proxy / PAC / auto-detect settings and the WinHTTP proxy.",
          verify="Settings returned."),
    Entry("network.flush_dns", "Network", R.LOW_RISK, "Flush DNS cache", "ipconfig /flushdns.", effects="Clears the DNS resolver cache.",
          verify="ipconfig exit code 0; follow with network.diagnostics.", rollback="Nothing to undo; the cache refills by itself."),
    Entry("network.renew_ip", "Network", R.SENSITIVE, "Renew IP address", "ipconfig /release then /renew (admin via the broker or a UAC prompt).",
          effects="Drops the network for a few seconds while DHCP renews.", verify="Adapters show an IPv4 address afterwards.",
          rollback="Nothing to undo; if no address comes back, restart the router or the PC.", requires_admin=True),
    Entry("network.adapter_reset", "Network", R.PRIVILEGED, "Reset network adapter", "Disable then re-enable ONE named adapter (netsh).",
          {"adapter": {"type": "string", "pattern": ADAPTER, "required": True}},
          effects="The adapter is offline for ~5 seconds; a remote session over it may drop.", verify="Adapter status is Up afterwards.",
          rollback="If it stays down: Settings → Network → the adapter → Enable (or restart the PC).", high_risk=True, requires_admin=True),
    # ---------------- Services ----------------
    Entry("service.list", "Services", R.READ_ONLY, "List services", "All Windows services with status and start type.", verify="List returned."),
    Entry("service.status", "Services", R.READ_ONLY, "Service status", "Status of one named service.", existing=True, verify="Status returned."),
    Entry("service.restart", "Services", R.PRIVILEGED, "Restart a service", "Restart an allowlisted service via the broker.",
          {"name": {"type": "string", "enum": SERVICES, "required": True}}, effects="The service stops and starts again.",
          verify="Service status is Running afterwards.", rollback="Nothing to undo.", requires_admin=True, existing=True),
    Entry("service.start", "Services", R.PRIVILEGED, "Start a service", "Start an allowlisted service via the broker.",
          {"name": {"type": "string", "enum": SERVICES, "required": True}}, effects="Starts the service.",
          verify="Service status is Running.", rollback="service.stop the same service.", requires_admin=True),
    Entry("service.stop", "Services", R.PRIVILEGED, "Stop a service", "Stop an allowlisted service via the broker.",
          {"name": {"type": "string", "enum": SERVICES, "required": True}}, effects="Stops the service until it is started again or the PC restarts.",
          verify="Service status is Stopped.", rollback="service.start the same service.", high_risk=True, requires_admin=True),
    # ---------------- Printers ----------------
    Entry("printer.list", "Printers", R.READ_ONLY, "List printers", "Installed printers, drivers and ports.", existing=True, verify="List returned."),
    Entry("printer.set_default", "Printers", R.SENSITIVE, "Set default printer", "Make ONE installed printer the user's default.",
          {"name": {"type": "string", "pattern": PRINTER, "required": True}}, effects="Changes the user's default printer.",
          verify="The default printer reads back as the requested one.", rollback="The previous default is returned; set it back the same way."),
    Entry("printer.clear_queue", "Printers", R.PRIVILEGED, "Clear stuck print queue", "Stop the Print Spooler, delete queued spool files "
          r"(C:\Windows\System32\spool\PRINTERS\*.SPL/*.SHD only), start the spooler.",
          effects="ALL waiting print jobs on this PC are deleted.", verify="Spooler Running; spool folder empty.",
          rollback="Not reversible: re-print the documents.", high_risk=True, requires_admin=True, reversible=False),
    # ---------------- Software ----------------
    Entry("app.list", "Software", R.READ_ONLY, "List installed programs", "Installed programs (optionally filtered by name).",
          {"name_filter": {"type": "string", "max": 80}}, verify="List returned."),
    Entry("app.uninstall", "Software", R.PRIVILEGED, "Uninstall a program", "Uninstall the ONE program whose name matches.",
          existing=True, effects="Removes the program.", verify="The program is no longer listed.", rollback="Reinstall it.",
          high_risk=True, requires_admin=True, reversible=False),
    Entry("app.install", "Software", R.PRIVILEGED, "Install software (allowlisted vendor)", "Download an .exe/.msi over https from an "
          "allowlisted vendor domain (Microsoft, Google, Mozilla, Adobe, Zoom), require that vendor's valid Authenticode signature, install.",
          {"vendor": {"type": "string", "enum": APP_VENDORS, "required": True}, "product": {"type": "string", "max": 80, "required": True},
           "url": {"type": "string", "max": 1000, "pattern": r"^https://[^\s]+$", "required": True},
           "sha256": {"type": "string", "pattern": r"^[a-f0-9]{64}$"},
           "silent_args": {"type": "string", "enum": ["", "/s", "/S", "/quiet", "-s", "/silent"]}},
          effects="Installs vendor software.", verify="Signature checked before running; app.list shows the product.",
          rollback="Uninstall it (app.uninstall or Settings → Apps).", high_risk=True, requires_admin=True, reversible=False),
    Entry("driver.install", "Software", R.PRIVILEGED, "Install printer driver (official vendor)", "HP/Canon/Epson/Brother driver from the "
          "vendor's official domain, signature-checked.", existing=True, effects="Installs driver software.",
          verify="printer.list shows the new driver.", rollback="Uninstall the driver package.", high_risk=True, requires_admin=True, reversible=False),
    # ---------------- Power ----------------
    Entry("system.restart", "Power", R.PRIVILEGED, "Restart the PC", "Restart after a 1-10 minute visible warning.", existing=True,
          effects="Unsaved work lost; session ends.", verify="Scheduled (shutdown /r).", rollback="STOP / system.power_cancel before it fires.",
          high_risk=True, reversible=False),
    Entry("system.shutdown", "Power", R.PRIVILEGED, "Shut down the PC", "Shut down after a 1-10 minute visible warning.", existing=True,
          effects="PC turns off.", verify="Scheduled (shutdown /s).", rollback="STOP / system.power_cancel before it fires.",
          high_risk=True, reversible=False),
    Entry("system.sleep", "Power", R.SENSITIVE, "Put the PC to sleep", "Sleep after a short delay (15-600 s).",
          {"delay_seconds": {"type": "integer", "min": 15, "max": 600}}, effects="The PC sleeps; the session pauses until it wakes.",
          verify="Scheduled; the companion reconnects after wake.", rollback="STOP / system.power_cancel before it fires; any key wakes it.",
          high_risk=True),
    Entry("system.power_cancel", "Power", R.LOW_RISK, "Cancel restart/shutdown/sleep", "shutdown /a and cancel a pending sleep.",
          existing=True, effects="Aborts the pending power action.", verify="Command exit code.", rollback="Nothing to undo."),
    # ---------------- Disk & cleanup ----------------
    Entry("disk.usage", "Disk & cleanup", R.READ_ONLY, "Disk space", "Free/total space per drive.", existing=True, verify="Drives returned."),
    Entry("disk.health", "Disk & cleanup", R.READ_ONLY, "Disk health", "Physical disk health status (Get-PhysicalDisk; chkdsk is never run).",
          verify="Health status returned."),
    Entry("disk.cleanup_temp", "Disk & cleanup", R.SENSITIVE, "Clean temp files", "Delete files older than N days (default 7) inside the "
          "user's %TEMP% folder only; files in use and links are skipped.",
          {"older_than_days": {"type": "integer", "min": 1, "max": 365}}, effects="Deletes old temporary files.",
          verify="Files deleted and MB freed are reported.", rollback="Not reversible (temporary files only).", high_risk=True, reversible=False),
    # ---------------- Updates ----------------
    Entry("update.status", "Updates", R.READ_ONLY, "Windows Update status", "Last 5 installed updates, reboot-pending flag, Windows Update service state.",
          verify="Status returned."),
    Entry("update.scan", "Updates", R.LOW_RISK, "Check for Windows updates", "Ask Windows Update to scan (UsoClient StartInteractiveScan). "
          "JoltIQ never installs updates itself.", effects="Windows Update starts a scan and may download updates per its own policy.",
          verify="Scan requested; see Settings → Windows Update.", rollback="Nothing to undo."),
    # ---------------- Accounts ----------------
    Entry("account.whoami", "Accounts", R.READ_ONLY, "Who am I", "Signed-in user, domain, machine, admin membership.", verify="Returned."),
    Entry("account.list_local", "Accounts", R.READ_ONLY, "List local users", "Local account names, enabled flag, last logon (no SIDs, no hashes).",
          verify="Returned."),
    # ---------------- Event logs ----------------
    Entry("eventlog.recent_errors", "Event logs", R.READ_ONLY, "Recent errors", "Error events from System and/or Application in the last N hours "
          "(messages redacted on the PC).", {"log": {"type": "string", "enum": ["Application", "System"]},
                                           "hours": {"type": "integer", "min": 1, "max": 168}}, verify="Events returned."),
    Entry("eventlog.query", "Event logs", R.READ_ONLY, "Event log query", "Filtered event log query.", existing=True, verify="Events returned."),
    # ---------------- System ----------------
    Entry("system.info", "System", R.READ_ONLY, "System info", "OS edition/version, uptime, CPU/RAM.", existing=True, verify="Returned."),
    Entry("process.list", "System", R.READ_ONLY, "Running programs", "Processes and whether they respond.", existing=True, verify="Returned."),
    # ---------------- Browser & SaaS ----------------
    Entry("browser.fix_sites", "Browser & SaaS", R.SENSITIVE, "Fix a website / SaaS setting", "Existing Fix-it-for-me browser path: JoltIQ "
          "drives the site in the customer's view after Approve + Allow.", effects="Changes the setting the request names.",
          verify="Seen on screen.", rollback="Change it back in the site.", existing=True),
]
BY_ID = {e.id: e for e in CATALOG}
NEW_IDS = [e.id for e in CATALOG if not e.existing]
# What the computer-use model may name in the windows_action tool.
TOOL_ACTIONS = sorted(e.id for e in CATALOG if e.id != "browser.fix_sites" and e.id not in ("eventlog.query",))


def specs():
    """ActionSpecs for the v14 entries (registry.py already defines the existing ones)."""
    from .registry import ActionSpec

    return [ActionSpec(e.id, "desktop", e.risk, e.description, dict(e.params), e.effects, reversible=e.reversible,
                       requires_admin=e.requires_admin, run_bound=True) for e in CATALOG if not e.existing]


def as_dict(e: Entry) -> dict[str, Any]:
    return {"id": e.id, "category": e.category, "risk": e.risk.value if hasattr(e.risk, "value") else str(e.risk), "title": e.title,
            "description": e.description, "params": e.params, "effects": e.effects, "verify": e.verify, "rollback": e.rollback,
            "high_risk": e.high_risk, "requires_admin": e.requires_admin, "reversible": e.reversible, "status": e.status,
            "needs_plan": not e.read_only}


def catalog() -> dict[str, Any]:
    out = [as_dict(e) for e in CATALOG]
    counts: dict[str, int] = {}
    for e in CATALOG:
        counts[e.category] = counts.get(e.category, 0) + 1
    return {"categories": CATEGORIES, "counts": counts, "total": len(out), "actions": out,
            "live": sum(1 for e in CATALOG if e.status == "live"), "stub": sum(1 for e in CATALOG if e.status == "stub")}
