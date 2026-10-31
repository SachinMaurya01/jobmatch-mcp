"""OpenTelemetry tracing and metrics for the MCP server."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any, cast

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.trace import Status, StatusCode

from jobmatch_mcp.core.config import Settings, get_settings

_log = logging.getLogger(__name__)

_SERVICE_VERSION = "0.1.0"

_initialized = False
_tracer_provider: TracerProvider | None = None
_meter_provider: MeterProvider | None = None

_INSTRUMENTS: dict[tuple[str, str, str], Any] = {}


def _resource(settings: Settings) -> Resource:
    return Resource.create(
        {
            "service.name": settings.otel_service_name,
            "service.version": _SERVICE_VERSION,
            "deployment.environment": settings.app_env,
        }
    )


def _otlp_endpoint(settings: Settings, signal: str) -> str | None:
    signal_key = f"OTEL_EXPORTER_OTLP_{signal.upper()}_ENDPOINT"
    event_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    return os.getenv(signal_key) or event_endpoint or settings.otel_endpoint


def _exporter_kind(settings: Settings) -> str:
    configured = settings.otel_exporter
    if any(
        os.getenv(key)
        for key in (
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
            "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
        )
    ):
        return "otlp"
    return configured


def _use_http_protocol() -> bool:
    return os.getenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc").lower().startswith("http")


def _span_exporter(settings: Settings) -> Any:
    endpoint = _otlp_endpoint(settings, "traces")
    if _use_http_protocol():
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter as HttpSpanExporter,
        )

        return HttpSpanExporter(endpoint=endpoint) if endpoint else HttpSpanExporter()
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
        OTLPSpanExporter as GrpcSpanExporter,
    )

    return GrpcSpanExporter(endpoint=endpoint) if endpoint else GrpcSpanExporter()


def _metric_exporter(settings: Settings) -> Any:
    endpoint = _otlp_endpoint(settings, "metrics")
    if _use_http_protocol():
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
            OTLPMetricExporter as HttpMetricExporter,
        )

        return HttpMetricExporter(endpoint=endpoint) if endpoint else HttpMetricExporter()
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
        OTLPMetricExporter as GrpcMetricExporter,
    )

    return GrpcMetricExporter(endpoint=endpoint) if endpoint else GrpcMetricExporter()


def setup_telemetry(settings: Settings | None = None) -> None:
    """Configure global tracer and meter providers.

    Exporters are opt-in: with the default settings the providers are created
    without exporters, so spans and metrics are recorded as no-ops until an
    exporter is configured. This keeps local runs quiet and dependency-free.
    """
    global _initialized, _tracer_provider, _meter_provider

    if _initialized:
        return
    settings = settings or get_settings()
    _initialized = True

    if not settings.otel_enabled:
        _log.info("OpenTelemetry disabled")
        return

    resource = _resource(settings)
    kind = _exporter_kind(settings)

    tracer_provider = TracerProvider(resource=resource)
    if kind == "otlp":
        tracer_provider.add_span_processor(BatchSpanProcessor(_span_exporter(settings)))
    elif kind == "console":
        tracer_provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(tracer_provider)
    _tracer_provider = tracer_provider

    readers: list[PeriodicExportingMetricReader] = []
    interval_ms = max(1000, int(settings.otel_metrics_interval_seconds * 1000))
    if kind == "otlp":
        readers.append(
            PeriodicExportingMetricReader(
                _metric_exporter(settings),
                export_interval_millis=interval_ms,
            )
        )
    elif kind == "console":
        readers.append(
            PeriodicExportingMetricReader(
                ConsoleMetricExporter(),
                export_interval_millis=interval_ms,
            )
        )
    meter_provider = MeterProvider(resource=resource, metric_readers=readers)
    metrics.set_meter_provider(meter_provider)
    _meter_provider = meter_provider

    _log.info(
        "OpenTelemetry initialized",
        extra={
            "fields": {
                "otel_exporter": kind,
                "otel_endpoint": _otlp_endpoint(settings, "traces"),
                "service_name": settings.otel_service_name,
            }
        },
    )


def shutdown_telemetry() -> None:
    """Flush and shut down the configured providers."""
    global _initialized
    if _tracer_provider is not None:
        _tracer_provider.shutdown()
    if _meter_provider is not None:
        _meter_provider.shutdown()
    _initialized = False


def get_tracer(name: str | None = None) -> trace.Tracer:
    """Return a tracer for the given instrumentation scope."""
    return trace.get_tracer(name or __name__)


def get_meter(name: str | None = None) -> metrics.Meter:
    """Return a meter for the given instrumentation scope."""
    return metrics.get_meter(name or __name__)


def _counter(name: str, description: str, unit: str = "1") -> metrics.Counter:
    key = ("counter", name, unit)
    if key not in _INSTRUMENTS:
        _INSTRUMENTS[key] = get_meter().create_counter(name, description=description, unit=unit)
    return cast(metrics.Counter, _INSTRUMENTS[key])


def _histogram(name: str, description: str, unit: str = "ms") -> metrics.Histogram:
    key = ("histogram", name, unit)
    if key not in _INSTRUMENTS:
        _INSTRUMENTS[key] = get_meter().create_histogram(name, description=description, unit=unit)
    return cast(metrics.Histogram, _INSTRUMENTS[key])


def _safe(record: Any) -> None:
    """Metric recording must never break a request."""
    try:
        record()
    except Exception:
        _log.debug("metric.record_failed", exc_info=True)


def record_tool_call(tool: str, status: str, duration_ms: float) -> None:
    attributes = {"tool": tool, "status": status}
    _safe(
        lambda: _histogram("mcp.tool.duration", "MCP tool call latency").record(
            duration_ms, attributes
        )
    )
    _safe(lambda: _counter("mcp.tool.calls", "MCP tool call count").add(1, attributes))


def record_source_call(source: str, status: str, duration_ms: float) -> None:
    attributes = {"source": source, "status": status}
    _safe(
        lambda: _histogram("connector.search.duration", "Job source search latency").record(
            duration_ms, attributes
        )
    )
    _safe(lambda: _counter("connector.search.calls", "Job source search count").add(1, attributes))


def record_cache_event(source: str, result: str) -> None:
    _safe(
        lambda: _counter("connector.cache.events", "Connector cache hit/miss events").add(
            1, {"source": source, "result": result}
        )
    )


def record_llm_call(
    provider: str,
    model: str,
    status: str,
    duration_ms: float,
    tokens_in: int,
    tokens_out: int,
    cost: float,
) -> None:
    attributes = {"provider": provider, "model": model, "status": status}
    _safe(
        lambda: _histogram("llm.call.duration", "LLM call latency").record(duration_ms, attributes)
    )
    _safe(lambda: _counter("llm.calls", "LLM call count").add(1, attributes))
    _safe(
        lambda: _counter("llm.tokens.input", "LLM input tokens").add(
            tokens_in, {"provider": provider, "model": model}
        )
    )
    _safe(
        lambda: _counter("llm.tokens.output", "LLM output tokens").add(
            tokens_out, {"provider": provider, "model": model}
        )
    )
    _safe(
        lambda: _counter("llm.cost.estimated", "Estimated LLM cost", "USD").add(
            cost, {"provider": provider, "model": model}
        )
    )


def record_fallback(provider: str, reason: str) -> None:
    _safe(
        lambda: _counter("llm.fallbacks", "LLM provider fallback count").add(
            1, {"provider": provider, "reason": reason}
        )
    )


def record_db_query(operation: str, duration_ms: float, success: bool = True) -> None:
    _safe(
        lambda: _histogram("db.query.duration", "Database operation latency").record(
            duration_ms, {"operation": operation, "status": "success" if success else "error"}
        )
    )


@contextmanager
def tool_span(name: str, attributes: Mapping[str, Any] | None = None) -> Iterator[trace.Span]:
    """Start a span for one tool call and record its latency."""
    started = time.perf_counter()
    with get_tracer().start_as_current_span(name) as span:
        span.set_attribute("operation.name", name)
        for key, value in (attributes or {}).items():
            span.set_attribute(key, value)
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            record_tool_call(name, "error", (time.perf_counter() - started) * 1000)
            raise
        else:
            span.set_status(Status(StatusCode.OK))
            record_tool_call(name, "success", (time.perf_counter() - started) * 1000)


def inject_trace_context_into_log(record: logging.LogRecord) -> None:
    """Merge the active trace context into a log record's structured fields."""
    fields = getattr(record, "fields", None)
    if not isinstance(fields, dict):
        fields = {}

    context = trace.get_current_span().get_span_context()
    if context.is_valid:
        fields.setdefault("trace_id", format(context.trace_id, "032x"))
        fields.setdefault("span_id", format(context.span_id, "016x"))
    else:
        fields.setdefault("trace_id", None)
    record.fields = fields
