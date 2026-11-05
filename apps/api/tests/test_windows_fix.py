"""v13 Windows Fix: typed uninstall / restart / shutdown / vendor driver actions behind ONE approval (+ ONE PC Allow)."""
from __future__ import annotations

import pytest
from test_computer_use import CU_CAPS, CUDevice, _code, drain, png

from joltiq.services import computer_use, windows_plan
from joltiq.services.actions.registry import ACTIONS, ActionValidationError, registry_for_prompt, validate_params

WIN_CAPS = (*CU_CAPS, "app.uninstall", "system.restart", "system.shutdown", "system.power_cancel", "driver.install", "printer.list")


# ---------------- plan parsing ----------------
def test_parse_uninstall_and_restart():
    items = windows_plan.parse("Please uninstall Notepad++ and then restart my PC")
    assert [i["action"] for i in items] == ["app.uninstall", "system.restart"]
    assert items[0]["params"] == {"name": "Notepad++"}
    assert items[1]["params"]["delay_seconds"] == windows_plan.POWER_DELAY_S and items[1]["high_risk"]
    assert windows_plan.needs_ack(items)


def test_uninstall_match_accepts_only_minor_spelling_correction_of_same_app():
    items = windows_plan.parse("uninstall adobe genunine service")
    assert windows_plan.match(items, "app.uninstall", {"name": "Adobe Genuine Service"})[0] == {
        "name": "Adobe Genuine Service",
    }
    assert windows_plan.match(items, "app.uninstall", {"name": "Adobe Genuine Service Helper"})[0] is None
    assert windows_plan.match(items, "app.uninstall", {"name": "Adobe Acrobat Service"})[0] is None


def test_uninstall_uses_one_typo_equivalent_name_discovered_in_same_run():
    class Results:
        def __init__(self, values):
            self.values = values

        def scalars(self):
            return self

        def all(self):
            return self.values

    class DB:
        def __init__(self, values):
            self.values = values

        def execute(self, _query):
            return Results(self.values)

    run = type("Run", (), {"id": "run-id"})()
    one = [{"ok": True, "apps": [{"name": "Adobe Genuine Service"}]}]
    assert computer_use._unique_discovered_app_name(DB(one), run, "adobe genunine service") == "Adobe Genuine Service"
    different = [{"ok": True, "apps": [{"name": "Adobe Acrobat Service"}]}]
    assert computer_use._unique_discovered_app_name(DB(different), run, "adobe genunine service") is None
    ambiguous = [{"ok": True, "apps": [{"name": "Adobe Genuine Service"}, {"name": "Adobe Genunine Service"}]}]
    assert computer_use._unique_discovered_app_name(DB(ambiguous), run, "adobe genunine service") is None


def test_parse_quoted_name_kept_exactly():
    items = windows_plan.parse('remove "JoltIQ Demo App" please')
    assert items[0]["params"]["name"] == "JoltIQ Demo App"


def test_parse_driver_never_guesses_url():
    items = windows_plan.parse("My HP 1500 printer stopped working, install the driver")
    assert items and items[0]["action"] == "driver.install"
    assert items[0]["params"]["vendor"] == "hp" and "url" not in items[0]["params"] and items[0]["high_risk"]


def test_parse_driver_url_only_if_allowlisted():
    good = windows_plan.parse("install the HP 1500 driver from https://ftp.hp.com/pub/softlib/x.exe")
    assert good[0]["params"].get("url") == "https://ftp.hp.com/pub/softlib/x.exe"
    bad = windows_plan.parse("install the HP 1500 driver from https://evil.example/x.exe")
    assert "url" not in bad[0]["params"]


def test_restart_service_is_not_a_pc_restart():
    assert not [i for i in windows_plan.parse("restart the print spooler service") if i["action"].startswith("system.")]


def test_plain_howto_has_no_windows_actions():
    assert windows_plan.parse("How do I change my desktop wallpaper?") == []


@pytest.mark.parametrize("url,ok", [
    ("https://ftp.hp.com/a/b.exe", True), ("https://support.hp.com/x/drv.msi", True), ("http://ftp.hp.com/a.exe", False),
    ("https://hp.com.evil.io/a.exe", False), ("https://user:pw@ftp.hp.com/a.exe", False), ("https://ftp.hp.com:8443/a.exe", False),
    ("https://ftp.hp.com/a.zip", False),
])
def test_driver_url_allowlist(url, ok):
    assert (windows_plan.check_driver_url("hp", url) is None) is ok


