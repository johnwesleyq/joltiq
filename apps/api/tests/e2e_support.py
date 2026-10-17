"""Starts the real browser worker and the authorized local test app as subprocesses for E2E tests."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time

import httpx

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
WORKER_PORT = int(os.environ.get("JOLTIQ_TEST_WORKER_PORT", "8199"))
TESTAPP_PORT = int(os.environ.get("JOLTIQ_TEST_APP_PORT", "8399"))
TESTAPP = f"http://127.0.0.1:{TESTAPP_PORT}"


def _free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def _wait(url: str, timeout: float = 30) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=1).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    raise RuntimeError(f"service at {url} did not start")


def start_services():
    procs = []
    py = sys.executable
    if _free(TESTAPP_PORT):
        procs.append(subprocess.Popen([py, "-m", "uvicorn", "app:app", "--port", str(TESTAPP_PORT), "--log-level", "warning"],
                                      cwd=os.path.join(ROOT, "tests", "e2e", "testapp")))
    if _free(WORKER_PORT):
        env = {**os.environ, "JOLTIQ_BROWSER_WORKER_TOKEN": "test-worker-token"}
        procs.append(subprocess.Popen([py, "-m", "uvicorn", "worker.app:app", "--port", str(WORKER_PORT), "--log-level", "warning"],
                                      cwd=os.path.join(ROOT, "services", "browser-worker"), env=env))
    _wait(f"{TESTAPP}/docs/index.json")
    _wait(f"http://127.0.0.1:{WORKER_PORT}/healthz")
    return procs


def stop_services(procs):
    for p in procs:
        p.terminate()
    for p in procs:
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
