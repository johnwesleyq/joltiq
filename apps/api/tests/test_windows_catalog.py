"""v14 Windows technician catalog + default MSP knowledge pack."""
from __future__ import annotations

import pytest
from test_ask_chat import _connect, _no_env_key, fake_openai  # noqa: F401
from test_computer_use import drain
from test_windows_fix import WIN_CAPS, _go, _start

from joltiq.services import windows_plan
from joltiq.services.actions import windows_catalog as wc
from joltiq.services.actions.registry import ACTIONS, ActionValidationError, registry_for_prompt, validate_params

TECH_CAPS = (*WIN_CAPS, *wc.NEW_IDS, "service.restart", "service.status", "disk.usage", "network.diagnostics", "system.info", "process.list")


def _acts(goal):
    return [(i["action"], i["params"]) for i in windows_plan.parse(goal)]


def test_catalog_shape_and_registry():
    c = wc.catalog()
    assert c["stub"] == 0 and c["live"] == c["total"] == len(wc.CATALOG)
    for cat in ("Network", "Services", "Printers", "Software", "Power", "Disk & cleanup", "Updates", "Accounts", "Event logs", "Browser & SaaS"):
        assert c["counts"][cat] >= 1
    for e in wc.CATALOG:
        assert e.verify and e.rollback, e.id
        if e.id != "browser.fix_sites":
            assert e.id in ACTIONS, e.id
    for i in wc.NEW_IDS:
        assert ACTIONS[i].run_bound  # only inside a consented Fix run, never by the planner
    prompt_ids = {x["id"] for x in registry_for_prompt()}
    assert not prompt_ids & set(wc.NEW_IDS)


@pytest.mark.parametrize("action,params,ok", [
    ("network.ping", {"host": "8.8.8.8", "count": 4}, True), ("network.ping", {"host": "a;rm -rf"}, False),
    ("network.ping", {"host": "x.com", "count": 50}, False), ("service.stop", {"name": "Spooler"}, True),
    ("service.stop", {"name": "WinDefend"}, False), ("network.adapter_reset", {"adapter": "Wi-Fi"}, True),
    ("network.adapter_reset", {"adapter": "Wi-Fi\" & calc"}, False), ("disk.cleanup_temp", {"older_than_days": 0}, False),
    ("app.install", {"vendor": "google", "product": "Chrome", "url": "https://dl.google.com/x.exe"}, True),
    ("app.install", {"vendor": "evil", "product": "x", "url": "https://dl.google.com/x.exe"}, False),
])
def test_param_validation(action, params, ok):
    if ok:
        validate_params(action, params)
    else:
        with pytest.raises(ActionValidationError):
            validate_params(action, params)


def test_natural_language_mapping():
    a = _acts("Check my wifi signal report, flush dns and renew my IP and ping 8.8.8.8")
    assert ("network.wifi_report", {}) in a and ("network.flush_dns", {}) in a and ("network.renew_ip", {}) in a
    assert ("network.ping", {"host": "8.8.8.8", "count": 4}) in a
    a = _acts("Print jobs stuck - clear the print queue, restart the print spooler and set “HP LaserJet 400” as default printer")
    assert ("printer.clear_queue", {}) in a and ("service.restart", {"name": "Spooler"}) in a
    assert ("printer.set_default", {"name": "HP LaserJet 400"}) in a and not [x for x in a if x[0] in ("driver.install", "system.restart")]
    a = _acts("install google chrome, clean temp files and check for updates")
    assert ("app.install", {"vendor": "google", "product": "google chrome"}) in a and ("disk.cleanup_temp", {"older_than_days": 7}) in a
    assert ("update.scan", {}) in a
    assert ("system.sleep", {"delay_seconds": 60}) in _acts("put the pc to sleep")
    assert ("service.stop", {"name": "WSearch"}) in _acts("stop the windows search service")
    assert ("eventlog.recent_errors", {"hours": 24}) in _acts("I got a blue screen this morning")
    assert ("account.whoami", {}) in _acts("am I admin on this laptop?")
    assert _acts("install some random tool from https://evil.example/x.exe") == []
    assert windows_plan.needs_ack(windows_plan.parse("clear the print queue"))
    assert not windows_plan.needs_ack(windows_plan.parse("flush dns"))


def test_shipped_windows_test_wording_maps_dns_flush_and_network_check():
    assert _acts("Flush my DNS and check my network.") == [
        ("network.flush_dns", {}),
        ("network.diagnostics", {}),
    ]


