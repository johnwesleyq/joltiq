"""Contract tests for the OpenAI / Anthropic / Gemini adapters: request shape (auth header, endpoint, structured-output
fields) and response parsing (text, structured data, token usage), plus retryable-error classification. HTTP is intercepted
with recorded-shape fixtures — LOCAL FIXTURES, not live calls (no provider keys in this environment)."""
from __future__ import annotations

import json

import httpx
import pytest

from joltiq.services.ai import providers as P

pytestmark = pytest.mark.mocked
SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"], "additionalProperties": False}


class Capture:
    def __init__(self, status: int, body: dict):
        self.status, self.body, self.req = status, body, None

    def __call__(self, url, headers=None, json=None, timeout=None):  # noqa: A002
        self.req = {"url": url, "headers": headers, "json": json}
        return httpx.Response(self.status, json=self.body, request=httpx.Request("POST", url))


def test_openai_structured_request_and_usage(monkeypatch):
    cap = Capture(200, {"choices": [{"message": {"content": '{"answer": "ok"}'}}], "usage": {"prompt_tokens": 11, "completion_tokens": 3}})
    monkeypatch.setattr(P.httpx, "post", cap)
    r = P.OpenAIProvider("sk-x").complete(model="gpt-4.1-mini", system="s", messages=[{"role": "user", "content": "q"}], schema=SCHEMA, schema_name="R")
    assert cap.req["url"].endswith("/chat/completions") and cap.req["headers"]["authorization"] == "Bearer sk-x"
    assert cap.req["json"]["response_format"]["type"] == "json_schema" and cap.req["json"]["messages"][0]["role"] == "system"
    assert r.data == {"answer": "ok"} and (r.input_tokens, r.output_tokens) == (11, 3) and r.live is True


def test_anthropic_forced_tool_and_usage(monkeypatch):
    cap = Capture(200, {"content": [{"type": "tool_use", "name": "R", "input": {"answer": "ok"}}], "usage": {"input_tokens": 20, "output_tokens": 5}})
    monkeypatch.setattr(P.httpx, "post", cap)
    r = P.AnthropicProvider("ak").complete(model="claude-sonnet-4-5", system="s", messages=[{"role": "user", "content": "q"}], schema=SCHEMA, schema_name="R")
    j = cap.req["json"]
    assert cap.req["url"].endswith("/messages") and cap.req["headers"]["x-api-key"] == "ak" and cap.req["headers"]["anthropic-version"]
    assert j["system"] == "s" and j["tool_choice"] == {"type": "tool", "name": "R"} and j["tools"][0]["input_schema"] == SCHEMA
    assert r.data == {"answer": "ok"} and (r.input_tokens, r.output_tokens) == (20, 5)


def test_gemini_schema_sanitised_and_usage(monkeypatch):
    cap = Capture(200, {"candidates": [{"content": {"parts": [{"text": '{"answer": "ok"}'}]}}], "usageMetadata": {"promptTokenCount": 7, "candidatesTokenCount": 2}})
    monkeypatch.setattr(P.httpx, "post", cap)
    r = P.GeminiProvider("gk").complete(model="gemini-2.5-flash", system="s", messages=[{"role": "user", "content": "q"}], schema=SCHEMA)
    gc = cap.req["json"]["generationConfig"]
    assert ":generateContent" in cap.req["url"] and cap.req["headers"]["x-goog-api-key"] == "gk"
    assert gc["responseMimeType"] == "application/json" and "additionalProperties" not in json.dumps(gc["responseSchema"])
    assert r.data == {"answer": "ok"} and (r.input_tokens, r.output_tokens) == (7, 2)


@pytest.mark.parametrize("cls,status,retryable", [(P.OpenAIProvider, 429, True), (P.AnthropicProvider, 529, True), (P.GeminiProvider, 400, False),
                                                  (P.OpenAIProvider, 401, False)])
def test_error_classification(monkeypatch, cls, status, retryable):
    monkeypatch.setattr(P.httpx, "post", Capture(status, {"error": {"message": "x"}}))
    with pytest.raises(P.ProviderError) as e:
        cls("k").complete(model="m", system="s", messages=[{"role": "user", "content": "q"}])
    assert e.value.retryable is retryable


def test_router_falls_back_and_records_costs(owner, monkeypatch):
    """First provider fails with a retryable error on every attempt; router falls back to the next approved provider."""
    import uuid

    from sqlalchemy import select

    from joltiq.db import session_scope
    from joltiq.models import ModelCall
    from joltiq.services.ai import router
    from joltiq.services.ai.schemas import TicketSummary

    owner.patch("/api/v1/admin/models/anthropic", json={"enabled": True, "approved_for_tenant_data": True, "api_key": "ak-test-0000000000", "max_retries": 0})
    owner.patch("/api/v1/admin/models/openai", json={"enabled": True, "approved_for_tenant_data": True, "api_key": "sk-test-0000000000",
                                                     "input_cost_per_mtok": 1.0, "output_cost_per_mtok": 2.0})

    def fake_post(url, headers=None, json=None, timeout=None):  # noqa: A002
        if "anthropic" in url:
            return httpx.Response(529, json={"error": "overloaded"}, request=httpx.Request("POST", url))
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "choices": [{"message": {"content": '{"summary": "printer offline", "category": null, "suggested_priority": null}'}}],
            "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 0}})

    monkeypatch.setattr(P.httpx, "post", fake_post)
    org = uuid.UUID(owner.org_id)
    with session_scope(org) as db:
        out, mc = router.call(db, org, "summary", system="s", messages=[{"role": "user", "content": "x"}], output=TicketSummary)
        assert out.summary == "printer offline" and mc.provider == "openai" and abs(mc.cost_usd - 1.0) < 1e-9
        calls = db.execute(select(ModelCall.provider, ModelCall.ok)).all()
        assert ("anthropic", False) in calls and ("openai", True) in calls
