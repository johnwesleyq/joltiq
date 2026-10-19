"""LOCAL policy: the companion re-validates every server command before touching the machine, even though the
envelope is signed. A compromised server still cannot make it run a shell, read files, or type secrets."""
from __future__ import annotations

import re

COMBO = re.compile(r"^[A-Za-z0-9_+\- ]{1,60}$")
SECRETS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b(?:sk|pk|rk|whsec|xox[abp]|ghp|gho|AKIA)[-_A-Za-z0-9]{12,}\b"),
    re.compile(r"(?i)\b(pass(?:word|wd)?|pwd|secret|token|api[_-]?key)\s*[:=]\s*\S+"),
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
]
# Key combos that lock the machine, log the user out, or power it off are refused outright.
BLOCKED_COMBOS = {"ctrl+alt+delete", "ctrl+alt+del", "super+l", "cmd+ctrl+q", "ctrl+cmd+q", "cmd+shift+q", "cmd+alt+shift+q",
                  "ctrl+alt+shift+q"}

COMPUTER_USE = {"screen.capture", "input.click", "input.drag", "input.move", "input.mouse", "input.type", "input.key", "input.scroll",
                "input.wait", "input.cursor", "url.open"}


def _luhn_card(text: str) -> bool:
    for m in re.finditer(r"(?:\d[ -]?){13,19}", text):
        d = [int(c) for c in re.sub(r"\D", "", m.group(0))]
        s = sum(d[-1::-2]) + sum(sum(divmod(2 * x, 10)) for x in d[-2::-2])
        if 13 <= len(d) <= 19 and s % 10 == 0:
            return True
    return False


def check(action: str, params: dict) -> str | None:
    """Returns a refusal reason, or None if allowed."""
    if action not in COMPUTER_USE:
        return f"action {action} is not supported by this companion"
    for k in ("x", "y", "to_x", "to_y"):
        if k in params and not (isinstance(params[k], int) and 0 <= params[k] <= 10000):
            return f"bad coordinate {k}"
    if action == "input.type":
        t = params.get("text")
        if not isinstance(t, str) or len(t) > 2000:
            return "bad text"
        if any(p.search(t) for p in SECRETS) or _luhn_card(t):
            return "refused: text looks like a secret; the person must type it themselves"
    if action == "input.key":
        c = str(params.get("combo", ""))
        if not COMBO.fullmatch(c):
            return "bad key combo"
        if c.lower().replace(" ", "") in BLOCKED_COMBOS:
            return "refused: that key combination locks, logs out or shuts down the computer"
    if action == "url.open":
        u = str(params.get("url", ""))
        if not re.fullmatch(r"https?://[^\s]{1,2000}", u):
            return "only http(s) URLs may be opened"
    return None
