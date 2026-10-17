"""Structured signal extraction from support content: symptoms, environment, error codes, attempted fixes,
and verified outcomes. Deterministic first pass (always runs, no data egress); an LLM pass can enrich it when an
approved provider is configured. Closed tickets are NOT assumed correct: outcome is 'verified' only if the
ticket carries an independently verified resolution."""
from __future__ import annotations

import re

ERROR_CODE = re.compile(
    r"\b(0x[0-9A-Fa-f]{6,8}|[A-Z]{2,6}[-_]?\d{3,6}|E\d{3,5}|HTTP\s?[45]\d\d|error\s+(?:code\s+)?\d{2,6}|"
    r"Event\s?ID\s?\d{2,5}|KB\d{6,8}|CVE-\d{4}-\d{4,7})\b",
    re.I,
)
PRODUCTS = [
    "outlook", "teams", "excel", "word", "onedrive", "sharepoint", "exchange", "windows 11", "windows 10", "windows server",
    "quickbooks", "adobe acrobat", "acrobat reader", "chrome", "edge", "firefox", "vpn", "globalprotect", "anyconnect",
    "printer", "servicetitan", "housecall pro", "jobber", "zoom", "office 365", "microsoft 365", "intune", "azure ad", "entra",
]
VERSION = re.compile(r"\b(?:v(?:ersion)?\s?)?(\d{1,2}\.\d{1,4}(?:\.\d{1,6}){0,2}|build\s\d{4,6}|\b(?:2016|2019|2021|2024)\b|\b2[0-9]H[12]\b)", re.I)
SYMPTOM_HINTS = re.compile(r"(crash\w*|freez\w*|hang\w*|won'?t (?:open|start|load|print|connect)|not (?:working|responding|syncing)|"
                           r"error\w*|fail\w*|slow\w*|disconnect\w*|stuck|blank screen|black screen|cannot \w+|can'?t \w+)", re.I)
FIX_HINTS = re.compile(r"(?:tried|attempted|restarted|reinstall\w*|repaired?|cleared?|disabled?|updated?|rebooted?|reset)\b[^.\n]{0,120}", re.I)


def extract(text: str) -> dict:
    low = text.lower()
    codes = sorted({m.group(0).strip() for m in ERROR_CODE.finditer(text)})[:25]
    products = [p for p in PRODUCTS if p in low]
    versions = sorted({m.group(0) for m in VERSION.finditer(text)})[:15]
    symptoms = []
    for sent in re.split(r"(?<=[.!?\n])\s+", text):
        if SYMPTOM_HINTS.search(sent) and len(sent) < 300:
            symptoms.append(sent.strip())
        if len(symptoms) >= 8:
            break
    fixes = [m.group(0).strip() for m in FIX_HINTS.finditer(text)][:10]
    return {"error_codes": codes, "products": products, "versions": versions, "symptoms": symptoms,
            "attempted_fixes": fixes, "extractor": "rules-v1"}
