import importlib.util
import json
import logging
import sys

import pytest

from harry import observability
from harry.logconfig import JSONFormatter

_OTEL_ENV_VARS = (
    "OTEL_SERVICE_NAME",
    "OTEL_RESOURCE_ATTRIBUTES",
    "OTEL_EXPORTER_OTLP_PROTOCOL",
    "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL",
)


@pytest.fixture
def otel(monkeypatch):
    """Fresh, network-free OpenTelemetry state around each test.

    Swaps the OTLP exporter for an in-memory one (no collector needed), resets the
    module's initialized flag, clears the OTEL_* env vars, and on teardown
    uninstruments everything and clears the global tracer provider so tests can't
    leak instrumentation into each other.
    """
    sdk_trace = pytest.importorskip("opentelemetry.sdk.trace")
    from opentelemetry import trace
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )
    from opentelemetry.util._once import Once

    for var in _OTEL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)

    exporter = InMemorySpanExporter()
    monkeypatch.setattr(observability, "_build_span_exporter", lambda: exporter)
    monkeypatch.setattr(observability, "_initialized", False)

    yield exporter

    from opentelemetry.instrumentation.django import DjangoInstrumentor
    from opentelemetry.instrumentation.logging import LoggingInstrumentor
    from opentelemetry.instrumentation.requests import RequestsInstrumentor

    for instrumentor in (
        DjangoInstrumentor(),
        RequestsInstrumentor(),
        LoggingInstrumentor(),
    ):
        if instrumentor.is_instrumented_by_opentelemetry:
            instrumentor.uninstrument()

    provider = trace.get_tracer_provider()
    if isinstance(provider, sdk_trace.TracerProvider):
        provider.shutdown()
    trace._TRACER_PROVIDER = None
    trace._TRACER_PROVIDER_SET_ONCE = Once()


# Missing extra


def test_missing_extra_raises_actionable_importerror(monkeypatch):
    """Without harry[otel], init raises an ImportError naming the extra to install."""
    monkeypatch.setattr(observability, "_initialized", False)
    # A None entry in sys.modules makes ``import opentelemetry`` raise ImportError,
    # simulating the extra being absent even when it is installed here.
    monkeypatch.setitem(sys.modules, "opentelemetry", None)
    with pytest.raises(ImportError, match=r"harry\[otel\]"):
        observability.init_observability()
    # The failed call must not latch the initialized flag; a corrected environment
    # can retry.
    assert observability._initialized is False


# Initialization


def test_init_sets_sdk_tracer_provider(otel):
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider

    observability.init_observability()
    assert isinstance(trace.get_tracer_provider(), TracerProvider)


def test_service_name_argument_beats_env(otel, monkeypatch):
    from opentelemetry import trace

    monkeypatch.setenv("OTEL_SERVICE_NAME", "from-env")
    observability.init_observability(service_name="from-arg")
    provider = trace.get_tracer_provider()
    assert provider.resource.attributes["service.name"] == "from-arg"


def test_service_name_from_env(otel, monkeypatch):
    from opentelemetry import trace

    monkeypatch.setenv("OTEL_SERVICE_NAME", "from-env")
    observability.init_observability()
    provider = trace.get_tracer_provider()
    assert provider.resource.attributes["service.name"] == "from-env"


def test_instruments_only_importable_libraries(otel):
    """Instrumentations whose target library is absent are skipped, not errors.

    psycopg is not installed in the test environment, so init succeeding at all
    proves the guard works (its instrumentor module cannot even be imported when
    psycopg is missing). The importable targets must all be instrumented.
    """
    observability.init_observability()
    instrumented = []
    for library, module_path, class_name in observability._INSTRUMENTORS:
        if importlib.util.find_spec(library) is None:
            continue
        instrumentor = getattr(importlib.import_module(module_path), class_name)()
        assert instrumentor.is_instrumented_by_opentelemetry
        instrumented.append(library)
    assert "django" in instrumented


# Idempotency


def test_init_twice_does_not_double_instrument(otel):
    from django.conf import settings
    from opentelemetry import trace

    observability.init_observability()
    provider = trace.get_tracer_provider()
    record_factory = logging.getLogRecordFactory()

    observability.init_observability()

    assert trace.get_tracer_provider() is provider
    assert logging.getLogRecordFactory() is record_factory
    otel_middleware = [m for m in settings.MIDDLEWARE if "opentelemetry" in m]
    assert len(otel_middleware) == 1


# Exporter protocol selection


def test_exporter_defaults_to_grpc(monkeypatch):
    grpc_exporter = pytest.importorskip(
        "opentelemetry.exporter.otlp.proto.grpc.trace_exporter"
    )
    for var in _OTEL_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    exporter = observability._build_span_exporter()
    try:
        assert isinstance(exporter, grpc_exporter.OTLPSpanExporter)
    finally:
        exporter.shutdown()


def test_exporter_honors_http_protocol_env(monkeypatch):
    http_exporter = pytest.importorskip(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter"
    )
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")
    exporter = observability._build_span_exporter()
    try:
        assert isinstance(exporter, http_exporter.OTLPSpanExporter)
    finally:
        exporter.shutdown()


def test_traces_protocol_env_beats_general_protocol_env(monkeypatch):
    http_exporter = pytest.importorskip(
        "opentelemetry.exporter.otlp.proto.http.trace_exporter"
    )
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL", "http/protobuf")
    exporter = observability._build_span_exporter()
    try:
        assert isinstance(exporter, http_exporter.OTLPSpanExporter)
    finally:
        exporter.shutdown()


# Integration: log↔trace correlation through the existing JSONFormatter


def test_log_lines_carry_ids_of_exported_spans(otel):
    """LoggingInstrumentor stamps otel* attrs that JSONFormatter promotes.

    Inside a span, a log record picks up ``otelTraceID``/``otelSpanID``/
    ``otelServiceName``, and the existing formatter emits them as ``trace_id``/
    ``span_id``/``service.name`` matching the span actually exported.
    """
    from opentelemetry import trace

    observability.init_observability(service_name="test-svc")

    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("harry.test_observability")
    handler = Capture()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        tracer = trace.get_tracer(__name__)
        with tracer.start_as_current_span("unit-of-work"):
            logger.info("inside span")
    finally:
        logger.removeHandler(handler)

    trace.get_tracer_provider().force_flush()
    spans = otel.get_finished_spans()
    assert len(spans) == 1
    span_context = spans[0].get_span_context()

    data = json.loads(JSONFormatter().format(records[0]))
    assert data["trace_id"] == format(span_context.trace_id, "032x")
    assert data["span_id"] == format(span_context.span_id, "016x")
    assert data["service.name"] == "test-svc"


def test_log_lines_outside_spans_have_no_trace_ids(otel):
    observability.init_observability()

    records = []

    class Capture(logging.Handler):
        def emit(self, record):
            records.append(record)

    logger = logging.getLogger("harry.test_observability")
    handler = Capture()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        logger.info("outside any span")
    finally:
        logger.removeHandler(handler)

    data = json.loads(JSONFormatter().format(records[0]))
    assert "trace_id" not in data
    assert "span_id" not in data
