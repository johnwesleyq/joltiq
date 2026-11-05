"""v13 Windows Fix: turn the customer's request into the EXACT typed Windows actions the plan card shows.

Only what is listed here (and approved once on the plan card, with an extra "I understand" tick for high-risk items)
can run in that Fix. The computer-use model can trigger a listed item with the ``windows_action`` tool; anything
not listed is refused and the customer is told to start a new request. Mirrors WindowsFixRules.cs on the companion.
"""
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import urlsplit

# vendor id -> (label, official download domains, keywords in a request). Keep in sync with WindowsFixRules.Vendors.
VENDORS: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    "hp": ("HP", ("hp.com",), ("hp", "hewlett", "laserjet", "deskjet", "officejet", "envy")),
    "canon": ("Canon", ("canon.com", "usa.canon.com", "canon-europe.com"), ("canon", "pixma", "imageclass")),
    "epson": ("Epson", ("epson.com", "ess.epson.biz"), ("epson", "ecotank", "workforce")),
    "brother": ("Brother", ("brother.com",), ("brother",)),
}
# v14 software vendors for app.install (keep in sync with WindowsFixRules.Vendors): id -> (label, domains, keywords).
APP_VENDORS: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    "microsoft": ("Microsoft", ("download.microsoft.com", "go.microsoft.com", "officecdn.microsoft.com"), ("teams", "office", "microsoft 365", "onedrive", "edge")),
    "google": ("Google", ("dl.google.com",), ("chrome", "google chrome", "google drive")),
    "mozilla": ("Mozilla", ("download.mozilla.org", "download-installer.cdn.mozilla.net"), ("firefox", "thunderbird")),
    "adobe": ("Adobe", ("ardownload2.adobe.com", "get.adobe.com"), ("acrobat", "adobe reader", "acrobat reader", "pdf reader")),
    "zoom": ("Zoom", ("zoom.us",), ("zoom",)),
}
ALL_VENDORS = {**VENDORS, **APP_VENDORS}
POWER_DELAY_S = 120
APP_NAME = re.compile(r"^[\w .,+()&'\-]{2,120}$")
_URL = re.compile(r"https://[^\s\"'<>]+", re.I)
_UNINSTALL = re.compile(r"\b(?:uninstall|remove|get rid of)\s+(?:[\"“]([^\"”]{2,120})[\"”]|(.+?)(?=\s*(?:,|;|\.\s|\.$|$|\band\b|\bthen\b|\bfrom (?:my|this|the)\b)))", re.I)
_RESTART = re.compile(r"\b(restart|reboot)\b", re.I)
_SHUTDOWN = re.compile(r"\b(shut ?down|power (?:off|down)|turn off (?:the |my |this )?(?:pc|computer|laptop))\b", re.I)
_DRIVER = re.compile(r"\b(driver|drivers|printer|print)\b", re.I)


def host_allowed(vendor: str, host: str) -> bool:
    host = (host or "").lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in ALL_VENDORS[vendor][1])


def check_driver_url(vendor: str, url: str) -> str | None:
    if vendor not in ALL_VENDORS:
        return "vendor is not on the official-vendor list"
    try:
        u = urlsplit(url)
    except ValueError:
        return "url is invalid"
    if u.scheme != "https":
        return "only https downloads are allowed"
    if u.username or u.password or u.port not in (None, 443):
        return "url must not carry credentials or a custom port"
    if not host_allowed(vendor, u.hostname or ""):
        return f"{u.hostname} is not an official {ALL_VENDORS[vendor][0]} download domain"
    if not re.search(r"\.(exe|msi)$", u.path, re.I):
        return "only .exe or .msi installers are allowed"
    return None


