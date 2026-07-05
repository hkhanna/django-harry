"""Shared views every production project wires the same way.

Currently just the health endpoint — the target for an external uptime monitor,
answering "is it up, can it reach its database" identically everywhere.
"""

import logging

from django.db import connection
from django.http import HttpRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger(__name__)


@csrf_exempt
def health(request: HttpRequest) -> JsonResponse:
    """Return 200 ``{"status": "ok"}`` if the default database answers ``SELECT 1``.

    Deliberately checks database connectivity only: cache/storage/external-API
    checks make healthchecks flaky and page for dependencies that have their own
    monitoring. No auth and no CSRF, so an uptime monitor can hit it bare; wire
    it explicitly with ``path("health/", health)`` — nothing registers it for you.
    """
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        # The generic detail is deliberate: connection errors carry DSNs and
        # hostnames, and this body is served unauthenticated.
        logger.exception("Healthcheck database connectivity failure")
        return JsonResponse(
            {"status": "error", "detail": "database unavailable"}, status=503
        )
    return JsonResponse({"status": "ok"})
