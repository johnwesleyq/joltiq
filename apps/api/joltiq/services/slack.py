"""Slack alerts for approvals (ADR 0021).

An org connects a ``slack`` integration with an incoming-webhook URL and/or a bot token plus channel. Both secrets are
packed into ONE encrypted Credential. They are never stored in ``config``, never returned by the API (only a
fingerprint), never put in job payloads or audit data, and never echoed in error text.

Validation: in production the webhook must be ``https://hooks.slack.com/services/...`` and bot calls always go to
``https://slack.com/api/chat.postMessage``. Outside production only, a URL under ``JOLTIQ_SLACK_MOCK_BASE_URL`` (the
labelled MOCK Slack in the ``dev-mock`` compose profile) is also accepted. Tests inject an httpx MockTransport through
``integrations._TRANSPORT_OVERRIDE['slack']``, so nothing ever reaches real Slack.

Messages are Block Kit: a header, then risk/action/expiry fields, then a single "Open in JoltIQ" deep-link button.
There are deliberately no interactive approve/deny buttons: the deep link sends the approver through the normal
decision flow, which enforces SoD, the action hash and MFA. Requester and approver names and the approval
reason are left out of Slack.
Sends are deduped per (kind, approval, integration), rate-limited per integration per hour (org setting
``slack_rate_per_hour``, default 30), delivered by the job queue with retries, and audited.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, timedelta

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
PROD_API = "https://slack.com/api"
_TOKEN = re.compile(r"^xox[bp]-[A-Za-z0-9-]{6,250}$")
_CHANNEL = re.compile(r"^(#?[a-z0-9][a-z0-9._-]{0,79}|[CG][A-Z0-9]{8,20})$")


def _mock_base() -> str | None:
    s = get_settings()
    return s.slack_mock_base_url.rstrip("/") if s.env != "production" and s.slack_mock_base_url else None


def validate_webhook_url(url: str) -> None:
    from urllib.parse import urlsplit

    p = urlsplit(url or "")
    if p.scheme == "https" and p.hostname == "hooks.slack.com" and p.port in (None, 443) and p.path.startswith("/services/") \
            and not p.username and not p.password:
        return
    mock = _mock_base()
    if mock and url.startswith(mock + "/services/"):
        return
    raise AppError("Slack webhook URL must be https://hooks.slack.com/services/…", code="url_blocked")


def clean_config(cfg: dict) -> tuple[dict, str | None]:
    """Returns (config without secrets, packed secret JSON or None)."""
    cfg = dict(cfg or {})
    webhook = (cfg.pop("webhook_url", None) or "").strip()
    token = (cfg.pop("bot_token", None) or "").strip()
    out: dict = {}
    if webhook:
        validate_webhook_url(webhook)
    if token and not _TOKEN.match(token):
        raise AppError("Slack bot token must look like xoxb-…", code="validation_error")
    channel = (cfg.get("channel") or "").strip()
    if channel:
        if not _CHANNEL.match(channel):
            raise AppError("Channel must be a name like #it-approvals or a channel ID", code="validation_error")
        out["channel"] = channel
    if token and not channel:
        raise AppError("A channel is required when using a bot token", code="validation_error")
    events = cfg.get("events", list(EVENTS))
    if not isinstance(events, list) or any(e not in EVENTS for e in events):
        raise AppError(f"events must be a subset of {', '.join(EVENTS)}", code="validation_error")
    out["events"] = [e for e in EVENTS if e in events]
    if cfg.get("use_mock"):
        if not _mock_base():
            raise AppError("The MOCK Slack service is only available outside production", code="validation_error")
        out["use_mock"] = True
    out["mode"] = "bot" if token else "webhook" if webhook else None
    secret = json.dumps({k: v for k, v in (("webhook_url", webhook), ("bot_token", token)) if v}) if (webhook or token) else None
    return out, secret


def update_config(integ: Integration, cfg: dict) -> dict:
    """Channel/events changes only (no secrets). Returns the changes for audit."""
    new = dict(integ.config or {})
    if "channel" in cfg:
        ch = (cfg.get("channel") or "").strip()
        if ch and not _CHANNEL.match(ch):
            raise AppError("Channel must be a name like #it-approvals or a channel ID", code="validation_error")
        if not ch and new.get("mode") == "bot":
            raise AppError("A channel is required when using a bot token", code="validation_error")
        new["channel"] = ch or None
    if "events" in cfg:
        ev = cfg["events"]
        if not isinstance(ev, list) or any(e not in EVENTS for e in ev):
            raise AppError(f"events must be a subset of {', '.join(EVENTS)}", code="validation_error")
        new["events"] = [e for e in EVENTS if e in ev]
    changes = {k: new.get(k) for k in ("channel", "events") if new.get(k) != (integ.config or {}).get(k)}
    integ.config = new
    return changes


def _secrets(db: Session, integ: Integration) -> dict:
    from .integrations import secret_of

    try:
        return json.loads(secret_of(db, integ))
    except (ValueError, TypeError):
        return {}


def post(db: Session, integ: Integration, text: str, blocks: list[dict]) -> dict:
    """Send one message. Returns {ok, detail}; detail never contains the URL or token."""
    from .integrations import _TRANSPORT_OVERRIDE

    sec = _secrets(db, integ)
    channel = (integ.config or {}).get("channel")
    body: dict = {"text": text, "blocks": blocks, "unfurl_links": False}
    try:
        with httpx.Client(transport=_TRANSPORT_OVERRIDE.get("slack"), timeout=10, follow_redirects=False) as c:
            if sec.get("bot_token"):
                base = (_mock_base() if (integ.config or {}).get("use_mock") else None) or PROD_API
                r = c.post(f"{base}/chat.postMessage", json={**body, "channel": channel},
                           headers={"Authorization": f"Bearer {sec['bot_token']}"})
                data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
                if r.status_code == 200 and data.get("ok"):
                    return {"ok": True, "detail": f"posted to {channel}"}
                return {"ok": False, "detail": f"Slack error: {str(data.get('error') or r.status_code)[:60]}"}
            if sec.get("webhook_url"):
                validate_webhook_url(sec["webhook_url"])
                if channel:
                    body["channel"] = channel
                r = c.post(sec["webhook_url"], json=body)
                if r.status_code == 200:
                    return {"ok": True, "detail": "posted via incoming webhook"}
                return {"ok": False, "detail": f"Slack webhook HTTP {r.status_code}"}
    except AppError as e:
        return {"ok": False, "detail": e.message}
    except httpx.HTTPError as e:
        return {"ok": False, "detail": f"Slack unreachable ({type(e).__name__})"}
    return {"ok": False, "detail": "no Slack credential stored"}


def test_message() -> tuple[str, list[dict]]:
    t = "JoltIQ test message: Slack approval alerts are connected."
    return t, [{"type": "section", "text": {"type": "mrkdwn", "text": f":white_check_mark: {t}"}}]


TITLES = {"approval_requested": "Approval needed", "approval_reminder": "Reminder: approval expiring soon", "approval_outcome": "Approval {verb}"}


def render(a: Approval, kind: str) -> tuple[str, list[dict]]:
    verb = str(a.status).split(".")[-1].lower()
    title = TITLES[kind].format(verb=verb)
    exp = a.expires_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    link = f"{get_settings().web_origin.rstrip('/')}/approvals/{a.id}"
    text = f"{title}: {a.action} ({a.risk}) — expires {exp}"
    blocks = [{"type": "header", "text": {"type": "plain_text", "text": title[:150]}},
              {"type": "section", "fields": [{"type": "mrkdwn", "text": f"*Risk*\n{a.risk}"}, {"type": "mrkdwn", "text": f"*Action*\n{a.action}"[:2000]},
                                             {"type": "mrkdwn", "text": f"*{'Expires' if kind != 'approval_outcome' else 'Status'}*\n"
                                                                        f"{exp if kind != 'approval_outcome' else verb}"}]},
              {"type": "actions", "elements": [{"type": "button", "text": {"type": "plain_text", "text": "Open in JoltIQ"}, "url": link,
                                                "action_id": "open_approval", **({"style": "primary"} if kind != "approval_outcome" else {})}]},
              {"type": "context", "elements": [{"type": "mrkdwn", "text": "Decide in JoltIQ: sign-in, separation of duties and MFA apply."}]}]
    return text, blocks


def fanout(db: Session, a: Approval, kind: str) -> list[str]:
    """Queue Slack alerts for every active Slack integration of the org subscribed to `kind`. Returns statuses."""
    from .approval_notify import _org_setting

    out = []
    for integ in db.execute(select(Integration).where(Integration.org_id == a.org_id, Integration.kind == "slack",
                                                      Integration.status == "connected")).scalars():
        if kind not in (integ.config or {}).get("events", EVENTS):
            continue
        key = f"slack:{kind}:{a.id}:{integ.id}"
        if db.execute(select(NotificationDispatch.id).where(NotificationDispatch.org_id == a.org_id, NotificationDispatch.dedupe_key == key)).first():
            out.append("duplicate")
            continue
        cap = _org_setting(db, a.org_id, "slack_rate_per_hour", 30)
        recent = db.execute(select(func.count()).select_from(NotificationDispatch).where(
            NotificationDispatch.org_id == a.org_id, NotificationDispatch.dedupe_key.like(f"slack:%:{integ.id}"),
            NotificationDispatch.created_at > utcnow() - timedelta(hours=1), NotificationDispatch.channels.contains(["slack"]))).scalar() or 0
        limited = recent >= cap
        d = NotificationDispatch(id=uuid.uuid4(), org_id=a.org_id, user_id=None, dedupe_key=key, kind=kind, approval_id=a.id,
                                 channels=[] if limited else ["slack"], suppressed=["slack:rate_limited"] if limited else [])
        try:
            with db.begin_nested():
                db.add(d)
        except IntegrityError:
            out.append("duplicate")
            continue
        if not limited:
            enqueue(db, "slack.deliver", {"dispatch_id": str(d.id)}, org_id=a.org_id, dedupe_key=f"slack.deliver:{d.id}", max_attempts=4)
        audit.record(db, org_id=a.org_id, actor_kind="system", actor_id=None, action=f"notification.slack.{kind}", resource_type="approval",
                     resource_id=a.id, data={"integration_id": str(integ.id), "status": "rate_limited" if limited else "queued"})
        out.append("rate_limited" if limited else "queued")
    return out


@handler("slack.deliver")
def _deliver(db: Session, payload: dict) -> None:
    d = db.get(NotificationDispatch, uuid.UUID(payload["dispatch_id"]))
    if not d or "slack:sent" in (d.suppressed or []):
        return
    integ_id = d.dedupe_key.rsplit(":", 1)[-1]
    integ = db.get(Integration, uuid.UUID(integ_id))
    a = db.get(Approval, d.approval_id) if d.approval_id else None
    if not integ or integ.status == "revoked" or not a:
        d.suppressed = [*(d.suppressed or []), "slack:cancelled"]
        return
    text, blocks = render(a, d.kind)
    res = post(db, integ, text, blocks)
    audit.record(db, org_id=d.org_id, actor_kind="system", actor_id=None, action="slack.sent" if res["ok"] else "slack.failed",
                 resource_type="approval", resource_id=a.id, data={"integration_id": integ_id, "kind": d.kind, "detail": res["detail"]})
    if res["ok"]:
        d.suppressed = [*(d.suppressed or []), "slack:sent"]
        return
    attempt = int(payload.get("attempt", 1))
    if attempt < 3:  # retry without raising, so the failure audit above is kept
        enqueue(db, "slack.deliver", {"dispatch_id": str(d.id), "attempt": attempt + 1}, org_id=d.org_id,
                delay_seconds=30 * attempt, dedupe_key=f"slack.deliver:{d.id}:{attempt + 1}")
    else:
        d.suppressed = [*(d.suppressed or []), "slack:failed"]

