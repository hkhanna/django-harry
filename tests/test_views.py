"""Tests for the shared /health endpoint.

These go through the test client and ``tests.urls`` (not ``RequestFactory``) so the
full middleware stack — CSRF included — is exercised the way an uptime monitor
would hit the endpoint.
"""

import json
from unittest import mock

import pytest
from django.db import OperationalError
from django.test import Client


@pytest.fixture
def client():
    return Client()


def body(response):
    return json.loads(response.content)


def test_healthy_db_returns_200_ok(client):
    response = client.get("/health/")
    assert response.status_code == 200
    assert response["Content-Type"] == "application/json"
    assert body(response) == {"status": "ok"}


def test_anonymous_access(client):
    # No login, no session, no credentials of any kind.
    response = client.get("/health/")
    assert response.status_code == 200


def test_failing_db_returns_503(client):
    with mock.patch(
        "harry.views.connection.cursor",
        side_effect=OperationalError("connection failed"),
    ):
        response = client.get("/health/")
    assert response.status_code == 503
    assert response["Content-Type"] == "application/json"
    assert body(response) == {"status": "error", "detail": "database unavailable"}


def test_db_error_detail_is_not_leaked(client):
    secret = 'connection to "postgres://harry:hunter2@db.internal:5432/prod" failed'
    with mock.patch(
        "harry.views.connection.cursor", side_effect=OperationalError(secret)
    ):
        response = client.get("/health/")
    assert response.status_code == 503
    assert "hunter2" not in response.content.decode()
    assert "db.internal" not in response.content.decode()


def test_unexpected_error_also_returns_503(client):
    # Not just database errors: anything raised by the check must map to a clean 503.
    with mock.patch("harry.views.connection.cursor", side_effect=RuntimeError("boom")):
        response = client.get("/health/")
    assert response.status_code == 503
    assert body(response)["status"] == "error"


def test_post_without_csrf_token_is_not_rejected():
    client = Client(enforce_csrf_checks=True)
    response = client.post("/health/")
    assert response.status_code == 200
    assert body(response) == {"status": "ok"}
