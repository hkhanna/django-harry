import asyncio
import logging

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse, StreamingHttpResponse
from django.test import RequestFactory

from harry.middleware import DEFAULT_REQUEST_LOG_IGNORE_PATHS, RequestLogMiddleware

from . import factories


@pytest.fixture
def rf():
    return RequestFactory()


@pytest.fixture
def capture(caplog):
    caplog.set_level(logging.INFO, logger="harry.request")
    return caplog


def run(request, *, status=200, response=None):
    """Send a request through the middleware with a stub view."""
    response = response if response is not None else HttpResponse(status=status)
    middleware = RequestLogMiddleware(lambda r: response)
    return middleware(request)


def access_records(caplog):
    return [r for r in caplog.records if r.name == "harry.request"]


# Access line content


def test_logs_one_info_line_on_harry_request_logger(rf, capture):
    run(rf.get("/invoices/42"))
    (record,) = access_records(capture)
    assert record.levelno == logging.INFO
    assert record.getMessage().startswith("GET /invoices/42 200 (")
    assert record.getMessage().endswith("ms)")


def test_extra_fields(rf, capture):
    run(rf.post("/invoices/"), status=201)
    (record,) = access_records(capture)
    assert record.method == "POST"
    assert record.path == "/invoices/"
    assert record.status == 201
    assert isinstance(record.duration_ms, int)
    assert record.duration_ms >= 0
    assert record.user_id is None


def test_user_id_for_authenticated_user(rf, capture):
    user = factories.user_create()
    request = rf.get("/")
    request.user = user
    run(request)
    (record,) = access_records(capture)
    assert record.user_id == user.pk


def test_user_id_none_for_anonymous_user(rf, capture):
    request = rf.get("/")
    request.user = AnonymousUser()
    run(request)
    (record,) = access_records(capture)
    assert record.user_id is None


def test_user_id_none_without_auth_middleware(rf, capture):
    # No request.user attribute at all must not raise.
    run(rf.get("/"))
    (record,) = access_records(capture)
    assert record.user_id is None


def test_error_responses_are_logged(rf, capture):
    run(rf.get("/boom"), status=500)
    (record,) = access_records(capture)
    assert record.status == 500


def test_streaming_response_logged_at_headers_time(rf, capture):
    response = StreamingHttpResponse(iter([b"a", b"b"]))
    run(rf.get("/export"), response=response)
    (record,) = access_records(capture)
    assert record.status == 200


# Ignore paths


@pytest.mark.parametrize(
    "path",
    [
        "/favicon.ico",
        "/robots.txt",
        "/apple-touch-icon.png",
        "/apple-touch-icon-precomposed.png",
        "/.well-known/security.txt",
        "/.well-known/acme-challenge/token",
    ],
)
def test_default_noise_paths_are_not_logged(rf, capture, path):
    run(rf.get(path))
    assert access_records(capture) == []


def test_healthcheck_paths_are_logged(rf, capture):
    run(rf.get("/health/"))
    (record,) = access_records(capture)
    assert record.path == "/health/"


@pytest.mark.parametrize(
    "path",
    [
        "/favicon.ico/nested",  # exact entries do not match as prefixes
        "/static/robots.txt",  # and never as substrings
        "/well-known",  # prefix entries require the full prefix
    ],
)
def test_matching_is_never_substring(rf, capture, path):
    run(rf.get(path))
    (record,) = access_records(capture)
    assert record.path == path


def test_ignore_paths_overridable_via_setting(rf, capture, settings):
    settings.REQUEST_LOG_IGNORE_PATHS = {"/health/", "/internal/"}
    run(rf.get("/health/"))
    run(rf.get("/internal/probe"))
    run(rf.get("/favicon.ico"))  # default set no longer applies
    (record,) = access_records(capture)
    assert record.path == "/favicon.ico"


def test_default_ignore_set_contents():
    assert DEFAULT_REQUEST_LOG_IGNORE_PATHS == {
        "/favicon.ico",
        "/robots.txt",
        "/apple-touch-icon.png",
        "/apple-touch-icon-precomposed.png",
        "/.well-known/",
    }


# Async


def test_async_get_response(rf, capture):
    async def get_response(request):
        return HttpResponse()

    middleware = RequestLogMiddleware(get_response)
    response = asyncio.run(middleware(rf.get("/async")))
    assert response.status_code == 200
    (record,) = access_records(capture)
    assert record.path == "/async"
    assert record.method == "GET"


def test_middleware_marked_coroutine_only_for_async_chain(rf):
    from asgiref.sync import iscoroutinefunction

    async def async_get_response(request):
        return HttpResponse()

    assert iscoroutinefunction(RequestLogMiddleware(async_get_response))
    assert not iscoroutinefunction(RequestLogMiddleware(lambda r: HttpResponse()))
