"""Contract tests for the OpenAI / Anthropic / Gemini adapters: request shape (auth header, endpoint, structured-output
fields) and response parsing (text, structured data, token usage), plus retryable-error classification. HTTP is intercepted
with recorded-shape fixtures — LOCAL FIXTURES, not live calls (no provider keys in this environment)."""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from joltiq.services.ai import providers as P

pytestmark = pytest.mark.mocked
SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}}, "required": ["answer"], "additionalProperties": False}


def test_openai_central_model_policy_and_standard_prices():
    from joltiq.services.ai import router

    assert P.DEFAULT_MODELS["openai"] == {
        "default": "gpt-6-luna",
        "planning": "gpt-6-luna",
        "vision": "gpt-6.1-terra",
        "computer_use": "gpt-6.1-terra",
        "escalation": "gpt-6.1-sol",
    }
    stale_tenant_rates = SimpleNamespace(input_cost_per_mtok=99.0, output_cost_per_mtok=199.0)
    assert router.calculate_cost("openai", "gpt-6-luna", 1_000_000, 1_000_000, stale_tenant_rates) == pytest.approx(0.60)
    assert router.calculate_cost("openai", "gpt-6.1-terra", 1_000_000, 1_000_000, stale_tenant_rates) == pytest.approx(6.0)
    assert router.calculate_cost("openai", "gpt-6.1-sol", 1_000_000, 1_000_000, stale_tenant_rates) == pytest.approx(12.0)
    assert router.calculate_cost("openai", "customer-fine-tune", 1_000_000, 1_000_000, stale_tenant_rates) == pytest.approx(298.0)


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
        # The recognized central model uses the deployment-owned Luna price,
        # not the deliberately stale tenant-configured $1/MTok rate.
        assert out.summary == "printer offline" and mc.provider == "openai" and abs(mc.cost_usd - 0.10) < 1e-9
        calls = db.execute(select(ModelCall.provider, ModelCall.ok)).all()
        assert ("anthropic", False) in calls and ("openai", True) in calls


def test_computer_router_never_fails_over_from_consented_destination(owner, monkeypatch):
    import uuid

    from joltiq.core.errors import Unavailable
    from joltiq.db import session_scope
    from joltiq.services.ai import computer, router

    attempted = []
    cfg = SimpleNamespace(timeout_seconds=60, max_retries=0, input_cost_per_mtok=0.0, output_cost_per_mtok=0.0)

    class Client:
        live = True

        def __init__(self, provider):
            self.provider = provider

        def turn(self, **_kwargs):
            attempted.append(self.provider)
            if self.provider == "openai":
                raise P.ProviderError("provider unavailable", retryable=True)
            return computer.Turn("done", [], [], provider=self.provider, model="other-model")

    monkeypatch.setattr(router, "available_providers", lambda *_args: [
        ("anthropic", "claude", cfg, "ak"),
        ("openai", "gpt-6.1-terra", cfg, "sk"),
    ])
    monkeypatch.setattr(computer, "build", lambda provider, *_args, **_kwargs: Client(provider))

    with session_scope(uuid.UUID(owner.org_id)) as db:
        with pytest.raises(Unavailable):
            router.computer_turn(
                db, uuid.UUID(owner.org_id), system="s", messages=[{"role": "user", "content": "x"}],
                display={"width": 10, "height": 10}, provider_name="openai", model_name="gpt-6.1-terra",
            )
    assert attempted == ["openai"]


def test_router_escalates_failed_luna_reasoning_to_sol(owner, monkeypatch):
    import uuid

    from sqlalchemy import select

    from joltiq.db import session_scope
    from joltiq.models import ModelCall, ModelProviderConfig
    from joltiq.services.ai import router
    from joltiq.services.ai.schemas import TicketSummary

    owner.patch("/api/v1/admin/models/openai", json={
        "enabled": True,
        "approved_for_tenant_data": True,
        "api_key": "sk-test-escalation-0000000000",
        "max_retries": 0,
    })
    attempted = []

    class Client:
        live = True

        def complete(self, *, model, **kwargs):
            attempted.append(model)
            if model == "gpt-6-luna":
                raise P.ProviderError("temporary failure", retryable=True)
            return P.ModelResult(
                text='{"summary":"recovered","category":null,"suggested_priority":null}',
                data={"summary": "recovered", "category": None, "suggested_priority": None},
                input_tokens=1000,
                output_tokens=100,
                model=model,
                provider="openai",
                live=True,
                latency_ms=7,
            )

    monkeypatch.setattr(router, "build", lambda *args, **kwargs: Client())
    org = uuid.UUID(owner.org_id)
    with session_scope(org) as db:
        cfg = db.execute(select(ModelProviderConfig).where(ModelProviderConfig.provider == "openai")).scalar_one()
        cfg.models = {}
        monkeypatch.setattr(router, "available_providers", lambda *_args: [("openai", "gpt-6-luna", cfg, "secret")])
        out, mc = router.call(db, org, "planning", system="s", messages=[{"role": "user", "content": "x"}], output=TicketSummary)
        assert out.summary == "recovered" and mc.model == "gpt-6.1-sol"
        calls = db.execute(select(ModelCall.model, ModelCall.ok).where(ModelCall.task == "planning").order_by(ModelCall.created_at)).all()

    assert attempted == ["gpt-6-luna", "gpt-6.1-sol"]
    assert calls[-2:] == [("gpt-6-luna", False), ("gpt-6.1-sol", True)]


