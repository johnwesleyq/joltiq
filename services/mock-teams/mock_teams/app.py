"""MOCK Microsoft Teams (DEV/TEST ONLY) - not Microsoft Teams.

* ``POST /webhook/{path}``: incoming-webhook / Workflows stand-in. Answers 200 ``1`` like a connector webhook, or 400
  for a body that is not a ``message`` with an Adaptive Card attachment (or a legacy MessageCard).
* ``GET /__mock/messages`` / ``POST /__mock/reset`` / ``GET /`` (labelled MOCK viewer).
Webhook paths (the secret part) are never recorded.
"""
from __future__ import annotations

import html
import threading
import time

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse

app = FastAPI(title="MOCK Teams (DEV/TEST ONLY)")
_lock = threading.Lock()
_msgs: list[dict] = []


@app.post("/webhook/{path:path}")
async def webhook(path: str, request: Request):
    try:
        body = await request.json()
    except ValueError:
        return PlainTextResponse("invalid payload", status_code=400)
    cards = [a.get("content") for a in body.get("attachments", []) if a.get("contentType") == "application/vnd.microsoft.card.adaptive"]
    if not cards and body.get("@type") != "MessageCard":
        return PlainTextResponse("Summary or Text is required.", status_code=400)
    with _lock:
        _msgs.append({"card": cards[0] if cards else body, "ts": f"{time.time():.6f}"})
    return PlainTextResponse("1")


@app.get("/__mock/messages")
def messages():
    with _lock:
        return {"mock": True, "messages": list(_msgs)}


@app.post("/__mock/reset")
def reset():
    with _lock:
        _msgs.clear()
    return {"ok": True}


def _lines(card: dict) -> list[str]:
    out = []
    for b in card.get("body", []):
        if b.get("type") == "TextBlock":
            out.append(b.get("text", ""))
        elif b.get("type") == "FactSet":
            out.append("  ·  ".join(f"{f.get('title')}: {f.get('value')}" for f in b.get("facts", [])))
    out += [f"[{a.get('title')} → {a.get('url')}]" for a in card.get("actions", [])]
    return out


@app.get("/", response_class=HTMLResponse)
def viewer():
    with _lock:
        rows = "".join("<div class='m'><div class='h'>Teams channel · via webhook</div>"
                       + "".join(f"<div class='b'>{html.escape(t)}</div>" for t in _lines(m["card"])) + "</div>" for m in reversed(_msgs))
    return ("<html><head><title>MOCK Teams</title><style>body{font-family:sans-serif;background:#1f1f2e;color:#ddd;margin:0}"
            ".bar{background:#b00;color:#fff;padding:8px 16px;font-weight:bold}.m{background:#2b2b40;margin:12px 16px;padding:12px;"
            "border-left:4px solid #6264a7;border-radius:6px}.h{color:#aaa;font-size:12px;margin-bottom:6px}.b{margin:4px 0;white-space:pre-wrap}"
            "</style></head><body><div class='bar'>MOCK Microsoft Teams — DEV/TEST ONLY, not Microsoft Teams. Nothing here leaves this machine.</div>"
            f"{rows or '<p style=padding:16px>No messages yet.</p>'}</body></html>")