def test_match_only_planned_items():
    items = windows_plan.parse("uninstall Notepad++ and restart")
    assert windows_plan.match(items, "app.uninstall", {"name": "notepad++"})[0] == {"name": "Notepad++"}
    assert windows_plan.match(items, "app.uninstall", {"name": "Microsoft 365"})[0] is None
    assert windows_plan.match(items, "system.shutdown", {})[0] is None


# ---------------- registry ----------------
def test_registry_specs():
    for a in ("app.uninstall", "system.restart", "system.shutdown", "driver.install"):
        assert ACTIONS[a].run_bound and ACTIONS[a].risk.value == "PRIVILEGED"
    assert ACTIONS["system.power_cancel"].risk.value == "LOW_RISK"
    names = {x["id"] for x in registry_for_prompt()}
    assert "app.uninstall" not in names
    with pytest.raises(ActionValidationError):
        validate_params("system.restart", {"delay_s": 5})
    with pytest.raises(ActionValidationError):
        validate_params("driver.install", {"vendor": "hp", "model": "HP 1500", "url": "http://ftp.hp.com/x.exe"})


# ---------------- end to end through the run engine (fixture model + scripted companion) ----------------
class WinDevice(CUDevice):
    fail_uninstall = False

    def answer_all(self, log):
        for run in self.get("/computer-use/active").json()["runs"]:
            if run["state"] == "waiting_local_consent":
                assert self.post(f"/computer-use/{run['id']}/local-consent", {"accepted": True}).status_code == 200
        for b in self.envelopes():
            if b["type"] == "session.begin":
                assert self.post(f"/sessions/{b['session_id']}/local-consent", {"accepted": True, "user_display_name": "John"}).status_code == 200
            elif b["type"] == "action":
                log.append((b["action"], b["params"], b.get("computer_use_run_id")))
                if b["action"] == "screen.capture":
                    assert self.post(f"/commands/{b['command_id']}/screenshot", raw=png(), ctype="image/png").status_code == 200
                    res = {"ok": True, "width": 1280, "height": 800, "frame_generation": 1, "layout_hash": "a" * 64}
                elif b["action"] == "app.uninstall":
                    if self.fail_uninstall:
                        assert self.post(f"/commands/{b['command_id']}/result", {"status": "failed", "error": "not found"}).status_code == 200
                        continue
                    res = {"ok": True, "text": f"Uninstalled {b['params']['name']}", "removed": True}
                else:
                    res = {"ok": True, "text": f"{b['action']} ok"}
                assert self.post(f"/commands/{b['command_id']}/result", {"status": "done", "result": res}).status_code == 200


def _start(app, owner, message, ack=True, fail=False):
    d = WinDevice(app)
    d.fail_uninstall = fail
    assert d.enroll(_code(owner), caps=WIN_CAPS).status_code == 200
    out = owner.post("/api/v1/agents/fix", json={"message": message, "device_id": d.device_id}).json()
    sid = out["session_id"]
    s = owner.get(f"/api/v1/sessions/{sid}").json()
    return d, sid, s


def _go(owner, d, sid, s):
    d.answer_all([])
    r = owner.post(f"/api/v1/sessions/{sid}/computer-use", json={"goal": s["request_details"]["full_fix"]["goal"], "mode": "fix"})
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    log: list = []
    run = None
    for _ in range(25):
        drain()
        d.answer_all(log)
        drain()
        run = owner.get(f"/api/v1/computer-use/{rid}").json()
        if run["state"] in ("completed", "failed", "stopped"):
            break
    return rid, run, log


