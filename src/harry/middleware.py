"""Request access logging middleware.

Adds one structured access line per request. Correlation with traces is not handled
here: when OpenTelemetry's logging instrumentation is active it stamps trace ids onto
every log record, and :class:`harry.logconfig.JSONFormatter` promotes them to
``trace_id``/``span_id`` JSON keys. This middleware deliberately mints no request id.
"""

import logging
import time
from collections.abc import Awaitable, Callable

from asgiref.sync import iscoroutinefunction, markcoroutinefunction
from django.conf import settings
from django.http import HttpRequest
from django.http.response import HttpResponseBase

logger = logging.getLogger("harry.request")

# Endpoints that generate traffic no one investigates. Healthcheck paths are absent on
# purpose: their access lines are a useful status/latency heartbeat in SigNoz.
DEFAULT_REQUEST_LOG_IGNORE_PATHS = frozenset(
    {
        "/favicon.ico",
        "/robots.txt",
        "/apple-touch-icon.png",
        "/apple-touch-icon-precomposed.png",
        "/.well-known/",
    }
)

GetResponse = Callable[[HttpRequest], HttpResponseBase]
AsyncGetResponse = Callable[[HttpRequest], Awaitable[HttpResponseBase]]


class RequestLogMiddleware:
    """Log one access line per request at INFO on the ``harry.request`` logger.

    Presence in ``MIDDLEWARE`` is the only switch. Place it after
    ``AuthenticationMiddleware`` so ``request.user`` is populated. To silence access
    lines in an environment without touching ``MIDDLEWARE``, raise the
    ``harry.request`` logger's level via ``build_logging_config(extra_loggers=...)``.

    The ``REQUEST_LOG_IGNORE_PATHS`` setting (default
    :data:`DEFAULT_REQUEST_LOG_IGNORE_PATHS`) suppresses lines for noise endpoints.
    Entries ending in ``/`` match as path prefixes; all other entries must match the
    path exactly — never by substring, so an entry cannot silently swallow a real
    route.

    ``duration_ms`` measures middleware entry to response return; for streaming
    responses that is time-to-headers, not time-to-last-byte.
    """

    sync_capable = True
    async_capable = True

    def __init__(self, get_response: GetResponse | AsyncGetResponse) -> None:
        self.get_response = get_response
        if iscoroutinefunction(get_response):
            markcoroutinefunction(self)

    def __call__(
        self, request: HttpRequest
    ) -> HttpResponseBase | Awaitable[HttpResponseBase]:
        if iscoroutinefunction(self.get_response):
            return self._acall(request)
        start = time.monotonic()
        response = self.get_response(request)
        self._log(request, response, start)  # type: ignore[arg-type]
        return response  # type: ignore[return-value]

    async def _acall(self, request: HttpRequest) -> HttpResponseBase:
        start = time.monotonic()
        response = await self.get_response(request)  # type: ignore[misc]
        self._log(request, response, start)
        return response

    def _log(
        self, request: HttpRequest, response: HttpResponseBase, start: float
    ) -> None:
        if self._ignored(request.path):
            return
        duration_ms = round((time.monotonic() - start) * 1000)
        user = getattr(request, "user", None)
        user_id = user.pk if user is not None and user.is_authenticated else None
        logger.info(
            "%s %s %s (%dms)",
            request.method,
            request.path,
            response.status_code,
            duration_ms,
            extra={
                "method": request.method,
                "path": request.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
                "user_id": user_id,
            },
        )

    @staticmethod
    def _ignored(path: str) -> bool:
        ignore = getattr(
            settings, "REQUEST_LOG_IGNORE_PATHS", DEFAULT_REQUEST_LOG_IGNORE_PATHS
        )
        return any(
            path.startswith(entry) if entry.endswith("/") else path == entry
            for entry in ignore
        )