def _clean_name(raw: str) -> str:
    raw = raw.strip()
    if len(raw) > 2 and raw[0] in "\"“'" and raw[-1] in "\"”'":
        return raw[1:-1].strip()  # quoted: exactly what the customer typed
    name = re.sub(r"^(?:the|my)\s+", "", raw, flags=re.I)
    name = re.sub(r"\s+(?:app|application|program|software)$", "", name, flags=re.I)
    return name.strip(" .")


def _vendor_and_model(goal: str) -> tuple[str | None, str | None]:
    low = goal.lower()
    for vid, (_label, _d, keys) in VENDORS.items():
        if any(re.search(rf"\b{re.escape(k)}\b", low) for k in keys):
            m = re.search(rf"\b(?:{'|'.join(map(re.escape, keys))})\b\s*((?:[a-z]+\s)?[a-z]*\s?\d{{2,5}}[a-z0-9-]*)", goal, re.I)
            model = re.sub(r"\s+", " ", m.group(1)).strip() if m else None
            return vid, model
    return None, None


def parse(goal: str) -> list[dict[str, Any]]:
    """Return plan items: {action, params, risk, high_risk, title, detail}. Deterministic; never guesses a URL."""
    goal = goal or ""
    items: list[dict[str, Any]] = []
    for m in _UNINSTALL.finditer(goal):
        name = m.group(1).strip() if m.group(1) else _clean_name(m.group(2))
        if re.search(r"\b(driver|virus|malware|everything|all)\b", name, re.I) or not APP_NAME.fullmatch(name):
            continue
        items.append({"action": "app.uninstall", "params": {"name": name}, "risk": "PRIVILEGED", "high_risk": True,
                      "title": f"Uninstall “{name}”",
                      "detail": f"Removes the ONE installed program named “{name}” (or “{name} (…edition)”) using its own uninstaller (admin rights via the "
                                "JoltIQ broker, or a Windows UAC prompt you answer). If the name doesn't match exactly one program, "
                                "nothing is removed and I tell you the closest names. JoltIQ can't undo this (reinstall to undo)."})
    vendor, model = _vendor_and_model(goal)
    if _DRIVER.search(goal) and vendor and re.search(r"\bdrivers?\b|\b(?:re)?install\b", goal, re.I):
        label, domains, _k = VENDORS[vendor]
        url = next((u.rstrip(".,)") for u in _URL.findall(goal) if check_driver_url(vendor, u.rstrip(".,)")) is None), None)
        model = model or "printer"
        params: dict[str, Any] = {"vendor": vendor, "model": f"{label} {model}" if not model.lower().startswith(label.lower()) else model}
        if url:
            params["url"] = url
        src = f"exactly {url}" if url else f"the official {label} support site ({', '.join(domains)} only; I find the file there)"
        items.append({"action": "driver.install", "params": params, "risk": "PRIVILEGED", "high_risk": True,
                      "title": f"Install the {label} driver for “{params['model']}”",
                      "detail": f"Download from {src} over https, refuse any file that isn't .exe/.msi or isn't digitally signed by "
                                f"{label}, then install it with admin rights (JoltIQ broker or a Windows UAC prompt). Saved in "
                                r"C:\ProgramData\JoltIQ\Downloads. Check: the printer shows the new driver."})
    items += _tech(goal)
    power = "system.shutdown" if _SHUTDOWN.search(goal) else "system.restart" if _RESTART.search(goal) else None
    if power and not re.search(r"\b(?:restart|reboot)\s+(?:the\s+|my\s+)?(?:[\w-]+\s+){0,2}(?:spooler|services?|outlook|apps?|application|teams|"
                               r"excel|word|browser|router|modem|printer|program)\b", goal, re.I):
        verb = "Shut down" if power == "system.shutdown" else "Restart"
        items.append({"action": power, "params": {"delay_seconds": POWER_DELAY_S}, "risk": "PRIVILEGED", "high_risk": True,
                      "title": f"{verb} this PC (HIGH RISK)",
                      "detail": f"{verb} Windows {POWER_DELAY_S // 60} minutes after everything else is done. Windows shows a warning "
                                "first; save your work. Unsaved work in open apps is lost and this JoltIQ session ends. Press STOP "
                                "before then to cancel it." + (" Someone must switch the PC back on." if power == "system.shutdown" else "")})
    # Power actions always run last (stable sort keeps the rest in order).
    items.sort(key=lambda i: i["action"] in ("system.sleep", "system.restart", "system.shutdown"))
    return [_enrich(it) for it in items]


