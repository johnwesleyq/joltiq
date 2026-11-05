"""D-023: unsupervised Fix is removed. Its endpoints are gone and no status field remains."""
from __future__ import annotations

import uuid


def test_unsupervised_endpoints_are_gone(owner):
    assert owner.get("/api/v1/organizations/current/unsupervised-fix").status_code in (404, 405)
    r = owner.put("/api/v1/organizations/current/unsupervised-fix",
                  json={"unsupervised_browser_fix": True, "confirm": "I UNDERSTAND THIS RUNS WITHOUT APPROVALS"})
    assert r.status_code in (404, 405)
    assert owner.post(f"/api/v1/devices/{uuid.uuid4()}/unsupervised-fix", json={"goal": "fix it now"}).status_code in (404, 405)


def test_ai_status_has_no_unsupervised_field(owner):
    assert "unsupervised_fix" not in owner.get("/api/v1/agents/ai-status").json()
