"""Every endpoint against a real Postgres (pgvector image), with the mock LLM.
Runs only when DATABASE_URL is set. WARNING: resets the app tables in that database.

    docker run -d --name dayforce-db -e POSTGRES_USER=app -e POSTGRES_PASSWORD=app \\
      -e POSTGRES_DB=timecards -p 5432:5432 pgvector/pgvector:pg16
    export DATABASE_URL=postgresql://app:app@localhost:5432/timecards
    pytest -m integration
"""

import os

import psycopg
import pytest
from fastapi.testclient import TestClient

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs DATABASE_URL"),
]

CASE = "55555555-5555-4555-8555-00000000000{}".format
PROP = "66666666-6666-4666-8666-00000000000{}".format
MANAGER = {"manager_id": "mgr-0001"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("LLM_MOCK_MODE", "ok")
    monkeypatch.setenv("RETRIEVER", "memory")
    monkeypatch.setenv("ENABLE_DEV_SEED", "true")  # these tests reset the data on purpose
    from app.main import create_app

    with TestClient(create_app()) as c:
        yield c


def seed(client, with_history=True):
    r = client.post("/api/dev/seed", params={"with_history": with_history})
    assert r.status_code == 200, r.text
    return r.json()


# ---- health / seed / read ----------------------------------------------------------------


def test_health_ok(client):
    assert client.get("/health").json() == {"status": "ok", "database": "ok"}


def test_seed_is_404_and_deletes_nothing_when_the_flag_is_off(client, monkeypatch):
    seed(client)
    before = len(client.get("/api/cases").json())
    monkeypatch.delenv("ENABLE_DEV_SEED")
    r = client.post("/api/dev/seed", params={"with_history": False})
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "NOT_FOUND"
    assert len(client.get("/api/cases").json()) == before == 8  # still all 8 cases
    assert client.get(f"/api/cases/{CASE(7)}").json()["exception"]["status"] == "RESOLVED"


def test_seed_works_when_the_flag_is_on(client, monkeypatch):
    monkeypatch.setenv("ENABLE_DEV_SEED", "true")
    r = client.post("/api/dev/seed")
    assert r.status_code == 200
    assert r.json()["exceptions"] == 8


def test_seed_counts(client):
    assert seed(client) == {
        "timecards": 8,
        "exceptions": 8,
        "proposals": 7,
        "decisions": 2,
        "audit_events": 18,
    }
    open_only = seed(client, with_history=False)
    assert open_only["proposals"] == 0 and open_only["exceptions"] == 7


def test_list_cases_and_filters(client):
    seed(client)
    items = client.get("/api/cases").json()
    assert len(items) == 8
    assert {"exception", "proposal"} <= set(items[0])
    escalated = client.get("/api/cases", params={"status": "ESCALATED"}).json()
    assert [i["exception"]["id"] for i in escalated] == [CASE(5)]
    assert escalated[0]["proposal"]["status"] == "ESCALATED"
    conflicts = client.get("/api/cases", params={"type": "SCHEDULE_CONFLICT"}).json()
    assert [i["exception"]["type"] for i in conflicts] == ["SCHEDULE_CONFLICT"]


def test_bad_filter_is_a_clean_422(client):
    r = client.get("/api/cases", params={"status": "NOPE"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_case_detail(client):
    seed(client)
    body = client.get(f"/api/cases/{CASE(8)}").json()
    assert set(body) == {"timecard", "exception", "proposal", "decision", "audit_events"}
    assert body["timecard"]["id"] == body["exception"]["timecard_id"]
    assert body["proposal"]["status"] == "REJECTED"
    assert body["decision"]["decision"] == "REJECT"
    assert any(e["event_type"] == "DECISION_REJECTED" for e in body["audit_events"])


def test_unknown_case_is_404(client):
    seed(client)
    for r in (
        client.get("/api/cases/nope"),
        client.post("/api/cases/nope/propose"),
        client.post("/api/cases/nope/approve", json=MANAGER),
    ):
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "NOT_FOUND"


def test_audit_endpoint(client):
    seed(client)
    events = client.get("/api/audit", params={"entity_id": PROP(7)}).json()
    assert {e["event_type"] for e in events} == {"PROPOSAL_CREATED", "DECISION_APPROVED"}
    everything = client.get("/api/audit").json()
    assert len(everything) == 18
    assert everything[0]["ts"] >= everything[-1]["ts"]  # newest first


# ---- propose -----------------------------------------------------------------------------


def test_propose_saves_the_proposal_and_its_audit_events(client):
    seed(client, with_history=False)
    r = client.post(f"/api/cases/{CASE(1)}/propose")
    assert r.status_code == 200, r.text
    proposal = r.json()
    assert proposal["status"] == "PENDING_REVIEW"
    assert proposal["source"] == "LLM"
    assert proposal["citations"][0]["clause_id"] == "POL-TA-01"

    detail = client.get(f"/api/cases/{CASE(1)}").json()
    assert detail["exception"]["status"] == "PROPOSED"
    assert detail["proposal"]["id"] == proposal["id"]
    types = {e["event_type"] for e in detail["audit_events"]}
    assert {"WORKFLOW_STARTED", "POLICY_RETRIEVED", "LLM_CALLED", "PROPOSAL_CREATED"} <= types
    assert detail["timecard"]["punches"][-1]["type"] == "IN"  # propose never edits a timecard

    again = client.post(f"/api/cases/{CASE(1)}/propose")
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "PROPOSAL_PENDING"


def test_llm_down_falls_back_to_the_rule_based_fix(client, monkeypatch):
    seed(client, with_history=False)
    monkeypatch.setenv("LLM_MOCK_MODE", "timeout")
    proposal = client.post(f"/api/cases/{CASE(1)}/propose").json()
    assert (proposal["source"], proposal["status"]) == ("RULES_FALLBACK", "PENDING_REVIEW")


def test_no_baseline_fix_and_llm_down_escalates(client, monkeypatch):
    seed(client, with_history=False)
    monkeypatch.setenv("LLM_MOCK_MODE", "timeout")
    proposal = client.post(f"/api/cases/{CASE(6)}/propose").json()  # no schedule that day
    assert proposal["status"] == "ESCALATED"
    assert client.get(f"/api/cases/{CASE(6)}").json()["exception"]["status"] == "ESCALATED"


def test_cannot_propose_on_a_resolved_case(client):
    seed(client)
    r = client.post(f"/api/cases/{CASE(7)}/propose")
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CASE_RESOLVED"


# ---- approve / reject --------------------------------------------------------------------


def test_approve_applies_the_fix_once(client):
    seed(client)
    r = client.post(f"/api/cases/{CASE(1)}/approve", json={**MANAGER, "comment": "ok"})
    assert r.status_code == 200, r.text
    detail = r.json()  # approve returns the updated case
    assert detail["exception"]["status"] == "RESOLVED"
    assert detail["proposal"]["status"] == "APPROVED"
    assert detail["decision"]["decision"] == "APPROVE"
    assert [p["type"] for p in detail["timecard"]["punches"]] == ["IN", "OUT"]
    types = [e["event_type"] for e in detail["audit_events"]]
    assert "DECISION_APPROVED" in types and "TIMECARD_UPDATED" in types

    twice = client.post(f"/api/cases/{CASE(1)}/approve", json=MANAGER)
    assert twice.status_code == 409
    assert twice.json()["error"]["code"] == "ALREADY_DECIDED"
    punches = client.get(f"/api/cases/{CASE(1)}").json()["timecard"]["punches"]
    assert len(punches) == 2  # the OUT punch was added once, not twice


def test_escalated_case_cannot_be_approved(client):
    seed(client)
    r = client.post(f"/api/cases/{CASE(5)}/approve", json=MANAGER)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "CASE_ESCALATED"


def test_case_without_proposal_cannot_be_approved(client):
    seed(client, with_history=False)
    r = client.post(f"/api/cases/{CASE(1)}/approve", json=MANAGER)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "NO_PROPOSAL"


def test_approve_needs_a_manager_id(client):
    seed(client)
    r = client.post(f"/api/cases/{CASE(1)}/approve", json={})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "VALIDATION_ERROR"


def test_reject_needs_a_comment_and_changes_nothing(client):
    seed(client)
    before = client.get(f"/api/cases/{CASE(1)}").json()["timecard"]

    for body in (MANAGER, {**MANAGER, "comment": "   "}):
        r = client.post(f"/api/cases/{CASE(1)}/reject", json=body)
        assert r.status_code == 422

    r = client.post(f"/api/cases/{CASE(1)}/reject", json={**MANAGER, "comment": "Not right."})
    assert r.status_code == 200
    detail = r.json()
    assert detail["timecard"] == before
    assert detail["proposal"]["status"] == "REJECTED"
    assert detail["exception"]["status"] == "OPEN"
    assert detail["decision"]["comment"] == "Not right."


def test_rejected_case_can_get_a_new_proposal(client):
    seed(client)
    r = client.post(f"/api/cases/{CASE(8)}/propose")  # case 8 was rejected in the seed
    assert r.status_code == 200
    assert client.get(f"/api/cases/{CASE(8)}").json()["proposal"]["id"] == r.json()["id"]


# ---- audit trail is append-only ----------------------------------------------------------


def test_audit_events_cannot_be_edited_or_deleted(client):
    seed(client)
    with psycopg.connect(os.environ["DATABASE_URL"].replace("+psycopg", "")) as conn:
        for sql in ("UPDATE audit_events SET entity_id = 'x'", "DELETE FROM audit_events"):
            with pytest.raises(psycopg.errors.RaiseException):
                conn.execute(sql)
            conn.rollback()
        count = conn.execute("SELECT count(*) FROM audit_events").fetchone()[0]
    assert count == 18
