"""The analysis, timecards and policy-search endpoints: the parts that need no database."""

import pytest
from fastapi.testclient import TestClient

from app.api.routes import MAX_ANALYSIS_TIMECARDS
from app.db.connection import get_conn
from app.main import create_app
from scripts.export_openapi import render
from tests.sample_timecards import clean_day, missed_punch


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("RETRIEVER", "memory")
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def client_no_db(client):
    """Same app, but the connection dependency hands out None: for checks that must fail
    before the database is touched (a request that touches it would crash)."""
    client.app.dependency_overrides[get_conn] = lambda: None
    yield client
    client.app.dependency_overrides.clear()


def dump(tcs):
    return [t.model_dump(mode="json") for t in tcs]


def error_code(r):
    return r.json()["error"]["code"]


def test_analysis_needs_at_least_one_timecard(client_no_db):
    r = client_no_db.post("/api/analysis/run", json={"timecards": []})
    assert r.status_code == 422 and error_code(r) == "VALIDATION_ERROR"


def test_analysis_limits_how_many_timecards(client_no_db):
    many = dump([clean_day() for _ in range(MAX_ANALYSIS_TIMECARDS + 1)])
    r = client_no_db.post("/api/analysis/run", json={"timecards": many})
    assert r.status_code == 422 and error_code(r) == "VALIDATION_ERROR"


def test_analysis_rejects_a_malformed_timecard(client_no_db):
    bad = dump([missed_punch()])
    bad[0]["punches"][0]["type"] = "TELEPORT"
    r = client_no_db.post("/api/analysis/run", json={"timecards": bad})
    assert r.status_code == 422 and error_code(r) == "VALIDATION_ERROR"


def test_analysis_rejects_duplicate_ids_in_one_request(client_no_db):
    tc = missed_punch()
    r = client_no_db.post("/api/analysis/run", json={"timecards": dump([tc, tc])})
    assert r.status_code == 422 and error_code(r) == "DUPLICATE_TIMECARD_IN_REQUEST"


def test_analysis_rejects_punches_of_another_employee(client_no_db):
    tc = missed_punch()
    other = tc.model_copy(
        update={"punches": [tc.punches[0].model_copy(update={"employee_id": "someone-else"})]}
    )
    r = client_no_db.post("/api/analysis/run", json={"timecards": dump([other])})
    assert r.status_code == 422 and error_code(r) == "EMPLOYEE_MISMATCH"


def test_valid_analysis_without_a_database_is_a_clean_503(client):
    r = client.post("/api/analysis/run", json={"timecards": dump([missed_punch()])})
    assert r.status_code == 503 and error_code(r) == "DB_NOT_CONFIGURED"


def test_timecards_rejects_a_bad_week_and_limit(client_no_db):
    assert client_no_db.get("/api/timecards", params={"week": "not-a-date"}).status_code == 422
    assert client_no_db.get("/api/timecards", params={"limit": 0}).status_code == 422


def test_policy_search_returns_the_matching_clause(client):
    r = client.get("/api/policies/search", params={"q": "clock-in without clock-out", "k": 2})
    assert r.status_code == 200
    body = r.json()
    assert len(body) <= 2
    assert body[0]["clause_id"] == "POL-TA-01"
    assert set(body[0]) == {"clause_id", "quote", "score"}


def test_policy_search_validates_its_input(client):
    assert client.get("/api/policies/search").status_code == 422
    assert client.get("/api/policies/search", params={"q": ""}).status_code == 422
    assert client.get("/api/policies/search", params={"q": "x", "k": 99}).status_code == 422


def test_openapi_lists_the_new_endpoints():
    import yaml

    paths = yaml.safe_load(render())["paths"]
    assert {"/api/analysis/run", "/api/timecards", "/api/policies/search"} <= set(paths)
    assert "post" in paths["/api/analysis/run"]


def test_new_metrics_are_exposed(client):
    text = client.get("/metrics").text
    for name in (
        "validator_rejections_total",
        "escalations_total",
        "llm_errors_total",
        "llm_request_duration_seconds",
        "analysis_runs_total",
    ):
        assert name in text
