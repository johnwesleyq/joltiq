"""Computer-use run engine end to end through the real signed device protocol, with a FAKE device (test fixture that
answers commands with generated screenshots; no real screen or input) and the scripted LOCAL FIXTURE model."""
from __future__ import annotations

import base64
import hashlib
import json
import socket
import struct
import time
import uuid
import zlib
from types import SimpleNamespace

import pytest
from test_device_protocol import FakeDevice, _code

pytestmark = pytest.mark.mocked
CU_CAPS = (
    "system.info", "app.info", "screen.capture", "input.click", "input.type", "input.key", "input.scroll", "input.move", "url.open",
    "guide.highlight", "guide.clear",
)


def png(w=64, h=40) -> bytes:
    raw = b"".join(b"\x00" + b"\x30\x60\x90" * w for _ in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


FRAME_SHA256 = hashlib.sha256(png()).hexdigest()


def public_dns(host: str, port: int, **_kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", port))]


def mixed_public_private_dns(host: str, port: int, **_kwargs):
    return [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", port)),
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.0.0.8", port)),
    ]


def test_prepare_native_url_canonicalizes_public_https_and_derives_one_origin():
    from joltiq.services.computer_use import prepare_native_url

    assert prepare_native_url(
        "HTTPS://Example.COM.:443/help?q=1#top",
        resolver=public_dns,
    ) == {
        "url": "https://example.com/help?q=1#top",
        "allowed_origins": ["https://example.com"],
    }


@pytest.mark.parametrize(
    ("url", "resolver"),
    [
        ("http://example.com/help", public_dns),
        ("https://user:pass@example.com/help", public_dns),
        ("https://example.com:8443/help", public_dns),
        ("https://localhost/help", public_dns),
        ("https://metadata.google.internal/latest", public_dns),
        ("https://169.254.169.254/latest", public_dns),
        ("https://example.com/help", mixed_public_private_dns),
    ],
)
def test_prepare_native_url_refuses_unsafe_targets(url, resolver):
    from joltiq.core.errors import AppError
    from joltiq.services.computer_use import prepare_native_url

    with pytest.raises(AppError) as exc:
        prepare_native_url(url, resolver=resolver)
    assert exc.value.code == "unsafe_url"


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
            elif b["type"] == "computer_use.approval" and b.get("authorization_id"):
                # This is an explicit fake native UI decision for automated tests only. Production companions
                # always show the signed exact-action prompt to the person at the PC.
                approved = self.post(f"/computer-use/{b['computer_use_run_id']}/respond", {
                    "approved": True,
                    "authorization_id": b["authorization_id"],
                    "action_hash": b["action_hash"],
                })
                assert approved.status_code == 200, approved.text
            elif b["type"] == "action":
                log.append((b["action"], b["params"]))
                assert self.post(f"/commands/{b['command_id']}/screenshot", raw=png(), ctype="image/png").status_code == 200
                r = self.post(f"/commands/{b['command_id']}/result", {"status": "done", "result": {
                    "ok": True, "width": 1280, "height": 800,
                    "frame_generation": 17, "layout_hash": "a" * 64,
                    "frame_sha256": FRAME_SHA256, "captured_at": int(time.time()),
                    "focused_target_hash": "c" * 64,
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
        # Exact-action authorization is a native companion prompt.  The fake
        # device answers it on the next polling round, so it is not a terminal
        # customer pause for this automated pump.  Legacy model-requested
        # questions/approvals still return to their tests as before.
        if run["state"] == "waiting_customer" and (run.get("waiting_for") or {}).get("kind") == "action_authorization":
            continue
        if run["state"] in until:
            return run
    return run


def _adobe_verification_run(app, owner, monkeypatch):
    from joltiq.services.ai import computer as C

    def finish_after_first_screen(self, **kw):
        inp = {"success": True, "summary": "Adobe Acrobat was removed and Adobe Genuine Service remains installed."}
        block = {"type": "tool_use", "id": "t_finish", "name": "finish", "input": inp}
        return C.Turn(
            "The requested uninstall appears complete.",
            [C.ToolUse("t_finish", "finish", inp, False)],
            [{"type": "text", "text": "The requested uninstall appears complete."}, block],
            live=False,
            provider="local_fixture",
            model="verification-test",
        )

    monkeypatch.setattr(C.FixtureComputer, "turn", finish_after_first_screen)
    d = CUDevice(app)
    assert d.enroll(_code(owner), caps=CU_CAPS).status_code == 200
    ticket = owner.post("/api/v1/tickets", json={
        "subject": "Uninstall Adobe Acrobat Reader 64-bit",
        "description": "Remove Acrobat but leave Adobe Genuine Service installed.",
        "device_id": d.device_id,
    }).json()
    session = owner.post("/api/v1/sessions", json={
        "kind": "desktop", "device_id": d.device_id, "ticket_id": ticket["id"],
    }).json()
    assert owner.post(f"/api/v1/sessions/{session['id']}/consent", json={"minutes": 30}).status_code == 200
    d.answer_all([])
    run = owner.post(f"/api/v1/sessions/{session['id']}/computer-use", json={
        "goal": "Uninstall Adobe Acrobat Reader 64-bit from this Windows computer, but leave Adobe Genuine Service installed.",
        "mode": "fix",
    }).json()
    assert d.post(f"/computer-use/{run['id']}/local-consent", {"accepted": True}).status_code == 200
    capture = next(e for e in d.envelopes() if e["type"] == "action")
    assert capture["action"] == "screen.capture"
    assert d.post(f"/commands/{capture['command_id']}/screenshot", raw=png(), ctype="image/png").status_code == 200
    assert d.post(f"/commands/{capture['command_id']}/result", {"status": "done", "result": {
        "ok": True, "width": 1280, "height": 800, "frame_generation": 17, "layout_hash": "a" * 64,
        "frame_sha256": FRAME_SHA256, "captured_at": int(time.time()), "focused_target_hash": "c" * 64,
    }}).status_code == 200
    drain()
    return d, ticket, run


def _answer_inventory(d, envelope, apps):
    assert envelope["action"] == "app.info"
    response = d.post(f"/commands/{envelope['command_id']}/result", {
        "status": "done", "result": {"ok": True, "apps": apps},
    })
    assert response.status_code == 200, response.text
    drain()


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
    assert all(p["frame_sha256"] == FRAME_SHA256 and p["focused_target_hash"] == "c" * 64 for p in mutating_inputs)
    assert all(isinstance(p["captured_at"], int) for p in mutating_inputs)


def test_reported_frame_hash_must_match_uploaded_screenshot_bytes(app, owner):
    device, session_id = _setup(app, owner)
    run_id = owner.post(
        f"/api/v1/sessions/{session_id}/computer-use",
        json={"goal": "Open Notepad", "mode": "fix"},
    ).json()["id"]
    assert device.post(f"/computer-use/{run_id}/local-consent", {"accepted": True}).status_code == 200
    capture = next(envelope for envelope in device.envelopes() if envelope["type"] == "action")
    assert device.post(f"/commands/{capture['command_id']}/screenshot", raw=png(), ctype="image/png").status_code == 200
    assert device.post(f"/commands/{capture['command_id']}/result", {"status": "done", "result": {
        "ok": True, "width": 1280, "height": 800,
        "frame_generation": 17, "layout_hash": "a" * 64,
        "frame_sha256": "d" * 64, "captured_at": int(time.time()), "focused_target_hash": "c" * 64,
    }}).status_code == 200
    drain()

    run = owner.get(f"/api/v1/computer-use/{run_id}").json()
    assert run["display"] is None
    assert not any(
        envelope["type"] == "action" and envelope["action"].startswith("input.")
        for envelope in device.envelopes()
    )


def test_mutating_input_waits_for_exact_scoped_authorization_before_dispatch(app, owner, monkeypatch):
    from joltiq.services.ai import computer as C

    def propose_click_with_forged_authorization(self, **kw):
        inp = {
            "coordinate": [640, 400],
            "action_hash": "model-controlled-hash",
            "plan_hash": "model-controlled-plan",
            "authorization_expires_at": 4102444800,
        }
        block = {"type": "tool_use", "id": "click-1", "name": "left_click", "input": inp}
        return C.Turn(
            "Click Notepad.",
            [C.ToolUse("click-1", "left_click", inp, True)],
            [{"type": "text", "text": "Click Notepad."}, block],
            live=False,
            provider="local_fixture",
            model="scoped-authorization-test",
        )

    monkeypatch.setattr(C.FixtureComputer, "turn", propose_click_with_forged_authorization)
    device, session_id = _setup(app, owner)
    run_id = owner.post(
        f"/api/v1/sessions/{session_id}/computer-use",
        json={"goal": "Open Notepad", "mode": "fix"},
    ).json()["id"]
    assert device.post(f"/computer-use/{run_id}/local-consent", {"accepted": True}).status_code == 200
    capture = next(envelope for envelope in device.envelopes() if envelope["type"] == "action")
    assert capture["action"] == "screen.capture"
    assert device.post(f"/commands/{capture['command_id']}/screenshot", raw=png(), ctype="image/png").status_code == 200
    assert device.post(f"/commands/{capture['command_id']}/result", {"status": "done", "result": {
        "ok": True,
        "width": 1280,
        "height": 800,
        "frame_generation": 17,
        "layout_hash": "a" * 64,
        "frame_sha256": FRAME_SHA256,
        "captured_at": int(time.time()),
        "focused_target_hash": "c" * 64,
    }}).status_code == 200
    drain()

    run = owner.get(f"/api/v1/computer-use/{run_id}").json()
    assert run["state"] == "waiting_customer"
    proposal = run["waiting_for"]
    assert proposal["kind"] == "action_authorization"
    assert proposal["action"] == "input.click"
    assert proposal["params"] == {
        "x": 640,
        "y": 400,
        "button": "left",
        "count": 1,
        "frame_generation": 17,
        "layout_hash": "a" * 64,
        "frame_sha256": FRAME_SHA256,
        "captured_at": proposal["captured_at"],
        "focused_target_hash": "c" * 64,
    }
    assert len(proposal["authorization_id"]) == 36
    assert len(proposal["action_hash"]) == 64 and proposal["action_hash"] != "model-controlled-hash"
    assert len(proposal["plan_hash"]) == 64 and proposal["plan_hash"] != "model-controlled-plan"
    assert proposal["authorization_expires_at"] < 4102444800
    envelopes = device.envelopes()
    approval = next(envelope for envelope in envelopes if envelope["type"] == "computer_use.approval")
    for field in (
        "authorization_id", "action", "params", "action_hash", "plan_hash", "expected_effect",
        "frame_generation", "layout_hash", "frame_sha256", "captured_at", "focused_target_hash",
        "authorization_expires_at",
    ):
        assert approval[field] == proposal[field]
    assert approval["authorization_expires_at"] <= approval["expires_at"]
    assert not any(
        envelope["type"] == "action" and envelope["action"].startswith("input.")
        for envelope in envelopes
    )

    approved = device.post(f"/computer-use/{run_id}/respond", {
        "approved": True,
        "authorization_id": proposal["authorization_id"],
        "action_hash": proposal["action_hash"],
    })
    assert approved.status_code == 200, approved.text
    action = next(envelope for envelope in device.envelopes() if envelope["type"] == "action")
    assert action["action"] == proposal["action"]
    assert action["params"] == proposal["params"]
    assert action["authorization_id"] == proposal["authorization_id"]
    assert action["action_hash"] == proposal["action_hash"]
    assert action["plan_hash"] == proposal["plan_hash"]
    assert action["authorization_expires_at"] == proposal["authorization_expires_at"]

    repeated = device.post(f"/computer-use/{run_id}/respond", {
        "approved": True,
        "authorization_id": proposal["authorization_id"],
        "action_hash": proposal["action_hash"],
    })
    assert repeated.status_code == 409


def test_native_open_url_replaces_forged_origin_and_requires_fresh_exact_authorization(app, owner, monkeypatch):
    from joltiq.services import computer_use as cu
    from joltiq.services.ai import computer as C

    proposed = {
        "url": "HTTPS://Example.COM.:443/support?q=printer",
        "allowed_origins": ["https://attacker.example", "http://127.0.0.1"],
    }

    def propose_url(self, **kw):
        block = {"type": "tool_use", "id": "url-1", "name": "open_url", "input": proposed}
        return C.Turn(
            "Open the public support page.",
            [C.ToolUse("url-1", "open_url", proposed, False)],
            [{"type": "text", "text": "Open the public support page."}, block],
            live=False,
            provider="local_fixture",
            model="native-url-policy-test",
        )

    original_prepare = cu.prepare_native_url
    monkeypatch.setattr(C.FixtureComputer, "turn", propose_url)
    monkeypatch.setattr(cu, "prepare_native_url", lambda url: original_prepare(url, resolver=public_dns))
    device, session_id = _setup(app, owner)
    run_id = owner.post(
        f"/api/v1/sessions/{session_id}/computer-use",
        json={"goal": "Open the printer vendor support page", "mode": "fix"},
    ).json()["id"]
    assert device.post(f"/computer-use/{run_id}/local-consent", {"accepted": True}).status_code == 200
    capture = next(envelope for envelope in device.envelopes() if envelope["type"] == "action")
    assert device.post(f"/commands/{capture['command_id']}/screenshot", raw=png(), ctype="image/png").status_code == 200
    assert device.post(f"/commands/{capture['command_id']}/result", {"status": "done", "result": {
        "ok": True, "width": 1280, "height": 800,
        "frame_generation": 17, "layout_hash": "a" * 64,
        "frame_sha256": FRAME_SHA256, "captured_at": int(time.time()),
        "focused_target_hash": "c" * 64,
    }}).status_code == 200
    drain()

    proposal = owner.get(f"/api/v1/computer-use/{run_id}").json()["waiting_for"]
    assert proposal["kind"] == "action_authorization"
    assert proposal["action"] == "url.open"
    assert proposal["params"] == {
        "url": "https://example.com/support?q=printer",
        "allowed_origins": ["https://example.com"],
    }
    approval = next(e for e in device.envelopes() if e["type"] == "computer_use.approval")
    assert approval["params"] == proposal["params"]
    assert not any(e.get("type") == "action" and e.get("action") == "url.open" for e in device.envelopes())

    approved = device.post(f"/computer-use/{run_id}/respond", {
        "approved": True,
        "authorization_id": proposal["authorization_id"],
        "action_hash": proposal["action_hash"],
    })
    assert approved.status_code == 200, approved.text
    action = next(e for e in device.envelopes() if e.get("action") == "url.open")
    assert action["params"] == proposal["params"]
    assert action["authorization_id"] == proposal["authorization_id"]

    repeated = device.post(f"/computer-use/{run_id}/respond", {
        "approved": True,
        "authorization_id": proposal["authorization_id"],
        "action_hash": proposal["action_hash"],
    })
    assert repeated.status_code == 409


def test_denied_native_open_url_never_dispatches(app, owner, monkeypatch):
    from joltiq.services import computer_use as cu
    from joltiq.services.ai import computer as C

    def propose_url(self, **kw):
        inp = {"url": "https://example.com/support"}
        return C.Turn(
            "Open support.",
            [C.ToolUse("url-denied", "open_url", inp, False)],
            [{"type": "tool_use", "id": "url-denied", "name": "open_url", "input": inp}],
            live=False,
            provider="local_fixture",
            model="native-url-denial-test",
        )

    original_prepare = cu.prepare_native_url
    monkeypatch.setattr(C.FixtureComputer, "turn", propose_url)
    monkeypatch.setattr(cu, "prepare_native_url", lambda url: original_prepare(url, resolver=public_dns))
    device, session_id = _setup(app, owner)
    run_id = owner.post(
        f"/api/v1/sessions/{session_id}/computer-use",
        json={"goal": "Open support", "mode": "fix"},
    ).json()["id"]
    assert device.post(f"/computer-use/{run_id}/local-consent", {"accepted": True}).status_code == 200
    capture = next(envelope for envelope in device.envelopes() if envelope["type"] == "action")
    assert device.post(f"/commands/{capture['command_id']}/screenshot", raw=png(), ctype="image/png").status_code == 200
    assert device.post(f"/commands/{capture['command_id']}/result", {"status": "done", "result": {
        "ok": True, "width": 1280, "height": 800,
        "frame_generation": 17, "layout_hash": "a" * 64,
        "frame_sha256": FRAME_SHA256, "captured_at": int(time.time()),
        "focused_target_hash": "c" * 64,
    }}).status_code == 200
    drain()
    proposal = owner.get(f"/api/v1/computer-use/{run_id}").json()["waiting_for"]

    denied = device.post(f"/computer-use/{run_id}/respond", {
        "approved": False,
        "authorization_id": proposal["authorization_id"],
        "action_hash": proposal["action_hash"],
    })
    assert denied.status_code == 200, denied.text
    assert not any(e.get("type") == "action" and e.get("action") == "url.open" for e in device.envelopes())


def test_denied_exact_action_never_dispatches_and_cannot_be_reused(app, owner, monkeypatch):
    from joltiq.services.ai import computer as C

    def propose_click(self, **kw):
        inp = {"coordinate": [640, 400]}
        block = {"type": "tool_use", "id": "click-denied", "name": "left_click", "input": inp}
        return C.Turn(
            "Click Notepad.",
            [C.ToolUse("click-denied", "left_click", inp, True)],
            [{"type": "text", "text": "Click Notepad."}, block],
            live=False,
            provider="local_fixture",
            model="scoped-authorization-denial-test",
        )

    monkeypatch.setattr(C.FixtureComputer, "turn", propose_click)
    device, session_id = _setup(app, owner)
    run_id = owner.post(
        f"/api/v1/sessions/{session_id}/computer-use",
        json={"goal": "Open Notepad", "mode": "fix"},
    ).json()["id"]
    assert device.post(f"/computer-use/{run_id}/local-consent", {"accepted": True}).status_code == 200
    capture = next(envelope for envelope in device.envelopes() if envelope["type"] == "action")
    assert device.post(f"/commands/{capture['command_id']}/screenshot", raw=png(), ctype="image/png").status_code == 200
    assert device.post(f"/commands/{capture['command_id']}/result", {"status": "done", "result": {
        "ok": True, "width": 1280, "height": 800, "frame_generation": 17, "layout_hash": "a" * 64,
        "frame_sha256": FRAME_SHA256, "captured_at": int(time.time()), "focused_target_hash": "c" * 64,
    }}).status_code == 200
    drain()
    proposal = owner.get(f"/api/v1/computer-use/{run_id}").json()["waiting_for"]

    denied = device.post(f"/computer-use/{run_id}/respond", {
        "approved": False,
        "authorization_id": proposal["authorization_id"],
        "action_hash": proposal["action_hash"],
    })
    assert denied.status_code == 200, denied.text
    assert not any(
        envelope["type"] == "action" and envelope.get("authorization_id") == proposal["authorization_id"]
        for envelope in device.envelopes()
    )
    repeated = device.post(f"/computer-use/{run_id}/respond", {
        "approved": True,
        "authorization_id": proposal["authorization_id"],
        "action_hash": proposal["action_hash"],
    })
    assert repeated.status_code == 409


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
        "frame_sha256": FRAME_SHA256,
        "captured_at": int(time.time()),
        "focused_target_hash": "c" * 64,
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
    assert begin["ai_data_destination"] == "the local JoltIQ test engine"
    assert begin["ai_model"] == "local-rules-v1"
    assert not [e for e in envelopes if e["type"] == "action"]

    accepted = d.post(f"/computer-use/{rid}/local-consent", {"accepted": True})
    assert accepted.status_code == 200, accepted.text
    action = next(e for e in d.envelopes() if e["type"] == "action")
    assert action["computer_use_run_id"] == rid
    assert action["computer_use_mode"] == "guide"


def test_retention_deletes_computer_use_screenshot_objects(app, owner):
    from datetime import timedelta

    from joltiq.db import session_scope
    from joltiq.models import ComputerUseRun, ComputerUseStep, Organization, utcnow
    from joltiq.services import retention, storage

    d, sid = _setup(app, owner)
    created = owner.post(
        f"/api/v1/sessions/{sid}/computer-use",
        json={"goal": "Show me how to open Windows Settings", "mode": "guide"},
    )
    rid = uuid.UUID(created.json()["id"])
    assert d.post(f"/computer-use/{rid}/local-consent", {"accepted": True}).status_code == 200
    assert d.answer_all([]) >= 1
    drain()

    org_id = uuid.UUID(owner.org_id)
    with session_scope(org_id) as db:
        run = db.get(ComputerUseRun, rid)
        step = db.query(ComputerUseStep).filter(ComputerUseStep.run_id == rid, ComputerUseStep.screenshot_key.is_not(None)).first()
        assert run is not None and step is not None and step.screenshot_key
        key = step.screenshot_key
        assert storage.get(org_id, key).startswith(b"\x89PNG")
        step.created_at = utcnow() - timedelta(days=2)
        org = db.get(Organization, org_id)
        org.settings = {**(org.settings or {}), "retention_days": {"screenshots": 1}}

    with session_scope(org_id) as db:
        out = retention.apply_retention(db, org_id)
        assert out["computer_use_screenshots"] >= 1
        step = db.query(ComputerUseStep).filter(ComputerUseStep.run_id == rid).order_by(ComputerUseStep.idx).first()
        assert step is not None and step.screenshot_key is None
        run = db.get(ComputerUseRun, rid)
        assert run is not None and run.last_screenshot_key is None
    with pytest.raises(FileNotFoundError):
        storage.get(org_id, key)


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


def test_guide_translates_input_and_refreshes_after_idempotent_done(app, owner):
    d, sid = _setup(app, owner)
    rid = owner.post(
        f"/api/v1/sessions/{sid}/computer-use",
        json={"goal": "Show me how to type a note in Notepad", "mode": "guide"},
    ).json()["id"]
    log: list = []
    run = _pump(d, owner, rid, log)

    assert run["state"] == "waiting_customer"
    assert run["waiting_for"]["kind"] == "guide_action"
    assert [action for action, _ in log] == ["screen.capture", "guide.highlight"]
    assert not [action for action, _ in log if action.startswith("input.")]

    completed = d.post(f"/computer-use/{rid}/respond", {"done": True})
    assert completed.status_code == 200, completed.text
    replay = d.post(f"/computer-use/{rid}/respond", {"done": True})
    assert replay.status_code == 200, replay.text

    run = _pump(d, owner, rid, log)
    assert run["state"] == "waiting_customer"
    assert [action for action, _ in log] == [
        "screen.capture", "guide.highlight", "guide.clear", "screen.capture", "guide.highlight",
    ]
    assert not [action for action, _ in log if action.startswith("input.")]


def test_switch_from_guide_creates_unapproved_fix_run(app, owner):
    d, sid = _setup(app, owner)
    guide_id = owner.post(
        f"/api/v1/sessions/{sid}/computer-use",
        json={"goal": "Show me how to type a note in Notepad", "mode": "guide"},
    ).json()["id"]
    log: list = []
    guide = _pump(d, owner, guide_id, log)
    assert guide["state"] == "waiting_customer"

    switched = d.post(f"/computer-use/{guide_id}/respond", {"switch_to_fix": True})
    assert switched.status_code == 200, switched.text
    fix = switched.json()
    assert fix["id"] != guide_id
    assert fix["mode"] == "fix"
    assert fix["state"] == "pending_approval"
    assert fix["local_consent_state"] == "pending"
    assert owner.get(f"/api/v1/computer-use/{guide_id}").json()["state"] == "stopped"

    before_approval = d.envelopes()
    assert any(e["type"] == "computer_use.end" and e["computer_use_run_id"] == guide_id for e in before_approval)
    assert not any(e["type"] == "computer_use.begin" and e["computer_use_run_id"] == fix["id"] for e in before_approval)
    assert not any(e["type"] == "action" and e.get("computer_use_run_id") == fix["id"] for e in before_approval)

    approved = owner.post(f"/api/v1/computer-use/{fix['id']}/approve", json={"approved": True})
    assert approved.status_code == 200, approved.text
    assert approved.json()["state"] == "waiting_local_consent"
    assert not any(e["type"] == "action" and e.get("computer_use_run_id") == fix["id"] for e in d.envelopes())
    assert d.post(f"/computer-use/{fix['id']}/local-consent", {"accepted": True}).status_code == 200
    action = next(e for e in d.envelopes() if e["type"] == "action" and e.get("computer_use_run_id") == fix["id"])
    assert action["action"] == "screen.capture"


def test_stale_guide_highlight_is_recorded_as_blocked_and_never_becomes_input(app, owner):
    d, sid = _setup(app, owner)
    rid = owner.post(
        f"/api/v1/sessions/{sid}/computer-use",
        json={"goal": "Show me how to type a note in Notepad", "mode": "guide"},
    ).json()["id"]
    assert d.post(f"/computer-use/{rid}/local-consent", {"accepted": True}).status_code == 200

    capture = next(e for e in d.envelopes() if e["type"] == "action")
    assert capture["action"] == "screen.capture"
    assert d.post(f"/commands/{capture['command_id']}/screenshot", raw=png(), ctype="image/png").status_code == 200
    assert d.post(f"/commands/{capture['command_id']}/result", {"status": "done", "result": {
        "ok": True, "width": 1280, "height": 800, "frame_generation": 17, "layout_hash": "a" * 64,
        "frame_sha256": FRAME_SHA256, "captured_at": int(time.time()), "focused_target_hash": "c" * 64,
    }}).status_code == 200
    drain()

    highlight = next(e for e in d.envelopes() if e["type"] == "action")
    assert highlight["action"] == "guide.highlight"
    assert highlight["params"]["frame_generation"] == 17
    assert d.post(f"/commands/{highlight['command_id']}/result", {
        "status": "rejected", "result": {"ok": False, "error": "stale frame or display layout"},
    }).status_code == 200
    drain()

    run = owner.get(f"/api/v1/computer-use/{rid}").json()
    refused = next(step for step in run["steps"] if step["kind"] == "left_click")
    assert refused["status"] == "blocked"
    assert "stale frame" in refused["error"]
    assert not any(e["type"] == "action" and e["action"].startswith("input.") for e in d.envelopes())


def test_in_scope_save_is_covered_by_one_time_goal_approval(app, owner):
    """Approval-once: the customer approved the goal; an in-scope save does not re-ask."""
    d, sid = _setup(app, owner)
    rid = owner.post(f"/api/v1/sessions/{sid}/computer-use", json={"goal": "Open notepad, type a note and save it"}).json()["id"]
    log: list = []
    run = _pump(d, owner, rid, log, until=("completed", "failed", "stopped"))
    assert run["state"] == "completed" and run["success"] is True, run
    assert [a for a, _ in log] == ["screen.capture", "input.click", "input.type", "input.key"]
    appr = next(s for s in run["steps"] if s["kind"] == "request_approval")
    assert appr["status"] == "done" and appr["params"]["covered_by_goal"] is True
    from sqlalchemy import select

    from joltiq.db import session_scope
    from joltiq.models.platform import AuditEvent

    with session_scope(uuid.UUID(owner.org_id)) as db:
        acts = [a for (a,) in db.execute(select(AuditEvent.action).where(AuditEvent.resource_id == rid))]
    assert "computer_use.approval_covered_by_goal" in acts


def test_full_run_with_approval_pause_and_screens(app, owner):
    d, sid = _setup(app, owner)
    r = owner.post(f"/api/v1/sessions/{sid}/computer-use", json={"goal": "Open notepad, delete the old draft, type a note and save it"})
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    assert r.json()["state"] == "waiting_local_consent"
    log: list = []
    run = _pump(d, owner, rid, log)
    assert run["state"] == "waiting_customer" and run["waiting_for"]["kind"] == "approval"
    assert "Delete the old draft" in run["waiting_for"]["text"] and "needs your OK" in run["waiting_for"]["text"]
    assert run["live_model"] is False
    assert [a for a, _ in log] == ["screen.capture", "input.click", "input.type"]
    assert {k: log[1][1][k] for k in ("x", "y", "button", "count", "frame_generation", "layout_hash", "frame_sha256", "focused_target_hash")} == {
        "x": 640,
        "y": 400,
        "button": "left",
        "count": 1,
        "frame_generation": 17,
        "layout_hash": "a" * 64,
        "frame_sha256": FRAME_SHA256,
        "focused_target_hash": "c" * 64,
    }
    assert isinstance(log[1][1]["captured_at"], int)
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


def test_adobe_finish_requires_inventory_and_resolves_verified_ticket(app, owner, monkeypatch):
    d, ticket, run = _adobe_verification_run(app, owner, monkeypatch)

    absent = next(e for e in d.envelopes() if e["type"] == "action")
    assert absent["action"] == "app.info"
    assert absent["params"] == {"name_filter": "Adobe Acrobat"}
    _answer_inventory(d, absent, [])

    present = next(e for e in d.envelopes() if e["type"] == "action")
    assert present["action"] == "app.info"
    assert present["params"] == {"name_filter": "Adobe Genuine Service"}
    _answer_inventory(d, present, [{"name": "Adobe Genuine Service", "version": "9.1.0.52", "publisher": "Adobe Inc."}])

    completed = owner.get(f"/api/v1/computer-use/{run['id']}").json()
    assert completed["state"] == "completed" and completed["success"] is True
    assert "independently verified" in completed["summary"].lower()
    assert [s["kind"] for s in completed["steps"]][-3:] == ["finish", "verify.app_absent", "verify.app_present"]
    resolved = owner.get(f"/api/v1/tickets/{ticket['id']}").json()
    assert resolved["status"] == "RESOLVED" and resolved["resolution_verified"] is True
    drafts = owner.get("/api/v1/knowledge/articles", params={"status": "draft"}).json()["items"]
    draft = next(article for article in drafts if article["source_run_id"] == run["id"])
    assert draft["verified_resolution"] is True and draft["executable"] is False
    assert [item["status"] for item in draft["body"]["verification_evidence"]] == ["VERIFIED", "VERIFIED"]


def test_adobe_inventory_contradiction_fails_closed_and_does_not_resolve(app, owner, monkeypatch):
    d, ticket, run = _adobe_verification_run(app, owner, monkeypatch)

    absent = next(e for e in d.envelopes() if e["type"] == "action")
    _answer_inventory(d, absent, [{"name": "Adobe Acrobat (64-bit)", "version": "25.001", "publisher": "Adobe Inc."}])

    failed = owner.get(f"/api/v1/computer-use/{run['id']}").json()
    assert failed["state"] == "failed" and failed["success"] is False
    assert "still installed" in failed["summary"].lower()
    unresolved = owner.get(f"/api/v1/tickets/{ticket['id']}").json()
    assert unresolved["status"] != "RESOLVED" and unresolved["resolution_verified"] is False


def test_adobe_unreadable_inventory_fails_closed_and_does_not_resolve(app, owner, monkeypatch):
    d, ticket, run = _adobe_verification_run(app, owner, monkeypatch)

    absent = next(e for e in d.envelopes() if e["type"] == "action")
    response = d.post(f"/commands/{absent['command_id']}/result", {
        "status": "done", "result": {"ok": True, "unexpected": []},
    })
    assert response.status_code == 200, response.text
    drain()

    failed = owner.get(f"/api/v1/computer-use/{run['id']}").json()
    assert failed["state"] == "failed" and failed["success"] is False
    assert "could not be read reliably" in failed["summary"].lower()
    unresolved = owner.get(f"/api/v1/tickets/{ticket['id']}").json()
    assert unresolved["status"] != "RESOLVED" and unresolved["resolution_verified"] is False


def test_denied_approval_finishes_without_change(app, owner):
    d, sid = _setup(app, owner)
    rid = owner.post(f"/api/v1/sessions/{sid}/computer-use", json={"goal": "Delete the old draft, type a note and save it"}).json()["id"]
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


def test_repeated_screenshot_loop_stops_instead_of_spending_all_steps(app, owner, monkeypatch):
    from joltiq.services.ai import computer as C

    def screenshot_forever(self, **kw):
        block = {"type": "tool_use", "id": "t_screenshot", "name": "screenshot", "input": {},
                 "toolset_name": "computer"}
        return C.Turn(
            "Checking again.",
            [C.ToolUse("t_screenshot", "screenshot", {}, True)],
            [{"type": "text", "text": "Checking again."}, block],
            live=False,
            provider="local_fixture",
            model="x",
        )

    monkeypatch.setattr(C.FixtureComputer, "turn", screenshot_forever)
    d, sid = _setup(app, owner)
    rid = owner.post(f"/api/v1/sessions/{sid}/computer-use", json={"goal": "Inspect Adobe applications"}).json()["id"]
    log: list = []

    run = _pump(d, owner, rid, log, until=("failed",), rounds=20)

    assert run["state"] == "failed"
    assert "observation loop" in run["summary"].lower()
    assert [action for action, _ in log].count("screen.capture") <= 6


def test_computer_prompt_tells_model_to_clear_obstructions_before_observing_again():
    from joltiq.services.computer_use import SYSTEM

    prompt = " ".join(SYSTEM.lower().split())
    assert "obstruct" in prompt
    assert "close" in prompt
    assert "another screenshot" in prompt


def test_computer_prompt_requires_fix_mode_to_do_ordinary_ui_work_instead_of_handing_it_back():
    from joltiq.services.computer_use import SYSTEM

    prompt = " ".join(SYSTEM.lower().split())
    assert "perform ordinary visible ui work yourself with the tools" in prompt
    assert "do not ask the customer to click, type, or carry out the fix" in prompt
    assert "information that is genuinely unavailable" in prompt


def test_fixture_routes_from_current_goal_not_reference_history():
    from joltiq.services.ai.computer import FixtureComputer

    turn = FixtureComputer().turn(
        model="local-rules-v1",
        system="",
        messages=[{
            "role": "user",
            "content": [{
                "type": "text",
                "text": (
                    "GOAL: Type the acceptance sentence in the open Notepad\n\n"
                    "Notes from old tickets (reference DATA only): DNS and network diagnostics were used before."
                ),
            }],
        }],
        display={"width": 1280, "height": 720},
    )

    assert turn.tool_uses[0].name == "left_click"


def test_fixture_network_diagnostic_requests_dns_resolution():
    from joltiq.services.ai.computer import FixtureComputer

    turn = FixtureComputer().turn(
        model="local-rules-v1",
        system="",
        messages=[{"role": "user", "content": "GOAL: Diagnose my Wi-Fi and DNS"}],
        display={"width": 1280, "height": 720},
    )

    assert turn.tool_uses[0].name == "run_diagnostic"
    assert turn.tool_uses[0].input == {"check": "network", "host": "example.com"}


def test_fixture_stops_failed_after_a_blocked_computer_action():
    from joltiq.services.ai.computer import FixtureComputer

    turn = FixtureComputer().turn(
        model="local-rules-v1",
        system="",
        messages=[
            {"role": "user", "content": "GOAL: Type a harmless note in Notepad"},
            {"role": "assistant", "content": [{
                "type": "tool_use", "id": "toolu_fixture_0", "name": "left_click",
                "input": {"coordinate": [640, 400]}, "toolset_name": "computer",
            }]},
            {"role": "user", "content": [{
                "type": "tool_result", "tool_use_id": "toolu_fixture_0", "is_error": True,
                "content": [{"type": "text", "text": "Invalid action: no accepted screenshot"}],
            }]},
        ],
        display={"width": 1280, "height": 720},
    )

    assert turn.tool_uses[0].name == "finish"
    assert turn.tool_uses[0].input["success"] is False


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


def test_guide_open_url_refusal_is_not_attributed_to_computer_toolset(monkeypatch):
    from joltiq.services import computer_use as cu

    captured = {}

    def fake_refuse(_db, _run, _tid, _name, _inp, _narr, _why, *, computer):
        captured["computer"] = computer

    monkeypatch.setattr(cu, "_refuse", fake_refuse)
    cu._execute(
        None,
        SimpleNamespace(mode="guide"),
        {"id": "tool-open", "name": "open_url", "input": {"url": "https://example.com"}, "narration": "", "computer": False},
    )

    assert captured == {"computer": False}


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
    r = owner.post("/api/v1/agents/chat", json={"mode": "troubleshoot", "message": "My printer shows offline"}).json()
    drain()
    msgs = [m["content"] for m in owner.get(f"/api/v1/tickets/{r['ticket_id']}/runs").json()[0]["messages"]]
    assert any("What fixed it: Printer IP changed" in m for m in msgs)
    r = owner.post("/api/v1/agents/chat", json={"mode": "troubleshoot", "message": "my coffee machine is broken"}).json()
    drain()
    msgs = [m["content"] for m in owner.get(f"/api/v1/tickets/{r['ticket_id']}/runs").json()[0]["messages"]]
    assert not any("What fixed it" in m for m in msgs)


def test_model_success_is_draft_only_until_authorized_publication(app, owner):
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
    assert learned == []
    drafts = owner.get("/api/v1/knowledge/articles", params={"status": "draft"}).json()["items"]
    draft = next(x for x in drafts if x["source_run_id"] == rid)
    assert draft["executable"] is False and draft["verified_resolution"] is False

    # A model-only success is not recallable before a human with publish permission approves it.
    rid2 = owner.post(f"/api/v1/sessions/{sid}/computer-use", json={"goal": "Type the weekly note and save it again"}).json()["id"]
    _pump(d, owner, rid2, [], rounds=2)
    with session_scope(_uuid.UUID(owner.org_id)) as db:
        first = db.get(ComputerUseRun, _uuid.UUID(rid2)).transcript[0]["content"][0]["text"]
    assert "fixed similar problems before" not in first
    assert owner.post(f"/api/v1/computer-use/{rid2}/stop").status_code == 200

    aid = draft["id"]
    assert owner.post(f"/api/v1/knowledge/articles/{aid}/review", json={"decision": "submit"}).status_code == 200
    assert owner.post(f"/api/v1/knowledge/articles/{aid}/review", json={"decision": "approve"}).status_code == 200
    assert owner.post(f"/api/v1/knowledge/articles/{aid}/review", json={"decision": "publish", "executable": False}).status_code == 200
    drain()

    rid3 = owner.post(f"/api/v1/sessions/{sid}/computer-use", json={"goal": "Type the weekly note and save it for Friday"}).json()["id"]
    _pump(d, owner, rid3, [], rounds=2)
    with session_scope(_uuid.UUID(owner.org_id)) as db:
        recalled = db.get(ComputerUseRun, _uuid.UUID(rid3)).transcript[0]["content"][0]["text"]
    assert "fixed similar problems before" in recalled and "Learned fix" in recalled


def test_ollama_is_tenant_approved_local_computer_provider(owner, monkeypatch):
    from joltiq.config import get_settings
    from joltiq.db import session_scope
    from joltiq.services.ai.router import available_providers

    monkeypatch.setattr(get_settings(), "ollama_base_url", "http://host.docker.internal:11434")
    created = owner.patch("/api/v1/admin/models/ollama", json={
        "enabled": True,
        "approved_for_tenant_data": True,
        "approved_for_computer_use": True,
        "models": {"computer_use": "gemma3:4b"},
    })
    assert created.status_code == 200, created.text
    with session_scope(uuid.UUID(owner.org_id)) as db:
        providers = available_providers(db, uuid.UUID(owner.org_id), "computer_use")
    assert [(provider, model, key) for provider, model, _cfg, key in providers if provider == "ollama"] == [
        ("ollama", "gemma3:4b", None),
    ]

    owner.patch("/api/v1/admin/models/ollama", json={"approved_for_tenant_data": False})
    with session_scope(uuid.UUID(owner.org_id)) as db:
        assert "ollama" not in [p[0] for p in available_providers(db, uuid.UUID(owner.org_id), "computer_use")]


def test_openai_computer_provider_requires_deployment_and_tenant_approval(owner, monkeypatch):
    from joltiq.config import get_settings
    from joltiq.db import session_scope
    from joltiq.services.ai.router import available_providers

    settings = get_settings()
    monkeypatch.setattr(settings, "allow_external_computer_use_models", False)
    created = owner.patch("/api/v1/admin/models/openai", json={
        "enabled": True,
        "approved_for_tenant_data": True,
        "approved_for_computer_use": False,
        "api_key": "sk-test-openai-computer-0000000000",
    })
    assert created.status_code == 200, created.text
    org_id = uuid.UUID(owner.org_id)
    with session_scope(org_id) as db:
        assert "openai" not in [p[0] for p in available_providers(db, org_id, "computer_use")]

    monkeypatch.setattr(settings, "allow_external_computer_use_models", True)
    with session_scope(org_id) as db:
        assert "openai" not in [p[0] for p in available_providers(db, org_id, "computer_use")]

    owner.patch("/api/v1/admin/models/openai", json={"approved_for_computer_use": True})
    with session_scope(org_id) as db:
        selected = [(provider, model, key) for provider, model, _cfg, key in available_providers(db, org_id, "computer_use")]
    assert ("openai", "gpt-6.1-terra", "sk-test-openai-computer-0000000000") in selected
    assert "local_fixture" not in [provider for provider, _model, _key in selected]

    owner.patch("/api/v1/admin/models/openai", json={"approved_for_tenant_data": False})
    with session_scope(org_id) as db:
        assert "openai" not in [p[0] for p in available_providers(db, org_id, "computer_use")]


def test_openai_computer_provider_rejects_unapproved_or_unpriced_model(owner, monkeypatch):
    from joltiq.config import get_settings
    from joltiq.db import session_scope
    from joltiq.services.ai.router import available_providers

    settings = get_settings()
    monkeypatch.setattr(settings, "allow_external_computer_use_models", True)
    monkeypatch.setattr(settings, "computer_use_cloud_models", ["gpt-6.1-terra"])
    owner.patch("/api/v1/admin/models/openai", json={
        "enabled": True,
        "approved_for_tenant_data": True,
        "approved_for_computer_use": True,
        "api_key": "sk-test-openai-computer-allowlist",
        "models": {"computer_use": "tenant-invented-free-model"},
    })
    with session_scope(uuid.UUID(owner.org_id)) as db:
        assert "openai" not in [p[0] for p in available_providers(db, uuid.UUID(owner.org_id), "computer_use")]

    owner.patch("/api/v1/admin/models/openai", json={"models": {"computer_use": "gpt-6.1-terra"}})
    with session_scope(uuid.UUID(owner.org_id)) as db:
        assert "openai" in [p[0] for p in available_providers(db, uuid.UUID(owner.org_id), "computer_use")]


def test_ollama_computer_normalizes_one_structured_action(monkeypatch):
    from joltiq.services.ai.computer import OllamaComputer

    class Response:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {
                "model": "gemma3:4b",
                "message": {"content": json.dumps({
                    "narration": "Opening Outlook.",
                    "action": "left_click",
                    "params": {"coordinate": [125, 77]},
                })},
                "prompt_eval_count": 91,
                "eval_count": 13,
            }

    sent = {}

    def fake_post(url, *, json, timeout):
        sent.update({"url": url, "body": json, "timeout": timeout})
        return Response()

    monkeypatch.setattr("joltiq.services.ai.computer.httpx.post", fake_post)
    turn = OllamaComputer("http://ollama.local:11434").turn(
        model="gemma3:4b",
        system="stay safe",
        messages=[{"role": "user", "content": [
            {"type": "text", "text": "GOAL: open Outlook"},
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "aGVsbG8="}},
        ]}],
        display={"width": 1280, "height": 800},
    )
    assert sent["url"] == "http://ollama.local:11434/api/chat"
    assert sent["body"]["stream"] is False and sent["body"]["format"]["type"] == "object"
    assert sent["body"]["options"]["num_predict"] == 384
    assert sent["body"]["messages"][-1]["images"] == ["aGVsbG8="]
    assert turn.text == "Opening Outlook."
    assert [(u.name, u.input, u.computer) for u in turn.tool_uses] == [
        ("left_click", {"coordinate": [125, 77]}, True),
    ]
    assert turn.input_tokens == 91 and turn.output_tokens == 13 and turn.live is True


def test_ollama_computer_refuses_malformed_or_unknown_action(monkeypatch):
    from joltiq.services.ai.computer import OllamaComputer
    from joltiq.services.ai.providers import ProviderError

    class Response:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"message": {"content": '{"narration":"no","action":"run_powershell","params":{}}'}}

    monkeypatch.setattr("joltiq.services.ai.computer.httpx.post", lambda *a, **kw: Response())
    with pytest.raises(ProviderError, match="unsupported action"):
        OllamaComputer("http://ollama.local:11434").turn(
            model="gemma3:4b", system="safe", messages=[{"role": "user", "content": "look"}],
            display={"width": 1280, "height": 800},
        )


def test_openai_responses_computer_sends_latest_screen_and_normalizes_one_action(monkeypatch):
    from joltiq.services.ai.computer import OpenAIComputer

    class Response:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {
                "id": "resp_123",
                "model": "gpt-6.1-sol",
                "output": [
                    {"type": "message", "role": "assistant", "content": [
                        {"type": "output_text", "text": "Opening the Apps settings."},
                    ]},
                    {"type": "function_call", "call_id": "call_123", "name": "left_click",
                     "arguments": '{"coordinate":[125,77]}'},
                ],
                "usage": {"input_tokens": 91, "output_tokens": 13},
            }

    sent = {}

    def fake_post(url, *, headers, json, timeout):
        sent.update({"url": url, "headers": headers, "body": json, "timeout": timeout})
        return Response()

    monkeypatch.setattr("joltiq.services.ai.computer.httpx.post", fake_post)
    turn = OpenAIComputer("sk-test", timeout=45).turn(
        model="gpt-6.1-sol",
        system="stay safe",
        messages=[
            {"role": "user", "content": [
                {"type": "text", "text": "GOAL: open Apps settings"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "b2xk"}},
            ]},
            {"role": "assistant", "content": [
                {"type": "tool_use", "id": "old", "name": "screenshot", "input": {}, "toolset_name": "computer"},
            ]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "old", "content": [
                {"type": "text", "text": "OK"},
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "bmV3"}},
            ], "toolset_name": "computer"}]},
        ],
        display={"width": 1280, "height": 800},
    )

    assert sent["url"] == "https://api.openai.com/v1/responses"
    assert sent["headers"]["authorization"] == "Bearer sk-test" and sent["timeout"] == 45
    assert sent["body"]["store"] is False and sent["body"]["parallel_tool_calls"] is False
    assert sent["body"]["tool_choice"] == "required"
    assert sent["body"]["instructions"].startswith("stay safe")
    tool_names = {tool["name"] for tool in sent["body"]["tools"]}
    assert {"screenshot", "left_click", "request_approval", "request_secure_input", "finish"} <= tool_names
    assert "run_powershell" not in tool_names
    image_parts = [part for item in sent["body"]["input"] for part in item.get("content", []) if part.get("type") == "input_image"]
    assert image_parts == [{"type": "input_image", "image_url": "data:image/png;base64,bmV3", "detail": "original"}]
    assert turn.text == "Opening the Apps settings."
    assert [(u.id, u.name, u.input, u.computer) for u in turn.tool_uses] == [
        ("call_123", "left_click", {"coordinate": [125, 77]}, True),
    ]
    assert (turn.input_tokens, turn.output_tokens, turn.model, turn.provider, turn.live) == (
        91, 13, "gpt-6.1-sol", "openai", True,
    )


