"""Client for the isolated browser worker (services/browser-worker)."""
from __future__ import annotations

from typing import Any

import httpx

from ..config import get_settings
from ..core.errors import Unavailable


def _h() -> dict[str, str]:
    return {"authorization": f"Bearer {get_settings().browser_worker_token}"}


def _url(path: str) -> str:
    return get_settings().browser_worker_url.rstrip("/") + path


def _req(method: str, path: str, **kw) -> dict[str, Any]:
    try:
        r = httpx.request(method, _url(path), headers=_h(), timeout=kw.pop("timeout", 45), **kw)
    except httpx.HTTPError as e:
        raise Unavailable(f"Browser worker unreachable ({type(e).__name__}). Start services/browser-worker.", code="browser_worker_down") from e
    if r.status_code == 404:
        raise Unavailable("Browser session no longer exists on the worker (expired or worker restarted)", code="browser_session_gone")
    if r.status_code >= 400:
        raise Unavailable(f"Browser worker error {r.status_code}: {r.text[:200]}", code="browser_worker_error")
    return r.json()


def health() -> dict[str, Any]:
    try:
        r = httpx.get(_url("/healthz"), timeout=3)
        return {"ok": r.status_code == 200, **r.json()}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": type(e).__name__}


def create(session_id: str, allowed_domains: list[str]) -> dict[str, Any]:
    return _req("POST", "/sessions", json={"session_id": session_id, "allowed_domains": allowed_domains})


def close(session_id: str) -> None:
    try:
        _req("DELETE", f"/sessions/{session_id}")
    except Unavailable:
        pass


def act(session_id: str, action: str, params: dict[str, Any], actor: str = "ai") -> dict[str, Any]:
    return _req("POST", f"/sessions/{session_id}/actions", json={"action": action, "params": params, "actor": actor})


def human_input(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    return _req("POST", f"/sessions/{session_id}/input", json=payload)


def state(session_id: str) -> dict[str, Any]:
    return _req("GET", f"/sessions/{session_id}/state")


def screenshot(session_id: str) -> dict[str, Any]:
    return _req("GET", f"/sessions/{session_id}/screenshot")


def pause(session_id: str, paused: bool) -> None:
    _req("POST", f"/sessions/{session_id}/pause", json={"paused": paused})


def readback(session_id: str, url: str, selector: str | None = None, expect_text: str | None = None,
             expect_value: str | None = None) -> dict[str, Any]:
    return _req("POST", f"/sessions/{session_id}/readback",
                json={"url": url, "selector": selector, "expect_text": expect_text, "expect_value": expect_value})
