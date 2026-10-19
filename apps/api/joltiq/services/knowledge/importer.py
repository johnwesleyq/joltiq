"""Bulk onboarding: a company's past tickets (CSV/JSON exports from Zendesk, Freshdesk, Intercom, HubSpot, Jira SM,
or any spreadsheet) and help-center articles (ZIP of md/html/txt/pdf/docx). Imported text is PII-redacted before
it is stored, and every record is labelled as historical (not a verified JoltIQ outcome)."""
from __future__ import annotations

import csv
import io
import json
import uuid
import zipfile
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.errors import AppError
from ...models import KnowledgeDocument
from ...security.redaction import redact_text
from .. import audit
from ..jobs import enqueue
from .service import _vis, create_upload

MAX_ROWS = 20_000
MAX_ZIP_FILES = 500
MAX_UNZIPPED = 200 * 1024 * 1024
ARTICLE_EXT = (".md", ".markdown", ".txt", ".html", ".htm", ".pdf", ".docx", ".csv")

# Column aliases seen in common helpdesk exports (lower-cased, spaces/underscores normalised away).
ALIASES = {
    "id": ["id", "ticketid", "ticketnumber", "number", "key", "issuekey", "conversationid"],
    "subject": ["subject", "ticketname", "title", "summary", "name", "issue"],
    "description": ["description", "ticketdescription", "body", "problem", "question", "details", "firstmessage", "message", "content"],
    "resolution": ["resolution", "resolutiondetails", "solution", "answer", "fix", "resolutionnotes", "resolutioncomment", "lastagentreply", "agentreply",
                   "internalnotes", "notes"],
    "status": ["status", "state", "ticketstatus", "pipelinestage", "stage"],
    "tags": ["tags", "labels", "category", "ticketcategory", "type", "tickettype"],
    "product": ["product", "application", "app", "service", "component"],
}
RESOLVED = {"resolved", "closed", "solved", "done", "completed", "fixed"}


@dataclass
class ImportResult:
    imported: int = 0
    skipped: int = 0
    duplicates: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"imported": self.imported, "skipped": self.skipped, "duplicates": self.duplicates, "errors": self.errors[:20]}


def _norm(k: str) -> str:
    return "".join(c for c in k.lower() if c.isalnum())


def _pick(row: dict[str, Any], field_name: str) -> str:
    norm = {_norm(k): v for k, v in row.items() if isinstance(k, str)}
    for a in ALIASES[field_name]:
        v = norm.get(a)
        if v not in (None, ""):
            if isinstance(v, list):
                return ", ".join(str(x) for x in v)
            if isinstance(v, dict):
                return json.dumps(v)[:2000]
            return str(v).strip()
    return ""


