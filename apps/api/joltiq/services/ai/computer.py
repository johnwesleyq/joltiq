"""Computer-use model adapters: one turn of "here is the screen → what do you do next".

OpenAI: Responses API with one strict function per JoltIQ-normalized computer/control action. Each call is stateless,
sends only the compact action history and latest screenshot, and requires exactly one next action.
Anthropic: Messages API with the computer toolset (`computer_toolset_20260801`, no beta header) for current models,
or the legacy single `computer` tool (`computer_20251124` + beta header) for older models. Both are normalised to
the toolset shape: each action is a member name (left_click, type, key, …) with that member's own input.

The local fixture is a scripted, clearly labelled stand-in for tests/demos (live=False). It is not an AI model.
"""
from __future__ import annotations

import json
import time
import uuid
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
COMPUTER_NAMES = {
    "screenshot", "left_click", "right_click", "middle_click", "double_click", "triple_click",
    "left_click_drag", "mouse_move", "left_mouse_down", "left_mouse_up", "scroll", "type", "key",
    "hold_key", "wait", "cursor_position",
}
OLLAMA_ACTIONS = sorted(COMPUTER_NAMES | CONTROL_NAMES)


def _strict_function(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        "strict": True,
    }


_COORD = {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2}
OPENAI_COMPUTER_TOOLS: list[dict[str, Any]] = [
    _strict_function("screenshot", "Capture the current screen before deciding what to do.", {}, []),
    *[
        _strict_function(name, f"{label} at screenshot pixel coordinates.", {"coordinate": _COORD}, ["coordinate"])
        for name, label in (
            ("left_click", "Left-click"), ("right_click", "Right-click"), ("middle_click", "Middle-click"),
            ("double_click", "Double-click"), ("triple_click", "Triple-click"),
        )
    ],
    _strict_function("left_click_drag", "Drag from one screenshot coordinate to another.",
                     {"start_coordinate": _COORD, "coordinate": _COORD}, ["start_coordinate", "coordinate"]),
    _strict_function("mouse_move", "Move the pointer to screenshot coordinates.", {"coordinate": _COORD}, ["coordinate"]),
    _strict_function("left_mouse_down", "Hold the left mouse button down.", {}, []),
    _strict_function("left_mouse_up", "Release the left mouse button.", {}, []),
    _strict_function("scroll", "Scroll at screenshot coordinates.", {
        "coordinate": _COORD,
        "scroll_direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
        "scroll_amount": {"type": "integer", "minimum": 1, "maximum": 20},
    }, ["coordinate", "scroll_direction", "scroll_amount"]),
    _strict_function("type", "Type non-secret text into the active control.", {"text": {"type": "string"}}, ["text"]),
    _strict_function("key", "Press a key or key combination.", {"text": {"type": "string"}}, ["text"]),
    _strict_function("hold_key", "Hold a key combination for a bounded duration.", {
        "text": {"type": "string"}, "duration": {"type": "integer", "minimum": 1, "maximum": 10},
    }, ["text", "duration"]),
    _strict_function("wait", "Wait briefly for the interface to update.", {
        "duration": {"type": "integer", "minimum": 1, "maximum": 30},
    }, ["duration"]),
    _strict_function("cursor_position", "Read the current cursor position.", {}, []),
    *[
        _strict_function(t["name"], t["description"], t["input_schema"]["properties"], t["input_schema"]["required"])
        for t in CONTROL_TOOLS
    ],
]


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


def _openai_context(messages: list[dict]) -> list[dict[str, Any]]:
    """Flatten the normalized provider-neutral transcript and attach only the newest hydrated screenshot."""
    lines: list[str] = []
    latest_image: dict[str, str] | None = None

    def consume(block: dict, role: str) -> None:
        nonlocal latest_image
        kind = block.get("type")
        if kind == "text" and block.get("text"):
            lines.append(f"{role}: {block['text']}")
        elif kind == "image":
            source = block.get("source") or {}
            if source.get("type") == "base64" and source.get("data"):
                latest_image = {"media_type": str(source.get("media_type") or "image/png"), "data": str(source["data"])}
        elif kind == "tool_use":
            lines.append(f"assistant requested {block.get('name')}: {json.dumps(block.get('input') or {}, separators=(',', ':'))}")
        elif kind == "tool_result":
            for nested in block.get("content") or []:
                if isinstance(nested, dict):
                    consume(nested, f"result for {block.get('tool_use_id')}")

    for message in messages:
        role = "assistant" if message.get("role") == "assistant" else "customer/device"
        content = message.get("content")
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    consume(block, role)
        elif content:
            lines.append(f"{role}: {content}")
    parts: list[dict[str, Any]] = [{"type": "input_text", "text": "\n".join(lines) or "Inspect the current screen and choose the next safe action."}]
    if latest_image:
        parts.append({
            "type": "input_image",
            "image_url": f"data:{latest_image['media_type']};base64,{latest_image['data']}",
            "detail": "original",
        })
    return [{"role": "user", "content": parts}]


