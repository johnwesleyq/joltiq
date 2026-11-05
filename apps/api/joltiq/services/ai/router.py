"""Task-aware model routing with tenant-approved providers only, ordered fallbacks, retries, timeouts, budgets,
cost accounting and schema validation."""
from __future__ import annotations

import logging
import time
import uuid
from typing import TypeVar

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...config import get_settings
from ...core.errors import EntitlementExceeded, Unavailable
from ...models import AgentRun, AIBudget, ModelCall, ModelProviderConfig
from ..billing import check_entitlement, meter, usage_total
from .providers import DEFAULT_MODELS, ProviderError, build, provider_credentials
from .schemas import json_schema

log = logging.getLogger("joltiq.ai")
T = TypeVar("T", bound=BaseModel)

TASKS = ["intent", "clarify", "retrieval", "diagnosis", "candidate_selection", "research", "planning", "vision", "tool_selection",
         "verification_analysis", "article_drafting", "summary", "chat", "guidance", "guidance_feedback", "computer_use"]
DEFAULT_ORDER = ["ollama", "anthropic", "openai", "gemini"]
TASK_TIER = {"planning": "planning", "diagnosis": "planning", "guidance": "planning", "vision": "vision", "computer_use": "computer_use"}
REASONING_ESCALATION_TASKS = {
    "diagnosis", "candidate_selection", "research", "planning", "tool_selection",
    "verification_analysis", "article_drafting", "guidance", "guidance_feedback",
}

# OpenAI standard token prices in USD per million tokens, verified against the
# official pricing page on 2026-11-03. Recognized deployment-owned models must
# not use tenant-editable rates, which could otherwise undercount spend.
OPENAI_STANDARD_PRICES: dict[str, tuple[float, float]] = {
    "gpt-6-luna": (0.10, 0.50),
    "gpt-6.1-terra": (1.00, 5.00),
    "gpt-6.1-sol": (2.00, 10.00),
}


def model_rates(provider: str, model: str, cfg: ModelProviderConfig | None) -> tuple[float, float]:
    """Resolve deployment-owned rates first, then configured rates for custom/other models."""
    if provider == "openai" and model in OPENAI_STANDARD_PRICES:
        return OPENAI_STANDARD_PRICES[model]
    if cfg:
        return float(cfg.input_cost_per_mtok or 0), float(cfg.output_cost_per_mtok or 0)
    return 0.0, 0.0


def calculate_cost(provider: str, model: str, input_tokens: int, output_tokens: int,
                   cfg: ModelProviderConfig | None) -> float:
    input_rate, output_rate = model_rates(provider, model, cfg)
    return input_tokens / 1e6 * input_rate + output_tokens / 1e6 * output_rate


