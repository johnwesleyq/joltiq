"""Model provider adapters (real HTTP APIs) with capability metadata.

Each adapter implements `complete()` returning text and, when a JSON schema is supplied, a validated object:
  - OpenAI: Chat Completions with response_format=json_schema (strict)
  - Anthropic: Messages API with a single forced tool whose input_schema is the requested schema
  - Gemini: generateContent with responseMimeType=application/json + responseSchema
The `local_fixture` provider is a deterministic rule engine for tests/demos without credentials. It is NOT an
AI model, is marked live=False on every record, and is refused in production.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...config import get_settings
from ...models import Credential, ModelProviderConfig
from ...security.crypto import decrypt_secret


@dataclass
class ModelResult:
    text: str
    data: Any = None
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    provider: str = ""
    live: bool = True
    latency_ms: int = 0
    raw: dict = field(default_factory=dict)


class ProviderError(Exception):
    def __init__(self, msg: str, retryable: bool = True):
        super().__init__(msg)
        self.retryable = retryable


CAPABILITIES: dict[str, dict[str, bool]] = {
    "openai": {"tools": True, "vision": True, "structured": True},
    "anthropic": {"tools": True, "vision": True, "structured": True},
    "gemini": {"tools": True, "vision": True, "structured": True},
    "ollama": {"tools": True, "vision": True, "structured": True},
    "local_fixture": {"tools": True, "vision": False, "structured": True},
}

DEFAULT_MODELS: dict[str, dict[str, str]] = {
    # Model ids are tenant-configurable in Admin → Models; these are starting defaults only.
    "openai": {"default": "gpt-4.1-mini", "planning": "gpt-4.1", "vision": "gpt-4.1"},
    "anthropic": {"default": "claude-sonnet-5-5", "planning": "claude-opus-5-5", "vision": "claude-sonnet-5-5", "computer_use": "claude-sonnet-5-5"},
    "gemini": {"default": "gemini-2.5-flash", "planning": "gemini-2.5-pro", "vision": "gemini-2.5-flash"},
    "ollama": {"default": "gemma3:4b", "vision": "gemma3:4b", "computer_use": "gemma3:4b"},
    "local_fixture": {"default": "local-rules-v1"},
}


def provider_credentials(db: Session, org_id: uuid.UUID | None, provider: str, *, require_tenant_data_approval: bool = True) -> str | None:
    """Tenant credential (encrypted) wins; platform env key is used only if the tenant enabled+approved the provider."""
    if org_id is None:
        return None
    cfg = db.execute(select(ModelProviderConfig).where(ModelProviderConfig.provider == provider)).scalar_one_or_none()
    if cfg is None or not cfg.enabled:
        return None
    if require_tenant_data_approval and not cfg.approved_for_tenant_data:
        return None
    if cfg.credential_id:
        cred = db.get(Credential, cfg.credential_id)
        if cred and not cred.revoked_at:
            return decrypt_secret(cred.ciphertext)
    s = get_settings()
    return {"openai": s.openai_api_key, "anthropic": s.anthropic_api_key, "gemini": s.gemini_api_key}.get(provider)


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        t = t.rsplit("```", 1)[0]
    return t.strip()


class OpenAIProvider:
    name = "openai"
    live = True

    def __init__(self, key: str, base_url: str = "https://api.openai.com/v1", timeout: int = 60):
        self.key, self.base_url, self.timeout = key, base_url, timeout

    def complete(self, *, model: str, system: str, messages: list[dict], schema: dict | None = None,
                 schema_name: str = "result", max_tokens: int = 2000, images: list[str] | None = None) -> ModelResult:
        msgs = [{"role": "system", "content": system}] + [{"role": m["role"], "content": m["content"]} for m in messages]
        if images:
            msgs[-1] = {"role": "user", "content": [{"type": "text", "text": messages[-1]["content"]}] +
                        [{"type": "image_url", "image_url": {"url": u}} for u in images]}
        body: dict[str, Any] = {"model": model, "messages": msgs, "max_completion_tokens": max_tokens}
        if schema:
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": schema_name, "schema": schema, "strict": False}}
        t0 = time.perf_counter()
        try:
            r = httpx.post(f"{self.base_url}/chat/completions", headers={"authorization": f"Bearer {self.key}"}, json=body, timeout=self.timeout)
        except httpx.HTTPError as e:
            raise ProviderError(f"openai transport error: {type(e).__name__}") from e
        if r.status_code >= 400:
            raise ProviderError(f"openai HTTP {r.status_code}: {r.text[:300]}", retryable=r.status_code in (408, 429, 500, 502, 503, 504))
        j = r.json()
        text = j["choices"][0]["message"].get("content") or ""
        data = json.loads(_strip_fences(text)) if schema else None
        usage = j.get("usage", {})
        return ModelResult(text, data, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), model, self.name, True,
                           int((time.perf_counter() - t0) * 1000))


class AnthropicProvider:
    name = "anthropic"
    live = True

    def __init__(self, key: str, base_url: str = "https://api.anthropic.com/v1", timeout: int = 60):
        self.key, self.base_url, self.timeout = key, base_url, timeout

    def complete(self, *, model: str, system: str, messages: list[dict], schema: dict | None = None,
                 schema_name: str = "result", max_tokens: int = 2000, images: list[str] | None = None) -> ModelResult:
        msgs = [{"role": m["role"] if m["role"] in ("user", "assistant") else "user", "content": m["content"]} for m in messages]
        if images:
            blocks: list[dict] = [{"type": "text", "text": messages[-1]["content"]}]
            for u in images:
                if u.startswith("data:"):
                    mt, b64 = u[5:].split(";base64,", 1)
                    blocks.append({"type": "image", "source": {"type": "base64", "media_type": mt, "data": b64}})
            msgs[-1] = {"role": "user", "content": blocks}
        body: dict[str, Any] = {"model": model, "system": system, "messages": msgs, "max_tokens": max_tokens}
        if schema:
            body["tools"] = [{"name": schema_name, "description": "Return the structured result.", "input_schema": schema}]
            body["tool_choice"] = {"type": "tool", "name": schema_name}
        t0 = time.perf_counter()
        try:
            r = httpx.post(f"{self.base_url}/messages", headers={"x-api-key": self.key, "anthropic-version": "2023-06-01"},
                           json=body, timeout=self.timeout)
        except httpx.HTTPError as e:
            raise ProviderError(f"anthropic transport error: {type(e).__name__}") from e
        if r.status_code >= 400:
            raise ProviderError(f"anthropic HTTP {r.status_code}: {r.text[:300]}", retryable=r.status_code in (408, 429, 500, 502, 503, 504, 529))
        j = r.json()
        text, data = "", None
        for block in j.get("content", []):
            if block.get("type") == "text":
                text += block["text"]
            elif block.get("type") == "tool_use":
                data = block.get("input")
        usage = j.get("usage", {})
        return ModelResult(text or json.dumps(data or {}), data, usage.get("input_tokens", 0), usage.get("output_tokens", 0), model, self.name, True,
                           int((time.perf_counter() - t0) * 1000))


def _gemini_schema(schema: dict) -> dict:
    """Gemini's responseSchema is an OpenAPI subset: drop unsupported keywords."""
    drop = {"additionalProperties", "$schema", "title", "default", "minLength", "maxLength", "pattern", "minItems", "maxItems"}
    if isinstance(schema, dict):
        return {k: _gemini_schema(v) for k, v in schema.items() if k not in drop}
    if isinstance(schema, list):
        return [_gemini_schema(x) for x in schema]
    return schema


