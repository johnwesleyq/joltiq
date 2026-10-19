"""Computer-use model adapters: one turn of "here is the screen → what do you do next".

Anthropic: Messages API with the computer toolset (`computer_toolset_20260801`, no beta header) for current models,
or the legacy single `computer` tool (`computer_20251124` + beta header) for older models. Both are normalised to
the toolset shape: each action is a member name (left_click, type, key, …) with that member's own input.

The local fixture is a scripted, clearly labelled stand-in for tests/demos (live=False). It is not an AI model.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from .providers import ProviderError

TOOLSET = "computer_toolset_20260801"
LEGACY_TOOL = "computer_20251124"
LEGACY_BETA = "computer-use-2025-11-24"

# Control tools JoltIQ adds next to the computer tools. They never touch the device directly.
CONTROL_TOOLS: list[dict[str, Any]] = [
    {"name": "open_url", "description": "Open an http(s) URL in the user's default web browser, where they are already signed in. "
                                        "Prefer this over typing into the address bar.",
     "input_schema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}},
    {"name": "ask_customer", "description": "Ask the person at the computer a question and wait for their answer (e.g. which account, which printer).",
     "input_schema": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]}},
    {"name": "request_approval", "description": "REQUIRED before any change that is hard to undo: saving/submitting settings, deleting, "
                                                "uninstalling or installing software, changing DNS/billing/security settings, sending messages, "
                                                "purchasing. Describe exactly what you are about to do. Wait for the answer.",
     "input_schema": {"type": "object", "properties": {"action_description": {"type": "string"}}, "required": ["action_description"]}},
    {"name": "request_secure_input", "description": "Hand the keyboard to the person so THEY type a password, MFA code, card number or other "
                                                    "secret. You must never type secrets yourself. Say exactly what they should enter and where.",
     "input_schema": {"type": "object", "properties": {"instructions": {"type": "string"}}, "required": ["instructions"]}},
    {"name": "finish", "description": "End the task. success=true only if you have SEEN on screen that the goal is achieved.",
     "input_schema": {"type": "object", "properties": {"success": {"type": "boolean"}, "summary": {"type": "string"}},
                      "required": ["success", "summary"]}},
]
CONTROL_NAMES = {t["name"] for t in CONTROL_TOOLS}


@dataclass
class ToolUse:
    id: str
    name: str
    input: dict[str, Any]
    computer: bool  # member of the computer toolset (vs a JoltIQ control tool)


@dataclass
class Turn:
    text: str
    tool_uses: list[ToolUse]
    assistant_content: list[dict[str, Any]]  # raw blocks to append to the transcript verbatim
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    provider: str = ""
    live: bool = True
    latency_ms: int = 0
    meta: dict = field(default_factory=dict)


def uses_legacy_tool(model: str) -> bool:
    """Claude 5.5+ (and the toolset compatibility list) take the toolset; older 4.x ids take the legacy tool."""
    m = model.lower()
    return any(t in m for t in ("-4-", "-4.", "claude-3"))


class AnthropicComputer:
    name = "anthropic"
    live = True

    def __init__(self, key: str, base_url: str = "https://api.anthropic.com/v1", timeout: int = 120):
        self.key, self.base_url, self.timeout = key, base_url, timeout

    def turn(self, *, model: str, system: str, messages: list[dict], display: dict[str, int], max_tokens: int = 2048) -> Turn:
        legacy = uses_legacy_tool(model)
        headers = {"x-api-key": self.key, "anthropic-version": "2023-06-01"}
        if legacy:
            computer = {"type": LEGACY_TOOL, "name": "computer", "display_width_px": int(display["width"]),
                        "display_height_px": int(display["height"])}
            headers["anthropic-beta"] = LEGACY_BETA
            msgs = [_to_legacy(m) for m in messages]
        else:
            computer = {"type": TOOLSET, "configs": {"zoom": {"enabled": False}}}
            msgs = messages
        body = {"model": model, "system": system, "max_tokens": max_tokens, "messages": msgs, "tools": [computer, *CONTROL_TOOLS]}
        t0 = time.perf_counter()
        try:
            r = httpx.post(f"{self.base_url}/messages", headers=headers, json=body, timeout=self.timeout)
        except httpx.HTTPError as e:
            raise ProviderError(f"anthropic transport error: {type(e).__name__}") from e
        if r.status_code >= 400:
            raise ProviderError(f"anthropic HTTP {r.status_code}: {r.text[:300]}", retryable=r.status_code in (408, 429, 500, 502, 503, 504, 529))
        j = r.json()
        text, uses, content = "", [], []
        for b in j.get("content", []):
            if b.get("type") == "text":
                text += b.get("text", "")
                content.append({"type": "text", "text": b.get("text", "")})
            elif b.get("type") == "tool_use":
                name, inp = b.get("name", ""), dict(b.get("input") or {})
                is_computer = b.get("toolset_name") == "computer" or name == "computer"
                if name == "computer":  # legacy shape: {"action": "...", ...}
                    name = inp.pop("action", "screenshot")
                uses.append(ToolUse(b["id"], name, inp, is_computer))
                # Always store the toolset shape; legacy conversion happens on send.
                blk = {"type": "tool_use", "id": b["id"], "name": name, "input": inp}
                if is_computer:
                    blk["toolset_name"] = "computer"
                content.append(blk)
        u = j.get("usage", {})
        return Turn(text, uses, content, u.get("input_tokens", 0), u.get("output_tokens", 0), model, self.name, True,
                    int((time.perf_counter() - t0) * 1000), {"stop_reason": j.get("stop_reason")})


def _to_legacy(m: dict) -> dict:
    """Toolset-shaped transcript → legacy single-tool shape."""
    if not isinstance(m.get("content"), list):
        return m
    out = []
    for b in m["content"]:
        if b.get("type") == "tool_use" and b.get("toolset_name") == "computer":
            out.append({"type": "tool_use", "id": b["id"], "name": "computer", "input": {"action": b["name"], **b.get("input", {})}})
        elif b.get("type") == "tool_result" and "toolset_name" in b:
            out.append({k: v for k, v in b.items() if k != "toolset_name"})
        else:
            out.append(b)
    return {"role": m["role"], "content": out}


class FixtureComputer:
    """Scripted stand-in (LOCAL FIXTURE, live=False): click, type, ask approval, save, finish. Exercises every
    control path of the run engine without a model. Refused in production by the router."""

    name = "local_fixture"
    live = False

    def turn(self, *, model: str, system: str, messages: list[dict], display: dict[str, int], max_tokens: int = 2048) -> Turn:
        n = sum(1 for m in messages if m["role"] == "assistant")
        last = messages[-1]["content"] if messages else []
        denied = any(b.get("type") == "tool_result" and "denied" in str(b.get("content", "")).lower() for b in (last if isinstance(last, list) else []))
        cx, cy = display["width"] // 2, display["height"] // 2
        script: list[tuple[str, str, dict, bool]] = [
            ("I can see the screen. Clicking into the window first.", "left_click", {"coordinate": [cx, cy]}, True),
            ("Typing the note.", "type", {"text": "JoltIQ test note"}, True),
            ("Saving is a change, so I'll ask first.", "request_approval", {"action_description": "Save the file with ctrl+s"}, False),
            ("Saving.", "key", {"text": "ctrl+s"}, True),
            ("Done.", "finish", {"success": True, "summary": "Typed the note and saved it (fixture script)."}, False),
        ]
        if denied:
            script[3] = ("The customer declined; stopping without saving.", "finish", {"success": False, "summary": "Customer declined the save."}, False)
        say, name, inp, comp = script[min(n, len(script) - 1)]
        tid = f"toolu_fixture_{n}"
        blk = {"type": "tool_use", "id": tid, "name": name, "input": inp, **({"toolset_name": "computer"} if comp else {})}
        return Turn(say, [ToolUse(tid, name, inp, comp)], [{"type": "text", "text": say}, blk], 0, 0, "local-computer-script-v1",
                    self.name, False, 1)


def build(provider: str, key: str | None, timeout: int = 120):
    if provider == "local_fixture":
        return FixtureComputer()
    if provider == "anthropic" and key:
        return AnthropicComputer(key, timeout=timeout)
    return None


SUPPORTED_PROVIDERS = ("anthropic", "local_fixture")