_HOST = re.compile(r"\b(?:ping|traceroute|tracert|trace(?: the)? route to)\s+(?:to\s+)?([A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?)\b", re.I)
_ADAPTER = re.compile(r"\breset\s+(?:the\s+|my\s+)?(?:network\s+)?adapter\s+(?:[\"“]([^\"”]{1,80})[\"”]|([A-Za-z][\w-]*(?: \d+)?))", re.I)
_ADAPTER_BARE = re.compile(r"\breset\s+(?:the\s+|my\s+)?(?:wi-?fi|wireless|ethernet|network)(?:\s+(?:card|adapter))?\b", re.I)
_SET_DEFAULT = re.compile(r"\b(?:set|make)\s+(?:[\"“]([^\"”]{1,120})[\"”]|(?:the\s+)?(.{1,60}?))\s+(?:as\s+)?(?:the\s+|my\s+)?default(?:\s+printer)?\b", re.I)
_SERVICE_WORDS = {"spooler": "Spooler", "print spooler": "Spooler", "windows update": "wuauserv", "wuauserv": "wuauserv", "bits": "BITS",
                  "dns client": "Dnscache", "dnscache": "Dnscache", "windows time": "W32Time", "w32time": "W32Time", "time service": "W32Time",
                  "windows search": "WSearch", "search service": "WSearch", "wsearch": "WSearch", "audio": "AudioSrv", "windows audio": "AudioSrv",
                  "audiosrv": "AudioSrv", "sound": "AudioSrv"}


def _item(action: str, params: dict[str, Any], detail: str = "", title: str = "") -> dict[str, Any]:
    from .actions.windows_catalog import BY_ID

    e = BY_ID[action]
    return {"action": action, "params": params, "risk": str(e.risk), "high_risk": e.high_risk,
            "title": title or e.title + (" (HIGH RISK)" if e.high_risk else ""),
            "detail": detail or f"{e.description} Check: {e.verify} Undo: {e.rollback}", "category": e.category,
            "effects": e.effects, "verify": e.verify, "rollback": e.rollback,
            "requires_admin": e.requires_admin, "reversible": e.reversible}


def _enrich(value: dict[str, Any]) -> dict[str, Any]:
    """Attach catalog-owned execution metadata to hand-written legacy plan cards."""
    from .actions.windows_catalog import BY_ID

    out = dict(value)
    entry = BY_ID[out["action"]]
    out.setdefault("category", entry.category)
    out.setdefault("effects", entry.effects)
    out.setdefault("verify", entry.verify)
    out.setdefault("rollback", entry.rollback)
    out.setdefault("requires_admin", entry.requires_admin)
    out.setdefault("reversible", entry.reversible)
    return out


def item(action: str, params: dict[str, Any]) -> dict[str, Any]:
    """Build an approval-card item whose risk and execution contract come from the catalog."""
    return _item(action, params)


def _service_in(text: str) -> str | None:
    low = text.lower()
    for k in sorted(_SERVICE_WORDS, key=len, reverse=True):
        if re.search(rf"\b{re.escape(k)}\b", low):
            return _SERVICE_WORDS[k]
    return None


