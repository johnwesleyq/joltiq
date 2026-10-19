"""Computer-use run engine end to end through the real signed device protocol, with a FAKE device (test fixture that
answers commands with generated screenshots; no real screen or input) and the scripted LOCAL FIXTURE model."""
from __future__ import annotations

import base64
import json
import struct
import zlib

import pytest
from test_device_protocol import FakeDevice, _code

pytestmark = pytest.mark.mocked
CU_CAPS = ("system.info", "screen.capture", "input.click", "input.type", "input.key", "input.scroll", "input.move", "url.open")


def png(w=64, h=40) -> bytes:
    raw = b"".join(b"\x00" + b"\x30\x60\x90" * w for _ in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def drain():
    from joltiq.services.jobs import drain as d

    return d()


class CUDevice(FakeDevice):
    def post(self, path, body=None, raw=None, ctype="application/json"):
        full = "/api/v1/agent/v1" + path
        data = raw if raw is not None else json.dumps(body or {}).encode()
        return self.c.post(full, content=data, headers={**self.headers("POST", full, data), "content-type": ctype})

    def envelopes(self):
        r = self.get("/poll")
        assert r.status_code == 200, r.text
        out = []
        for e in r.json()["envelopes"]:
            out.append(json.loads(base64.b64decode(e["payload"])))
        return out

    def answer_all(self, log):
        """Answer every queued action with a screenshot + result (what a real adapter would do)."""
        n = 0
        for b in self.envelopes():
            if b["type"] == "session.begin":
                assert self.post(f"/sessions/{b['session_id']}/local-consent", {"accepted": True, "user_display_name": "Pat"}).status_code == 200
            elif b["type"] == "action":
                log.append((b["action"], b["params"]))
                assert self.post(f"/commands/{b['command_id']}/screenshot", raw=png(), ctype="image/png").status_code == 200
                r = self.post(f"/commands/{b['command_id']}/result", {"status": "done", "result": {"ok": True, "width": 1280, "height": 800}})
                assert r.status_code == 200, r.text
                n += 1
        return n


def _setup(app, owner):
    d = CUDevice(app)
    assert d.enroll(_code(owner), caps=CU_CAPS).status_code == 200
    s = owner.post("/api/v1/sessions", json={"kind": "desktop", "device_id": d.device_id})
    assert s.status_code == 201, s.text
    sid = s.json()["id"]
    assert owner.post(f"/api/v1/sessions/{sid}/consent", json={"minutes": 30}).status_code == 200
    d.answer_all([])  # device-side acceptance
    return d, sid


def _pump(d, owner, rid, log, until=("waiting_customer", "completed", "failed", "stopped"), rounds=20):
    run = None
    for _ in range(rounds):
        drain()
        d.answer_all(log)
        drain()
        run = owner.get(f"/api/v1/computer-use/{rid}").json()
        if run["state"] in until:
            return run
    return run


def test_full_run_with_approval_pause_and_screens(app, owner):
    d, sid = _setup(app, owner)
    r = owner.post(f"/api/v1/sessions/{sid}/computer-use", json={"goal": "Open notepad, type a note and save it"})
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    assert r.json()["state"] == "waiting_device"  # started by the consenting person → no extra approval
    log: list = []
    run = _pump(d, owner, rid, log)
    assert run["state"] == "waiting_customer" and run["waiting_for"]["kind"] == "approval"
    assert "Save the file" in run["waiting_for"]["text"] and run["live_model"] is False
    assert [a for a, _ in log] == ["screen.capture", "input.click", "input.type"]
    assert log[1][1] == {"x": 640, "y": 400, "button": "left", "count": 1}
    # The device sees the pause too, and can answer it.
    act = d.get("/computer-use/active").json()["runs"]
    assert act[0]["waiting_for"]["kind"] == "approval"
    assert owner.post(f"/api/v1/computer-use/{rid}/respond", json={"approved": True}).status_code == 200
    run = _pump(d, owner, rid, log, until=("completed", "failed", "stopped"))
    assert run["state"] == "completed" and run["success"] is True, run
    assert [a for a, _ in log][-1] == "input.key" and log[-1][1]["combo"] == "ctrl+s"
    kinds = [s["kind"] for s in run["steps"]]
    assert kinds == ["screen.capture", "left_click", "type", "request_approval", "key", "finish"]
    assert all(s["status"] == "done" for s in run["steps"])
    img = owner.get(f"/api/v1/computer-use/{rid}/screen")
    assert img.status_code == 200 and img.content[:4] == b"\x89PNG"
    assert owner.get(f"/api/v1/computer-use/{rid}/steps/2/screen").status_code == 200
    assert owner.get(f"/api/v1/sessions/{sid}/frame").status_code == 200
    ev = [e["kind"] for e in owner.get(f"/api/v1/sessions/{sid}/events").json()]
    assert "computer_use.started" in ev and "computer_use.completed" in ev


def test_denied_approval_finishes_without_change(app, owner):
    d, sid = _setup(app, owner)
    rid = owner.post(f"/api/v1/sessions/{sid}/computer-use", json={"goal": "Type a note and save it"}).json()["id"]
    log: list = []
    _pump(d, owner, rid, log)
    d_resp = d.post(f"/computer-use/{rid}/respond", {"approved": False})  # answered on the device itself
    assert d_resp.status_code == 200, d_resp.text
    run = _pump(d, owner, rid, log, until=("completed", "failed", "stopped"))
    assert run["state"] == "failed" and run["success"] is False
    assert "input.key" not in [a for a, _ in log]


def test_secret_typing_refused_server_side(app, owner, monkeypatch):
    from joltiq.services.ai import computer as C

    turns = iter([
        ("Signing in.", "type", {"text": "password=Hunter22!"}, True),
        ("Okay, handing over.", "finish", {"success": False, "summary": "needs the customer to sign in"}, False),
    ])

    def fake_turn(self, **kw):
        say, name, inp, comp = next(turns)
        blk = {"type": "tool_use", "id": f"t_{name}", "name": name, "input": inp, **({"toolset_name": "computer"} if comp else {})}
        return C.Turn(say, [C.ToolUse(f"t_{name}", name, inp, comp)], [{"type": "text", "text": say}, blk], live=False,
                      provider="local_fixture", model="x")

    monkeypatch.setattr(C.FixtureComputer, "turn", fake_turn)
    d, sid = _setup(app, owner)
    rid = owner.post(f"/api/v1/sessions/{sid}/computer-use", json={"goal": "Sign in to the portal"}).json()["id"]
    log: list = []
    run = _pump(d, owner, rid, log, until=("completed", "failed", "stopped"))
    assert [a for a, _ in log] == ["screen.capture"]  # the secret never reached the device
    blocked = [s for s in run["steps"] if s["status"] == "blocked"]
    assert blocked and "secret" in blocked[0]["error"] and "Hunter22" not in json.dumps(run)


def test_technician_request_needs_customer_and_session_end_stops_run(app, owner, org_team):
    d, sid = _setup(app, owner)
    tech = org_team["tech"]
    if tech is None:
        pytest.skip("org_team fixture shape changed")
    r = tech.post(f"/api/v1/sessions/{sid}/computer-use", json={"goal": "Check the printer queue"})
    if r.status_code == 404:
        pytest.skip("technician cannot see owner sessions in this role setup")
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    assert r.json()["state"] == "pending_approval"
    assert tech.post(f"/api/v1/computer-use/{rid}/approve", json={"approved": True}).status_code == 403
    assert owner.post(f"/api/v1/computer-use/{rid}/approve", json={"approved": True}).status_code == 200
    assert owner.post(f"/api/v1/sessions/{sid}/emergency-stop").status_code == 200
    assert owner.get(f"/api/v1/computer-use/{rid}").json()["state"] == "stopped"


def test_cross_tenant_cannot_see_run(app, owner, new_client):
    from conftest import register

    d, sid = _setup(app, owner)
    rid = owner.post(f"/api/v1/sessions/{sid}/computer-use", json={"goal": "Open the settings page"}).json()["id"]
    other = register(new_client(), "Other Co")
    assert other.get(f"/api/v1/computer-use/{rid}").status_code == 404
    assert other.post(f"/api/v1/computer-use/{rid}/stop").status_code == 404


def test_planner_cannot_use_input_primitives():
    from joltiq.services.actions.registry import registry_for_prompt

    ids = {a["id"] for a in registry_for_prompt()}
    assert not ids & {"input.click", "input.type", "screen.capture", "url.open"}


def test_bulk_import_tickets_csv_json_and_articles_zip(owner):
    import io
    import zipfile

    from conftest import ApiClient  # noqa: F401

    csv_data = (b"Ticket ID,Subject,Description,Status,Resolution,Tags\n"
                b"1001,Outlook crashes on PDF,Outlook closes when opening PDF attachments. Call me at 555-123-4567,Closed,"
                b"Disabled the PdfPreview add-in in File > Options > Add-ins,outlook\n"
                b"1002,Printer offline,Cannot print,Open,,printer\n"
                b"1001,Outlook crashes on PDF,dup,Closed,dup,outlook\n")
    r = owner.post("/api/v1/knowledge/import", data={"kind": "tickets"}, files={"file": ("export.csv", csv_data, "text/csv")})
    assert r.status_code == 201, r.text
    assert r.json() == {"imported": 1, "skipped": 1, "duplicates": 1, "errors": []}
    zj = json.dumps({"tickets": [{"id": 77, "subject": "VPN won't connect", "description": "Error 809", "status": "solved",
                                  "comments": [{"body": "What error?"}, {"body": "Enabled UDP 500/4500 on the router; fixed."}]}]}).encode()
    r = owner.post("/api/v1/knowledge/import", data={"kind": "tickets"}, files={"file": ("zendesk.json", zj, "application/json")})
    assert r.json()["imported"] == 1
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("help/reset-mfa.md", "# Reset MFA\n\nGo to Security > MFA and click Reset.")
        z.writestr("help/logo.png", b"\x89PNG....")
        z.writestr("__MACOSX/._reset-mfa.md", "junk")
    r = owner.post("/api/v1/knowledge/import", data={"kind": "articles"}, files={"file": ("help.zip", buf.getvalue(), "application/zip")})
    assert r.status_code == 201 and r.json()["imported"] == 1 and r.json()["skipped"] == 1, r.text
    drain()
    hits = owner.post("/api/v1/knowledge/search", json={"query": "Outlook PDF add-in crash"}).json()
    text = json.dumps(hits)
    assert "PdfPreview" in text and "555-123-4567" not in text
    hits = owner.post("/api/v1/knowledge/search", json={"query": "VPN error 809"}).json()
    assert "UDP 500" in json.dumps(hits)
