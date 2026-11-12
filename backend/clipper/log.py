"""Logging setup shared by the API, the worker and the CLI.

In containers every line is one JSON object, ready for a log collector. On a
developer machine the same records print as readable text. Extra fields passed
with `extra={...}` (job_id, video_id, request_id, duration_ms...) are kept.
"""

import json
import logging
import sys
from datetime import UTC, datetime

from . import config

_STANDARD = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime", "taskName"}


def _extras(record: logging.LogRecord) -> dict:
    return {k: v for k, v in record.__dict__.items() if k not in _STANDARD}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": record.getMessage(),
            **_extras(record),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str, ensure_ascii=False)


class TextFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        stamp = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        extras = " ".join(f"{k}={v}" for k, v in _extras(record).items())
        line = f"{stamp} {record.levelname:<7} {record.name}: {record.getMessage()}"
        if extras:
            line += f"  [{extras}]"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def configure(service: str) -> logging.Logger:
    """Install the handler once and return the logger for `service`."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if config.log_format() == "json" else TextFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(config.log_level())
    # Third-party loggers that are noisy at INFO.
    for name in ("uvicorn.access", "httpx", "httpx2", "RapidOCR", "alembic.runtime.migration"):
        logging.getLogger(name).setLevel(logging.WARNING)
    return logging.getLogger(f"clipper.{service}")
