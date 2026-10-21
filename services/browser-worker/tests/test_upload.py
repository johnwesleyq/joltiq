from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

os.environ["JOLTIQ_BROWSER_WORKER_TOKEN"] = "upload-test-token"
os.environ["JOLTIQ_BROWSER_MAX_UPLOAD_BYTES"] = "16"

from worker.app import S, app  # noqa: E402


AUTH = {"authorization": "Bearer upload-test-token"}
MARKER = b"proof-123"


class UploadPageHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        variants = {
            "/upload": '<input type="file" id="file"><pre id="receipt">ready</pre>',
            "/autosubmit": '<form method="post"><input type="file" id="file" name="file" onchange="this.form.submit()"></form>'
                           '<pre id="receipt">ready</pre>',
            "/login": '<input type="password" id="password"><input type="file" id="file"><pre id="receipt">ready</pre>',
            "/nonfile": '<input type="text" id="file"><pre id="receipt">ready</pre>',
            "/disabled": '<input type="file" id="file" disabled><pre id="receipt">ready</pre>',
            "/multiple": '<input type="file" class="file"><input type="file" class="file"><pre id="receipt">ready</pre>',
        }
        body = variants.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        script = """<script>
        for (const input of document.querySelectorAll('input[type=file]')) {
          input.addEventListener('change', async () => {
            const f = input.files[0];
            document.querySelector('#receipt').textContent = `${f.name}|${f.size}|${await f.text()}`;
          });
        }
        </script>"""
        data = f"<!doctype html><html><body>{body}{script}</body></html>".encode()
        self.send_response(200)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):  # noqa: N802
        content_length = int(self.headers.get("content-length", "0"))
        self.rfile.read(content_length)
        self.send_response(303)
        self.send_header("location", "/upload")
        self.end_headers()

    def log_message(self, *_args):
        return


@pytest.fixture(scope="module")
def upload_site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), UploadPageHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def close_test_sessions(client):
    yield
    for sid in list(S.sessions):
        client.delete(f"/sessions/{sid}", headers=AUTH)


def start_session(client: TestClient, upload_site: str, page: str = "/upload") -> str:
    sid = uuid.uuid4().hex
    created = client.post("/sessions", headers=AUTH, json={"session_id": sid, "allowed_domains": ["127.0.0.1"]})
    assert created.status_code == 200, created.text
    navigated = client.post(
        f"/sessions/{sid}/actions",
        headers=AUTH,
        json={"action": "browser.navigate", "params": {"url": upload_site + page}, "actor": "ai"},
    )
    assert navigated.status_code == 200 and navigated.json()["ok"] is True
    return sid


def upload(client: TestClient, sid: str, *, selector: str = "#file", content: bytes = MARKER, digest: str | None = None):
    return client.post(
        f"/sessions/{sid}/upload",
        headers=AUTH,
        data={
            "selector": selector,
            "filename": "customer-note.txt",
            "content_type": "text/plain",
            "sha256": digest or hashlib.sha256(content).hexdigest(),
        },
        files={"file": ("transfer.bin", content, "application/octet-stream")},
    )


def receipt(client: TestClient, sid: str) -> str:
    response = client.post(
        f"/sessions/{sid}/actions",
        headers=AUTH,
        json={"action": "browser.read", "params": {"selector": "#receipt"}, "actor": "ai"},
    )
    return response.json()["text"]


def test_upload_sets_exact_in_memory_file_and_logs_only_safe_metadata(client, upload_site):
    sid = start_session(client, upload_site)

    response = upload(client, sid)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["upload"] == {
        "filename": "customer-note.txt",
        "size": 9,
        "sha256": "eaca9f757e34a5377c91f27c7584425b45fd868ff6bc93ce36556a5e7c88aeda",
    }
    assert receipt(client, sid) == "customer-note.txt|9|proof-123"
    serialized = json.dumps(S.sessions[sid].log)
    assert "proof-123" not in serialized
    assert "transfer.bin" not in serialized
    assert "/tmp/" not in serialized


def test_upload_requires_worker_bearer_token(client, upload_site):
    sid = start_session(client, upload_site)

    response = client.post(
        f"/sessions/{sid}/upload",
        data={"selector": "#file", "filename": "x.txt", "content_type": "text/plain", "sha256": "0" * 64},
        files={"file": ("x", b"x")},
    )

    assert response.status_code == 401
    assert receipt(client, sid) == "ready"


def test_navigation_surfaces_authentication_required_before_sensitive_action(client, upload_site):
    sid = uuid.uuid4().hex
    created = client.post("/sessions", headers=AUTH, json={"session_id": sid, "allowed_domains": ["127.0.0.1"]})
    assert created.status_code == 200

    response = client.post(
        f"/sessions/{sid}/actions",
        headers=AUTH,
        json={"action": "browser.navigate", "params": {"url": upload_site + "/login"}, "actor": "ai"},
    )

    assert response.status_code == 200
    assert response.json()["auth_required"] is True
    assert response.json()["kind"] == "login"


def test_upload_waits_for_auto_submit_navigation_before_building_response(client, upload_site):
    sid = start_session(client, upload_site, "/autosubmit")

    response = upload(client, sid)

    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True
    assert response.json()["page"]["url"] == upload_site + "/upload"


@pytest.mark.parametrize(
    ("case", "page", "selector", "content", "digest", "pause", "error"),
    [
        ("oversized", "/upload", "#file", b"x" * 17, None, False, "size"),
        ("wrong digest", "/upload", "#file", MARKER, "0" * 64, False, "digest"),
        ("paused", "/upload", "#file", MARKER, None, True, "paused"),
        ("auth page", "/login", "#file", MARKER, None, False, "authentication"),
        ("missing", "/upload", "#missing", MARKER, None, False, "exactly one"),
        ("not file", "/nonfile", "#file", MARKER, None, False, "file input"),
        ("disabled", "/disabled", "#file", MARKER, None, False, "disabled"),
        ("multiple", "/multiple", ".file", MARKER, None, False, "exactly one"),
    ],
)
def test_upload_refuses_unsafe_or_ambiguous_target_without_setting_a_file(
    client, upload_site, case, page, selector, content, digest, pause, error
):
    sid = start_session(client, upload_site, page)
    if pause:
        assert client.post(f"/sessions/{sid}/pause", headers=AUTH, json={"paused": True}).status_code == 200

    response = upload(client, sid, selector=selector, content=content, digest=digest)

    assert response.status_code == 200, f"{case}: {response.text}"
    assert response.json()["ok"] is False
    assert error in response.json()["error"].lower()
    if pause:
        assert client.post(f"/sessions/{sid}/pause", headers=AUTH, json={"paused": False}).status_code == 200
    assert receipt(client, sid) == "ready"

