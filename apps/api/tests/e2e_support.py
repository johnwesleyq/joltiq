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
TEST_HOST = os.environ.get("JOLTIQ_TEST_HOST", "127.0.0.1")
TESTAPP = f"http://{TEST_HOST}:{TESTAPP_PORT}"
WORKER = f"http://{TEST_HOST}:{WORKER_PORT}"


def _free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def _wait(url: str, timeout: float = 30, proc: subprocess.Popen | None = None) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            raise RuntimeError(f"service for {url} exited early with code {proc.returncode}")
        try:
            if httpx.get(url, timeout=1).status_code < 500:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.25)
    raise RuntimeError(f"service at {url} did not start")


def _owned(port: int, name: str) -> bool:
    """True when we must start the service ourselves. A busy local port is an error, never silently reused:
    a stale worker from an earlier run (different token/code) was the cause of flaky E2E failures."""
    if TEST_HOST != "127.0.0.1":
        return False
    if not _free(port):
        if os.environ.get("JOLTIQ_TEST_REUSE_SERVICES") == "1":
            return False
        raise RuntimeError(f"{name} port {port} is already in use; unset JOLTIQ_TEST_*_PORT or set JOLTIQ_TEST_REUSE_SERVICES=1")
    return True


def start_services():
    procs = []
    py = sys.executable
    app_proc = worker_proc = None
    if _owned(TESTAPP_PORT, "test app"):
        app_proc = subprocess.Popen([py, "-m", "uvicorn", "app:app", "--port", str(TESTAPP_PORT), "--log-level", "warning"],
                                      cwd=os.path.join(ROOT, "tests", "e2e", "testapp"))
        procs.append(app_proc)
    if _owned(WORKER_PORT, "browser worker"):
        env = {**os.environ, "JOLTIQ_BROWSER_WORKER_TOKEN": "test-worker-token"}
        worker_proc = subprocess.Popen([py, "-m", "uvicorn", "worker.app:app", "--port", str(WORKER_PORT), "--log-level", "warning"],
                                       cwd=os.path.join(ROOT, "services", "browser-worker"), env=env)
        procs.append(worker_proc)
    try:
        _wait(f"{TESTAPP}/docs/index.json", proc=app_proc)
        _wait(f"{WORKER}/healthz", timeout=60, proc=worker_proc)
    except Exception:
        stop_services(procs)
        raise
    return procs


def stop_services(procs):
    for p in procs:
        p.terminate()
    for p in procs:
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
