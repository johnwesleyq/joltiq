"""Contract test: the product Freshdesk adapter against the labelled local mock."""
import pytest
from fastapi.testclient import TestClient
from mock_freshdesk.app import app, state

integrations = pytest.importorskip("joltiq.services.integrations")


def adapter(key: str = "mock-fd-key-0001"):
    state.reset()
    client = TestClient(app)
    return integrations.Freshdesk("", key, transport=client._transport, base_url="http://testserver"), client


def test_me_and_bad_key() -> None:
    freshdesk, _ = adapter()
    assert freshdesk.me()["id"]
    bad, _ = adapter("wrong-key")
    with pytest.raises(integrations.Unavailable):
        bad.me()


def test_poll_note_and_status_round_trip() -> None:
    freshdesk, client = adapter()
    response = client.post(
        "/api/v2/tickets",
        headers=freshdesk.c.headers,
        json={"subject": "DEMO printer offline", "description": "DEMO", "email": "user@example.com", "priority": 2, "status": 2},
    )
    assert response.status_code == 201
    ticket_id = str(response.json()["id"])
    assert any(str(ticket["id"]) == ticket_id for ticket in freshdesk.tickets_since(None))
    freshdesk.add_note(ticket_id, "DEMO private note", private=True)
    freshdesk.set_status(ticket_id, 4)
    assert freshdesk.get_ticket(ticket_id)["status"] == 4
