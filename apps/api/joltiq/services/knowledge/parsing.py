"""Document parsing into ordered sections that preserve page / heading provenance for citations."""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass


@dataclass
class Section:
    text: str
    page: int | None = None
    heading: str | None = None


class ParseError(ValueError):
    pass


def parse(data: bytes, content_type: str, filename: str = "") -> list[Section]:
    ct = content_type.split(";")[0].strip().lower()
    if ct == "application/pdf":
        return _pdf(data)
    if ct.endswith("wordprocessingml.document"):
        return _docx(data)
    if ct.endswith("spreadsheetml.sheet"):
        return _xlsx(data)
    if ct == "text/csv":
        return _csv(data.decode("utf-8", errors="replace"))
    if ct == "text/html":
        return html_sections(data.decode("utf-8", errors="replace"))
    if ct == "text/markdown":
        return _markdown(data.decode("utf-8", errors="replace"))
    if ct in ("text/plain", "application/json"):
        text = data.decode("utf-8", errors="replace")
        if filename.lower().endswith((".vtt", ".srt")) or _looks_like_transcript(text):
            return _transcript(text)
        return _markdown(text)
    raise ParseError(f"unsupported content type {ct}")


def _pdf(data: bytes) -> list[Section]:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as e:  # noqa: BLE001
        raise ParseError(f"could not read PDF: {e}") from e
    if reader.is_encrypted:
        raise ParseError("encrypted PDFs are not supported")
    out = []
    for i, page in enumerate(reader.pages, start=1):
        t = (page.extract_text() or "").strip()
        if t:
            out.append(Section(t, page=i))
    if not out:
        raise ParseError("PDF has no extractable text (scanned image PDFs need OCR, not enabled)")
    return out


def _docx(data: bytes) -> list[Section]:
    import docx

    d = docx.Document(io.BytesIO(data))
    out: list[Section] = []
    heading, buf = None, []
    for p in d.paragraphs:
        style = (p.style.name or "").lower() if p.style is not None else ""
        if style.startswith("heading") and p.text.strip():
            if buf:
                out.append(Section("\n".join(buf), heading=heading))
            heading, buf = p.text.strip(), []
        elif p.text.strip():
            buf.append(p.text.strip())
    for table in d.tables:
        rows = [" | ".join(c.text.strip() for c in row.cells) for row in table.rows]
        buf.append("\n".join(rows))
    if buf:
        out.append(Section("\n".join(buf), heading=heading))
    return out


def _xlsx(data: bytes) -> list[Section]:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    out = []
    for ws in wb.worksheets:
        rows = []
        header = None
        for row in ws.iter_rows(values_only=True):
            vals = ["" if v is None else str(v) for v in row]
            if not any(vals):
                continue
            if header is None:
                header = vals
                continue
            rows.append("; ".join(f"{h}: {v}" for h, v in zip(header, vals, strict=False) if v))
            if len(rows) >= 50:
                out.append(Section("\n".join(rows), heading=ws.title))
                rows = []
        if rows:
            out.append(Section("\n".join(rows), heading=ws.title))
    return out


def _csv(text: str) -> list[Section]:
    reader = csv.DictReader(io.StringIO(text))
    out, rows = [], []
    for r in reader:
        rows.append("; ".join(f"{k}: {v}" for k, v in r.items() if v))
        if len(rows) >= 50:
            out.append(Section("\n".join(rows)))
            rows = []
    if rows:
        out.append(Section("\n".join(rows)))
    return out


def html_sections(html: str) -> list[Section]:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "iframe", "svg", "nav", "footer", "header", "form"]):
        tag.decompose()
    out: list[Section] = []
    heading, buf = None, []
    body = soup.body or soup
    for el in body.find_all(["h1", "h2", "h3", "h4", "p", "li", "pre", "code", "td", "blockquote"]):
        t = el.get_text(" ", strip=True)
        if not t:
            continue
        if el.name in ("h1", "h2", "h3", "h4"):
            if buf:
                out.append(Section("\n".join(buf), heading=heading))
            heading, buf = t[:300], []
        elif el.name != "code" or el.parent.name != "pre":
            buf.append(t)
    if buf:
        out.append(Section("\n".join(buf), heading=heading))
    if not out:
        t = body.get_text(" ", strip=True)
        if t:
            out.append(Section(t))
    return out


def _markdown(text: str) -> list[Section]:
    out: list[Section] = []
    heading, buf = None, []
    for line in text.splitlines():
        m = re.match(r"^\s{0,3}#{1,6}\s+(.*)$", line)
        if m:
            if "\n".join(buf).strip():
                out.append(Section("\n".join(buf).strip(), heading=heading))
            heading, buf = m.group(1).strip()[:300], []
        else:
            buf.append(line)
    if "\n".join(buf).strip():
        out.append(Section("\n".join(buf).strip(), heading=heading))
    return out


_TS = re.compile(r"^\s*(\d{1,2}:)?\d{1,2}:\d{2}([.,]\d{1,3})?\s*-->")


def _looks_like_transcript(text: str) -> bool:
    lines = text.splitlines()[:40]
    speakers = sum(1 for ln in lines if re.match(r"^\s*(agent|customer|caller|tech(nician)?|rep|speaker \d+)\s*:", ln, re.I))
    return speakers >= 3


def _transcript(text: str) -> list[Section]:
    """Call transcripts (VTT/SRT or 'Speaker: text' format) → turns grouped into sections."""
    turns = []
    for ln in text.splitlines():
        s = ln.strip()
        if not s or s.upper() == "WEBVTT" or s.isdigit() or _TS.match(s):
            continue
        turns.append(s)
    out, buf = [], []
    for t in turns:
        buf.append(t)
        if len(buf) >= 12:
            out.append(Section("\n".join(buf), heading="Call transcript"))
            buf = []
    if buf:
        out.append(Section("\n".join(buf), heading="Call transcript"))
    return out