def test_app_install_url_allowlist_and_match():
    items = windows_plan.parse("install Firefox from https://download.mozilla.org/firefox-setup.exe")
    assert items[0]["params"]["url"] == "https://download.mozilla.org/firefox-setup.exe"
    assert windows_plan.check_driver_url("mozilla", "https://download.mozilla.org.evil.io/x.exe")
    items = windows_plan.parse("install zoom")
    out, why = windows_plan.match(items, "app.install", {"url": "https://zoom.us/client/latest/ZoomInstaller.exe"})
    assert out == {"vendor": "zoom", "product": "zoom", "url": "https://zoom.us/client/latest/ZoomInstaller.exe"}, why
    assert windows_plan.match(items, "app.install", {"url": "https://evil.example/ZoomInstaller.exe"})[0] is None
    assert windows_plan.match(windows_plan.parse("ping 1.1.1.1"), "network.ping", {"host": "9.9.9.9"})[0] is None


def test_catalog_endpoint(owner):
    r = owner.get("/api/v1/agents/windows-catalog")
    assert r.status_code == 200 and r.json()["total"] == len(wc.CATALOG)


def test_catalog_actions_end_to_end(app, owner):
    """flush DNS + ping + clear queue: one Approve (+ high-risk tick) + one PC Allow; fixture model runs each once."""
    import test_windows_fix as twf

    old = twf.WIN_CAPS
    twf.WIN_CAPS = TECH_CAPS
    try:
        d, sid, s = _start(app, owner, "Printing is stuck: flush dns, ping 8.8.8.8 and clear the print queue")
    finally:
        twf.WIN_CAPS = old
    plan = [i["action"] for i in s["request_details"]["full_fix"]["windows_actions"]]
    assert plan == ["network.ping", "network.flush_dns", "printer.clear_queue"], plan
    assert owner.post(f"/api/v1/sessions/{sid}/consent", json={"minutes": 30}).status_code == 409
    assert owner.post(f"/api/v1/sessions/{sid}/consent", json={"minutes": 30, "acknowledge_high_risk": True}).status_code == 200
    _rid, run, log = _go(owner, d, sid, s)
    assert run["state"] == "completed" and run["success"] is True, run
    sent = [(a, p) for a, p, _ in log if a in plan]
    assert sent == [("network.ping", {"host": "8.8.8.8", "count": 4}), ("network.flush_dns", {}), ("printer.clear_queue", {})]


def test_default_pack_load_idempotent_and_used_by_ask(owner, fake_openai, monkeypatch):  # noqa: F811
    r = owner.get("/api/v1/knowledge/packs")
    assert r.status_code == 200 and len(r.json()["items"][0]["documents"]) == 7
    r = owner.post("/api/v1/knowledge/packs/msp-default")
    assert r.status_code == 201, r.text
    assert len(r.json()["added"]) == 7 and all(a["title"].startswith("[DEFAULT]") for a in r.json()["added"])
    again = owner.post("/api/v1/knowledge/packs/msp-default").json()
    assert again["added"] == [] and len(again["skipped"]) == 7
    assert owner.post("/api/v1/knowledge/packs/nope").status_code == 404
    drain()
    docs = owner.get("/api/v1/knowledge/documents").json()["items"]
    assert sum(1 for d in docs if d["title"].startswith("[DEFAULT]") and d["status"] == "indexed") == 7
    from joltiq.services.knowledge import embeddings, service

    monkeypatch.setattr(service, "embedder_for", lambda db, org_id: embeddings.Embedder())  # no outbound embedding call in tests
    _no_env_key(monkeypatch)
    _connect(owner)
    assert owner.post("/api/v1/agents/chat", json={"message": "The printer is offline and print jobs are stuck"}).status_code == 201
    sysp = fake_openai[-1]["system"]
    assert "COMPANY KNOWLEDGE" in sysp and "[DEFAULT] Printer" in sysp


def test_power_actions_are_planned_last():
    acts = [i["action"] for i in windows_plan.parse("Put the PC to sleep after you clean temp files and check for updates")]
    assert acts[-1] == "system.sleep" and "disk.cleanup_temp" in acts


def test_plan_prompt_carries_effect_verify_rollback_and_order_contract():
    items = windows_plan.parse("Flush my DNS and then restart my PC")

    prompt = windows_plan.plan_prompt(items)

    assert prompt.index("network.flush_dns") < prompt.index("system.restart")
    assert "Effect: Clears the DNS resolver cache." in prompt
    assert "Verify: ipconfig exit code 0; follow with network.diagnostics." in prompt
    assert "Rollback: STOP / system.power_cancel before it fires." in prompt
    assert "Diagnose before changing" in prompt
    assert "verify each result after changing" in prompt
    assert "never open Command Prompt or use screen input" in prompt
