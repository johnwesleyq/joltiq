"""Fix it for me on an enrolled PC: one approval (+ one Allow on the PC), then the hybrid network diagnostics run."""
from __future__ import annotations

from test_computer_use import CU_CAPS, CUDevice, _code, drain, png

from joltiq.services import computer_use, windows_plan


class NetDevice(CUDevice):
    def __init__(self, app, healthy=True):
        super().__init__(app)
        self.healthy = healthy

    def answer_all(self, log):
        for run in self.get("/computer-use/active").json()["runs"]:
            if run["state"] == "waiting_local_consent":
                assert self.post(f"/computer-use/{run['id']}/local-consent", {"accepted": True}).status_code == 200
        for b in self.envelopes():
            if b["type"] == "session.begin":
                assert self.post(f"/sessions/{b['session_id']}/local-consent", {"accepted": True, "user_display_name": "John"}).status_code == 200
            elif b["type"] == "action":
                log.append((b["action"], b["params"]))
                if b["action"] == "network.diagnostics":
                    res = {"ok": True, "gateway_reachable": self.healthy, "dns_ok": self.healthy,
                           "adapters": [{"name": "Wi-Fi", "status": "Up", "ssid": "Office"}]}
                else:
                    assert self.post(f"/commands/{b['command_id']}/screenshot", raw=png(), ctype="image/png").status_code == 200
                    res = {"ok": True, "width": 1280, "height": 800, "frame_generation": 17, "layout_hash": "a" * 64}
                assert self.post(f"/commands/{b['command_id']}/result", {"status": "done", "result": res}).status_code == 200


def _run(app, owner, healthy):
    d = NetDevice(app, healthy)
    assert d.enroll(_code(owner), caps=(*CU_CAPS, "network.diagnostics")).status_code == 200
    targets = owner.get("/api/v1/agents/fix/targets").json()
    assert any(x["id"] == d.device_id for x in targets["devices"])
    out = owner.post("/api/v1/agents/fix", json={"message": "My Wi-Fi keeps dropping and the internet is slow", "device_id": d.device_id})
    assert out.status_code in (200, 201), out.text
    out = out.json()
    sid = out["session_id"]
    s = owner.get(f"/api/v1/sessions/{sid}").json()
    assert s["state"] == "pending_consent" and s["request_details"]["full_fix"]["goal"].startswith("My Wi-Fi")
    # ONE approval in the web app; the PC companion accepts the session and Allows the run once.
    assert owner.post(f"/api/v1/sessions/{sid}/consent", json={"minutes": 30}).status_code == 200
    d.answer_all([])
    r = owner.post(f"/api/v1/sessions/{sid}/computer-use", json={"goal": s["request_details"]["full_fix"]["goal"], "mode": "fix"})
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    log: list = []
    run = None
    for _ in range(20):
        drain()
        d.answer_all(log)
        drain()
        run = owner.get(f"/api/v1/computer-use/{rid}").json()
        if run["state"] in ("completed", "failed", "stopped", "waiting_customer"):
            break
    return run, log, out


def test_wifi_fix_runs_network_diagnostics_hybrid(app, owner):
    run, log, out = _run(app, owner, healthy=True)
    assert run["state"] == "completed" and run["success"] is True, run
    assert [a for a, _ in log] == ["screen.capture", "network.diagnostics"]
    diag = next(s for s in run["steps"] if s["kind"] == "diagnostic.network")
    assert diag["status"] == "done"
    assert out["channel"] == "desktop"


def test_wifi_fix_unhealthy_network_fails_honestly(app, owner):
    run, log, _ = _run(app, owner, healthy=False)
    assert run["state"] == "failed" and run["success"] is False
    assert "network.diagnostics" in [a for a, _ in log]


def test_opening_prompt_allows_typed_actions_without_a_screen_but_blocks_ui_input():
    text = computer_use._opening_transcript_text(
        goal="Flush DNS",
        os_line=" The computer runs Windows 11.",
        display={},
        memory="",
        items=windows_plan.parse("Flush DNS"),
        has_frame=False,
    )

    assert "NO SCREEN FRAME" in text
    assert "typed approved windows_action" in text
    assert "do not click, type, press keys, or claim visual verification" in text
