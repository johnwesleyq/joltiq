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
        for run in self.get("/computer-use/active").json()["runs"]:
            if run["state"] == "waiting_local_consent":
                accepted = self.post(f"/computer-use/{run['id']}/local-consent", {"accepted": True})
                assert accepted.status_code == 200, accepted.text
        n = 0
        for b in self.envelopes():
            if b["type"] == "session.begin":
                assert self.post(f"/sessions/{b['session_id']}/local-consent", {"accepted": True, "user_display_name": "Pat"}).status_code == 200
            elif b["type"] == "action":
                log.append((b["action"], b["params"]))
                assert self.post(f"/commands/{b['command_id']}/screenshot", raw=png(), ctype="image/png").status_code == 200
                r = self.post(f"/commands/{b['command_id']}/result", {"status": "done", "result": {
                    "ok": True, "width": 1280, "height": 800,
                    "frame_generation": 17, "layout_hash": "a" * 64,
                }})
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


def test_frame_binding_is_required_and_copied_from_latest_device_screenshot(app, owner):
    from joltiq.services.actions.registry import ActionValidationError, validate_params

    with pytest.raises(ActionValidationError, match="frame_generation"):
        validate_params("input.click", {"x": 10, "y": 20, "button": "left", "count": 1})

    d, sid = _setup(app, owner)
    rid = owner.post(
        f"/api/v1/sessions/{sid}/computer-use",
        json={"goal": "Open notepad, type a note and save it", "mode": "fix"},
    ).json()["id"]
    log: list = []
    _pump(d, owner, rid, log)

    mutating_inputs = [params for action, params in log if action.startswith("input.") and action not in ("input.wait", "input.cursor")]
    assert mutating_inputs
    assert all(p["frame_generation"] == 17 and p["layout_hash"] == "a" * 64 for p in mutating_inputs)


def test_customer_selects_guide_or_fix_mode(app, owner):
    d, sid = _setup(app, owner)
    guide = owner.post(
        f"/api/v1/sessions/{sid}/computer-use",
        json={"goal": "Show me how to open Windows Settings", "mode": "guide"},
    )
    assert guide.status_code == 201, guide.text
    assert guide.json()["mode"] == "guide"
    assert guide.json()["local_consent_state"] == "pending"
    assert guide.json()["state"] == "waiting_local_consent"

    rid = guide.json()["id"]
    accepted = d.post(f"/computer-use/{rid}/local-consent", {"accepted": True})
    assert accepted.status_code == 200, accepted.text
    started = owner.get(f"/api/v1/computer-use/{rid}").json()
    assert started["mode"] == "guide"
    assert started["local_consent_state"] == "approved"
    assert started["state"] == "waiting_device"

    assert owner.post(f"/api/v1/computer-use/{rid}/stop").status_code == 200
    fix = owner.post(f"/api/v1/sessions/{sid}/computer-use", json={"goal": "Open Windows Settings for me"})
    assert fix.status_code == 201, fix.text
    assert fix.json()["mode"] == "fix"


def test_invalid_mode_and_mode_change_are_refused(app, owner):
    _d, sid = _setup(app, owner)
    invalid = owner.post(
        f"/api/v1/sessions/{sid}/computer-use",
        json={"goal": "Open Windows Settings", "mode": "automatic"},
    )
    assert invalid.status_code == 422

    guide = owner.post(
        f"/api/v1/sessions/{sid}/computer-use",
        json={"goal": "Show me Windows Settings", "mode": "guide"},
    )
    assert guide.status_code == 201, guide.text
    rid = guide.json()["id"]
    changed = owner.patch(f"/api/v1/computer-use/{rid}", json={"mode": "fix"})
    assert changed.status_code == 405
    assert owner.get(f"/api/v1/computer-use/{rid}").json()["mode"] == "guide"


