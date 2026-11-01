"""Structured JSON logging with per-request correlation IDs.

Message content is never logged; only metadata (lengths, latency, status).
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime, timezone

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

_RESERVED = set(vars(logging.makeLogRecord({})).keys()) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        # Anything passed via `extra=` is included as top-level fields.
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


# Libraries that would otherwise print warnings through Python's last-resort handler.
_LIBRARIES = ("httpx", "httpcore", "google_genai", "slowapi", "multipart", "PIL", "pypdf", "rapidocr_onnxruntime")


def configure_logging(level: str) -> None:
    """Set up app logging. LOG_LEVEL=OFF (default) leaves the terminal with uvicorn's own output only.

    Any other level (DEBUG/INFO/WARNING/ERROR) emits structured JSON logs, e.g. for production.
    Uvicorn's loggers are never touched, so its startup and access lines always look as usual.
    """
    root = logging.getLogger()
    root.handlers = [h for h in root.handlers if not isinstance(h.formatter, JsonFormatter)]

    if level.upper() == "OFF":
        # A NullHandler on root also stops Python's last-resort handler printing library warnings.
        root.addHandler(logging.NullHandler())
        root.setLevel(logging.CRITICAL + 1)
        for name in _LIBRARIES:
            logging.getLogger(name).setLevel(logging.CRITICAL + 1)
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level.upper())
    for name in _LIBRARIES:
        logging.getLogger(name).setLevel(logging.WARNING)