def estimate_input_tokens(system: str, messages: list[dict], images: list[str] | None = None) -> int:
    """A deliberately conservative upper estimate used only for fail-closed budget admission."""
    byte_count = len((system or "").encode("utf-8"))
    byte_count += sum(len(str(message.get("content", "")).encode("utf-8")) for message in messages)
    for image in images or []:
        # A remote image still incurs visual tokens; a data URL is roughly four
        # base64 bytes per three source bytes. Both branches intentionally pad.
        byte_count += max(2_000, len(image) // 2)
    return max(1, byte_count)


def preflight_budget(db: Session, org_id: uuid.UUID, *, provider: str, model: str,
                     cfg: ModelProviderConfig | None, system: str, messages: list[dict], max_tokens: int,
                     run_id: uuid.UUID | None = None, spent_usd: float | None = None,
                     images: list[str] | None = None) -> float:
    """Refuse a call before dispatch when its conservative maximum cannot fit a budget."""
    estimated_cost = calculate_cost(
        provider,
        model,
        estimate_input_tokens(system, messages, images),
        max_tokens,
        cfg,
    )
    budget = db.execute(select(AIBudget)).scalar_one_or_none()
    if budget:
        current_run_cost = spent_usd if spent_usd is not None else (run_cost(db, run_id) if run_id else None)
        if current_run_cost is not None and current_run_cost + estimated_cost > budget.per_run_usd_limit:
            raise EntitlementExceeded(
                f"This AI call cannot fit the remaining per-run budget (${budget.per_run_usd_limit:.2f} limit)",
                code="run_budget_exhausted",
                details={"estimated_max_usd": round(estimated_cost, 6)},
            )
        month_spend = usage_total(db, org_id, "ai_usd")
        if month_spend + estimated_cost > budget.monthly_usd_limit:
            raise EntitlementExceeded(
                f"This AI call cannot fit the remaining tenant monthly budget (${budget.monthly_usd_limit:.2f} limit)",
                code="tenant_ai_budget_exhausted",
                details={"estimated_max_usd": round(estimated_cost, 6)},
            )
    check_entitlement(db, org_id, "ai_usd", adding=estimated_cost)
    return estimated_cost


def _model_attempts(provider: str, model: str, cfg: ModelProviderConfig | None, task: str) -> list[str]:
    """Return the base model and, for failed Luna reasoning only, the Sol escalation."""
    if provider != "openai" or task not in REASONING_ESCALATION_TASKS or model != "gpt-6-luna":
        return [model]
    escalation = ((cfg.models or {}).get("escalation") if cfg else None) or DEFAULT_MODELS["openai"]["escalation"]
    return [model] if escalation == model else [model, escalation]


def available_providers(db: Session, org_id: uuid.UUID, task: str) -> list[tuple[str, str, ModelProviderConfig | None, str | None]]:
    """Returns [(provider, model, config, key)] in routing order. Only providers the tenant enabled AND approved for
    tenant data are eligible. The local fixture is eligible only outside production and only if no live provider is."""
    budget = db.execute(select(AIBudget)).scalar_one_or_none()
    order = ((budget.routing or {}).get(task) if budget else None) or DEFAULT_ORDER
    out: list[tuple[str, str, ModelProviderConfig | None, str | None]] = []
    cfgs = {c.provider: c for c in db.execute(select(ModelProviderConfig)).scalars()}
    for prov in order:
        if prov == "local_fixture":
            continue
        cfg = cfgs.get(prov)
        if prov == "ollama":
            s = get_settings()
            if (cfg is None or not cfg.enabled or not cfg.approved_for_tenant_data or not s.ollama_base_url
                    or (task == "computer_use" and not cfg.approved_for_computer_use)):
                continue
            model = (cfg.models or {}).get(task) or (cfg.models or {}).get(TASK_TIER.get(task, "default")) or \
                DEFAULT_MODELS[prov].get(TASK_TIER.get(task, "default"), s.ollama_model)
            out.append((prov, model, cfg, None))
            continue
        key = provider_credentials(db, org_id, prov, require_tenant_data_approval=True)
        if cfg is None or not key:
            continue
        model = (cfg.models or {}).get(task) or (cfg.models or {}).get(TASK_TIER.get(task, "default")) or \
            DEFAULT_MODELS[prov].get(TASK_TIER.get(task, "default"), DEFAULT_MODELS[prov]["default"])
        if task == "computer_use":
            if not cfg.approved_for_computer_use:
                continue
            s = get_settings()
            # Managed production computer use currently has one reviewed cloud boundary: official OpenAI.
            # Unknown models are excluded because a zero/tenant-supplied price would defeat budget admission.
            if prov != "openai" or model not in s.computer_use_cloud_models or model not in OPENAI_STANDARD_PRICES:
                continue
        out.append((prov, model, cfg, key))
    s = get_settings()
    if task == "computer_use" and not s.allow_external_computer_use_models:
        out = [candidate for candidate in out if candidate[0] == "ollama"]
    fixture_cfg = cfgs.get("local_fixture")
    if s.enable_local_fixture_providers and s.env != "production" and (not out or (fixture_cfg and fixture_cfg.enabled)):
        out.append(("local_fixture", "local-rules-v1", fixture_cfg, None))
    return out


def run_cost(db: Session, run_id: uuid.UUID | None) -> float:
    if not run_id:
        return 0.0
    return float(db.execute(select(func.coalesce(func.sum(ModelCall.cost_usd), 0)).where(ModelCall.run_id == run_id)).scalar() or 0)


def call(db: Session, org_id: uuid.UUID, task: str, *, system: str, messages: list[dict], output: type[T], run_id: uuid.UUID | None = None,
         max_tokens: int = 2000, images: list[str] | None = None) -> tuple[T, ModelCall]:
    if task not in TASKS:
        raise ValueError(f"unknown task {task}")
    budget = db.execute(select(AIBudget)).scalar_one_or_none()
    if budget and run_id and run_cost(db, run_id) >= budget.per_run_usd_limit:
        raise EntitlementExceeded(f"Per-run AI budget (${budget.per_run_usd_limit:.2f}) exhausted", code="run_budget_exhausted")
    check_entitlement(db, org_id, "ai_usd", adding=0.0)
    candidates = available_providers(db, org_id, task)
    if images:
        candidates = [c for c in candidates if c[0] != "local_fixture"] or candidates
    if not candidates:
        raise Unavailable("No AI provider is configured and approved for this organization. An admin can add one under Admin → Models.",
                          code="ai_not_configured")
    schema = json_schema(output)
    errors: list[str] = []
    for prov, base_model, cfg, key in candidates:
        client = build(prov, key, timeout=(cfg.timeout_seconds if cfg else 60))
        if client is None:
            continue
        attempts = 1 + (cfg.max_retries if cfg else 0)
        stop_provider = False
        for model in _model_attempts(prov, base_model, cfg, task):
            for attempt in range(attempts):
                preflight_budget(
                    db, org_id, provider=prov, model=model, cfg=cfg, system=system, messages=messages,
                    max_tokens=max_tokens, run_id=run_id, images=images,
                )
                t0 = time.perf_counter()
                try:
                    res = client.complete(model=model, system=system, messages=messages, schema=schema, schema_name=output.__name__,
                                          max_tokens=max_tokens, images=images)
                    obj = output.model_validate(res.data if res.data is not None else {})
                except ProviderError as e:
                    errors.append(f"{prov}/{model}: {e}")
                    _log_call(db, org_id, run_id, task, prov, model, 0, 0, 0.0, int((time.perf_counter() - t0) * 1000), False, str(e), prov != "local_fixture")
                    if not e.retryable:
                        stop_provider = True
                        break
                    if attempt + 1 < attempts:
                        time.sleep(min(4.0, 0.5 * 2**attempt))
                    continue
                except (ValidationError, ValueError) as e:
                    errors.append(f"{prov}/{model}: invalid structured output")
                    _log_call(db, org_id, run_id, task, prov, model, 0, 0, 0.0, int((time.perf_counter() - t0) * 1000), False,
                              f"schema validation failed: {str(e)[:300]}", prov != "local_fixture")
                    break  # try the escalation model or next provider
                actual_model = res.model or model
                cost = calculate_cost(prov, actual_model, res.input_tokens, res.output_tokens, cfg)
                mc = _log_call(db, org_id, run_id, task, prov, actual_model, res.input_tokens, res.output_tokens, cost,
                               res.latency_ms, True, None, res.live)
                meter(db, org_id, "ai_tokens", res.input_tokens + res.output_tokens, f"mc-tokens:{mc.id}", {"provider": prov})
                if cost:
                    meter(db, org_id, "ai_usd", cost, f"mc-usd:{mc.id}", {"provider": prov, "model": actual_model})
                if run_id:
                    run = db.get(AgentRun, run_id)
                    if run:
                        run.cost_usd = (run.cost_usd or 0) + cost
                return obj, mc
            if stop_provider:
                break
    raise Unavailable("All configured AI providers failed: " + "; ".join(errors[-4:]), code="ai_failed")


def _log_call(db, org_id, run_id, task, prov, model, tin, tout, cost, ms, ok, err, live) -> ModelCall:
    mc = ModelCall(org_id=org_id, run_id=run_id, task=task, provider=prov, model=model, input_tokens=tin, output_tokens=tout,
                   cost_usd=cost, latency_ms=ms, ok=ok, error=err, live=live)
    db.add(mc)
    db.flush()
    return mc


def computer_turn(db: Session, org_id: uuid.UUID, *, system: str, messages: list[dict], display: dict[str, int], spent_usd: float = 0.0,
                  toolset: str = "desktop", provider_name: str | None = None, model_name: str | None = None):
    """One computer-use model turn through the first approved provider that supports computer use.
    Returns (Turn, cost_usd). Same tenant-approval, budget, fallback and cost rules as `call()`."""
    from . import computer

    budget = db.execute(select(AIBudget)).scalar_one_or_none()
    if budget and spent_usd >= budget.per_run_usd_limit:
        raise EntitlementExceeded(f"Per-run AI budget (${budget.per_run_usd_limit:.2f}) exhausted", code="run_budget_exhausted")
    check_entitlement(db, org_id, "ai_usd", adding=0.0)
    candidates = [c for c in available_providers(db, org_id, "computer_use") if c[0] in computer.SUPPORTED_PROVIDERS]
    if provider_name or model_name:
        candidates = [c for c in candidates if (provider_name is None or c[0] == provider_name)
                      and (model_name is None or c[1] == model_name)]
    if not candidates:
        raise Unavailable("Computer use needs a provider explicitly approved for computer screens and enabled by the deployment.",
                          code="computer_use_not_configured")
    errors: list[str] = []
    for prov, model, cfg, key in candidates:
        client = computer.build(prov, key, timeout=max(60, cfg.timeout_seconds if cfg else 120), toolset=toolset)
        if client is None:
            continue
        for attempt in range(1 + (cfg.max_retries if cfg else 0)):
            preflight_budget(
                db, org_id, provider=prov, model=model, cfg=cfg, system=system, messages=messages,
                max_tokens=2048, spent_usd=spent_usd,
            )
            t0 = time.perf_counter()
            try:
                t = (client.turn(model=model, system=system, messages=messages, display=display, toolset=toolset) if toolset != "desktop"
                     else client.turn(model=model, system=system, messages=messages, display=display))
            except ProviderError as e:
                errors.append(f"{prov}: {e}")
                _log_call(db, org_id, None, "computer_use", prov, model, 0, 0, 0.0, int((time.perf_counter() - t0) * 1000), False, str(e), client.live)
                if not e.retryable:
                    break
                time.sleep(min(4.0, 0.5 * 2**attempt))
                continue
            actual_model = t.model or model
            cost = calculate_cost(prov, actual_model, t.input_tokens, t.output_tokens, cfg)
            mc = _log_call(db, org_id, None, "computer_use", prov, actual_model, t.input_tokens, t.output_tokens, cost, t.latency_ms, True, None, t.live)
            meter(db, org_id, "ai_tokens", t.input_tokens + t.output_tokens, f"mc-tokens:{mc.id}", {"provider": prov})
            if cost:
                meter(db, org_id, "ai_usd", cost, f"mc-usd:{mc.id}", {"provider": prov, "model": actual_model})
            return t, cost
        # A computer-use run pins its data destination before local consent. Never fail over to another provider.
        if provider_name or model_name:
            break
    raise Unavailable("All computer-use providers failed: " + "; ".join(errors[-4:]), code="ai_failed")


def ai_status(db: Session, org_id: uuid.UUID) -> dict:
    """Plain-English answer to "is a real AI model connected?" for Ask UIs. Never includes keys."""
    s = get_settings()
    chat = [c[0] for c in available_providers(db, org_id, "chat") if c[0] != "local_fixture"]
    from .computer import SUPPORTED_PROVIDERS

    cu_all = [c[0] for c in available_providers(db, org_id, "computer_use") if c[0] in SUPPORTED_PROVIDERS]
    cu_live = [p for p in cu_all if p != "local_fixture"]
    notice = None
    if not chat:
        notice = ("No AI model is connected, so JoltIQ is answering with its built-in offline rules and can't really chat. "
                  "An admin needs to open Admin → Models, add an OpenAI (or other) API key, enable it and tick "
                  "'approved for tenant data'. A server key in JOLTIQ_OPENAI_API_KEY is used only after OpenAI is "
                  "enabled and approved there.")
    cu_notice = None
    if not cu_live:
        cu_notice = ("Fix it for me needs a computer-use provider approved for computer screens: JoltIQ-managed OpenAI "
                     "with cloud computer use enabled, or an approved local Ollama gateway."
                     + (" Right now only the offline test model is available." if cu_all else ""))
    return {"live_chat": bool(chat), "chat_providers": chat, "live_computer_use": bool(cu_live),
            "computer_use_providers": cu_live, "notice": notice, "computer_use_notice": cu_notice,
            "offline_rules_allowed": bool(s.enable_local_fixture_providers and s.env != "production")}
