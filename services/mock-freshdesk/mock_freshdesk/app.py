"""MOCK Freshdesk (DEV/TEST ONLY) - not Freshdesk, not affiliated with Freshworks.

An in-memory stand-in for the subset of the public Freshdesk REST API v2
(https://developers.freshdesk.com/api/) used by the product's ticketing adapter:

* ``GET /api/v2/agents/me`` (connection test), ``GET /api/v2/contacts/{id}``
* ``GET /api/v2/tickets`` (``updated_since``, ``order_by``, ``order_type``, ``page``,
  ``per_page`` <= 100, ``include=requester,description``, ``Link: <...>; rel="next"``)
* ``POST /api/v2/tickets``, ``GET|PUT /api/v2/tickets/{id}``
* ``POST /api/v2/tickets/{id}/notes`` (``private``), ``POST /api/v2/tickets/{id}/reply``,
  ``GET /api/v2/tickets/{id}/conversations``
* Basic auth ``<api key>:X`` (401 ``invalid_credentials``), ``X-RateLimit-*`` headers and
  ``429`` + ``Retry-After`` when the per-minute budget is spent or a fault is injected.

Freshdesk itself has no signed webhooks: an admin adds a "Trigger webhook" action to an
automation rule with a custom header. This mock emulates that rule: on ticket create/update
it POSTs ``{"freshdesk_webhook": {...}}`` with the configured header to the configured URL.

Dev-only control endpoints live under ``/__mock`` (reset, faults, webhook config, state,
request log, simulate an agent edit in the Freshdesk UI). Timestamps are second precision
``YYYY-MM-DDTHH:MM:SSZ`` like the real API.
"""

from __future__ import annotations

import base64
import html
import os
import re
import threading
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

MOCK_LABEL = "MOCK Freshdesk (DEV/TEST ONLY - not Freshdesk)"
API_KEYS = {
    k.strip()
    for k in os.getenv("MOCK_FRESHDESK_API_KEYS", "mock-fd-key-0001").split(",")
    if k.strip()
}
RATE_LIMIT_PER_MIN = int(os.getenv("MOCK_FRESHDESK_RATE_LIMIT", "200"))
STATUS_NAMES = {2: "Open", 3: "Pending", 4: "Resolved", 5: "Closed"}
PRIORITY_NAMES = {1: "Low", 2: "Medium", 3: "High", 4: "Urgent"}
AGENT = {"id": 9001, "contact": {"name": "Mock Agent", "email": "agent@mock-freshdesk.test"}}


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(ts: str) -> datetime:
    ts = ts.strip().replace("Z", "+00:00")
    if len(ts) == 10:
        ts += "T00:00:00+00:00"
    d = datetime.fromisoformat(ts)
    return d if d.tzinfo else d.replace(tzinfo=UTC)


class State:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.reset()

    def reset(self) -> None:
        with self.lock:
            self.tickets: dict[int, dict[str, Any]] = {}
            self.contacts: dict[int, dict[str, Any]] = {}
            self.conversations: dict[int, list[dict[str, Any]]] = {}
            self.next_ticket = 1001
            self.next_contact = 5001
            self.next_conv = 70001
            self.faults: list[dict[str, Any]] = []
            self.webhook: dict[str, Any] | None = None
            self.webhook_log: list[dict[str, Any]] = []
            self.requests: list[dict[str, Any]] = []
            self.window_start = time.monotonic()
            self.window_used = 0

    def contact_for(self, email: str, name: str | None) -> dict[str, Any]:
        email = email.strip().lower()
        for c in self.contacts.values():
            if c["email"] == email:
                return c
        cid = self.next_contact
        self.next_contact += 1
        c = {
            "id": cid,
            "name": name or email.split("@")[0],
            "email": email,
            "phone": None,
            "mobile": None,
            "created_at": _now(),
            "updated_at": _now(),
        }
        self.contacts[cid] = c
        return c

    def touch(self, tid: int) -> None:
        t = self.tickets[tid]
        # Keep updated_at strictly increasing per ticket (second precision like the real API).
        now = _now()
        if now <= t["updated_at"]:
            now = datetime.fromtimestamp(_parse(t["updated_at"]).timestamp() + 1, UTC).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
        t["updated_at"] = now


state = State()
app = FastAPI(title=MOCK_LABEL)


