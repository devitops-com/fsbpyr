"""OpenTelemetry, prometheus_client, structlog, loguru and sentry_sdk, all in memory."""

import os
import sys

os.environ["OTEL_PROPAGATORS"] = "tracecontext,baggage"

import loguru
import sentry_sdk
import structlog
from opentelemetry import context, propagate, trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from sentry_sdk.transport import Transport


def tracing() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("corpus")
    with (
        tracer.start_as_current_span("outer", attributes={"kind": "request"}),
        tracer.start_as_current_span("inner") as inner,
    ):
        inner.set_attribute("items", 3)
    names = {span.context.span_id: span.name for span in exporter.get_finished_spans()}
    for span in exporter.get_finished_spans():
        parent = names.get(span.parent.span_id) if span.parent else None
        print("span", span.name, dict(span.attributes), "parent", parent)
    print("context impl", type(context._RUNTIME_CONTEXT).__name__)
    print("propagator fields", sorted(propagate.get_global_textmap().fields))
    print("tracer provider", type(trace.get_tracer_provider()).__name__)


def metrics() -> None:
    registry = CollectorRegistry()
    Counter("requests", "Requests served", registry=registry).inc(3)
    Gauge("temperature", "Current temperature", registry=registry).set(21.5)
    histogram = Histogram("latency", "Latency", buckets=(0.1, 1.0), registry=registry)
    histogram.observe(0.05)
    histogram.observe(0.5)
    for line in generate_latest(registry).decode().splitlines():
        if not line.startswith("#") and "_created" not in line:
            print("prom", line)


def logging_libraries() -> None:
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.EventRenamer("msg"),
            structlog.processors.JSONRenderer(sort_keys=True),
        ],
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
    )
    structlog.get_logger().info("structlog line", user="ada")
    loguru.logger.remove()
    loguru.logger.add(sys.stdout, format="{level} {message}")
    loguru.logger.warning("loguru line")


class ListTransport(Transport):
    def __init__(self, envelopes: list) -> None:
        super().__init__()
        self.envelopes = envelopes

    def capture_envelope(self, envelope) -> None:
        self.envelopes.append(envelope)


def sentry() -> None:
    envelopes: list = []
    sentry_sdk.init(
        dsn="https://public@example.invalid/1", send_default_pii=False, default_integrations=False,
        transport=ListTransport(envelopes),
    )  # fmt: skip
    sentry_sdk.capture_message("something happened")
    try:
        raise ValueError("boom")
    except ValueError as error:
        sentry_sdk.capture_exception(error)
    sentry_sdk.flush()
    for envelope in envelopes:
        event = envelope.get_event()
        error = (event.get("exception") or {}).get("values", [{}])[0].get("type")
        print("sentry", event.get("level"), event.get("message"), error)


def main() -> None:
    tracing()
    metrics()
    logging_libraries()
    sentry()