def test_openai_strict_tool_schemas_require_every_declared_property():
    """OpenAI rejects strict function schemas when any property is omitted from required."""
    from joltiq.services.ai.computer import BROWSER_OPENAI_TOOLS, OPENAI_COMPUTER_TOOLS

    tools = [*OPENAI_COMPUTER_TOOLS, *BROWSER_OPENAI_TOOLS]
    for tool in tools:
        parameters = tool["parameters"]
        assert set(parameters["required"]) == set(parameters["properties"]), tool["name"]

    diagnostic = next(tool for tool in OPENAI_COMPUTER_TOOLS if tool["name"] == "run_diagnostic")
    assert {schema.get("type") for schema in diagnostic["parameters"]["properties"]["host"]["anyOf"]} == {
        "string", "null",
    }


@pytest.mark.parametrize(
    ("output", "error"),
    [
        ([{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "done"}]}],
         "exactly one action"),
        ([{"type": "function_call", "call_id": "c1", "name": "left_click", "arguments": "not-json"}],
         "invalid action arguments"),
        ([{"type": "function_call", "call_id": "c1", "name": "run_powershell", "arguments": "{}"}],
         "unsupported action"),
        ([{"type": "function_call", "call_id": "c1", "name": "screenshot", "arguments": "{}"},
          {"type": "function_call", "call_id": "c2", "name": "left_click", "arguments": '{"coordinate":[1,2]}'}],
         "exactly one action"),
    ],
)
def test_openai_responses_computer_fails_closed_on_invalid_actions(monkeypatch, output, error):
    from joltiq.services.ai.computer import OpenAIComputer
    from joltiq.services.ai.providers import ProviderError

    class Response:
        status_code = 200
        text = ""

        @staticmethod
        def json():
            return {"id": "resp_bad", "model": "gpt-6.1-sol", "output": output, "usage": {}}

    monkeypatch.setattr("joltiq.services.ai.computer.httpx.post", lambda *a, **kw: Response())
    with pytest.raises(ProviderError, match=error):
        OpenAIComputer("sk-test").turn(
            model="gpt-6.1-sol", system="safe", messages=[{"role": "user", "content": "look"}],
            display={"width": 1280, "height": 800},
        )


