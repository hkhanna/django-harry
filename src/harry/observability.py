"""One-call OpenTelemetry initialization for projects that install ``harry[otel]``.

Call :func:`init_observability` from your project's ``settings.py``::

    from harry.observability import init_observability

    init_observability()  # OTEL_SERVICE_NAME + OTEL_EXPORTER_OTLP_* from env

Initialization is programmatic rather than via the ``opentelemetry-instrument``
wrapper: the same call works identically under gunicorn, ``manage.py`` commands, and
any future task runner without changing how processes are launched, so there is only
one documented path.

``service_name`` is the only parameter. Everything else — endpoint, protocol,
headers, resource attributes — comes from the standard ``OTEL_*`` environment
variables. The OpenTelemetry packages are an optional extra: plain ``harry`` installs
none of them, and this module only imports them inside :func:`init_observability`.
"""

import importlib
import importlib.util
import os
from typing import Any

__all__ = ["init_observability"]

_INSTALL_HINT = (
    "init_observability() requires the OpenTelemetry packages, which harry does not "
    "install by default. Install the extra: "
    "uv add 'harry[otel] @ git+https://github.com/hkhanna/django-harry'"
)

# Instrumentations enabled when their target library is importable. The instrumentor
# packages themselves always arrive with the extra; the libraries they patch may not
# be present (psycopg and requests are not dependencies of harry).
_INSTRUMENTORS: tuple[tuple[str, str, str], ...] = (
    ("django", "opentelemetry.instrumentation.django", "DjangoInstrumentor"),
    ("psycopg", "opentelemetry.instrumentation.psycopg", "PsycopgInstrumentor"),
    ("requests", "opentelemetry.instrumentation.requests", "RequestsInstrumentor"),
)

_initialized = False


def _build_span_exporter() -> Any:
    """Construct an OTLP span exporter honoring ``OTEL_EXPORTER_OTLP_PROTOCOL``.

    The exporter classes read endpoint, headers, certificates, and timeout from the
    ``OTEL_EXPORTER_OTLP_*`` environment variables themselves; only the choice
    between the gRPC and HTTP wire protocols has to be made here, because they are
    different classes. Defaults to gRPC, the OpenTelemetry specification's default.
    """
    protocol = (
        os.environ.get("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL")
        or os.environ.get("OTEL_EXPORTER_OTLP_PROTOCOL")
        or "grpc"
    )
    if protocol.startswith("http"):
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter as HTTPSpanExporter,
        )

        return HTTPSpanExporter()
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
        OTLPSpanExporter,
    )

    return OTLPSpanExporter()


def _enable_instrumentations() -> None:
    """Instrument every library from ``_INSTRUMENTORS`` that is importable."""
    for library, module_path, class_name in _INSTRUMENTORS:
        if importlib.util.find_spec(library) is None:
            continue
        instrumentor = getattr(importlib.import_module(module_path), class_name)()
        if not instrumentor.is_instrumented_by_opentelemetry:
            instrumentor.instrument()


def init_observability(service_name: str | None = None) -> None:
    """Set up OpenTelemetry tracing and log↔trace correlation for this process.

    Configures a ``TracerProvider`` exporting spans via OTLP, instruments Django,
    psycopg, and requests (each only if importable), and enables the logging
    instrumentation that stamps ``otelTraceID``/``otelSpanID``/``otelServiceName``
    onto every ``LogRecord`` — the attributes :class:`harry.logconfig.JSONFormatter`
    promotes to ``trace_id``/``span_id``/``service.name``.

    ``service_name`` overrides the ``OTEL_SERVICE_NAME`` environment variable; the
    exporter endpoint, protocol, and headers come from the standard
    ``OTEL_EXPORTER_OTLP_*`` environment variables. Calling it more than once is a
    no-op, so it is safe under Django's autoreloader. Raises :class:`ImportError`
    with installation instructions when the ``harry[otel]`` extra is missing.
    """
    global _initialized
    if _initialized:
        return

    try:
        from opentelemetry import trace
        from opentelemetry.instrumentation.logging import LoggingInstrumentor
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as exc:
        raise ImportError(_INSTALL_HINT) from exc

    # Resource.create() merges OTEL_SERVICE_NAME / OTEL_RESOURCE_ATTRIBUTES from the
    # environment; an explicit service_name argument wins over both.
    resource = Resource.create({SERVICE_NAME: service_name} if service_name else None)
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(_build_span_exporter()))
    trace.set_tracer_provider(provider)

    _enable_instrumentations()

    # After set_tracer_provider: the instrumentor reads service.name from the
    # active provider's resource when stamping otelServiceName.
    # set_logging_format=False keeps it from calling logging.basicConfig(), which
    # would fight harry.logconfig's dictConfig; inject_trace_context is what stamps
    # otelTraceID/otelSpanID/otelServiceName onto every LogRecord.
    logging_instrumentor = LoggingInstrumentor()
    if not logging_instrumentor.is_instrumented_by_opentelemetry:
        logging_instrumentor.instrument(
            set_logging_format=False, inject_trace_context=True
        )

    _initialized = True