class GeminiProvider:
    name = "gemini"
    live = True

    def __init__(self, key: str, base_url: str = "https://generativelanguage.googleapis.com/v1beta", timeout: int = 60):
        self.key, self.base_url, self.timeout = key, base_url, timeout

    def complete(self, *, model: str, system: str, messages: list[dict], schema: dict | None = None,
                 schema_name: str = "result", max_tokens: int = 2000, images: list[str] | None = None) -> ModelResult:
        contents: list[dict[str, Any]] = [{"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
                                          for m in messages]
        if images:
            for u in images:
                if u.startswith("data:"):
                    mt, b64 = u[5:].split(";base64,", 1)
                    contents[-1]["parts"].append({"inline_data": {"mime_type": mt, "data": b64}})
        gen: dict[str, Any] = {"maxOutputTokens": max_tokens}
        if schema:
            gen["responseMimeType"] = "application/json"
            gen["responseSchema"] = _gemini_schema(schema)
        body = {"contents": contents, "systemInstruction": {"parts": [{"text": system}]}, "generationConfig": gen}
        t0 = time.perf_counter()
        try:
            r = httpx.post(f"{self.base_url}/models/{model}:generateContent", headers={"x-goog-api-key": self.key}, json=body, timeout=self.timeout)
        except httpx.HTTPError as e:
            raise ProviderError(f"gemini transport error: {type(e).__name__}") from e
        if r.status_code >= 400:
            raise ProviderError(f"gemini HTTP {r.status_code}: {r.text[:300]}", retryable=r.status_code in (408, 429, 500, 502, 503, 504))
        j = r.json()
        parts = (j.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts)
        data = json.loads(_strip_fences(text)) if schema and text else None
        usage = j.get("usageMetadata", {})
        return ModelResult(text, data, usage.get("promptTokenCount", 0), usage.get("candidatesTokenCount", 0), model, self.name, True,
                           int((time.perf_counter() - t0) * 1000))


def build(provider: str, key: str | None, timeout: int = 60):
    if provider == "local_fixture":
        from .fixture import LocalFixtureProvider

        return LocalFixtureProvider()
    if not key:
        return None
    return {"openai": OpenAIProvider, "anthropic": AnthropicProvider, "gemini": GeminiProvider}[provider](key, timeout=timeout)


def test_connectivity(provider: str, key: str, model: str) -> tuple[bool, str]:
    p = build(provider, key, timeout=20)
    if p is None:
        return False, "no key"
    try:
        res = p.complete(model=model, system="Reply with the single word OK.", messages=[{"role": "user", "content": "ping"}], max_tokens=10)
        return True, f"ok ({res.latency_ms} ms, model {res.model})"
    except ProviderError as e:
        return False, str(e)[:300]
