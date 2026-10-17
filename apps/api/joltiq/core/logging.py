"""Structured JSON logs with request correlation. A redaction filter scrubs secrets from every record."""
from __future__ import annotations

import json
import logging
import sys

from ..security.redaction import redact_text
from .context import request_id_var

_STD = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"), "level": record.levelname, "logger": record.name,
               "msg": redact_text(record.getMessage()).text, "request_id": request_id_var.get() or None}
        for k, v in record.__dict__.items():
            if k not in _STD and not k.startswith("_"):
                out[k] = redact_text(v).text if isinstance(v, str) else v
        if record.exc_info:
            out["exc"] = redact_text(self.formatException(record.exc_info)).text
        return json.dumps(out, default=str)


_configured = False


def configure_logging(level: str = "INFO") -> None:
    global _configured
    if _configured:
        return
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [h]
    root.setLevel(level)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    _configured = True
