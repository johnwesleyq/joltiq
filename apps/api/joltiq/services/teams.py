"""Microsoft Teams alerts for approvals (ADR 0022, same pattern as Slack in ADR 0021).

An org connects a ``teams`` integration with an incoming-webhook URL: either an Office 365 connector webhook
(``https://<tenant>.webhook.office.com/webhookb2/...`` or legacy ``https://outlook.office.com/webhook/...``) or a
Power Automate / Workflows HTTP trigger (``https://*.logic.azure.com/workflows/...`` or
``https://*.environment.api.powerplatform.com/...``). The URL is the secret: it is stored only as an encrypted
Credential and is never returned, logged, put in job payloads or audit data, or echoed in errors.
JoltIQ has no bot-framework pattern, so no Teams bot mode is offered.

Outside production only, a URL under ``JOLTIQ_TEAMS_MOCK_BASE_URL`` + ``/webhook/`` (the labelled MOCK Teams in the
``dev-mock`` compose profile) is also accepted. Tests inject a transport via ``integrations._TRANSPORT_OVERRIDE['teams']``.

Messages are an Adaptive Card (in a ``message`` attachment, accepted by both connectors and Workflows) with risk,
action and expiry facts and one ``Action.OpenUrl`` "Open in JoltIQ" deep link. No approve/deny actions, no names or
reasons. Dedupe, the org hourly cap (``teams_rate_per_hour``, default 30), queued retries and audit mirror Slack.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, timedelta
from urllib.parse import urlsplit

import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import get_settings
from ..core.errors import AppError
from ..models import Approval, Integration, NotificationDispatch, utcnow
from . import audit
from .jobs import enqueue, handler

EVENTS = ("approval_requested", "approval_reminder", "approval_outcome")


def _mock_base() -> str | None:
    s = get_settings()
    return s.teams_mock_base_url.rstrip("/") if s.env != "production" and s.teams_mock_base_url else None


def _host_ok(host: str, path: str) -> bool:
    if host.endswith(".webhook.office.com") and path.startswith("/webhookb2/"):
        return True
    if host in ("outlook.office.com", "outlook.office365.com") and path.startswith(("/webhook/", "/webhookb2/")):
        return True
    if host.endswith(".logic.azure.com") and path.startswith("/workflows/"):
        return True
    return host.endswith(".environment.api.powerplatform.com") and "/workflows/" in path


def validate_webhook_url(url: str) -> None:
    p = urlsplit(url or "")
    host = (p.hostname or "").lower()
    if p.scheme == "https" and p.port in (None, 443) and not p.username and not p.password and _host_ok(host, p.path):
        return
    mock = _mock_base()
    if mock and (url or "").startswith(mock + "/webhook/"):
        return
    raise AppError("Teams webhook URL must be a Microsoft Teams incoming webhook (…webhook.office.com) or a Power Automate "
                   "workflow URL (…logic.azure.com)", code="url_blocked")


def _events(ev) -> list[str]:
    if not isinstance(ev, list) or any(e not in EVENTS for e in ev):
        raise AppError(f"events must be a subset of {', '.join(EVENTS)}", code="validation_error")
    return [e for e in EVENTS if e in ev]


def clean_config(cfg: dict) -> tuple[dict, str | None]:
    cfg = dict(cfg or {})
    webhook = (cfg.pop("webhook_url", None) or "").strip()
    if webhook:
        validate_webhook_url(webhook)
    out: dict = {"events": _events(cfg.get("events", list(EVENTS))), "mode": "webhook" if webhook else None}
    if cfg.get("use_mock"):
        if not _mock_base():
            raise AppError("The MOCK Teams service is only available outside production", code="validation_error")
        out["use_mock"] = True
    flavour = "power_automate" if webhook and ("logic.azure.com" in webhook or "powerplatform.com" in webhook) else "connector"
    if webhook:
        out["flavour"] = flavour
    return out, (json.dumps({"webhook_url": webhook}) if webhook else None)


def update_config(integ: Integration, cfg: dict) -> dict:
    new = dict(integ.config or {})
    if "events" in cfg:
        new["events"] = _events(cfg["events"])
    changes = {k: new.get(k) for k in ("events",) if new.get(k) != (integ.config or {}).get(k)}
    integ.config = new
    return changes


def _secrets(db: Session, integ: Integration) -> dict:
    from .integrations import secret_of

    try:
        return json.loads(secret_of(db, integ))
    except (ValueError, TypeError):
        return {}


def post(db: Session, integ: Integration, card: dict) -> dict:
    """Send one Adaptive Card. Returns {ok, detail}; detail never contains the URL."""
    from .integrations import _TRANSPORT_OVERRIDE

    sec = _secrets(db, integ)
    if not sec.get("webhook_url"):
        return {"ok": False, "detail": "no Teams credential stored"}
    body = {"type": "message", "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None, "content": card}]}
    try:
        validate_webhook_url(sec["webhook_url"])
        with httpx.Client(transport=_TRANSPORT_OVERRIDE.get("teams"), timeout=10, follow_redirects=False) as c:
            r = c.post(sec["webhook_url"], json=body)
        if 200 <= r.status_code < 300:
            return {"ok": True, "detail": "posted via Teams webhook"}
        return {"ok": False, "detail": f"Teams webhook HTTP {r.status_code}"}
    except AppError as e:
        return {"ok": False, "detail": e.message}
    except httpx.HTTPError as e:
        return {"ok": False, "detail": f"Teams unreachable ({type(e).__name__})"}


def _card(body: list[dict], actions: list[dict] | None = None) -> dict:
    c = {"type": "AdaptiveCard", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "version": "1.4", "body": body}
    if actions:
        c["actions"] = actions
    return c


def test_message() -> dict:
    return _card([{"type": "TextBlock", "text": "JoltIQ test message: Teams approval alerts are connected.", "wrap": True}])


TITLES = {"approval_requested": "Approval needed", "approval_reminder": "Reminder: approval expiring soon", "approval_outcome": "Approval {verb}"}


def render(a: Approval, kind: str) -> dict:
    verb = str(a.status).split(".")[-1].lower()
    exp = a.expires_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    link = f"{get_settings().web_origin.rstrip('/')}/approvals/{a.id}"
    last = ("Status", verb) if kind == "approval_outcome" else ("Expires", exp)
    return _card([
        {"type": "TextBlock", "size": "Large", "weight": "Bolder", "text": TITLES[kind].format(verb=verb)[:150], "wrap": True},
        {"type": "FactSet", "facts": [{"title": "Risk", "value": str(a.risk)}, {"title": "Action", "value": str(a.action)[:500]},
                                       {"title": last[0], "value": last[1]}]},
        {"type": "TextBlock", "isSubtle": True, "size": "Small", "wrap": True,
         "text": "Decide in JoltIQ: sign-in, separation of duties and MFA apply."}],
        [{"type": "Action.OpenUrl", "title": "Open in JoltIQ", "url": link}])


def fanout(db: Session, a: Approval, kind: str) -> list[str]:
    from .approval_notify import _org_setting

    out = []
    for integ in db.execute(select(Integration).where(Integration.org_id == a.org_id, Integration.kind == "teams",
                                                      Integration.status != "revoked")).scalars():
        if kind not in (integ.config or {}).get("events", EVENTS):
            continue
        key = f"teams:{kind}:{a.id}:{integ.id}"
        if db.execute(select(NotificationDispatch.id).where(NotificationDispatch.org_id == a.org_id, NotificationDispatch.dedupe_key == key)).first():
            out.append("duplicate")
            continue
        cap = _org_setting(db, a.org_id, "teams_rate_per_hour", 30)
        recent = db.execute(select(func.count()).select_from(NotificationDispatch).where(
            NotificationDispatch.org_id == a.org_id, NotificationDispatch.dedupe_key.like(f"teams:%:{integ.id}"),
            NotificationDispatch.created_at > utcnow() - timedelta(hours=1), NotificationDispatch.channels.contains(["teams"]))).scalar() or 0
        limited = recent >= cap
        d = NotificationDispatch(id=uuid.uuid4(), org_id=a.org_id, user_id=None, dedupe_key=key, kind=kind, approval_id=a.id,
                                 channels=[] if limited else ["teams"], suppressed=["teams:rate_limited"] if limited else [])
        try:
            with db.begin_nested():
                db.add(d)
        except IntegrityError:
            out.append("duplicate")
            continue
        if not limited:
            enqueue(db, "teams.deliver", {"dispatch_id": str(d.id)}, org_id=a.org_id, dedupe_key=f"teams.deliver:{d.id}", max_attempts=4)
        audit.record(db, org_id=a.org_id, actor_kind="system", actor_id=None, action=f"notification.teams.{kind}", resource_type="approval",
                     resource_id=a.id, data={"integration_id": str(integ.id), "status": "rate_limited" if limited else "queued"})
        out.append("rate_limited" if limited else "queued")
    return out


@handler("teams.deliver")
def _deliver(db: Session, payload: dict) -> None:
    d = db.get(NotificationDispatch, uuid.UUID(payload["dispatch_id"]))
    if not d or "teams:sent" in (d.suppressed or []):
        return
    integ_id = d.dedupe_key.rsplit(":", 1)[-1]
    integ = db.get(Integration, uuid.UUID(integ_id))
    a = db.get(Approval, d.approval_id) if d.approval_id else None
    if not integ or integ.status == "revoked" or not a:
        d.suppressed = [*(d.suppressed or []), "teams:cancelled"]
        return
    res = post(db, integ, render(a, d.kind))
    audit.record(db, org_id=d.org_id, actor_kind="system", actor_id=None, action="teams.sent" if res["ok"] else "teams.failed",
                 resource_type="approval", resource_id=a.id, data={"integration_id": integ_id, "kind": d.kind, "detail": res["detail"]})
    if res["ok"]:
        d.suppressed = [*(d.suppressed or []), "teams:sent"]
        return
    attempt = int(payload.get("attempt", 1))
    if attempt < 3:
        enqueue(db, "teams.deliver", {"dispatch_id": str(d.id), "attempt": attempt + 1}, org_id=d.org_id,
                delay_seconds=30 * attempt, dedupe_key=f"teams.deliver:{d.id}:{attempt + 1}")
    else:
        d.suppressed = [*(d.suppressed or []), "teams:failed"]
