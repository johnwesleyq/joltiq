"""Plain, live-model Ask JoltIQ chat.

Ordinary Ask messages never enter computer-use or approval flows. Guide/Fix remain
separate, explicitly consented actions.
"""
from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..core.errors import AppError
from ..models import AIBudget, AgentMessage, AgentRun, ModelProviderConfig, Organization, Ticket
from ..models.enums import TERMINAL_RUN_STATES, RunState
from . import audit
from .ai.providers import ProviderError, build
from .ai.router import _log_call, available_providers
from .billing import check_entitlement, meter

CHAT_PROVIDERS = ("openai", "anthropic", "gemini", "ollama")
HISTORY_LIMIT = 20
SETUP_NOTICE = (
    "Connect OpenAI to start chatting: an admin can open Admin → Models → OpenAI, "
    "add the key, enable it, and approve it for tenant data."
)
SYSTEM_PROMPT = """You are JoltIQ, a friendly expert IT support technician for ordinary businesses and MSPs.
Help with Windows, macOS, Microsoft 365, networking, cloud services, coding, browsers, accounts, and business apps.
If the issue is unclear, ask at most two short clarifying questions. Otherwise provide concise numbered steps,
safest first, and say what the user should see. Never request passwords, MFA codes, or secret keys.
You are only chatting: never claim you viewed, controlled, or changed the computer. Hands-on help requires the
separate Guide me or Fix it for me action and the user's explicit permission on that computer."""


class AskNotConnected(AppError):
    status = 503

    def __init__(self) -> None:
        super().__init__(SETUP_NOTICE, code="ask_ai_not_connected")


def bootstrap_env_openai(db: Session, org_id: uuid.UUID) -> bool:
    """Development-only convenience; never overrides an admin's provider record."""
    settings = get_settings()
    if settings.env == "production" or not settings.dev_bootstrap_openai_from_env or not settings.openai_api_key:
        return False
    first_org = db.execute(select(Organization.id).order_by(Organization.created_at, Organization.id).limit(1)).scalar()
    if first_org != org_id:
        return False
    existing = db.execute(
        select(ModelProviderConfig).where(
            ModelProviderConfig.org_id == org_id,
            ModelProviderConfig.provider == "openai",
        )
    ).scalar_one_or_none()
    if existing is not None:
        return False
    config = ModelProviderConfig(org_id=org_id, provider="openai", models={}, enabled=True, approved_for_tenant_data=True)
    db.add(config)
    db.flush()
    audit.record(
        db,
        org_id=org_id,
        actor_kind="system",
        actor_id=None,
        action="models.dev_bootstrap_from_env",
        resource_type="model_provider",
        resource_id=config.id,
        data={"provider": "openai", "source": "server environment (development)"},
    )
    return True


def _candidates(db: Session, org_id: uuid.UUID):
    bootstrap_env_openai(db, org_id)
    candidates = [item for item in available_providers(db, org_id, "chat") if item[0] in CHAT_PROVIDERS]
    candidates.sort(key=lambda item: 0 if item[0] == "openai" else 1)
    return candidates


def _authorize_usage(db: Session, org_id: uuid.UUID, run: AgentRun) -> None:
    """Apply the same tenant entitlement and per-run budget guard as structured AI calls."""
    from ..core.errors import EntitlementExceeded

    check_entitlement(db, org_id, "ai_usd", adding=0.0)
    budget = db.execute(select(AIBudget).where(AIBudget.org_id == org_id)).scalar_one_or_none()
    if budget and (run.cost_usd or 0) >= budget.per_run_usd_limit:
        raise EntitlementExceeded(
            f"Per-run AI budget (${budget.per_run_usd_limit:.2f}) exhausted",
            code="run_budget_exhausted",
        )


def status(db: Session, org_id: uuid.UUID) -> dict:
    candidates = _candidates(db, org_id)
    if not candidates:
        return {"connected": False, "provider": None, "model": None, "source": None, "notice": SETUP_NOTICE}
    provider, model, config, _ = candidates[0]
    source = "local gateway" if provider == "ollama" else "admin key" if config and config.credential_id else "server key"
    return {"connected": True, "provider": provider, "model": model, "source": source, "notice": None}


def chat_run(db: Session, org_id: uuid.UUID, ticket: Ticket, started_by) -> AgentRun:
    run = db.execute(
        select(AgentRun).where(AgentRun.ticket_id == ticket.id).order_by(AgentRun.created_at.desc())
    ).scalars().first()
    if run is not None and run.state not in TERMINAL_RUN_STATES:
        return run
    run = AgentRun(
        org_id=org_id,
        ticket_id=ticket.id,
        state=RunState.INTAKE,
        paused_for="chat",
        correlation_id=uuid.uuid4().hex,
        started_by=started_by,
        context={"mode": "chat", "evidence": [], "observations": [], "plan_hashes": [], "clarify_rounds": 0},
    )
    db.add(run)
    db.flush()
    return run


def _history(db: Session, run: AgentRun) -> list[dict]:
    rows = db.execute(
        select(AgentMessage)
        .where(AgentMessage.run_id == run.id, AgentMessage.role.in_(("user", "assistant")))
        .order_by(AgentMessage.created_at.desc())
        .limit(HISTORY_LIMIT)
    ).scalars().all()
    messages = [{"role": row.role, "content": row.content} for row in reversed(rows)]
    while messages and messages[0]["role"] != "user":
        messages.pop(0)
    return messages


