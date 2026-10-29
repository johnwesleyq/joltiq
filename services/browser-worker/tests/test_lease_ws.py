"""Control-lease epochs fence revoked controllers and the allowlist covers WebSockets."""
from __future__ import annotations

import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("JOLTIQ_BROWSER_WORKER_TOKEN", "upload-test-token")
os.environ.setdefault("JOLTIQ_BROWSER_MAX_UPLOAD_BYTES", "16")

from worker.app import S, app  # noqa: E402

AUTH = {"authorization": f"Bearer {os.environ['JOLTIQ_BROWSER_WORKER_TOKEN']}"}


class Pages(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = (
            "<input id='f'><script>"
            "const ws = new WebSocket('ws://blocked.invalid/feed');"
            "ws.onclose = () => { document.title = 'closed'; };"
            "ws.onerror = () => { document.title = 'closed'; };"
            "</script>"
        )
        self.send_response(200)
        self.send_header("content-type", "text/html")
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *_args):
        return


@pytest.fixture(scope="module")
def site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Pages)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def _session(client):
    session_id = uuid.uuid4().hex
    response = client.post(
        "/sessions",
        headers=AUTH,
        json={"session_id": session_id, "allowed_domains": ["127.0.0.1"]},
    )
    assert response.status_code == 200
    return session_id


def _act(client, session_id, url, epoch=None, actor="ai"):
    return client.post(
        f"/sessions/{session_id}/actions",
        headers=AUTH,
        json={"action": "browser.navigate", "params": {"url": url}, "actor": actor, "epoch": epoch},
    )


def test_stale_missing_and_future_lease_epochs(client, site):
    session_id = _session(client)
    try:
        assert _act(client, session_id, site).status_code == 200
        assert client.post(
            f"/sessions/{session_id}/lease",
            headers=AUTH,
            json={"epoch": 3, "controller": "customer"},
        ).status_code == 200
        assert _act(client, session_id, site, epoch=2).status_code == 409
        assert _act(client, session_id, site).status_code == 409
        assert _act(client, session_id, site, epoch=4).status_code == 409
        assert _act(client, session_id, site, epoch=3, actor="customer").status_code == 200
    finally:
        client.delete(f"/sessions/{session_id}", headers=AUTH)


def test_customer_technician_ai_handoff_revokes_previous_controller(client, site):
    session_id = _session(client)
    try:
        for epoch, controller in ((1, "ai"), (2, "technician"), (3, "customer"), (4, "ai")):
            response = client.post(
                f"/sessions/{session_id}/lease",
                headers=AUTH,
                json={"epoch": epoch, "controller": controller},
            )
            assert response.status_code == 200
            assert _act(client, session_id, site, epoch=epoch, actor=controller).status_code == 200
            if epoch > 1:
                assert _act(client, session_id, site, epoch=epoch - 1).status_code == 409
    finally:
        client.delete(f"/sessions/{session_id}", headers=AUTH)


def test_websocket_to_unlisted_host_is_blocked(client, site):
    session_id = _session(client)
    try:
        assert _act(client, session_id, site).status_code == 200
        deadline = time.time() + 10
        while time.time() < deadline and not any(
            "blocked.invalid" in url for url in S.sessions[session_id].blocked_requests
        ):
            time.sleep(0.2)
        assert any(
            url.startswith("ws://blocked.invalid")
            for url in S.sessions[session_id].blocked_requests
        )
    finally:
        client.delete(f"/sessions/{session_id}", headers=AUTH)
