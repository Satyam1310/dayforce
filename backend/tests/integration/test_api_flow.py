"""Full flow against a real Postgres: seed -> list -> propose (mock LLM) -> approve/reject ->
audit. Runs only when DATABASE_URL is set. WARNING: wipes the app tables in that database.

    docker run -d --name dayforce-db -e POSTGRES_USER=app -e POSTGRES_PASSWORD=app \\
      -e POSTGRES_DB=timecards -p 5432:5432 pgvector/pgvector:pg16
    export DATABASE_URL=postgresql://app:app@localhost:5432/timecards
    pytest -m integration
"""

import os

import pytest
from fastapi.testclient import TestClient

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs DATABASE_URL"),
]

EXC = "55555555-5555-4555-8555-00000000000{}".format
PROP = "66666666-6666-4666-8666-00000000000{}".format
MANAGER = "mgr-0001"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("LLM_MOCK_MODE", "ok")
    monkeypatch.setenv("RETRIEVER", "memory")
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


def seed(client, with_history=True):
    r = client.post("/api/v1/seed/load", params={"with_history": with_history})
    assert r.status_code == 200, r.text
    return r.json()


def test_health_ok(client):
    assert client.get("/health").json() == {"status": "ok", "database": "ok"}


def test_seed_and_list(client):
    counts = seed(client)
    assert counts["exceptions"] == 8
    r = client.get("/api/v1/exceptions")
    assert r.status_code == 200
    assert len(r.json()) == 8
    escalated = client.get("/api/v1/exceptions", params={"status": "ESCALATED"}).json()
    assert [e["id"] for e in escalated] == [EXC(5)]


def test_detail_has_timecard_proposal_decision_and_audit(client):
    seed(client)
    body = client.get(f"/api/v1/exceptions/{EXC(8)}").json()
    assert body["exception"]["id"] == EXC(8)
    assert body["timecard"]["id"] == body["exception"]["timecard_id"]
    assert body["proposal"]["status"] == "REJECTED"
    assert body["decision"]["decision"] == "REJECT"
    assert any(e["event_type"] == "DECISION_REJECTED" for e in body["audit_events"])


def test_unknown_exception_is_404(client):
    seed(client)
    r = client.get("/api/v1/exceptions/does-not-exist")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"


def test_propose_then_approve_updates_the_timecard(client):
    seed(client, with_history=False)  # every case OPEN, no proposals yet

    r = client.post(f"/api/v1/exceptions/{EXC(1)}/propose")
    assert r.status_code == 200, r.text
    proposal = r.json()
    assert proposal["status"] == "PENDING_REVIEW"
    assert proposal["source"] == "LLM"
    assert proposal["citations"][0]["clause_id"] == "POL-TA-01"
    assert client.get(f"/api/v1/exceptions/{EXC(1)}").json()["exception"]["status"] == "PROPOSED"

    # a second propose while one is pending is refused
    again = client.post(f"/api/v1/exceptions/{EXC(1)}/propose")
    assert again.status_code == 409

    r = client.post(
        f"/api/v1/proposals/{proposal['id']}/decision",
        json={"decision": "APPROVE", "manager_id": MANAGER, "comment": "ok"},
    )
    assert r.status_code == 200, r.text

    detail = client.get(f"/api/v1/exceptions/{EXC(1)}").json()
    assert detail["exception"]["status"] == "RESOLVED"
    assert detail["proposal"]["status"] == "APPROVED"
    assert [p["type"] for p in detail["timecard"]["punches"]] == ["IN", "OUT"]
    types = [e["event_type"] for e in detail["audit_events"]]
    assert "DECISION_APPROVED" in types and "TIMECARD_UPDATED" in types
    assert "PROPOSAL_CREATED" in types


def test_reject_needs_a_comment_and_changes_nothing(client):
    seed(client)
    url = f"/api/v1/proposals/{PROP(1)}/decision"

    r = client.post(url, json={"decision": "REJECT", "manager_id": MANAGER})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "COMMENT_REQUIRED"

    before = client.get(f"/api/v1/exceptions/{EXC(1)}").json()["timecard"]
    r = client.post(url, json={"decision": "REJECT", "manager_id": MANAGER, "comment": "No."})
    assert r.status_code == 200
    detail = client.get(f"/api/v1/exceptions/{EXC(1)}").json()
    assert detail["timecard"] == before
    assert detail["proposal"]["status"] == "REJECTED"
    assert detail["exception"]["status"] == "OPEN"


def test_cannot_decide_twice(client):
    seed(client)
    url = f"/api/v1/proposals/{PROP(1)}/decision"
    ok = client.post(url, json={"decision": "APPROVE", "manager_id": MANAGER})
    assert ok.status_code == 200
    twice = client.post(url, json={"decision": "APPROVE", "manager_id": MANAGER})
    assert twice.status_code == 409
    assert twice.json()["error"]["code"] == "PROPOSAL_NOT_PENDING"


def test_llm_down_falls_back_to_the_rule_based_fix(client, monkeypatch):
    seed(client, with_history=False)
    monkeypatch.setenv("LLM_MOCK_MODE", "timeout")
    proposal = client.post(f"/api/v1/exceptions/{EXC(1)}/propose").json()
    assert proposal["source"] == "RULES_FALLBACK"
    assert proposal["status"] == "PENDING_REVIEW"


def test_no_baseline_fix_and_llm_down_escalates(client, monkeypatch):
    seed(client, with_history=False)
    monkeypatch.setenv("LLM_MOCK_MODE", "timeout")
    proposal = client.post(f"/api/v1/exceptions/{EXC(6)}/propose").json()  # no schedule
    assert proposal["status"] == "ESCALATED"
    status = client.get(f"/api/v1/exceptions/{EXC(6)}").json()["exception"]["status"]
    assert status == "ESCALATED"


def test_audit_filter(client):
    seed(client)
    events = client.get("/api/v1/audit", params={"entity_id": PROP(7)}).json()
    assert {e["event_type"] for e in events} == {"PROPOSAL_CREATED", "DECISION_APPROVED"}