def test_fix_persists_live_planner_provenance(app, owner, monkeypatch):
    from joltiq.services import windows_autoplan

    d = WinDevice(app)
    assert d.enroll(_code(owner), caps=WIN_CAPS).status_code == 200
    proposed = windows_autoplan.PlannedWindowsRepair(
        items=[windows_plan.item("printer.list", {})],
        planner="live_ai",
        model="openai/gpt-5.1",
        live=True,
        rationale="Inspect installed printers before making a change.",
        fallback_reason=None,
    )
    monkeypatch.setattr(windows_autoplan, "build", lambda *_args, **_kwargs: proposed)

    out = owner.post("/api/v1/agents/fix", json={"message": "Why is my printer unavailable?", "device_id": d.device_id})

    assert out.status_code == 201, out.text
    session = owner.get(f"/api/v1/sessions/{out.json()['session_id']}").json()
    full_fix = session["request_details"]["full_fix"]
    assert [item["action"] for item in full_fix["windows_actions"]] == ["printer.list"]
    assert full_fix["planner"] == "live_ai"
    assert full_fix["planner_model"] == "openai/gpt-5.1"
    assert full_fix["planner_live"] is True
    assert full_fix["planner_rationale"] == proposed.rationale
    assert full_fix["planner_fallback_reason"] is None


def test_uninstall_and_restart_end_to_end(app, owner):
    d, sid, s = _start(app, owner, "uninstall Notepad++ and then restart my PC")
    plan = s["request_details"]["full_fix"]["windows_actions"]
    assert [i["action"] for i in plan] == ["app.uninstall", "system.restart"]
    # high-risk steps: approval without the "I understand" tick is refused
    r = owner.post(f"/api/v1/sessions/{sid}/consent", json={"minutes": 30})
    assert r.status_code == 409 and "high_risk_ack_required" in r.text
    assert owner.post(f"/api/v1/sessions/{sid}/consent", json={"minutes": 30, "acknowledge_high_risk": True}).status_code == 200
    rid, run, log = _go(owner, d, sid, s)
    assert run["state"] == "completed" and run["success"] is True, run
    acts = [a for a, _, _ in log]
    assert acts.index("app.uninstall") < acts.index("system.restart")
    assert next(p for a, p, _ in log if a == "app.uninstall") == {"name": "Notepad++"}
    assert all(r == rid for a, _, r in log if a in ("app.uninstall", "system.restart"))
    # STOP after the run still cancels the scheduled restart
    stop = owner.post(f"/api/v1/computer-use/{rid}/stop")
    assert stop.status_code == 200
    log2: list = []
    d.answer_all(log2)
    assert "system.power_cancel" in [a for a, _, _ in log2]


def test_failed_uninstall_stops_before_restart(app, owner):
    d, sid, s = _start(app, owner, "uninstall Notepad++ and then restart my PC", fail=True)
    assert owner.post(f"/api/v1/sessions/{sid}/consent", json={"minutes": 30, "acknowledge_high_risk": True}).status_code == 200
    _, run, log = _go(owner, d, sid, s)
    assert run["success"] is False
    assert "system.restart" not in [a for a, _, _ in log]


def test_driver_without_url_fails_honestly_in_fixture(app, owner):
    d, sid, s = _start(app, owner, "My HP 1500 printer needs its driver installed")
    assert owner.post(f"/api/v1/sessions/{sid}/consent", json={"minutes": 30, "acknowledge_high_risk": True}).status_code == 200
    _, run, log = _go(owner, d, sid, s)
    assert run["success"] is False and "driver.install" not in [a for a, _, _ in log]


def test_driver_with_official_url_installs(app, owner):
    d, sid, s = _start(app, owner, "install the HP 1500 printer driver from https://ftp.hp.com/pub/softlib/hp1500.exe")
    assert owner.post(f"/api/v1/sessions/{sid}/consent", json={"minutes": 30, "acknowledge_high_risk": True}).status_code == 200
    _, run, log = _go(owner, d, sid, s)
    assert run["success"] is True, run
    p = next(p for a, p, _ in log if a == "driver.install")
    assert p["vendor"] == "hp" and p["url"].startswith("https://ftp.hp.com/")


def test_run_bound_action_refused_outside_run(app, owner):
    import uuid

    from joltiq.core.errors import AppError
    from joltiq.db import session_scope
    from joltiq.models import Device, SupportSession
    from joltiq.services import devices

    d, sid, s = _start(app, owner, "uninstall Notepad++")
    assert owner.post(f"/api/v1/sessions/{sid}/consent", json={"minutes": 30, "acknowledge_high_risk": True}).status_code == 200
    d.answer_all([])
    with session_scope(uuid.UUID(owner.org_id)) as db:
        sess = db.get(SupportSession, uuid.UUID(sid))
        dev = db.get(Device, sess.device_id)
        with pytest.raises(AppError):
            devices.queue_command(db, dev, action="app.uninstall", params={"name": "Notepad++"}, session=sess)
