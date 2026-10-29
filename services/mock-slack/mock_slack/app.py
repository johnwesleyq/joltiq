"""MOCK Slack (DEV/TEST ONLY) - not Slack.

* ``POST /services/{a}/{b}/{c}``: incoming-webhook stand-in. It answers ``ok`` like Slack, or 404 ``no_service``
  when the path isn't registered in MOCK_SLACK_WEBHOOK_PATHS (comma-separated; ``*`` accepts any).
* ``POST /api/chat.postMessage``: Web API stand-in. It needs ``Authorization: Bearer <token>`` with a token in
  MOCK_SLACK_BOT_TOKENS and returns ``{"ok": true, "channel", "ts"}`` or ``{"ok": false, "error": "invalid_auth"}``.
* ``GET /__mock/messages`` returns the recorded messages. ``POST /__mock/reset`` clears them. ``GET /`` serves a small
  labelled MOCK viewer page.
Tokens and webhook paths are never recorded.
"""
from __future__ import annotations

import html
import json
import os
import threading
import time

from fastapi import FastAPI, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

app = FastAPI(title="MOCK Slack (DEV/TEST ONLY)")
_lock = threading.Lock()
_msgs: list[dict] = []
PATHS = {p.strip() for p in os.environ.get("MOCK_SLACK_WEBHOOK_PATHS", "*").split(",") if p.strip()}
TOKENS = {t.strip() for t in os.environ.get("MOCK_SLACK_BOT_TOKENS", "").split(",") if t.strip()}


def _record(via: str, channel: str | None, body: dict) -> dict:
    m = {"via": via, "channel": channel, "text": body.get("text", ""), "blocks": body.get("blocks", []), "ts": f"{time.time():.6f}"}
    with _lock:
        _msgs.append(m)
    return m


@app.post("/services/{a}/{b}/{c}")
async def webhook(a: str, b: str, c: str, request: Request):
    if "*" not in PATHS and f"{a}/{b}/{c}" not in PATHS:
        return PlainTextResponse("no_service", status_code=404)
    try:
        body = json.loads(await request.body())
    except ValueError:
        return PlainTextResponse("invalid_payload", status_code=400)
    _record("webhook", None, body)
    return PlainTextResponse("ok")


@app.post("/api/chat.postMessage")
async def post_message(request: Request, authorization: str = Header(default="")):
    tok = authorization.removeprefix("Bearer ").strip()
    if not tok or tok not in TOKENS:
        return JSONResponse({"ok": False, "error": "invalid_auth"})
    body = await request.json()
    if not body.get("channel"):
        return JSONResponse({"ok": False, "error": "channel_not_found"})
    m = _record("bot", body["channel"], body)
    return JSONResponse({"ok": True, "channel": body["channel"], "ts": m["ts"]})


@app.get("/__mock/messages")
def messages():
    with _lock:
        return {"mock": True, "messages": list(_msgs)}


@app.post("/__mock/reset")
def reset():
    with _lock:
        _msgs.clear()
    return {"ok": True}


def _block_text(b: dict) -> str:
    if b.get("type") == "section":
        return (b.get("text") or {}).get("text", "") + " " + " ".join(f.get("text", "") for f in b.get("fields", []))
    if b.get("type") == "actions":
        return " ".join(f"[{(e.get('text') or {}).get('text', '')} → {e.get('url', '')}]" for e in b.get("elements", []))
    if b.get("type") in ("header", "context"):
        t = b.get("text") or {}
        return t.get("text", "") if t else " ".join(e.get("text", "") for e in b.get("elements", []))
    return ""


@app.get("/", response_class=HTMLResponse)
def viewer():
    with _lock:
        rows = "".join(
            f"<div class='m'><div class='h'>#{html.escape(m['channel'] or 'webhook channel')} · via {m['via']}</div>"
            + "".join(f"<div class='b'>{html.escape(_block_text(b))}</div>" for b in m["blocks"])
            + "</div>" for m in reversed(_msgs))
    return ("<html><head><title>MOCK Slack</title><style>body{font-family:sans-serif;background:#1a1d21;color:#ddd;margin:0}"
            ".bar{background:#b00;color:#fff;padding:8px 16px;font-weight:bold}.m{background:#222529;margin:12px 16px;padding:12px;border-radius:8px}"
            ".h{color:#aaa;font-size:12px;margin-bottom:6px}.b{margin:4px 0;white-space:pre-wrap}</style></head><body>"
            "<div class='bar'>MOCK Slack — DEV/TEST ONLY, not Slack. Nothing here leaves this machine.</div>"
            f"{rows or '<p style=padding:16px>No messages yet.</p>'}</body></html>")