def test_router_does_not_escalate_successful_luna(owner, monkeypatch):
    import uuid

    from sqlalchemy import select

    from joltiq.db import session_scope
    from joltiq.models import ModelProviderConfig
    from joltiq.services.ai import router
    from joltiq.services.ai.schemas import TicketSummary

    owner.patch("/api/v1/admin/models/openai", json={
        "enabled": True,
        "approved_for_tenant_data": True,
        "api_key": "sk-test-no-escalation-0000000000",
        "max_retries": 0,
    })
    attempted = []

    class Client:
        live = True

        def complete(self, *, model, **kwargs):
            attempted.append(model)
            return P.ModelResult(
                text='{"summary":"routine","category":null,"suggested_priority":null}',
                data={"summary": "routine", "category": None, "suggested_priority": None},
                model=model,
                provider="openai",
            )

    monkeypatch.setattr(router, "build", lambda *args, **kwargs: Client())
    org = uuid.UUID(owner.org_id)
    with session_scope(org) as db:
        cfg = db.execute(select(ModelProviderConfig).where(ModelProviderConfig.provider == "openai")).scalar_one()
        cfg.models = {}
        monkeypatch.setattr(router, "available_providers", lambda *_args: [("openai", "gpt-6-luna", cfg, "secret")])
        out, mc = router.call(db, org, "planning", system="s", messages=[{"role": "user", "content": "x"}], output=TicketSummary)

    assert out.summary == "routine" and mc.model == "gpt-6-luna"
    assert attempted == ["gpt-6-luna"]


def test_router_refuses_call_before_provider_when_run_budget_cannot_fit(owner, monkeypatch):
    import uuid

    from sqlalchemy import select

    from joltiq.core.errors import EntitlementExceeded
    from joltiq.db import session_scope
    from joltiq.models import AgentRun, AIBudget, ModelProviderConfig
    from joltiq.services.ai import router
    from joltiq.services.ai.schemas import TicketSummary

    owner.patch("/api/v1/admin/models/openai", json={
        "enabled": True,
        "approved_for_tenant_data": True,
        "api_key": "sk-test-budget-0000000000",
        "max_retries": 0,
    })
    contacted = []

    class Client:
        live = True

        def complete(self, **kwargs):
            contacted.append(True)
            return P.ModelResult(
                text='{"summary":"should not run","category":null,"suggested_priority":null}',
                data={"summary": "should not run", "category": None, "suggested_priority": None},
                model="gpt-6-luna",
                provider="openai",
            )

    monkeypatch.setattr(router, "build", lambda *args, **kwargs: Client())
    org = uuid.UUID(owner.org_id)
    with session_scope(org) as db:
        cfg = db.execute(select(ModelProviderConfig).where(ModelProviderConfig.provider == "openai")).scalar_one()
        cfg.models = {}
        budget = db.execute(select(AIBudget)).scalar_one_or_none()
        if budget is None:
            budget = AIBudget(org_id=org)
            db.add(budget)
        budget.per_run_usd_limit = 0.0001
        db.flush()
        run = AgentRun(org_id=org, correlation_id=uuid.uuid4().hex)
        db.add(run)
        db.flush()
        monkeypatch.setattr(router, "available_providers", lambda *_args: [("openai", "gpt-6-luna", cfg, "secret")])

        with pytest.raises(EntitlementExceeded) as exc:
            router.call(
                db,
                org,
                "planning",
                system="system",
                messages=[{"role": "user", "content": "diagnose this"}],
                output=TicketSummary,
                run_id=run.id,
                max_tokens=2000,
            )

    assert exc.value.code == "run_budget_exhausted"
    assert contacted == []
