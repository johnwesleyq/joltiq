"""Sensitive-data redaction applied to: logs, session events, screenshots metadata, model context,
and outbound research queries (privacy-safe research)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),
    ("api_key", re.compile(r"\b(?:sk|pk|rk|whsec|xox[abp]|ghp|gho|AKIA)[-_A-Za-z0-9]{12,}\b")),
    ("bearer", re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{12,}=*")),
    ("password_kv", re.compile(r"(?i)\b(pass(?:word|wd)?|pwd|secret|token|api[_-]?key)\s*[:=]\s*\S+")),
    ("card", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    ("phone", re.compile(r"(?<!\d)(?:\+?1[ .-]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}(?!\d)")),
    ("ipv4", re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b")),
    ("unc_user_path", re.compile(r"(?i)C:\\Users\\[^\\\s]+")),
    ("mac", re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b")),
]

# Field names whose values are always masked regardless of content.
SECRET_KEYS = re.compile(r"(?i)(password|passwd|secret|token|cookie|authorization|api[_-]?key|mfa|otp|private[_-]?key|session)")


@dataclass
class RedactionResult:
    text: str
    findings: list[str] = field(default_factory=list)


def redact_text(text: str, *, extra_terms: list[str] | None = None, keep: set[str] | None = None) -> RedactionResult:
    keep = keep or set()
    findings: list[str] = []
    out = text
    for name, pat in _PATTERNS:
        if name in keep:
            continue
        if name == "card":
            def _card(m: re.Match[str]) -> str:
                digits = re.sub(r"\D", "", m.group(0))
                if 13 <= len(digits) <= 19 and _luhn(digits):
                    findings.append("card")
                    return "[REDACTED:card]"
                return m.group(0)
            out = pat.sub(_card, out)
            continue
        if name == "unc_user_path":
            new = pat.sub(r"C:\\Users\\[user]", out)
        else:
            new = pat.sub(f"[REDACTED:{name}]", out)
        if new != out:
            findings.append(name)
        out = new
    for term in extra_terms or []:
        if term and len(term) >= 3 and term.lower() in out.lower():
            out = re.sub(re.escape(term), "[REDACTED:name]", out, flags=re.I)
            findings.append("name")
    return RedactionResult(out, sorted(set(findings)))


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for d in reversed(digits):
        n = int(d)
        if alt:
            n *= 2
            if n > 9:
                n -= 9
        total += n
        alt = not alt
    return total % 10 == 0


def redact_obj(obj: Any, depth: int = 0) -> Any:
    """Recursively redact a JSON-like structure. Secret-named keys are fully masked."""
    if depth > 12:
        return "[TRUNCATED]"
    if isinstance(obj, dict):
        return {
            k: ("[REDACTED]" if isinstance(k, str) and SECRET_KEYS.search(k) else redact_obj(v, depth + 1))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [redact_obj(v, depth + 1) for v in obj]
    if isinstance(obj, str):
        return redact_text(obj).text
    return obj