class OpenAIComputer:
    """OpenAI Responses adapter. JoltIQ still validates and executes every selected action itself."""

    name = "openai"
    live = True

    def __init__(self, key: str, base_url: str = "https://api.openai.com/v1", timeout: int = 120):
        self.key, self.base_url, self.timeout = key, base_url.rstrip("/"), timeout

    def turn(self, *, model: str, system: str, messages: list[dict], display: dict[str, int], max_tokens: int = 2048) -> Turn:
        body = {
            "model": model,
            "instructions": (
                system + f"\n\nThe current screenshot is {int(display['width'])}x{int(display['height'])} pixels. "
                "Choose exactly one next action. Use screenshot coordinates. Never claim success without visible verification."
            ),
            "input": _openai_context(messages),
            "tools": OPENAI_COMPUTER_TOOLS,
            "tool_choice": "required",
            "parallel_tool_calls": False,
            "max_output_tokens": max_tokens,
            "store": False,
        }
        t0 = time.perf_counter()
        try:
            response = httpx.post(
                f"{self.base_url}/responses",
                headers={"authorization": f"Bearer {self.key}", "content-type": "application/json"},
                json=body,
                timeout=self.timeout,
            )
        except httpx.HTTPError as e:
            raise ProviderError(f"openai transport error: {type(e).__name__}") from e
        if response.status_code >= 400:
            raise ProviderError(
                f"openai HTTP {response.status_code}: {response.text[:300]}",
                retryable=response.status_code in (408, 429, 500, 502, 503, 504),
            )
        try:
            raw = response.json()
        except ValueError as e:
            raise ProviderError("openai returned invalid JSON", retryable=False) from e
        output = raw.get("output") or []
        calls = [item for item in output if item.get("type") == "function_call"]
        if len(calls) != 1:
            raise ProviderError("openai must return exactly one action", retryable=False)
        call = calls[0]
        name = str(call.get("name") or "")
        if name not in COMPUTER_NAMES | CONTROL_NAMES:
            raise ProviderError(f"openai returned unsupported action: {name}", retryable=False)
        try:
            arguments = call.get("arguments") or "{}"
            params = json.loads(arguments) if isinstance(arguments, str) else arguments
        except (TypeError, ValueError) as e:
            raise ProviderError("openai returned invalid action arguments", retryable=False) from e
        if not isinstance(params, dict):
            raise ProviderError("openai returned invalid action arguments", retryable=False)
        narration = "".join(
            str(part.get("text") or "")
            for item in output if item.get("type") == "message"
            for part in item.get("content") or [] if part.get("type") == "output_text"
        ).strip()
        tool_id = str(call.get("call_id") or call.get("id") or f"call_openai_{uuid.uuid4().hex}")
        computer = name in COMPUTER_NAMES
        block = {"type": "tool_use", "id": tool_id, "name": name, "input": params}
        if computer:
            block["toolset_name"] = "computer"
        usage = raw.get("usage") or {}
        return Turn(
            narration,
            [ToolUse(tool_id, name, params, computer)],
            *([[{"type": "text", "text": narration}, block]] if narration else [[block]]),
            int(usage.get("input_tokens") or 0),
            int(usage.get("output_tokens") or 0),
            str(raw.get("model") or model),
            self.name,
            True,
            int((time.perf_counter() - t0) * 1000),
            {"response_id": raw.get("id")},
        )


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