def _record(db: Session, run: AgentRun, provider: str, model: str, config, result) -> None:
    cost = 0.0
    if config:
        cost = result.input_tokens / 1e6 * config.input_cost_per_mtok + result.output_tokens / 1e6 * config.output_cost_per_mtok
    call = _log_call(db, run.org_id, run.id, "chat", provider, model, result.input_tokens, result.output_tokens,
                     cost, result.latency_ms, True, None, True)
    meter(db, run.org_id, "ai_tokens", result.input_tokens + result.output_tokens, f"mc-tokens:{call.id}", {"provider": provider})
    if cost:
        meter(db, run.org_id, "ai_usd", cost, f"mc-usd:{call.id}", {"provider": provider})
    run.cost_usd = (run.cost_usd or 0) + cost


def _save_reply(db: Session, run: AgentRun, content: str, provider: str, model: str) -> dict:
    from .agent import add_message

    message = add_message(db, run, "assistant", content, data={"live_model": True, "provider": provider, "model": model, "mode": "chat"})
    return {"id": str(message.id), "content": content, "live_model": True, "provider": provider, "model": model}


def reply(db: Session, org_id: uuid.UUID, run: AgentRun) -> dict:
    _authorize_usage(db, org_id, run)
    candidates = _candidates(db, org_id)
    if not candidates:
        raise AskNotConnected()
    errors = []
    for provider, model, config, key in candidates:
        client = build(provider, key, timeout=config.timeout_seconds if config else 60)
        if client is None:
            continue
        try:
            result = client.complete(model=model, system=SYSTEM_PROMPT, messages=_history(db, run), max_tokens=1200)
        except ProviderError as exc:
            errors.append(f"{provider}: {str(exc)[:200]}")
            continue
        actual_model = result.model or model
        _record(db, run, provider, actual_model, config, result)
        return _save_reply(db, run, result.text.strip() or "The model returned an empty answer. Please try again.", provider, actual_model)
    raise AppError("The AI model did not answer: " + "; ".join(errors[-2:]), code="ask_ai_failed", status=502)


def stream_plan(db: Session, org_id: uuid.UUID, run: AgentRun) -> dict | None:
    _authorize_usage(db, org_id, run)
    candidates = _candidates(db, org_id)
    if not candidates:
        return None
    provider, model, config, key = candidates[0]
    return {"provider": provider, "model": model, "key": key, "timeout": config.timeout_seconds if config else 60,
            "messages": _history(db, run), "run_id": str(run.id), "org_id": str(org_id)}


def stream_tokens(plan: dict) -> Iterator[tuple[str, dict]]:
    """Stream OpenAI natively; other supported providers return one visible chunk."""
    if plan["provider"] != "openai":
        result = build(plan["provider"], plan["key"], timeout=plan["timeout"]).complete(
            model=plan["model"], system=SYSTEM_PROMPT, messages=plan["messages"], max_tokens=1200
        )
        yield "delta", {"text": result.text}
        yield "usage", {"in": result.input_tokens, "out": result.output_tokens, "ms": result.latency_ms, "model": result.model or plan["model"]}
        return
    body = {"model": plan["model"], "stream": True, "stream_options": {"include_usage": True}, "max_completion_tokens": 1200,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}, *plan["messages"]]}
    usage = {"in": 0, "out": 0, "model": plan["model"]}
    started = time.perf_counter()
    with httpx.stream("POST", f"{get_settings().openai_base_url.rstrip('/')}/chat/completions",
                      headers={"authorization": f"Bearer {plan['key']}"}, json=body, timeout=plan["timeout"]) as response:
        if response.status_code >= 400:
            response.read()
            raise ProviderError(f"openai HTTP {response.status_code}: {response.text[:200]}")
        for line in response.iter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            event = json.loads(data)
            if event.get("usage"):
                usage.update({"in": event["usage"].get("prompt_tokens", 0), "out": event["usage"].get("completion_tokens", 0)})
            usage["model"] = event.get("model") or usage["model"]
            for choice in event.get("choices") or []:
                piece = (choice.get("delta") or {}).get("content")
                if piece:
                    yield "delta", {"text": piece}
    usage["ms"] = int((time.perf_counter() - started) * 1000)
    yield "usage", usage


def finish_stream(db: Session, plan: dict, content: str, usage: dict) -> dict:
    run = db.get(AgentRun, uuid.UUID(plan["run_id"]))
    config = db.execute(
        select(ModelProviderConfig).where(
            ModelProviderConfig.org_id == run.org_id,
            ModelProviderConfig.provider == plan["provider"],
        )
    ).scalar_one_or_none()
    result = type("StreamResult", (), {"input_tokens": usage.get("in", 0), "output_tokens": usage.get("out", 0), "latency_ms": usage.get("ms", 0)})()
    model = usage.get("model") or plan["model"]
    _record(db, run, plan["provider"], model, config, result)
    return _save_reply(db, run, content.strip() or "The model returned an empty answer. Please try again.", plan["provider"], model)