def _rows(filename: str, data: bytes) -> list[dict[str, Any]]:
    name = filename.lower()
    text = data.decode("utf-8-sig", errors="replace")
    if name.endswith(".json") or text.lstrip()[:1] in ("[", "{"):
        try:
            j = json.loads(text)
        except json.JSONDecodeError as e:
            raise AppError(f"Not valid JSON: {e}", code="validation_error") from e
        if isinstance(j, dict):
            j = j.get("tickets") or j.get("results") or j.get("data") or j.get("conversations") or j.get("issues") or []
        if not isinstance(j, list):
            raise AppError("JSON must be a list of tickets (or {\"tickets\": [...]})", code="validation_error")
        rows = []
        for t in j:
            if isinstance(t, dict):
                # Zendesk/Freshdesk exports nest conversation in comments/conversations: use the last public reply as resolution.
                conv = t.get("comments") or t.get("conversations") or []
                if conv and not _pick(t, "resolution") and isinstance(conv, list):
                    last = conv[-1] if isinstance(conv[-1], dict) else {}
                    t = {**t, "resolution": last.get("body") or last.get("plain_body") or last.get("body_text") or ""}
                rows.append(t)
        return rows
    try:
        dialect = csv.Sniffer().sniff(text[:5000], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return list(csv.DictReader(io.StringIO(text), dialect=dialect))


def _ticket_files(filename: str, data: bytes) -> list[tuple[str, bytes]]:
    """Large helpdesk exports (e.g. HubSpot CSVs over 2 MB) arrive zipped: accept a ZIP of CSV/JSON files."""
    if not data[:4] == b"PK\x03\x04":
        return [(filename, data)]
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise AppError("Not a valid ZIP file", code="validation_error") from e
    out = []
    for i in zf.infolist():
        n = i.filename.rsplit("/", 1)[-1]
        if i.is_dir() or i.filename.startswith("__MACOSX/") or n.startswith(".") or not n.lower().endswith((".csv", ".json", ".tsv", ".txt")):
            continue
        if i.file_size > MAX_UNZIPPED:
            raise AppError("ZIP expands to more than 200 MB", code="upload_rejected")
        out.append((n, zf.read(i)))
    if not out:
        raise AppError("The ZIP has no CSV or JSON files. XLSX isn't supported: export as CSV.", code="validation_error")
    return out


def import_tickets(db: Session, org_id: uuid.UUID, *, filename: str, data: bytes, user_id, include_unresolved: bool = False) -> ImportResult:
    if filename.lower().endswith((".xlsx", ".xls")):
        raise AppError("Excel files aren't supported yet. Export as CSV instead.", code="validation_error")
    rows = [r for n, d in _ticket_files(filename, data) for r in _rows(n, d)]
    if not rows:
        raise AppError("No tickets found. Expected a CSV with a header row, or a JSON list.", code="validation_error")
    if len(rows) > MAX_ROWS:
        raise AppError(f"Too many rows ({len(rows)}); split the file into parts of {MAX_ROWS}", code="validation_error")
    res = ImportResult()
    batch = uuid.uuid4().hex[:12]
    seen = {u for (u,) in db.execute(select(KnowledgeDocument.source_uri).where(KnowledgeDocument.source_type == "imported_ticket"))}
    for i, row in enumerate(rows, start=2):
        subject, desc, fix = _pick(row, "subject"), _pick(row, "description"), _pick(row, "resolution")
        status, ext = _pick(row, "status").lower(), _pick(row, "id")
        if not subject and not desc:
            res.skipped += 1
            continue
        resolved = bool(fix) or status in RESOLVED
        if not resolved and not include_unresolved:
            res.skipped += 1
            continue
        uri = f"import:{ext or uuid.uuid5(uuid.NAMESPACE_OID, subject + desc).hex}"
        if uri in seen:
            res.duplicates += 1
            continue
        seen.add(uri)
        body = (f"Problem: {subject}\n\n{desc[:20_000]}\n\n" + (f"Resolution: {fix[:20_000]}\n" if fix else "Resolution: (not recorded)\n") +
                "".join(f"{k.title()}: {v}\n" for k in ("tags", "product") if (v := _pick(row, k))))
        red = redact_text(body)
        doc = KnowledgeDocument(org_id=org_id, title=(f"Past ticket {ext}: " if ext else "Past ticket: ") + redact_text(subject or desc[:80]).text[:250],
                                source_type="imported_ticket", source_uri=uri[:1000], content_type="text/plain", visibility=_vis(None),
                                status="pending", uploaded_by=user_id,
                                meta={"text": red.text[:100_000], "outcome": "historical", "status": status or None, "import_batch": batch,
                                      "redacted": red.findings})
        db.add(doc)
        db.flush()
        doc.current_version = 1
        enqueue(db, "knowledge.index", {"document_id": str(doc.id), "version": 1}, org_id=org_id, dedupe_key=f"kindex:{doc.id}:1")
        res.imported += 1
        if i > MAX_ROWS + 2:
            break
    audit.record(db, org_id=org_id, actor_kind="user", actor_id=user_id, action="knowledge.tickets_imported", resource_type="import",
                 resource_id=batch, data={**res.as_dict(), "file": filename[:200]})
    return res


def import_articles_zip(db: Session, org_id: uuid.UUID, *, filename: str, data: bytes, user_id, visibility: list[str] | None) -> ImportResult:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise AppError("Not a valid ZIP file", code="validation_error") from e
    infos = [i for i in zf.infolist() if not i.is_dir() and not i.filename.startswith(("__MACOSX/", ".")) and "/." not in i.filename]
    if len(infos) > MAX_ZIP_FILES:
        raise AppError(f"ZIP has {len(infos)} files; the limit is {MAX_ZIP_FILES}", code="validation_error")
    if sum(i.file_size for i in infos) > MAX_UNZIPPED:
        raise AppError("ZIP expands to more than 200 MB", code="upload_rejected")
    res = ImportResult()
    for info in infos:
        name = info.filename.rsplit("/", 1)[-1]
        if not name.lower().endswith(ARTICLE_EXT):
            res.skipped += 1
            continue
        if info.file_size > 25 * 1024 * 1024 or (info.compress_size and info.file_size / max(1, info.compress_size) > 200):
            res.errors.append(f"{name}: too large or suspicious compression")
            continue
        try:
            create_upload(db, org_id, title=name.rsplit(".", 1)[0].replace("-", " ").replace("_", " ")[:300], filename=name,
                          data=zf.read(info), uploaded_by=user_id, visibility=visibility, source_type="article")
            res.imported += 1
        except AppError as e:
            res.errors.append(f"{name}: {e}")
    audit.record(db, org_id=org_id, actor_kind="user", actor_id=user_id, action="knowledge.articles_imported", resource_type="import",
                 resource_id=None, data={**res.as_dict(), "file": filename[:200]})
    return res