def _tech(goal: str) -> list[dict[str, Any]]:
    """v14 catalog rules: plain English -> catalog actions. Conservative: only clear phrasings produce a change."""
    low = goal.lower()
    out: list[dict[str, Any]] = []
    add = out.append
    has = lambda pat: re.search(pat, low) is not None  # noqa: E731
    # Network
    if has(r"\b(wi-?fi|wireless|wlan)\b") and has(r"\b(signal|report|strength)\b"):
        # Only on an explicit ask; a plain "Wi-Fi keeps dropping" goes to the structured network diagnostics.
        add(_item("network.wifi_report", {}))
    for m in _HOST.finditer(goal):
        host = m.group(1).rstrip(".")
        act = "network.ping" if m.group(0).lower().startswith("ping") else "network.traceroute"
        add(_item(act, {"host": host, **({"count": 4} if act == "network.ping" else {})}, title=f"{BY_TITLE[act]} {host}"))
    if has(r"\bproxy|\bpac file"):
        add(_item("network.proxy_check", {}))
    if has(r"\b(flush|clear|reset)\s+(?:(?:the|my)\s+)?dns\b|\bflushdns\b|\bdns cache\b"):
        add(_item("network.flush_dns", {}))
    if has(r"\b(check|diagnose|test)\s+(?:(?:the|my)\s+)?network\b|\bnetwork diagnostics?\b"):
        add(_item("network.diagnostics", {}))
    if has(r"\b(renew|release)\b.{0,20}\bip\b|\bnew ip\b|\bipconfig /renew\b"):
        add(_item("network.renew_ip", {}))
    adapter_match = _ADAPTER.search(goal)
    if adapter_match:
        name = (adapter_match.group(1) or adapter_match.group(2)).strip()
        if re.fullmatch(r"[A-Za-z0-9 _().#\-]{1,80}", name):
            add(_item("network.adapter_reset", {"adapter": name}, title=f"Reset network adapter “{name}” (HIGH RISK)"))
    elif _ADAPTER_BARE.search(goal):
        name = "Wi-Fi" if has(r"wi-?fi|wireless") else "Ethernet"
        add(_item("network.adapter_reset", {"adapter": name}, title=f"Reset network adapter “{name}” (HIGH RISK)"))
    # Services
    svc = _service_in(goal)
    if has(r"\b(list|show)\b.{0,15}\bservices\b"):
        add(_item("service.list", {}))
    if svc:
        from .actions.windows_catalog import SERVICE_LABELS

        label = SERVICE_LABELS[svc]
        for verb, act in (("restart", "service.restart"), ("start", "service.start"), ("stop", "service.stop")):
            if has(rf"\b{verb}\b(?:\s+\w+){{0,3}}\s+(?:the\s+)?(?:{'|'.join(re.escape(k) for k in _SERVICE_WORDS if _SERVICE_WORDS[k] == svc)})\b"):
                add(_item(act, {"name": svc}, title=f"{verb.title()} the {label} service" + (" (HIGH RISK)" if act == "service.stop" else ""),
                          detail=f"{verb.title()} the Windows service {svc} ({label}) via the JoltIQ broker or a UAC prompt. "
                                 "Check: the service status afterwards."))
                break
    # Printers
    if has(r"\b(stuck|clear|purge|cancel)\b.{0,25}\b(print(?:er)?\s*(?:queue|jobs?)|queue)\b|\bprint jobs? (?:are )?stuck\b"):
        add(_item("printer.clear_queue", {}))
    if has(r"\b(printer|printing|print)\b.{0,30}\b(offline|not printing|won'?t print|doesn'?t print|stopped)\b|\b(list|show)\b.{0,10}\bprinters\b"):
        add(_item("printer.list", {}))
    default_match = _SET_DEFAULT.search(goal)
    if default_match:
        name = (default_match.group(1) or default_match.group(2) or "").strip(" .")
        name = re.sub(r"^(?:the|my)\s+", "", name, flags=re.I)
        if name and re.fullmatch(r"[\w .,()#&+\-]{1,120}", name) and name.lower() not in ("it", "this", "that", "printer"):
            add(_item("printer.set_default", {"name": name}, title=f"Set “{name}” as the default printer"))
    # Software
    if has(r"\b(list|show|what)\b.{0,20}\b(programs|apps|software)\b.{0,15}(installed)?"):
        add(_item("app.list", {}))
    install_match = re.search(r"\b(?:install|reinstall|download and install)\s+(?:the\s+)?(?:latest\s+)?([\w .+-]{2,40}?)(?=\s*(?:,|;|\.\s|\.$|$|\band\b|\bthen\b|\bfrom\b|\bon\b))", goal, re.I)
    if install_match and not _DRIVER.search(install_match.group(1)) and not has(r"\binstall\b.{0,20}\bupdates?\b"):
        prod = install_match.group(1).strip()
        vid = next((v for v, (_l, _d, keys) in APP_VENDORS.items() if any(re.search(rf"\b{re.escape(k)}\b", prod.lower()) for k in keys)), None)
        if vid:
            label, domains, _k = APP_VENDORS[vid]
            p: dict[str, Any] = {"vendor": vid, "product": prod[:80]}
            url = next((u.rstrip(".,)") for u in _URL.findall(goal) if check_driver_url(vid, u.rstrip(".,)")) is None), None)
            if url:
                p["url"] = url
            src = f"exactly {url}" if url else f"{label}'s official download site ({', '.join(domains)} only)"
            add(_item("app.install", p, title=f"Install “{prod}” from {label} (HIGH RISK)",
                      detail=f"Download from {src} over https; refuse anything that isn't .exe/.msi or isn't digitally signed by {label}; "
                             "install with admin rights. Undo: uninstall it."))
    # Power
    if has(r"\b(put|send)\b.{0,20}\bto sleep\b|\bsleep (?:mode|the pc|my pc|the computer)\b"):
        add(_item("system.sleep", {"delay_seconds": 60}))
    # Disk
    if has(r"\b(disk|drive|storage)\b.{0,15}\b(space|full|usage)\b|\b(space|full)\b.{0,15}\b(disk|drive|c:)|\bslow (?:pc|computer|laptop)\b|\b(pc|computer|laptop) is slow\b"):
        add(_item("disk.usage", {}))
    if has(r"\b(clean|clear|delete|remove)\b.{0,15}\b(temp|temporary|junk)\b"):
        add(_item("disk.cleanup_temp", {"older_than_days": 7}))
    if has(r"\b(disk|drive|ssd|hard drive)\b.{0,15}\b(health|failing|dying|smart)\b"):
        add(_item("disk.health", {}))
    # Updates
    if has(r"\b(check|scan|look)\b.{0,15}\b(for\s+)?(windows\s+)?updates?\b"):
        add(_item("update.scan", {}))
    if has(r"\bupdate (?:status|history)\b|\b(pending|last|recent) (?:windows )?updates?\b|\bis (?:windows|it) up to date\b"):
        add(_item("update.status", {}))
    # Accounts
    if has(r"\b(local )?(users|accounts)\b.{0,10}\b(on this pc|local|list)\b|\blist (?:local )?(?:users|accounts)\b"):
        add(_item("account.list_local", {}))
    if has(r"\bwho ?am ?i\b|\bam i (?:an )?admin\b|\bwhich account\b"):
        add(_item("account.whoami", {}))
    # Event logs
    if has(r"\b(recent|latest|last)\b.{0,10}\berrors?\b|\bevent ?(?:log|viewer)\b|\bbsod\b|\bblue screen\b|\bcrash(?:es|ed|ing)?\b"):
        add(_item("eventlog.recent_errors", {"hours": 24}))
    # De-duplicate by action+params
    seen: set[str] = set()
    uniq = []
    for it in out:
        k = it["action"] + json.dumps(it["params"], sort_keys=True)
        if k not in seen:
            seen.add(k)
            uniq.append(it)
    return uniq


