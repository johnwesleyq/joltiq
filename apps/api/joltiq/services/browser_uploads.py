"""Resolve an approved browser upload to one tenant- and ticket-bound attachment."""
from __future__ import annotations

import hashlib
import hmac
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AgentRun, SupportSession, TicketAttachment
from . import storage
from .procedures import validate_browser_executable_body


class BrowserUploadRejected(ValueError):
    """The requested attachment cannot safely be transferred to the browser worker."""


@dataclass(frozen=True)
class BrowserUpload:
    filename: str
    content_type: str
    size: int
    sha256: str
    data: bytes


def materialize_selected_attachment(body: dict, attachment_id: str | None) -> list[dict]:
    """Resolve the sole approved runtime binding without mutating the published procedure."""
    if not attachment_id:
        raise BrowserUploadRejected("customer-selected attachment is required")
    try:
        selected_id = str(uuid.UUID(attachment_id))
    except (TypeError, ValueError) as exc:
        raise BrowserUploadRejected("customer-selected attachment is invalid") from exc
    definition = validate_browser_executable_body(body)
    steps = [step.model_dump(mode="json") for step in definition.procedure]
    for step in steps:
        if step["action"] == "browser.upload":
            step["params"] = {
                "selector": step["params"]["selector"],
                "attachment_id": selected_id,
            }
    return steps


def load_for_execution(db: Session, run: AgentRun, session: SupportSession, attachment_id: str) -> BrowserUpload:
    try:
        parsed_id = uuid.UUID(attachment_id)
    except (TypeError, ValueError) as exc:
        raise BrowserUploadRejected("attachment not found for this ticket") from exc
    if run.org_id != session.org_id:
        raise BrowserUploadRejected("browser session belongs to another organization")
    if run.ticket_id is None:
        raise BrowserUploadRejected("run has no ticket for an attachment upload")
    if session.ticket_id != run.ticket_id:
        raise BrowserUploadRejected("browser session is linked to a different ticket")

    attachment = db.execute(
        select(TicketAttachment).where(
            TicketAttachment.id == parsed_id,
            TicketAttachment.org_id == run.org_id,
            TicketAttachment.ticket_id == run.ticket_id,
        )
    ).scalar_one_or_none()
    if attachment is None:
        raise BrowserUploadRejected("attachment not found for this ticket")
    try:
        data = storage.get(run.org_id, attachment.storage_key)
    except (FileNotFoundError, OSError, PermissionError) as exc:
        raise BrowserUploadRejected("attachment content is unavailable") from exc
    if len(data) != attachment.size_bytes:
        raise BrowserUploadRejected("attachment size does not match its stored record")
    actual_sha = hashlib.sha256(data).hexdigest()
    if not hmac.compare_digest(actual_sha, attachment.sha256.lower()):
        raise BrowserUploadRejected("attachment digest does not match its stored record")
    try:
        actual_type = storage.sniff(attachment.filename, data)
    except storage.UploadRejected as exc:
        raise BrowserUploadRejected(f"attachment content failed validation: {exc}") from exc
    if actual_type != attachment.content_type:
        raise BrowserUploadRejected("attachment content type does not match its stored record")
    return BrowserUpload(
        filename=attachment.filename,
        content_type=attachment.content_type,
        size=attachment.size_bytes,
        sha256=actual_sha,
        data=data,
    )
