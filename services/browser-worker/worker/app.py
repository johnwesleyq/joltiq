"""JoltIQ isolated browser worker.

Runs headless Chromium via Playwright. Each JoltIQ support session gets its own BrowserContext (separate cookies,
storage, cache) that is destroyed on close/expiry. The worker trusts only the API (bearer token) and enforces, on its
own side, the session's domain allowlist at the network layer (route interception), so even a compromised plan
cannot reach other hosts.

Security behaviour:
  * secrets: typing into password / one-time-code fields is refused for the AI; humans type them via /input with
    `protected=true`, which is never logged or returned.
  * login / MFA / CAPTCHA pages are detected and reported as `auth_required` so the API pauses the AI.
  * screenshots mask password/OTP/card inputs before capture.
  * downloads land in a per-session quarantine dir; never opened or executed.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import os
import re
import shutil
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from playwright.async_api import Browser, BrowserContext, Page, Route, async_playwright
from pydantic import BaseModel, Field

log = logging.getLogger("joltiq.browser")
TOKEN = os.environ.get("JOLTIQ_BROWSER_WORKER_TOKEN", "dev-browser-worker-token-change-me")
MAX_SESSIONS = int(os.environ.get("JOLTIQ_BROWSER_MAX_SESSIONS", "8"))
IDLE_TIMEOUT = int(os.environ.get("JOLTIQ_BROWSER_IDLE_SECONDS", "900"))
MAX_UPLOAD_BYTES = int(os.environ.get("JOLTIQ_BROWSER_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))
SECRET_SELECTOR = ('input[type=password], input[autocomplete=one-time-code], input[autocomplete="current-password"], '
                   'input[autocomplete="new-password"], input[autocomplete^="cc-"], input[name*="otp" i], input[name*="cvv" i]')
CAPTCHA_HINTS = re.compile(r"(recaptcha|hcaptcha|turnstile|captcha)", re.I)
MFA_HINTS = re.compile(r"(verification code|authenticator app|one[- ]time (pass)?code|two[- ]factor|2fa|enter the code|approve sign[- ]in)", re.I)


class Session:
    def __init__(self, sid: str, ctx: BrowserContext, page: Page, allowed: list[str], download_dir: str):
        self.id, self.ctx, self.page, self.allowed, self.download_dir = sid, ctx, page, allowed, download_dir
        self.last_used = time.time()
        self.created = time.time()
        self.paused = False
        self.blocked_requests: list[str] = []
        self.log: list[dict[str, Any]] = []
        self.downloads: list[dict[str, Any]] = []


class State:
    pw = None
    browser: Browser | None = None
    sessions: dict[str, Session] = {}
    startup_ms: float = 0.0


S = State()


def domain_allowed(host: str, allowed: list[str]) -> bool:
    host = (host or "").lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in allowed)


async def _reaper():
    while True:
        await asyncio.sleep(30)
        now = time.time()
        for sid, s in list(S.sessions.items()):
            if now - s.last_used > IDLE_TIMEOUT:
                await close_session(sid)


@asynccontextmanager
async def lifespan(app: FastAPI):
    t0 = time.perf_counter()
    S.pw = await async_playwright().start()
    S.browser = await S.pw.chromium.launch(headless=True, args=["--disable-dev-shm-usage", "--no-first-run",
                                                                "--disable-background-networking", "--disable-sync"])
    S.startup_ms = (time.perf_counter() - t0) * 1000
    task = asyncio.create_task(_reaper())
    yield
    task.cancel()
    for sid in list(S.sessions):
        await close_session(sid)
    await S.browser.close()
    await S.pw.stop()


app = FastAPI(title="JoltIQ Browser Worker", lifespan=lifespan)


def auth(authorization: str = Header(default="")) -> None:
    if not hmac.compare_digest(authorization, f"Bearer {TOKEN}"):
        raise HTTPException(401, "unauthorized")


def get(sid: str) -> Session:
    s = S.sessions.get(sid)
    if not s:
        raise HTTPException(404, "session not found or expired")
    s.last_used = time.time()
    return s


class CreateIn(BaseModel):
    session_id: str = Field(max_length=64)
    allowed_domains: list[str] = Field(min_length=1, max_length=20)
    allow_private: bool = False  # only for the local authorized test app in dev/CI


@app.get("/healthz")
async def healthz():
    return {"status": "ok", "sessions": len(S.sessions), "browser_connected": bool(S.browser and S.browser.is_connected()),
            "startup_ms": round(S.startup_ms, 1)}


@app.post("/sessions", dependencies=[Depends(auth)])
async def create(body: CreateIn):
    if body.session_id in S.sessions:
        return {"session_id": body.session_id, "existing": True}
    if len(S.sessions) >= MAX_SESSIONS:
        raise HTTPException(503, "worker at capacity")
    t0 = time.perf_counter()
    dl = tempfile.mkdtemp(prefix=f"jq-dl-{body.session_id[:8]}-")
    ctx = await S.browser.new_context(accept_downloads=True, viewport={"width": 1280, "height": 800},
                                      user_agent="Mozilla/5.0 JoltIQ-Assist (isolated session)", java_script_enabled=True,
                                      service_workers="block")
    allowed = [d.lower() for d in body.allowed_domains]
    sess_ref: dict[str, Session] = {}

    async def guard(route: Route):
        host = urlsplit(route.request.url).hostname or ""
        scheme = urlsplit(route.request.url).scheme
        if scheme in ("data", "blob", "about"):
            return await route.continue_()
        if scheme not in ("http", "https") or not domain_allowed(host, allowed):
            if "s" in sess_ref:
                sess_ref["s"].blocked_requests.append(route.request.url[:200])
            return await route.abort("blockedbyclient")
        return await route.continue_()

    await ctx.route("**/*", guard)
    page = await ctx.new_page()
    s = Session(body.session_id, ctx, page, allowed, dl)
    sess_ref["s"] = s

    async def on_download(d):
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", d.suggested_filename)[:120]
        path = os.path.join(dl, f"{uuid.uuid4().hex[:8]}-{safe}")
        await d.save_as(path)
        s.downloads.append({"filename": safe, "path": path, "size": os.path.getsize(path), "quarantined": True})

    page.on("download", lambda d: asyncio.create_task(on_download(d)))
    page.on("popup", lambda p: asyncio.create_task(_adopt_popup(s, p)))
    S.sessions[body.session_id] = s
    return {"session_id": body.session_id, "startup_ms": round((time.perf_counter() - t0) * 1000, 1)}


async def _adopt_popup(s: Session, p: Page):
    # Popups stay inside the same isolated context and allowlist; we switch focus so the user sees them.
    s.page = p
    s.log.append({"t": time.time(), "event": "popup", "url": p.url[:200]})


async def close_session(sid: str) -> None:
    s = S.sessions.pop(sid, None)
    if s:
        try:
            await s.ctx.close()
        finally:
            shutil.rmtree(s.download_dir, ignore_errors=True)


@app.delete("/sessions/{sid}", dependencies=[Depends(auth)])
async def delete(sid: str):
    await close_session(sid)
    return {"closed": True}


async def detect_auth(page: Page) -> dict[str, Any]:
    try:
        has_secret = await page.locator(SECRET_SELECTOR).first.is_visible(timeout=300)
    except Exception:  # noqa: BLE001
        has_secret = False
    html = ""
    try:
        html = (await page.content())[:200_000]
    except Exception:  # noqa: BLE001
        pass
    frames = " ".join(f.url for f in page.frames)
    captcha = bool(CAPTCHA_HINTS.search(frames)) or bool(re.search(r'class="[^"]*(g-recaptcha|h-captcha|cf-turnstile)', html))
    try:
        body_text = (await page.inner_text("body", timeout=1000))[:5000]
    except Exception:  # noqa: BLE001
        body_text = ""
    mfa = bool(MFA_HINTS.search(body_text))
    kind = "captcha" if captcha else ("mfa" if mfa else ("login" if has_secret else None))
    return {"auth_required": kind is not None, "kind": kind}


async def page_state(s: Session, include_snapshot: bool = True) -> dict[str, Any]:
    p = s.page
    out: dict[str, Any] = {"url": p.url, "title": await p.title(), **(await detect_auth(p))}
    if include_snapshot:
        try:
            out["accessibility"] = (await p.locator("body").aria_snapshot(timeout=2000))[:8000]
        except Exception:  # noqa: BLE001
            out["accessibility"] = None
    out["blocked_requests"] = s.blocked_requests[-5:]
    return out


class ActionIn(BaseModel):
    action: str
    params: dict[str, Any] = {}
    actor: str = "ai"  # ai|customer|technician (lease already checked by the API)


def _resolve(page: Page, selector: str):
    # Deterministic selectors: role=button[name="Save"], testid=..., text=..., or CSS.
    m = re.match(r'^role=(\w+)\[name="(.+)"\]$', selector)
    if m:
        return page.get_by_role(m.group(1), name=m.group(2), exact=True)
    if selector.startswith("testid="):
        return page.get_by_test_id(selector[7:])
    if selector.startswith("label="):
        return page.get_by_label(selector[6:], exact=True)
    if selector.startswith("text="):
        return page.get_by_text(selector[5:], exact=True)
    return page.locator(selector)


def _safe_filename(filename: str) -> str:
    base = filename.replace("\\", "/").rsplit("/", 1)[-1]
    return re.sub(r"[^A-Za-z0-9._-]", "_", base)[:120] or "file"


async def _upload_refusal(s: Session, error: str, **extra) -> dict[str, Any]:
    result = {"ok": False, "error": error, **extra, "page": await page_state(s, include_snapshot=False)}
    s.log.append({"t": time.time(), "actor": "ai", "action": "browser.upload", "ok": False, "error": error})
    return result


@app.post("/sessions/{sid}/upload", dependencies=[Depends(auth)])
async def upload(
    sid: str,
    selector: str = Form(..., max_length=300),
    filename: str = Form(..., max_length=255),
    content_type: str = Form(..., max_length=120),
    sha256: str = Form(..., pattern=r"^[0-9a-fA-F]{64}$"),
    file: UploadFile = File(...),
):
    """Set one API-supplied attachment on one deterministic file input without accepting a host path."""
    s = get(sid)
    if s.paused:
        return await _upload_refusal(s, "session paused")
    auth_state = await detect_auth(s.page)
    if auth_state["auth_required"]:
        return await _upload_refusal(s, "authentication required — handing control to the user",
                                     auth_required=True, kind=auth_state["kind"])

    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES or await file.read(1):
        return await _upload_refusal(s, f"file size exceeds {MAX_UPLOAD_BYTES} bytes")
    actual_sha = hashlib.sha256(data).hexdigest()
    if not hmac.compare_digest(actual_sha, sha256.lower()):
        return await _upload_refusal(s, "file digest does not match approved attachment")

    loc = _resolve(s.page, selector)
    if await loc.count() != 1:
        return await _upload_refusal(s, "selector must match exactly one file input")
    element = loc.first
    target = await element.evaluate("e => ({tag: e.tagName.toLowerCase(), type: (e.type || '').toLowerCase()})")
    if target != {"tag": "input", "type": "file"}:
        return await _upload_refusal(s, "selector must target a file input")
    if not await element.is_enabled():
        return await _upload_refusal(s, "file input is disabled")

    safe_name = _safe_filename(filename)
    await element.set_input_files({"name": safe_name, "mimeType": content_type, "buffer": data})
    # File inputs often auto-submit on change. Let that navigation start, then avoid reading title/URL mid-commit.
    await asyncio.sleep(0.15)
    try:
        await s.page.wait_for_load_state("domcontentloaded", timeout=5000)
    except Exception:  # noqa: BLE001,S110 - non-navigating upload widgets are also valid
        pass
    metadata = {"filename": safe_name, "size": len(data), "sha256": actual_sha}
    s.log.append({"t": time.time(), "actor": "ai", "action": "browser.upload", "ok": True, "upload": metadata})
    return {"ok": True, "upload": metadata, "page": await page_state(s, include_snapshot=False)}


@app.post("/sessions/{sid}/actions", dependencies=[Depends(auth)])
async def act(sid: str, body: ActionIn):
    s = get(sid)
    if s.paused and body.actor == "ai":
        raise HTTPException(409, "session paused")
    p = s.page
    a, prm = body.action, body.params
    t0 = time.perf_counter()
    result: dict[str, Any] = {"ok": True}
    try:
        if body.actor == "ai":
            st = await detect_auth(p)
            if st["auth_required"] and a not in ("browser.read", "browser.navigate"):
                return {"ok": False, "auth_required": True, "kind": st["kind"], "error": "authentication required — handing control to the user"}
        if a == "browser.navigate":
            host = urlsplit(prm["url"]).hostname or ""
            if not domain_allowed(host, s.allowed):
                return {"ok": False, "error": f"domain {host} not allowed in this session"}
            resp = await p.goto(prm["url"], wait_until="domcontentloaded", timeout=20000)
            result["status"] = resp.status if resp else None
        elif a == "browser.read":
            if prm.get("selector"):
                loc = _resolve(p, prm["selector"])
                await loc.first.wait_for(timeout=5000)
                tag = await loc.first.evaluate("e => e.tagName.toLowerCase()")
                if tag in ("input", "select", "textarea"):
                    secret = await loc.first.evaluate("e => e.type === 'password' || (e.autocomplete||'').includes('one-time-code')")
                    result["value"] = "[secret field]" if secret else await loc.first.input_value()
                result["text"] = (await loc.first.inner_text())[:4000] if tag not in ("input", "select", "textarea") else None
            else:
                result["text"] = (await p.inner_text("body"))[:6000]
        elif a in ("browser.click", "browser.submit"):
            loc = _resolve(p, prm["selector"])
            await loc.first.click(timeout=8000)
            try:
                await p.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception:  # noqa: BLE001,S110
                pass
        elif a == "browser.type":
            loc = _resolve(p, prm["selector"])
            await loc.first.wait_for(timeout=5000)
            is_secret = await loc.first.evaluate(
                "e => e.type === 'password' || /one-time-code|password|cc-/.test(e.autocomplete || '') || /otp|cvv|pass/i.test(e.name || '')")
            if is_secret:
                return {"ok": False, "error": "refusing to type into a secret field; the user must enter it directly", "secret_field": True}
            await loc.first.fill(prm["text"], timeout=5000)
        elif a == "browser.select":
            await _resolve(p, prm["selector"]).first.select_option(prm["value"], timeout=5000)
        elif a == "browser.download":
            async with p.expect_download(timeout=15000) as dl_info:
                await _resolve(p, prm["selector"]).first.click()
            d = await dl_info.value
            await asyncio.sleep(0.2)
            result["download"] = {"filename": d.suggested_filename, "quarantined": True}
        else:
            return {"ok": False, "error": f"unsupported action {a}"}
    except Exception as e:  # noqa: BLE001
        result = {"ok": False, "error": f"{type(e).__name__}: {str(e).splitlines()[0][:300]}"}
    result["duration_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    s.log.append({"t": time.time(), "actor": body.actor, "action": a, "ok": result.get("ok"),
                  "params": {k: ("[text]" if k == "text" else v) for k, v in prm.items()}})
    result["page"] = await page_state(s, include_snapshot=False)
    if body.actor == "ai" and result["page"].get("auth_required"):
        result["auth_required"] = True
        result["kind"] = result["page"].get("kind")
    return result


class InputIn(BaseModel):
    """Human input relayed from the live viewer (customer/technician holding the control lease)."""

    kind: str = Field(pattern="^(click|type|key|scroll)$")
    x: float | None = None
    y: float | None = None
    text: str | None = Field(default=None, max_length=2000)
    key: str | None = Field(default=None, max_length=40)
    dy: float | None = None
    protected: bool = False  # secret entry: never logged, never returned


@app.post("/sessions/{sid}/input", dependencies=[Depends(auth)])
async def human_input(sid: str, body: InputIn):
    s = get(sid)
    p = s.page
    if body.kind == "click" and body.x is not None and body.y is not None:
        await p.mouse.click(body.x, body.y)
    elif body.kind == "type" and body.text is not None:
        await p.keyboard.type(body.text, delay=10)
    elif body.kind == "key" and body.key:
        if not re.fullmatch(r"(Enter|Tab|Backspace|Escape|Arrow(Up|Down|Left|Right)|Shift\+Tab|Delete|Home|End)", body.key):
            raise HTTPException(400, "key not allowed")
        await p.keyboard.press(body.key)
    elif body.kind == "scroll":
        await p.mouse.wheel(0, body.dy or 300)
    s.log.append({"t": time.time(), "actor": "human", "input": body.kind, "protected": body.protected})
    await asyncio.sleep(0.15)
    return {"ok": True, "page": await page_state(s, include_snapshot=False)}


@app.get("/sessions/{sid}/state", dependencies=[Depends(auth)])
async def state(sid: str):
    s = get(sid)
    return {**(await page_state(s)), "paused": s.paused, "downloads": [{k: v for k, v in d.items() if k != "path"} for d in s.downloads]}


@app.get("/sessions/{sid}/screenshot", dependencies=[Depends(auth)])
async def screenshot(sid: str, quality: int = 60):
    s = get(sid)
    masks = [s.page.locator(SECRET_SELECTOR)]
    img = await s.page.screenshot(type="jpeg", quality=max(20, min(90, quality)), mask=masks, mask_color="#111827",
                                  animations="disabled", timeout=8000)
    return {"image": base64.b64encode(img).decode(), "url": s.page.url, "ts": time.time()}


class PauseIn(BaseModel):
    paused: bool


@app.post("/sessions/{sid}/pause", dependencies=[Depends(auth)])
async def pause(sid: str, body: PauseIn):
    s = get(sid)
    s.paused = body.paused
    return {"paused": s.paused}


class ReadbackIn(BaseModel):
    url: str
    selector: str | None = None
    expect_text: str | None = None
    expect_value: str | None = None


@app.post("/sessions/{sid}/readback", dependencies=[Depends(auth)])
async def readback(sid: str, body: ReadbackIn):
    """Independent observation for the verifier: a FRESH page (same session cookies) loads the URL from the server,
    so results reflect persisted application state, not the DOM the actions just manipulated."""
    s = get(sid)
    host = urlsplit(body.url).hostname or ""
    if not domain_allowed(host, s.allowed):
        return {"ok": False, "error": "readback domain not allowed"}
    page = await s.ctx.new_page()
    try:
        resp = await page.goto(body.url, wait_until="domcontentloaded", timeout=20000)
        out: dict[str, Any] = {"ok": True, "status": resp.status if resp else None, "url": page.url}
        if body.selector:
            loc = _resolve(page, body.selector)
            await loc.first.wait_for(timeout=5000)
            tag = await loc.first.evaluate("e => e.tagName.toLowerCase()")
            out["text"] = (await loc.first.inner_text())[:2000] if tag not in ("input", "select", "textarea") else None
            out["value"] = await loc.first.input_value() if tag in ("input", "select", "textarea") else None
        else:
            out["text"] = (await page.inner_text("body"))[:6000]
        if body.expect_text is not None:
            out["matched"] = body.expect_text in (out.get("text") or "")
        if body.expect_value is not None:
            out["matched"] = (out.get("value") == body.expect_value) or (body.expect_value == (out.get("text") or "").strip())
        out["auth"] = await detect_auth(page)
        return out
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {str(e).splitlines()[0][:200]}"}
    finally:
        await page.close()


@app.get("/sessions/{sid}/log", dependencies=[Depends(auth)])
async def action_log(sid: str):
    return {"log": get(sid).log[-200:]}
