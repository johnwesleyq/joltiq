"""END-TO-END desktop flow through the REAL companion protocol code (.NET JoltIQ.Agent.Core) against a REAL running API
(uvicorn + PostgreSQL). The operating system is SIMULATED (JoltIQ.Agent.Simulator, a labelled test fixture) because this
environment is Linux; real Windows execution must be verified on a Windows 11 host (see PROJECT_STATUS.md)."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

import httpx
import pytest
from conftest import invite_and_join, publish_executable

pytestmark = [pytest.mark.mocked]
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
AGENT_DIR = os.path.join(ROOT, "apps", "windows-agent")
SIM_DLL = os.path.join(AGENT_DIR, "src", "JoltIQ.Agent.Simulator", "bin", "Debug", "net8.0", "JoltIQ.Agent.Simulator.dll")
PORT = 8297
URL = f"http://127.0.0.1:{PORT}"


@pytest.fixture(scope="module")
def api_server():
    if not shutil.which("dotnet"):
        pytest.skip("dotnet SDK not installed")
    b = subprocess.run(["dotnet", "build", os.path.join(AGENT_DIR, "src", "JoltIQ.Agent.Simulator"), "-v", "q", "-nologo",
                        "--configfile", os.path.join(AGENT_DIR, "nuget.offline.config")], capture_output=True, text=True)
    assert b.returncode == 0, b.stdout[-2000:]
    env = {**os.environ}
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "joltiq.main:app", "--port", str(PORT), "--log-level", "warning"],
                         cwd=os.path.join(ROOT, "apps", "api"), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(80):
        try:
            if httpx.get(f"{URL}/healthz", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.25)
    yield
    p.terminate()
    p.wait(10)


def sim(*args, background=False):
    cmd = ["dotnet", SIM_DLL, *args]
    if background:
        return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


def drain():
    from joltiq.services.jobs import drain as d

    return d()


def publish_desktop_procedure(app, owner):
    body = {"procedure_schema": 2, "kind": "windows_outlook_pdf_crash",
            "display": {"addin_name": "PDF Preview", "addin_progid": "PdfPreview.Connect"},
            "symptoms": ["Outlook closes when opening PDF attachments"], "environment": ["Classic Outlook for Microsoft 365"],
            "prerequisites": ["Enrolled device", "Customer consent"],
            "diagnostics": [{"action": "outlook.pdf_crash.inspect",
                             "params": {"addin_progid": "PdfPreview.Connect", "faulting_module": "PdfPreview.dll", "lookback_hours": 24},
                             "expect": {"supported": True, "outlook.kind": "classic", "addin.installed": True, "crash.matched": True}}],
            "remediation": ["Disable the PdfPreview add-in for the user", "Restart Outlook"], "risk": "SENSITIVE",
            "verification": ["Add-in LoadBehavior = 2", "Outlook responding"],
            "procedure": [
                {"action": "office.addin.set_load_behavior", "params": {"app": "Outlook", "addin_progid": "PdfPreview.Connect", "enabled": False},
                 "rationale": "Disable faulty PDF preview add-in",
                 "postconditions": [{"type": "device_config_equals", "params": {"enabled": False}}]},
                {"action": "app.restart", "params": {"process": "outlook.exe"}, "rationale": "Restart Outlook to apply",
                 "postconditions": [{"type": "device_process_responding", "params": {"process": "outlook.exe"}}]}],
            "rollback": {"action": "office.addin.restore_load_behavior", "source_step": 0,
                         "restart": {"action": "app.restart", "params": {"process": "outlook.exe"}}},
            "messages": {"diagnostic_consent": "Check Outlook crash evidence", "remediation": "Apply the approved Outlook fix"}}
    art = owner.post("/api/v1/knowledge/articles", json={"title": "Outlook crashes opening PDF attachments (PdfPreview add-in)", "body": body}).json()
    publish_executable(app, owner, art["id"])
    drain()


def run_until(c, run_id, pred, proc=None, timeout=60):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        drain()
        last = c.get(f"/api/v1/agents/runs/{run_id}").json()
        if pred(last):
            return last
        time.sleep(0.3)
    out = proc.stdout.read() if proc and proc.poll() is not None else ""
    raise AssertionError(f"timeout: {last['state']} {last['paused_for']} {last['transitions'][-3:]} {out[-1500:]}")


def setup_device(app, owner, tmp):
    acct = owner.post("/api/v1/customers", json={"name": "Acme HVAC"}).json()
    customer = invite_and_join(app, owner, "customer", acct["id"])
    code = customer.post("/api/v1/devices/enrollment-codes", json={}).json()["code"]
    dev = json.loads(sim("enroll", URL, code, tmp).strip().splitlines()[-1])
    return customer, dev


def test_desktop_fix_verified_via_companion_protocol(api_server, app, owner):
    tmp = tempfile.mkdtemp()
    publish_desktop_procedure(app, owner)
    customer, dev = setup_device(app, owner, tmp)
    t = customer.post("/api/v1/tickets", json={"subject": "Outlook crashes when opening PDF attachments",
                                               "description": "Outlook closes every time I open a PDF attachment.", "device_id": dev["device_id"]}).json()
    run_id = customer.post("/api/v1/agents/runs", json={"ticket_id": t["id"]}).json()["run_id"]
    run = run_until(customer, run_id, lambda r: r["state"] == "AWAITING_DIAGNOSTIC_CONSENT")
    assert run["plans"] == []  # no executable plan exists before exact device diagnosis
    sess = customer.get("/api/v1/sessions", params={"ticket_id": t["id"]}).json()["items"][0]
    assert sess["kind"] == "desktop" and sess["state"] == "pending_consent"
    assert sess["request_details"]["addin_progid"] == "PdfPreview.Connect"
    assert sess["request_details"]["read_only"] is True
    assert customer.post(f"/api/v1/sessions/{sess['id']}/consent", json={"minutes": 30}).status_code == 200
    proc = sim("run", URL, tmp, "45", background=True)
    run = run_until(customer, run_id, lambda r: r["state"] == "AWAITING_APPROVAL")
    assert [s["action"] for s in run["plans"][-1]["steps"]] == ["office.addin.set_load_behavior", "app.restart"]
    assert run["plans"][-1]["procedure"]["hash"] == sess["request_details"]["procedure_hash"]
    assert any(o.get("task") == "candidate_selection" for o in run["model_calls"])
    assert all(s["params"].get("addin_progid", "PdfPreview.Connect") == "PdfPreview.Connect" for s in run["plans"][-1]["steps"])
    for a in run["approvals"]:
        assert customer.post(f"/api/v1/approvals/{a['id']}/decision", json={"decision": "approve"}).status_code == 200
    run = run_until(customer, run_id, lambda r: r["state"] in ("RESOLVED", "ESCALATED", "FAILED", "PARTIALLY_RESOLVED"), proc, timeout=45)
    proc.terminate()
    out = proc.communicate(timeout=10)[0]
    assert run["state"] == "RESOLVED", (run["transitions"][-4:], out[-1500:])
    checks = {d["type"]: d for d in run["verifications"][-1]["details"]}
    assert checks["device_config_equals"]["status"] == "VERIFIED" and checks["device_process_responding"]["status"] == "VERIFIED"
    assert customer.get(f"/api/v1/tickets/{t['id']}").json()["resolution_verified"] is True


def test_device_declines_consent_nothing_runs(api_server, app, owner):
    tmp = tempfile.mkdtemp()
    publish_desktop_procedure(app, owner)
    customer, dev = setup_device(app, owner, tmp)
    t = customer.post("/api/v1/tickets", json={"subject": "Outlook crashes when opening PDF attachments", "description": "crash on pdf",
                                               "device_id": dev["device_id"]}).json()
    run_id = customer.post("/api/v1/agents/runs", json={"ticket_id": t["id"]}).json()["run_id"]
    run = run_until(customer, run_id, lambda r: r["state"] == "AWAITING_DIAGNOSTIC_CONSENT")
    assert run["plans"] == []
    sess = customer.get("/api/v1/sessions", params={"ticket_id": t["id"]}).json()["items"][0]
    customer.post(f"/api/v1/sessions/{sess['id']}/consent", json={})
    out = sim("run", URL, tmp, "6", "--decline")
    assert "actions_executed=0" in out
    run = run_until(customer, run_id, lambda r: r["state"] == "ESCALATED")
    assert run["plans"] == []
    assert customer.get(f"/api/v1/sessions/{sess['id']}").json()["state"] == "terminated"
