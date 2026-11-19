"""API behaviour that needs no database: error shape, request IDs, metrics, health when the
database is down."""

import psycopg
import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.main import create_app


@pytest.fixture
def client(monkeypatch):
    def no_db(*args, **kwargs):
        raise psycopg.OperationalError("no database in unit tests")

    # every DB connection attempt fails fast
    monkeypatch.setattr(main_module, "connect", no_db)
    monkeypatch.setattr("app.db.connection.connect", no_db)
    monkeypatch.setattr("app.db.connection.psycopg.connect", no_db)
    with TestClient(create_app()) as c:
        yield c


def test_unknown_route_uses_the_error_shape(client):
    r = client.get("/api/v1/nope")
    assert r.status_code == 404
    body = r.json()["error"]
    assert body["code"] == "NOT_FOUND"
    assert body["request_id"] == r.headers["X-Request-ID"]


def test_request_id_is_echoed(client):
    r = client.get("/api/v1/nope", headers={"X-Request-ID": "abc123"})
    assert r.headers["X-Request-ID"] == "abc123"
    assert r.json()["error"]["request_id"] == "abc123"


def test_health_reports_database_down(client):
    r = client.get("/health")
    assert r.status_code == 503
    assert r.json() == {"status": "degraded", "database": "unreachable"}


def test_database_errors_become_503_with_error_shape(client):
    r = client.get("/api/v1/exceptions")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "DB_UNAVAILABLE"


def test_missing_query_param_is_a_422_in_the_error_shape(client):
    r = client.get("/api/v1/policies/search")  # q is required
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_metrics_endpoint(client):
    client.get("/api/v1/nope")
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "http_requests_total" in r.text
