# ADR 0001: Stdlib logging + JSONFormatter, not structlog

- Status: accepted
- Date: 2026-07-05

## Context

harry's logging standard requires structured JSON events queryable in SigNoz: event
names with fields, trace-id correlation, and protection against application fields
clobbering canonical keys (`level`, `ts`, …) that the log shipper parses. structlog is
the usual library reached for here, and it offers nicer call-site ergonomics
(`logger.info("payment_failed", user_id=...)` instead of `extra={...}`).

## Decision

Stay on stdlib logging with `harry.logconfig.JSONFormatter`. Do not adopt structlog.

`harry.logconfig` already delivers everything the standard needs with zero
dependencies:

- structured JSON events — `extra={}` keys are lifted to top-level JSON keys;
- trace-id promotion — OpenTelemetry's `otelTraceID`/`otelSpanID`/`otelServiceName`
  record attributes are emitted as `trace_id`/`span_id`/`service.name`;
- canonical-field clobber protection — an `extra` key can't overwrite `level`, `ts`,
  or the other fields the shipper depends on.

The `extra={}` syntax is the accepted cost of staying on stdlib.

## Consequences

- No third-party logging dependency; nothing to version-manage or integrate with
  Django's `LOGGING` dictConfig, and third-party libraries' stdlib logging flows
  through the same formatter with no adapter.
- Call sites are slightly noisier than structlog's keyword style.
- Re-opening this decision requires a concrete failure of the current setup, not a
  preference for structlog's ergonomics.
