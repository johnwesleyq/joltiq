import base64

from fastapi.testclient import TestClient
from mock_freshdesk.app import app, state

AUTH = {"Authorization": "Basic " + base64.b64encode(b"mock-fd-key-0001:X").decode()}


def client() -> TestClient:
    state.reset()
    return TestClient(app)


def test_requires_basic_auth() -> None:
    c = client()
    assert c.get("/api/v2/agents/me").status_code == 401
    bad = {"Authorization": "Basic " + base64.b64encode(b"nope:X").decode()}
    assert c.get("/api/v2/agents/me", headers=bad).status_code == 401
    r = c.get("/api/v2/agents/me", headers=AUTH)
    assert r.status_code == 200 and r.headers["X-RateLimit-Total"]


def test_ticket_lifecycle_notes_reply_and_status() -> None:
    c = client()
    r = c.post(
        "/api/v2/tickets",
        headers=AUTH,
        json={"email": "ana@x.example", "name": "Ana", "subject": "Outlook", "description": "d"},
    )
    assert r.status_code == 201
    tid = r.json()["id"]
    assert r.json()["status"] == 2 and r.headers["Location"].endswith(f"/api/v2/tickets/{tid}")
    v = c.get(f"/api/v2/tickets/{tid}?include=requester", headers=AUTH).json()
    assert v["requester"]["email"] == "ana@x.example"
    assert (
        c.post(
            f"/api/v2/tickets/{tid}/notes", headers=AUTH, json={"body": "n", "private": True}
        ).status_code
        == 201
    )
    assert (
        c.post(f"/api/v2/tickets/{tid}/reply", headers=AUTH, json={"body": "fixed"}).status_code
        == 201
    )
    assert c.post(f"/api/v2/tickets/{tid}/notes", headers=AUTH, json={}).status_code == 400
    assert c.put(f"/api/v2/tickets/{tid}", headers=AUTH, json={"status": 9}).status_code == 400
    assert c.put(f"/api/v2/tickets/{tid}", headers=AUTH, json={"status": 4}).json()["status"] == 4
    convs = c.get(f"/api/v2/tickets/{tid}/conversations", headers=AUTH).json()
    assert [x["private"] for x in convs] == [True, False]


def test_updated_since_ordering_and_pagination() -> None:
    c = client()
    for i in range(3):
        c.post("/__mock/tickets", json={"email": f"u{i}@x.example", "subject": f"t{i}"})
    r = c.get(
        "/api/v2/tickets?updated_since=2000-01-01T00:00:00Z&order_by=updated_at&order_type=asc&per_page=2",
        headers=AUTH,
    )
    assert len(r.json()) == 2 and 'rel="next"' in r.headers["link"]
    assert c.get("/api/v2/tickets?updated_since=2999-01-01T00:00:00Z", headers=AUTH).json() == []
    assert c.get("/api/v2/tickets?per_page=101", headers=AUTH).status_code == 400


def test_injected_429_has_retry_after() -> None:
    c = client()
    c.post(
        "/__mock/faults",
        json={"status": 429, "count": 1, "retry_after": 7, "path_contains": "/agents"},
    )
    r = c.get("/api/v2/agents/me", headers=AUTH)
    assert r.status_code == 429 and r.headers["Retry-After"] == "7"
    assert c.get("/api/v2/agents/me", headers=AUTH).status_code == 200


def test_ui_is_labelled_mock() -> None:
    c = client()
    assert "MOCK Freshdesk" in c.get("/").text