def test_fix_run_marks_no_rollback_up_front_and_escalates_on_failure(app, owner):
    """ADR 0019: clicks/typing have no safe inverse → shown before approval, recorded + escalated when the run fails."""
    d, sid = _setup(app, owner)
    r = owner.post(f"/api/v1/sessions/{sid}/computer-use", json={"goal": "Delete the old draft, type a note and save it"})
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    run = r.json()
    assert run["mode"] == "fix"
    assert run["rollback"]["status"] == "NO_ROLLBACK_AVAILABLE" and "approval_hash" not in run["rollback"]
    log: list = []
    _pump(d, owner, rid, log)
    assert d.post(f"/computer-use/{rid}/respond", {"approved": False}).status_code == 200
    run = _pump(d, owner, rid, log, until=("completed", "failed", "stopped"))
    assert run["state"] == "failed"
    assert run["rollback"]["outcome"] == "NO_ROLLBACK_AVAILABLE"


def test_rollback_marker_is_hashed_with_goal_and_mode():
    from joltiq.services.computer_use import rollback_marker

    g = rollback_marker("Fix printer", "guide")
    f = rollback_marker("Fix printer", "fix")
    assert g["status"] == "NOT_NEEDED" and f["status"] == "NO_ROLLBACK_AVAILABLE"
    assert g["approval_hash"] != f["approval_hash"] != rollback_marker("Fix printer!", "fix")["approval_hash"]
