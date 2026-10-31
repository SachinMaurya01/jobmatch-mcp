"""Tracing, metrics, and trace-correlated logging."""

from __future__ import annotations

import json
import logging

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from jobmatch_mcp.core import telemetry
from jobmatch_mcp.core.logging import JsonFormatter
from jobmatch_mcp.core.telemetry import tool_span


def install_in_memory_tracer(monkeypatch: pytest.MonkeyPatch) -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(
        telemetry,
        "get_tracer",
        lambda name=None: provider.get_tracer("jobmatch-tests"),
    )
    return exporter


def test_tool_span_records_success(monkeypatch: pytest.MonkeyPatch) -> None:
    exporter = install_in_memory_tracer(monkeypatch)

    with tool_span("tool.demo", {"job.id": "abc"}):
        pass

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "tool.demo"
    assert spans[0].attributes is not None
    assert spans[0].attributes["job.id"] == "abc"
    assert spans[0].status.status_code is StatusCode.OK


def test_tool_span_records_error(monkeypatch: pytest.MonkeyPatch) -> None:
    exporter = install_in_memory_tracer(monkeypatch)

    with pytest.raises(ValueError):
        with tool_span("tool.demo"):
            raise ValueError("boom")

    span = exporter.get_finished_spans()[0]
    assert span.status.status_code is StatusCode.ERROR
    assert any(event.name == "exception" for event in span.events)


def test_json_log_carries_trace_context_and_redacts_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_in_memory_tracer(monkeypatch)
    record = logging.LogRecord(
        "jobmatch",
        logging.INFO,
        __file__,
        1,
        "contact a@b.com",
        None,
        None,
    )
    record.__dict__["fields"] = {
        "request_id": "req-1",
        "email": "a@b.com",
        "api_key": "super-secret-value",
        "note": "Authorization: Bearer abc.def.ghi",
    }

    with tool_span("tool.demo"):
        payload = json.loads(JsonFormatter().format(record))

    assert payload["request_id"] == "req-1"
    assert payload["trace_id"]
    assert payload["span_id"]
    assert payload["email"] == "[redacted-email]"
    assert payload["api_key"] == "[redacted-secret]"
    assert "super-secret-value" not in json.dumps(payload)
    assert "abc.def.ghi" not in json.dumps(payload)


def test_metrics_recording_is_safe_without_exporter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken_instrument(*args: object, **kwargs: object) -> None:
        raise RuntimeError("no meter")

    monkeypatch.setattr(telemetry, "_counter", broken_instrument)
    monkeypatch.setattr(telemetry, "_histogram", broken_instrument)

    telemetry.record_tool_call("tool.demo", "success", 1.0)
    telemetry.record_source_call("jsearch", "error", 2.0)
    telemetry.record_cache_event("jsearch", "hit")
    telemetry.record_llm_call("ollama", "qwen", "success", 3.0, 1, 2, 0.0)
    telemetry.record_fallback("ollama", "timeout")
    telemetry.record_db_query("session", 4.0, True)