def test_portal_approval_waits_for_native_run_consent(app, owner):
    d, sid = _setup(app, owner)
    created = owner.post(
        f"/api/v1/sessions/{sid}/computer-use",
        json={"goal": "Open Notepad and show me the File menu", "mode": "fix"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["state"] == "waiting_local_consent"
    assert created.json()["local_consent_state"] == "pending"
    assert not [e for e in d.envelopes() if e["type"] == "action"]


def test_guide_begin_and_actions_are_bound_to_exact_signed_run(app, owner):
    from joltiq.services.actions.registry import ActionValidationError, registry_for_prompt, validate_params

    clean = validate_params("guide.highlight", {
        "frame_generation": 17,
        "layout_hash": "a" * 64,
        "x": 320,
        "y": 240,
        "width": 180,
        "height": 44,
        "frame_width": 1280,
        "frame_height": 800,
        "instruction": "Click Windows Settings.",
        "expected_control_label": "Settings",
    })
    assert clean["expected_control_label"] == "Settings"
    with pytest.raises(ActionValidationError):
        validate_params("guide.highlight", {**clean, "instruction": "x" * 501})
    assert "guide.highlight" not in {a["id"] for a in registry_for_prompt()}

    d, sid = _setup(app, owner)
    created = owner.post(
        f"/api/v1/sessions/{sid}/computer-use",
        json={"goal": "Show me how to open Windows Settings", "mode": "guide"},
    )
    assert created.status_code == 201, created.text
    rid = created.json()["id"]

    envelopes = d.envelopes()
    begin = next(e for e in envelopes if e["type"] == "computer_use.begin")
    assert begin["computer_use_run_id"] == rid
    assert begin["session_id"] == sid
    assert begin["mode"] == "guide"
    assert begin["goal"] == "Show me how to open Windows Settings"
    assert not [e for e in envelopes if e["type"] == "action"]

    accepted = d.post(f"/computer-use/{rid}/local-consent", {"accepted": True})
    assert accepted.status_code == 200, accepted.text
    action = next(e for e in d.envelopes() if e["type"] == "action")
    assert action["computer_use_run_id"] == rid
    assert action["computer_use_mode"] == "guide"


def test_device_guide_response_requires_exactly_one_declared_choice(app, owner):
    d, sid = _setup(app, owner)
    rid = owner.post(
        f"/api/v1/sessions/{sid}/computer-use",
        json={"goal": "Show me Windows Settings", "mode": "guide"},
    ).json()["id"]
    assert d.post(f"/computer-use/{rid}/respond", {}).status_code == 400
    assert d.post(
        f"/computer-use/{rid}/respond",
        {"done": True, "switch_to_fix": True},
    ).status_code == 400


def test_full_run_with_approval_pause_and_screens(app, owner):
    d, sid = _setup(app, owner)
    r = owner.post(f"/api/v1/sessions/{sid}/computer-use", json={"goal": "Open notepad, type a note and save it"})
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    assert r.json()["state"] == "waiting_local_consent"
    log: list = []
    run = _pump(d, owner, rid, log)
    assert run["state"] == "waiting_customer" and run["waiting_for"]["kind"] == "approval"
    assert "Save the file" in run["waiting_for"]["text"] and run["live_model"] is False
    assert [a for a, _ in log] == ["screen.capture", "input.click", "input.type"]
    assert log[1][1] == {
        "x": 640,
        "y": 400,
        "button": "left",
        "count": 1,
        "frame_generation": 17,
        "layout_hash": "a" * 64,
    }
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


def test_import_hubspot_service_hub_zipped_csv(owner):
    import io
    import zipfile

    hub = (b"Ticket ID,Ticket name,Ticket description,Pipeline,Ticket status,Priority,Category\n"
           b"88001,Can't log in to portal,User gets error 403 after password reset,Support Pipeline,Closed,High,Account access\n"
           b"88002,Billing question,Invoice amount looks wrong,Support Pipeline,New,Low,Billing\n")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("hubspot-crm-exports-all-tickets-2026-10-19.csv", hub)
    r = owner.post("/api/v1/knowledge/import", data={"kind": "tickets"}, files={"file": ("export.zip", buf.getvalue(), "application/zip")})
    assert r.status_code == 201, r.text
    assert r.json()["imported"] == 1 and r.json()["skipped"] == 1
    r = owner.post("/api/v1/knowledge/import", data={"kind": "tickets"}, files={"file": ("t.xlsx", b"PK\x03\x04xx", "application/octet-stream")})
    assert r.status_code == 400 and "CSV" in r.json()["error"]["message"]


def test_chat_shares_known_fix_from_imported_history(owner):
    csv_data = (b"Ticket ID,Ticket name,Ticket description,Ticket status,Resolution\n"
                b"7001,Printer shows offline,HP printer offline on laptop,Closed,Printer IP changed; updated the TCP/IP port and reserved the IP.\n")
    owner.post("/api/v1/knowledge/import", data={"kind": "tickets"}, files={"file": ("h.csv", csv_data, "text/csv")})
    drain()
    r = owner.post("/api/v1/agents/chat", json={"message": "My printer shows offline"}).json()
    drain()
    msgs = [m["content"] for m in owner.get(f"/api/v1/tickets/{r['ticket_id']}/runs").json()[0]["messages"]]
    assert any("What fixed it: Printer IP changed" in m for m in msgs)
    r = owner.post("/api/v1/agents/chat", json={"message": "my coffee machine is broken"}).json()
    drain()
    msgs = [m["content"] for m in owner.get(f"/api/v1/tickets/{r['ticket_id']}/runs").json()[0]["messages"]]
    assert not any("What fixed it" in m for m in msgs)


def test_successful_run_is_remembered_and_recalled_next_time(app, owner):
    import uuid as _uuid

    from joltiq.db import session_scope
    from joltiq.models import ComputerUseRun

    d, sid = _setup(app, owner)
    rid = owner.post(f"/api/v1/sessions/{sid}/computer-use", json={"goal": "Type the weekly note and save it"}).json()["id"]
    log: list = []
    _pump(d, owner, rid, log)
    owner.post(f"/api/v1/computer-use/{rid}/respond", json={"approved": True})
    run = _pump(d, owner, rid, log, until=("completed", "failed", "stopped"))
    assert run["state"] == "completed" and run["success"] is True
    drain()
    docs = owner.get("/api/v1/knowledge/documents?page_size=50").json()["items"]
    learned = [x for x in docs if x["source_type"] == "learned_fix"]
    assert learned and learned[0]["status"] == "indexed" and "weekly note" in learned[0]["title"]
    # Next similar request starts with the remembered fix in front of the model.
    rid2 = owner.post(f"/api/v1/sessions/{sid}/computer-use", json={"goal": "Type the weekly note and save it again"}).json()["id"]
    _pump(d, owner, rid2, [], rounds=2)
    with session_scope(_uuid.UUID(owner.org_id)) as db:
        first = db.get(ComputerUseRun, _uuid.UUID(rid2)).transcript[0]["content"][0]["text"]
    assert "fixed similar problems before" in first and "Learned fix" in first
