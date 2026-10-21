"""Structured JSON logging.

MCP servers using stdio must never write log output to stdout, so all log
records are emitted on stderr. Message text and custom fields are redacted
before serialization.
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

from jobmatch_mcp.core.redaction import redact, redact_value

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
trace_id_var: ContextVar[str | None] = ContextVar("trace_id", default=None)

_LOGGER_NAMES_TO_QUIET = ("httpx", "httpcore")


class JsonFormatter(logging.Formatter):
    """Render log records as single-line JSON."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
            "request_id": request_id_var.get(),
            "trace_id": trace_id_var.get(),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(redact_value(fields))
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_logging(level: str = "INFO") -> None:
    """Configure the root logger for JSON output on stderr."""
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    for name in _LOGGER_NAMES_TO_QUIET:
        logging.getLogger(name).setLevel(logging.WARNING)


@contextmanager
def request_context(request_id: str | None = None) -> Iterator[str]:
    """Bind a request id to the current async/context scope."""
    active_id = request_id or uuid.uuid4().hex
    token = request_id_var.set(active_id)
    trace_token = trace_id_var.set(active_id)
    try:
        yield active_id
    finally:
        request_id_var.reset(token)
        trace_id_var.reset(trace_token)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