def _ollama_message(message: dict) -> dict:
    """Convert the normalized computer-use transcript into Ollama chat text plus image payloads."""
    content = message.get("content")
    if not isinstance(content, list):
        return {"role": message.get("role", "user"), "content": str(content or "")}
    text: list[str] = []
    images: list[str] = []
    for block in content:
        kind = block.get("type")
        if kind == "text":
            text.append(str(block.get("text", "")))
        elif kind == "image":
            source = block.get("source") or {}
            if source.get("type") == "base64" and source.get("data"):
                images.append(str(source["data"]))
        elif kind == "tool_use":
            text.append(f"Previous action: {block.get('name')} {json.dumps(block.get('input') or {}, separators=(',', ':'))}")
        elif kind == "tool_result":
            nested = _ollama_message({"role": "user", "content": block.get("content") or []})
            text.append(f"Result of {block.get('tool_use_id')}: {nested.get('content', '')}")
            images.extend(nested.get("images") or [])
    out = {"role": "assistant" if message.get("role") == "assistant" else "user", "content": "\n".join(x for x in text if x)}
    if images:
        out["images"] = images
    return out


class OllamaComputer:
    """Customer-network multimodal model. It selects one normalized action; JoltIQ policy still validates/executes it."""

    name = "ollama"
    live = True

    def __init__(self, base_url: str, timeout: int = 120):
        self.base_url, self.timeout = base_url.rstrip("/"), timeout

    def turn(self, *, model: str, system: str, messages: list[dict], display: dict[str, int], max_tokens: int = 2048) -> Turn:
        schema = {
            "type": "object",
            "properties": {
                "narration": {"type": "string"},
                "action": {"type": "string", "enum": OLLAMA_ACTIONS},
                "params": {"type": "object"},
            },
            "required": ["narration", "action", "params"],
            "additionalProperties": False,
        }
        prompt = (
            system + f"\n\nThe screen is {int(display['width'])}x{int(display['height'])}. "
            "Choose exactly one next action. Coordinates are screenshot pixels. Return only the requested JSON object."
        )
        body = {
            "model": model,
            "stream": False,
            "format": schema,
            # One action is deliberately small. A large generation budget can make small on-prem models spend
            # minutes elaborating instead of returning the single policy-constrained decision we asked for.
            "options": {"temperature": 0, "num_predict": min(max_tokens, 384)},
            "messages": [{"role": "system", "content": prompt}, *[_ollama_message(m) for m in messages]],
        }
        t0 = time.perf_counter()
        try:
            response = httpx.post(f"{self.base_url}/api/chat", json=body, timeout=self.timeout)
        except httpx.HTTPError as e:
            raise ProviderError(f"ollama transport error: {type(e).__name__}") from e
        if response.status_code >= 400:
            raise ProviderError(f"ollama HTTP {response.status_code}: {response.text[:300]}", retryable=response.status_code >= 500)
        try:
            raw = response.json()
            decision = json.loads(raw.get("message", {}).get("content") or "")
        except (ValueError, TypeError) as e:
            raise ProviderError("ollama returned invalid structured output", retryable=False) from e
        name = decision.get("action")
        params = decision.get("params")
        narration = str(decision.get("narration") or "").strip()
        if name not in OLLAMA_ACTIONS:
            raise ProviderError(f"ollama returned unsupported action: {name}", retryable=False)
        if not isinstance(params, dict):
            raise ProviderError("ollama returned invalid action parameters", retryable=False)
        tool_id = f"toolu_ollama_{uuid.uuid4().hex}"
        computer = name in COMPUTER_NAMES
        block = {"type": "tool_use", "id": tool_id, "name": name, "input": params}
        if computer:
            block["toolset_name"] = "computer"
        return Turn(
            narration,
            [ToolUse(tool_id, name, params, computer)],
            [{"type": "text", "text": narration}, block],
            int(raw.get("prompt_eval_count") or 0),
            int(raw.get("eval_count") or 0),
            str(raw.get("model") or model),
            self.name,
            True,
            int((time.perf_counter() - t0) * 1000),
        )


def build(provider: str, key: str | None, timeout: int = 120):
    if provider == "local_fixture":
        return FixtureComputer()
    if provider == "anthropic" and key:
        return AnthropicComputer(key, timeout=timeout)
    if provider == "openai" and key:
        return OpenAIComputer(key, timeout=timeout)
    if provider == "ollama":
        from ...config import get_settings

        if get_settings().ollama_base_url:
            return OllamaComputer(get_settings().ollama_base_url, timeout=timeout)
    return None


SUPPORTED_PROVIDERS = ("ollama", "openai", "anthropic", "local_fixture")
