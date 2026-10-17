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
from ..billing import check_entitlement, meter
from .providers import DEFAULT_MODELS, ProviderError, build, provider_credentials
from .schemas import json_schema

log = logging.getLogger("joltiq.ai")
T = TypeVar("T", bound=BaseModel)

TASKS = ["intent", "clarify", "retrieval", "diagnosis", "research", "planning", "vision", "tool_selection",
         "verification_analysis", "article_drafting", "summary", "chat"]
DEFAULT_ORDER = ["anthropic", "openai", "gemini"]
TASK_TIER = {"planning": "planning", "diagnosis": "planning", "vision": "vision"}


def available_providers(db: Session, org_id: uuid.UUID, task: str) -> list[tuple[str, str, ModelProviderConfig | None, str | None]]:
    """Returns [(provider, model, config, key)] in routing order. Only providers the tenant enabled AND approved for
    tenant data are eligible. The local fixture is eligible only outside production and only if no live provider is."""
    budget = db.execute(select(AIBudget)).scalar_one_or_none()
    order = ((budget.routing or {}).get(task) if budget else None) or DEFAULT_ORDER
    out = []
    cfgs = {c.provider: c for c in db.execute(select(ModelProviderConfig)).scalars()}
    for prov in order:
        if prov == "local_fixture":
            continue
        cfg = cfgs.get(prov)
        key = provider_credentials(db, org_id, prov, require_tenant_data_approval=True)
        if cfg is None or not key:
            continue
        model = (cfg.models or {}).get(task) or (cfg.models or {}).get(TASK_TIER.get(task, "default")) or \
            DEFAULT_MODELS[prov].get(TASK_TIER.get(task, "default"), DEFAULT_MODELS[prov]["default"])
        out.append((prov, model, cfg, key))
    s = get_settings()
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
    for prov, model, cfg, key in candidates:
        client = build(prov, key, timeout=(cfg.timeout_seconds if cfg else 60))
        if client is None:
            continue
        attempts = 1 + (cfg.max_retries if cfg else 0)
        for attempt in range(attempts):
            t0 = time.perf_counter()
            try:
                res = client.complete(model=model, system=system, messages=messages, schema=schema, schema_name=output.__name__,
                                      max_tokens=max_tokens, images=images)
                obj = output.model_validate(res.data if res.data is not None else {})
            except ProviderError as e:
                errors.append(f"{prov}: {e}")
                _log_call(db, org_id, run_id, task, prov, model, 0, 0, 0.0, int((time.perf_counter() - t0) * 1000), False, str(e), prov != "local_fixture")
                if not e.retryable:
                    break
                time.sleep(min(4.0, 0.5 * 2**attempt))
                continue
            except (ValidationError, ValueError) as e:
                errors.append(f"{prov}: invalid structured output")
                _log_call(db, org_id, run_id, task, prov, model, 0, 0, 0.0, int((time.perf_counter() - t0) * 1000), False,
                          f"schema validation failed: {str(e)[:300]}", prov != "local_fixture")
                break  # don't retry the same provider on schema failure; fall back
            cost = 0.0
            if cfg:
                cost = res.input_tokens / 1e6 * cfg.input_cost_per_mtok + res.output_tokens / 1e6 * cfg.output_cost_per_mtok
            mc = _log_call(db, org_id, run_id, task, prov, res.model or model, res.input_tokens, res.output_tokens, cost,
                           res.latency_ms, True, None, res.live)
            meter(db, org_id, "ai_tokens", res.input_tokens + res.output_tokens, f"mc-tokens:{mc.id}", {"provider": prov})
            if cost:
                meter(db, org_id, "ai_usd", cost, f"mc-usd:{mc.id}", {"provider": prov})
            if run_id:
                run = db.get(AgentRun, run_id)
                if run:
                    run.cost_usd = (run.cost_usd or 0) + cost
            return obj, mc
    raise Unavailable("All configured AI providers failed: " + "; ".join(errors[-4:]), code="ai_failed")


def _log_call(db, org_id, run_id, task, prov, model, tin, tout, cost, ms, ok, err, live) -> ModelCall:
    mc = ModelCall(org_id=org_id, run_id=run_id, task=task, provider=prov, model=model, input_tokens=tin, output_tokens=tout,
                   cost_usd=cost, latency_ms=ms, ok=ok, error=err, live=live)
    db.add(mc)
    db.flush()
    return mc
