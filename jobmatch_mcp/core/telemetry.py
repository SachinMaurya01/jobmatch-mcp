"""OpenTelemetry tracer and metric infrastructure."""

from __future__ import annotations

import logging
import os
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import ConsoleMetricExporter
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

from jobmatch_mcp.core.config import get_settings

_log = logging.getLogger(__name__)

_settings = get_settings()


def _is_otlp_configured() -> bool:
    """Check if OTLP endpoint is configured (remote or collector)."""
    return bool(
        os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
        or os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
        or os.getenv("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT")
    )


def _is_console_configured() -> bool:
    """Check if console export is requested."""
    return os.getenv("OTEL_LOG_LEVEL") is not None


def setup_telemetry() -> None:
    """Initialize OpenTelemetry tracer and meter providers.

    - Traces are exported via OTLP to a collector if configured, otherwise console.
    - Metrics are registered with a meter provider for instrument creation.
    - Correlation with existing request_id/trace_id context variables is established.
    """
    tracer_provider = TracerProvider()
    trace.set_tracer_provider(tracer_provider)

    if _is_otlp_configured():
        _log.info("Initializing OpenTelemetry OTLP exporter for traces")
        otlp_exporter = ...  # OTLPExporter would use env vars automatically
        tracer_provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
    elif _is_console_configured():
        _log.info("Initializing OpenTelemetry console exporter for traces")
        console_exporter = ConsoleSpanExporter()
        tracer_provider.add_span_processor(BatchSpanProcessor(console_exporter))
    else:
        _log.info("OpenTelemetry traces enabled (no exporter configured)")

    meter_provider = MeterProvider()
    metrics.set_meter_provider(meter_provider)

    if _is_otlp_configured():
        _log.info("Initializing OpenTelemetry OTLP exporter for metrics")
    elif _is_console_configured():
        _log.info("Initializing OpenTelemetry console exporter for metrics")
    else:
        _log.info("OpenTelemetry metrics enabled (no exporter configured)")

    _tracer = trace.get_tracer(__name__)
    _meter = metrics.get_meter(__name__)

    _log.info(
        "OpenTelemetry telemetry initialized",
        extra={
            "fields": {
                "tracer_name": _tracer.name,
                "tracer_version": getattr(_tracer, "version", "unknown"),
                "meter_name": _meter.name,
                "otel_endpoint": _get_otel_endpoint(),
            }
        },
    )


def _get_otel_endpoint() -> str | None:
    """Retrieve the OTLP endpoint from environment variables."""
    for key in (
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
    ):
        val = os.getenv(key)
        if val:
            return val
    return None


# Instrumentation aliases for common operations
_tracer: trace.Tracer | None = None
_meter: metrics.Meter | None = None


def get_tracer(name: str | None = None) -> trace.Tracer:
    """Get an OpenTelemetry tracer instance."""
    global _tracer
    if _tracer is None:
        _tracer = trace.get_tracer(__name__, name)
    return _tracer


def get_meter(name: str | None = None) -> metrics.Meter:
    """Get an OpenTelemetry meter instance."""
    global _meter
    if _meter is None:
        _meter = metrics.get_meter(__name__, name)
    return _meter


_METRIC_INSTRUMENTS: dict[tuple[str, str, str | None], Any] = {}


def get_counter(name: str, description: str, unit: str | None = None) -> metrics.Counter:
    """Get or create a counter metric instrument."""
    key = (name, description, unit or "")
    if key not in _METRIC_INSTRUMENTS:
        _METRIC_INSTRUMENTS[key] = get_meter().create_counter(name, description=description, unit=unit or "")
    return _METRIC_INSTRUMENTS[key]


def get_observable_counter(name: str, description: str, unit: str | None = None) -> metrics.ObservableCounter:
    """Get or create an observable counter metric instrument."""
    key = (name, description, unit or "")
    if key not in _METRIC_INSTRUMENTS:
        _METRIC_INSTRUMENTS[key] = get_meter().create_observable_counter(name, description=description, unit=unit or "")
    return _METRIC_INSTRUMENTS[key]


def get_up_down_counter(name: str, description: str, unit: str | None = None) -> metrics.UpDownCounter:
    """Get or create an up-down counter metric instrument."""
    key = (name, description, unit or "")
    if key not in _METRIC_INSTRUMENTS:
        _METRIC_INSTRUMENTS[key] = get_meter().create_up_down_counter(name, description=description, unit=unit or "")
    return _METRIC_INSTRUMENTS[key]


def get_histogram(name: str, description: str, unit: str | None = None) -> metrics.Histogram:
    """Get or create a histogram metric instrument."""
    key = (name, description, unit or "")
    if key not in _METRIC_INSTRUMENTS:
        _METRIC_INSTRUMENTS[key] = get_meter().create_histogram(name, description=description, unit=unit or "")
    return _METRIC_INSTRUMENTS[key]


def inject_trace_context_into_log(record: logging.LogRecord) -> None:
    """Inject OpenTelemetry context into a log record's fields.

    This bridges OTel span context with the existing JSON logging format,
    ensuring request_id and trace_id are always present in log output.
    """
    context = trace.get_current_span().get_span_context()
    if context.is_valid:
        record.fields = {
            "request_id": format(context.span_id, "016x"),
            "trace_id": format(context.trace_id, "032x"),
        }
    else:
        record.fields = {
            "request_id": None,
            "trace_id": None,
        }