def _err(status: int, code: str, message: str, headers: dict[str, str] | None = None) -> Response:
    return JSONResponse(
        {"description": message, "errors": [{"code": code, "message": message}]},
        status_code=status,
        headers=headers,
    )


@app.middleware("http")
async def api_guard(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    path = request.url.path
    if not path.startswith("/api/v2/"):
        return await call_next(request)
    entry: dict[str, Any] = {"at": _now(), "method": request.method, "path": path, "status": 0}
    with state.lock:
        state.requests.append(entry)
        del state.requests[:-500]
    auth = request.headers.get("authorization", "")
    key = ""
    if auth.lower().startswith("basic "):
        try:
            key = base64.b64decode(auth[6:]).decode().split(":", 1)[0]
        except Exception:  # noqa: BLE001
            key = ""
    if key not in API_KEYS:
        entry["status"] = 401
        return _err(401, "invalid_credentials", "You have to be logged in to perform this action.")
    with state.lock:
        for f in state.faults:
            if (
                f["count"] > 0
                and f.get("path_contains", "") in path
                and (not f.get("method") or f["method"] == request.method)
            ):
                f["count"] -= 1
                entry["status"] = f["status"]
                entry["fault"] = True
                if f["status"] == 429:
                    return _err(
                        429,
                        "rate_limit",
                        "You have exceeded the limit of requests per minute",
                        {"Retry-After": str(f.get("retry_after", 1))},
                    )
                return _err(f["status"], "mock_fault", "Injected MOCK fault")
        if time.monotonic() - state.window_start >= 60:
            state.window_start, state.window_used = time.monotonic(), 0
        if state.window_used >= RATE_LIMIT_PER_MIN:
            retry = max(1, int(60 - (time.monotonic() - state.window_start)))
            entry["status"] = 429
            return _err(429, "rate_limit", "Rate limit exceeded", {"Retry-After": str(retry)})
        state.window_used += 1
        remaining = RATE_LIMIT_PER_MIN - state.window_used
    resp: Response = await call_next(request)
    entry["status"] = resp.status_code
    resp.headers["X-RateLimit-Total"] = str(RATE_LIMIT_PER_MIN)
    resp.headers["X-RateLimit-Remaining"] = str(remaining)
    resp.headers["X-RateLimit-Used-CurrentRequest"] = "1"
    resp.headers["X-Freshdesk-Api-Version"] = "latest=v2; requested=v2"
    resp.headers["X-Mock"] = "mock-freshdesk"
    return resp


def _ticket_out(t: dict[str, Any], include: set[str]) -> dict[str, Any]:
    out = {k: v for k, v in t.items() if k not in ("description", "description_text")}
    if "description" in include or "_view" in include:
        out["description"] = t["description"]
        out["description_text"] = t["description_text"]
    if "requester" in include:
        c = state.contacts[t["requester_id"]]
        out["requester"] = {k: c[k] for k in ("id", "name", "email", "mobile", "phone")}
    return out


def _fire(tid: int, event: str) -> None:
    hook = state.webhook
    if not hook:
        return
    t = state.tickets[tid]
    payload = {
        "freshdesk_webhook": {
            "ticket_id": t["id"],
            "ticket_status": STATUS_NAMES.get(t["status"], str(t["status"])),
            "ticket_subject": t["subject"],
            "triggered_event": event,
        }
    }
    log_entry: dict[str, Any] = {"at": _now(), "ticket_id": tid, "event": event, "status": None}
    state.webhook_log.append(log_entry)
    del state.webhook_log[:-200]

    def send() -> None:
        time.sleep(float(hook.get("delay_s", 0.2)))
        try:
            r = httpx.post(
                hook["url"],
                json=payload,
                headers={hook.get("header", "X-Webhook-Secret"): hook["secret"]},
                timeout=10,
            )
            log_entry["status"] = r.status_code
        except httpx.HTTPError as e:
            log_entry["status"] = f"error: {type(e).__name__}"

    if hook.get("sync"):
        send()
    else:
        threading.Thread(target=send, daemon=True).start()


async def _json(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return {}
    return body if isinstance(body, dict) else {}


# ---- API v2 --------------------------------------------------------------------------------


@app.get("/api/v2/agents/me")
def agents_me() -> dict[str, Any]:
    return AGENT


@app.get("/api/v2/contacts/{cid}")
def view_contact(cid: int) -> Response:
    c = state.contacts.get(cid)
    if c is None:
        return _err(404, "not_found", "Contact not found")
    return JSONResponse(c)


def _validate_ticket_fields(body: dict[str, Any]) -> Response | None:
    if "status" in body and body["status"] not in STATUS_NAMES:
        return _err(400, "invalid_value", "It should be one of these values: '2,3,4,5'")
    if "priority" in body and body["priority"] not in PRIORITY_NAMES:
        return _err(400, "invalid_value", "It should be one of these values: '1,2,3,4'")
    return None


def _create(body: dict[str, Any]) -> tuple[int, dict[str, Any]] | Response:
    if bad := _validate_ticket_fields(body):
        return bad
    with state.lock:
        if body.get("requester_id") in state.contacts:
            contact = state.contacts[body["requester_id"]]
        elif body.get("email"):
            contact = state.contact_for(str(body["email"]), body.get("name"))
        else:
            return _err(400, "missing_field", "requester_id or email is mandatory")
        tid = state.next_ticket
        state.next_ticket += 1
        now = _now()
        desc = str(body.get("description", ""))
        t = {
            "id": tid,
            "subject": str(body.get("subject", "")),
            "description": desc,
            "description_text": desc,
            "status": int(body.get("status", 2)),
            "priority": int(body.get("priority", 1)),
            "source": int(body.get("source", 2)),
            "type": body.get("type"),
            "requester_id": contact["id"],
            "responder_id": None,
            "company_id": None,
            "group_id": None,
            "tags": list(body.get("tags", [])),
            "custom_fields": dict(body.get("custom_fields", {})),
            "cc_emails": [],
            "spam": False,
            "deleted": False,
            "is_escalated": False,
            "created_at": now,
            "updated_at": now,
            "due_by": None,
            "fr_due_by": None,
        }
        state.tickets[tid] = t
        state.conversations[tid] = []
        _fire(tid, "ticket_created")
        return tid, t


@app.post("/api/v2/tickets", status_code=201)
async def create_ticket(request: Request) -> Response:
    res = _create(await _json(request))
    if isinstance(res, Response):
        return res
    tid, t = res
    return JSONResponse(
        _ticket_out(t, {"_view"}),
        status_code=201,
        headers={"Location": f"{request.base_url}api/v2/tickets/{tid}"},
    )


@app.get("/api/v2/tickets")
def list_tickets(
    request: Request,
    updated_since: str | None = None,
    order_by: str = "created_at",
    order_type: str = "desc",
    page: int = 1,
    per_page: int = 30,
    include: str = "",
) -> Response:
    if per_page < 1 or per_page > 100 or page < 1:
        return _err(400, "invalid_value", "per_page must be 1..100 and page >= 1")
    if order_by not in ("created_at", "due_by", "updated_at", "status"):
        return _err(400, "invalid_value", "order_by is invalid")
    inc = {s.strip() for s in include.split(",") if s.strip()}
    with state.lock:
        rows = [t for t in state.tickets.values() if not t["deleted"] and not t["spam"]]
        if updated_since:
            try:
                since = _parse(updated_since)
            except ValueError:
                return _err(400, "invalid_value", "updated_since is invalid")
            rows = [t for t in rows if _parse(t["updated_at"]) >= since]
        rows.sort(key=lambda t: (t[order_by] or "", t["id"]), reverse=order_type != "asc")
        chunk = rows[(page - 1) * per_page : page * per_page]
        out = [_ticket_out(t, inc) for t in chunk]
    headers = {}
    if page * per_page < len(rows):
        q = dict(request.query_params)
        q["page"] = str(page + 1)
        headers["link"] = f'<{request.base_url}api/v2/tickets?{urlencode(q)}>; rel="next"'
    return JSONResponse(out, headers=headers)


@app.get("/api/v2/tickets/{tid}")
def view_ticket(tid: int, include: str = "") -> Response:
    with state.lock:
        t = state.tickets.get(tid)
        if t is None or t["deleted"]:
            return _err(404, "not_found", "Ticket not found")
        inc = {s.strip() for s in include.split(",") if s.strip()} | {"_view"}
        out = _ticket_out(t, inc)
        if "conversations" in inc:
            out["conversations"] = state.conversations[tid][:10]
        return JSONResponse(out)


def _update(tid: int, body: dict[str, Any], event: str) -> Response:
    with state.lock:
        t = state.tickets.get(tid)
        if t is None or t["deleted"]:
            return _err(404, "not_found", "Ticket not found")
        if bad := _validate_ticket_fields(body):
            return bad
        allowed = {"status", "priority", "subject", "description", "tags", "custom_fields", "type"}
        unknown = set(body) - allowed - {"responder_id", "group_id"}
        if unknown:
            return _err(400, "invalid_field", f"Unexpected field(s): {sorted(unknown)}")
        for k, v in body.items():
            t[k] = v
            if k == "description":
                t["description_text"] = v
        state.touch(tid)
        _fire(tid, event)
        return JSONResponse(_ticket_out(t, {"_view"}))


@app.put("/api/v2/tickets/{tid}")
async def update_ticket(tid: int, request: Request) -> Response:
    return _update(tid, await _json(request), "ticket_updated")


def _html_to_text(body: str) -> str:
    """Like Freshdesk's body_text: the HTML body flattened to plain text."""
    t = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>", "\n", body)
    t = re.sub(r"(?i)<li[^>]*>", "- ", t)
    t = re.sub(r"<[^>]+>", "", t)
    return re.sub(r"\n{3,}", "\n\n", html.unescape(t)).strip()


def _conversation(tid: int, body: dict[str, Any], *, private: bool, incoming: bool) -> Response:
    with state.lock:
        if tid not in state.tickets or state.tickets[tid]["deleted"]:
            return _err(404, "not_found", "Ticket not found")
        text = body.get("body")
        if not isinstance(text, str) or not text.strip():
            return _err(400, "missing_field", "body is mandatory")
        cid = state.next_conv
        state.next_conv += 1
        c = {
            "id": cid,
            "ticket_id": tid,
            "body": text,
            "body_text": _html_to_text(text),
            "private": private,
            "incoming": incoming,
            "source": 2 if private else 0,
            "user_id": AGENT["id"],
            "created_at": _now(),
            "updated_at": _now(),
        }
        state.conversations[tid].append(c)
        state.touch(tid)
        return JSONResponse(c, status_code=201)


@app.post("/api/v2/tickets/{tid}/notes", status_code=201)
async def create_note(tid: int, request: Request) -> Response:
    body = await _json(request)
    return _conversation(tid, body, private=bool(body.get("private", True)), incoming=False)


@app.post("/api/v2/tickets/{tid}/reply", status_code=201)
async def create_reply(tid: int, request: Request) -> Response:
    return _conversation(tid, await _json(request), private=False, incoming=False)


@app.get("/api/v2/tickets/{tid}/conversations")
def list_conversations(tid: int) -> Response:
    if tid not in state.tickets:
        return _err(404, "not_found", "Ticket not found")
    return JSONResponse(state.conversations[tid])


# ---- dev-only control plane (/__mock) ------------------------------------------------------


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "mock-freshdesk", "label": MOCK_LABEL}


@app.post("/__mock/reset")
def mock_reset() -> dict[str, str]:
    state.reset()
    return {"status": "reset"}


@app.post("/__mock/faults")
async def mock_faults(request: Request) -> dict[str, Any]:
    """Inject faults: {"status": 429, "count": 2, "retry_after": 1, "path_contains": "/notes"}."""
    body = await _json(request)
    with state.lock:
        state.faults.append(
            {
                "status": int(body.get("status", 429)),
                "count": int(body.get("count", 1)),
                "retry_after": int(body.get("retry_after", 1)),
                "path_contains": str(body.get("path_contains", "")),
                "method": body.get("method"),
            }
        )
        return {"faults": state.faults}


@app.put("/__mock/webhook")
async def mock_webhook(request: Request) -> dict[str, Any]:
    """Emulate an automation rule 'Trigger webhook': {"url", "secret", "header"?}."""
    body = await _json(request)
    with state.lock:
        state.webhook = (
            {
                "url": str(body["url"]),
                "secret": str(body.get("secret", "")),
                "header": str(body.get("header", "X-Webhook-Secret")),
                "sync": bool(body.get("sync", False)),
                "delay_s": float(body.get("delay_s", 0.2)),
            }
            if body.get("url")
            else None
        )
        return {"configured": state.webhook is not None}


@app.post("/__mock/tickets")
async def mock_create_ticket(request: Request) -> Response:
    """A customer submits a ticket in the (mock) Freshdesk portal."""
    res = _create(await _json(request))
    if isinstance(res, Response):
        return res
    return JSONResponse(_ticket_out(res[1], {"_view"}), status_code=201)


@app.post("/__mock/tickets/{tid}/agent-update")
async def mock_agent_update(tid: int, request: Request) -> Response:
    """A human agent edits the ticket in the Freshdesk UI (used for conflict tests)."""
    return _update(tid, await _json(request), "ticket_updated")


@app.get("/__mock/state")
def mock_state() -> dict[str, Any]:
    with state.lock:
        return {
            "label": MOCK_LABEL,
            "tickets": [
                {**t, "conversations": state.conversations[t["id"]]}
                for t in sorted(state.tickets.values(), key=lambda t: t["id"])
            ],
            "contacts": list(state.contacts.values()),
            "webhook_configured": state.webhook is not None,
            "webhook_log": state.webhook_log[-50:],
            "faults": state.faults,
        }


@app.get("/__mock/requests")
def mock_requests() -> list[dict[str, Any]]:
    with state.lock:
        return list(state.requests)


def _esc(s: Any) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


@app.get("/", response_class=HTMLResponse)
@app.get("/__mock/ui", response_class=HTMLResponse)
def ui(ticket: int | None = None) -> str:
    """Read-only HTML view of the mock helpdesk (for screenshots and manual checks)."""
    with state.lock:
        tickets = sorted(state.tickets.values(), key=lambda t: -t["id"])
        parts = [
            "<!doctype html><html><head><meta charset='utf-8'><title>MOCK Freshdesk</title>",
            "<style>body{font-family:system-ui,sans-serif;margin:0;background:#f5f7f9;color:#12344d}"
            ".banner{background:#b42318;color:#fff;padding:8px 16px;font-weight:700}"
            "main{padding:16px 24px}table{border-collapse:collapse;width:100%;background:#fff}"
            "td,th{border-bottom:1px solid #e3e8ec;padding:8px;text-align:left;vertical-align:top}"
            ".note{border-left:4px solid #f5a623;background:#fff8e6;padding:8px;margin:8px 0;white-space:pre-wrap}"
            ".reply{border-left:4px solid #2c5cc5;background:#eef3ff;padding:8px;margin:8px 0;white-space:pre-wrap}"
            ".pill{display:inline-block;border-radius:10px;padding:1px 8px;background:#e3e8ec}</style>",
            "</head><body>",
            f"<div class='banner' data-testid='mock-banner'>{_esc(MOCK_LABEL)} - in-memory test helpdesk</div><main>",
        ]
        if ticket is not None and ticket in state.tickets:
            t = state.tickets[ticket]
            c = state.contacts[t["requester_id"]]
            parts.append(
                f"<p><a href='/'>&larr; all tickets</a></p><h2 data-testid='mock-ticket-title'>#{t['id']} {_esc(t['subject'])}</h2>"
                f"<p>Requester: {_esc(c['name'])} &lt;{_esc(c['email'])}&gt; &middot; Status: "
                f"<span class='pill' data-testid='mock-ticket-status'>{STATUS_NAMES[t['status']]}</span> &middot; "
                f"Priority: {PRIORITY_NAMES[t['priority']]} &middot; Updated {t['updated_at']}</p>"
                f"<p>{_esc(t['description_text'])}</p><h3>Conversations</h3>"
            )
            for cv in state.conversations[ticket]:
                kind = "Private note" if cv["private"] else "Public reply"
                cls = "note" if cv["private"] else "reply"
                parts.append(
                    f"<div class='{cls}' data-testid='mock-conversation'><b>{kind}</b> &middot; {cv['created_at']}\n{_esc(cv['body_text'])}</div>"
                )
        else:
            parts.append(
                "<h2>Tickets</h2><table><tr><th>ID</th><th>Subject</th><th>Requester</th><th>Status</th><th>Notes / replies</th><th>Updated</th></tr>"
            )
            for t in tickets:
                c = state.contacts[t["requester_id"]]
                convs = state.conversations[t["id"]]
                n = sum(1 for x in convs if x["private"])
                parts.append(
                    f"<tr data-testid='mock-ticket-row'><td><a href='/?ticket={t['id']}'>#{t['id']}</a></td><td>{_esc(t['subject'])}</td>"
                    f"<td>{_esc(c['email'])}</td><td>{STATUS_NAMES[t['status']]}</td><td>{n} / {len(convs) - n}</td><td>{t['updated_at']}</td></tr>"
                )
            parts.append("</table>")
        parts.append("</main></body></html>")
        return "".join(parts)