BY_TITLE = {"network.ping": "Ping", "network.traceroute": "Traceroute to"}


def needs_ack(items: list[dict[str, Any]]) -> bool:
    return any(i.get("high_risk") for i in items)


def _same_app_name_with_minor_typo(requested: str, installed: str) -> bool:
    """Accept only a very close correction of the approved app name.

    The Windows companion still requires exactly one installed-program match. This
    only lets a discovered canonical name correct a small spelling error such as
    ``genunine`` -> ``genuine``; it cannot add/drop words or select an edition.
    """
    clean = lambda value: re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()  # noqa: E731
    left, right = clean(requested), clean(installed)
    if not left or not right or left == right:
        return left == right
    left_words, right_words = left.split(), right.split()
    if len(left_words) != len(right_words) or len(left) < 8:
        return False
    changed = [
        (a, b) for a, b in zip(left_words, right_words, strict=True)
        if a != b
    ]
    return (
        len(changed) == 1
        and min(map(len, changed[0])) >= 5
        and SequenceMatcher(None, *changed[0]).ratio() >= 0.85
        and SequenceMatcher(None, left, right).ratio() >= 0.94
    )


def match(items: list[dict[str, Any]], action: str, params: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """Is (action, params) the planned item? Returns (canonical params to send, reason-if-not)."""
    for it in items:
        if it["action"] != action:
            continue
        p = it["params"]
        if action == "app.uninstall":
            actual = str(params.get("name", "")).strip()
            if actual.casefold() == p["name"].casefold():
                return dict(p), ""
            if _same_app_name_with_minor_typo(p["name"], actual):
                return {"name": actual}, ""
            return None, f"the approved plan only uninstalls “{p['name']}”"
        if action in ("system.restart", "system.shutdown", "system.sleep"):
            return dict(p), ""
        if action in ("driver.install", "app.install"):
            if params.get("vendor", p["vendor"]) != p["vendor"]:
                return None, f"the approved plan only installs a {ALL_VENDORS[p['vendor']][0]} driver"
            url = p.get("url") or str(params.get("url") or "")
            if p.get("url") and params.get("url") and params["url"] != p["url"]:
                return None, f"the approved plan only downloads {p['url']}"
            why = check_driver_url(p["vendor"], url)
            if why:
                return None, why
            out = {"vendor": p["vendor"], "url": url}
            out["model" if action == "driver.install" else "product"] = p["model" if action == "driver.install" else "product"]
            for k in ("sha256", "silent_args"):
                if params.get(k) is not None:
                    out[k] = params[k]
            return out, ""
        # v14 catalog actions: the planned params are canonical; the model can't widen them.
        if action in ("network.ping", "network.traceroute") and params.get("host") and str(params["host"]).lower() != p["host"].lower():
            continue  # maybe another planned ping/traceroute matches
        return dict(p), ""
    return None, "that change is not on the plan the customer approved; ask them to start a new Fix request for it"


def plan_prompt(items: list[dict[str, Any]]) -> str:
    if not items:
        return ""
    lines = "\n".join(
        f"- {it['action']} {json.dumps(it['params'])}: {it['title']}\n"
        f"  Effect: {it.get('effects', 'Not specified.')}\n"
        f"  Verify: {it.get('verify', 'Report the typed action result.')}\n"
        f"  Rollback: {it.get('rollback', 'Not specified.')}"
        for it in items
    )
    return ("\n\nAPPROVED WINDOWS ACTIONS (the customer approved exactly these; run each with the windows_action tool, at most "
            "once. Diagnose before changing; run read-only checks first and use their result; verify each result after changing. "
            "The structured windows_action result is the verification evidence; never open Command Prompt or use screen input "
            "just to repeat or display a successful typed result. "
            "Stop on a failed verification instead of continuing. Run restart/shutdown/sleep LAST after everything else is verified; for a "
            "driver or app install without a URL, open the vendor's official support or download site with open_url, find the installer for this exact model and Windows version, and pass its https URL):\n"
            + lines)
