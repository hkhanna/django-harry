"""Reusable Django logging configuration, shared across projects that install harry.

Assign the result of :func:`build_logging_config` to ``LOGGING`` in your project settings::

    from harry.logconfig import build_logging_config

    LOGGING = build_logging_config()

The builder reads ``DJANGO_ENV`` (``local``/``test``/``prod``), ``DJANGO_LOG_LEVEL``, and
``DJANGO_LOG_FORMAT`` so the same code logs human-readable console output in development and
structured JSON in production. In production the JSON goes to stdout, where a host
OpenTelemetry Collector (journald/filelog) forwards it to SigNoz.
"""

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

# Environment profiles and their default level + format.
_VALID_ENVS = ("local", "test", "prod")
_LEVEL_BY_ENV = {"local": "DEBUG", "test": "WARNING", "prod": "INFO"}
_FORMAT_BY_ENV = {"local": "console", "test": "console", "prod": "json"}

# Attributes always present on a LogRecord; anything else came from ``extra={...}``.
_RESERVED_ATTRS = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime"}

# Fields OpenTelemetry's logging instrumentation injects when tracing is active. The JSON
# formatter promotes these to top-level keys so SigNoz can correlate logs with traces.
_OTEL_ATTRS = ("otelTraceID", "otelSpanID", "otelServiceName", "otelTraceSampled")


class JSONFormatter(logging.Formatter):
    """Format log records as one JSON object per line, with no third-party dependencies."""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        """Render the record timestamp as ISO 8601 in UTC."""
        dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
        return dt.strftime(datefmt) if datefmt else dt.isoformat()

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "func": record.funcName,
            "lineno": record.lineno,
            "msg": record.getMessage(),
        }

        # Trace correlation fields, only when OpenTelemetry has populated them.
        if trace_id := getattr(record, "otelTraceID", None):
            payload["trace_id"] = trace_id
        if span_id := getattr(record, "otelSpanID", None):
            payload["span_id"] = span_id
        if service_name := getattr(record, "otelServiceName", None):
            payload["service.name"] = service_name

        # Anything passed via ``logger.info(..., extra={...})``.
        for key, value in record.__dict__.items():
            if key not in _RESERVED_ATTRS and key not in _OTEL_ATTRS:
                payload[key] = value

        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)

        return json.dumps(payload, default=str)


def build_logging_config(
    *,
    env: str | None = None,
    level: str | None = None,
    fmt: str | None = None,
    extra_loggers: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a Django ``LOGGING`` dictConfig with consistent, environment-aware defaults.

    ``env`` selects a profile (``local``/``test``/``prod``); ``level`` and ``fmt``
    (``console``/``json``) override the per-environment defaults. Each argument falls back
    to an environment variable (``DJANGO_ENV``/``DJANGO_LOG_LEVEL``/``DJANGO_LOG_FORMAT``)
    and then to the profile default. ``extra_loggers`` is merged over the built-in loggers
    so projects can add or override entries without losing the defaults.

    ``env`` is read from ``os.environ`` rather than ``django.conf.settings`` because this
    runs while ``settings.py`` is still executing, before Django settings are configured.
    """
    env = (env or os.environ.get("DJANGO_ENV") or "local").lower()
    if env not in _VALID_ENVS:
        raise ValueError(
            f"Unknown env {env!r}; expected one of {', '.join(_VALID_ENVS)}"
        )

    level = (level or os.environ.get("DJANGO_LOG_LEVEL") or _LEVEL_BY_ENV[env]).upper()
    fmt = (fmt or os.environ.get("DJANGO_LOG_FORMAT") or _FORMAT_BY_ENV[env]).lower()
    if fmt not in ("console", "json"):
        raise ValueError(f"Unknown fmt {fmt!r}; expected 'console' or 'json'")

    # django.request logs 4xx as WARNING and 5xx as ERROR. In production demote to
    # ERROR so routine client errors (404s, invalid tokens) aren't noise; keep WARNING
    # in dev/test where seeing them is useful.
    request_level = "ERROR" if env == "prod" else "WARNING"

    loggers: dict[str, Any] = {
        "django": {"level": "INFO", "handlers": ["console"], "propagate": False},
        # Routed explicitly so runserver's request lines use this config's handler
        # rather than the one Django's defaults leave behind on the merge path.
        "django.server": {"level": "INFO", "handlers": ["console"], "propagate": False},
        "django.request": {
            "level": request_level,
            "handlers": ["console"],
            "propagate": False,
        },
        "django.security": {
            "level": "WARNING",
            "handlers": ["console"],
            "propagate": False,
        },
        "harry": {"level": level, "handlers": ["console"], "propagate": False},
    }
    if extra_loggers:
        loggers.update(extra_loggers)

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "require_debug_true": {"()": "django.utils.log.RequireDebugTrue"},
            "require_debug_false": {"()": "django.utils.log.RequireDebugFalse"},
        },
        "formatters": {
            "console": {
                "format": "{asctime} {levelname} {name} {message}",
                "style": "{",
            },
            "json": {"()": "harry.logconfig.JSONFormatter"},
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "formatter": fmt,
            },
        },
        "loggers": loggers,
        "root": {"level": level, "handlers": ["console"]},
    }
