"""OpenTelemetry tracing. Exports via OTLP when JOLTIQ_OTEL_EXPORTER_ENDPOINT is set; otherwise a no-op
tracer provider is installed (spans still carry request correlation for log linking)."""
from __future__ import annotations

import logging

from ..config import get_settings

log = logging.getLogger("joltiq.telemetry")


def setup_tracing(app) -> None:  # noqa: ANN001
    s = get_settings()
    try:
        from opentelemetry import trace
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider

        provider = TracerProvider(resource=Resource.create({"service.name": "joltiq-api", "deployment.environment": s.env}))
        if s.otel_exporter_endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
                from opentelemetry.sdk.trace.export import BatchSpanProcessor

                provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=s.otel_exporter_endpoint)))
            except ImportError:
                log.warning("OTLP exporter package not installed; traces not exported")
        trace.set_tracer_provider(provider)
        FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz,metrics")
    except Exception:  # noqa: BLE001
        log.exception("tracing setup failed (continuing without tracing)")


def tracer():
    from opentelemetry import trace

    return trace.get_tracer("joltiq")
