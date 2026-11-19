"""A write must be committed BEFORE the response is sent.

Newer FastAPI runs the code after `yield` in a dependency once the response has gone out. If
the commit lived there, a client could get "201 Created" and have its very next request
answered "404 Not Found" because the commit had not happened yet. A real server and a commit
made deliberately slow show it: the client reads straight after each write.
Runs only when DATABASE_URL is set.
"""

import os
import socket
import threading
import time

import httpx
import psycopg
import pytest
import uvicorn
from psycopg.rows import dict_row

from tests.sample_timecards import missed_punch

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="needs DATABASE_URL"),
]

COMMIT_DELAY = 0.5  # seconds; far longer than the client needs to send its next request
MANAGER = {"manager_id": "mgr-commit-test"}


class SlowCommitConnection(psycopg.Connection):
    slow = False

    def commit(self):
        if SlowCommitConnection.slow:
            time.sleep(COMMIT_DELAY)
        super().commit()


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("LLM_MOCK_MODE", "ok")
    monkeypatch.setenv("RETRIEVER", "memory")
    import app.db.connection as connection
    from app.main import create_app

    def slow_connect():
        return SlowCommitConnection.connect(
            connection.database_dsn(), row_factory=dict_row, connect_timeout=5
        )

    monkeypatch.setattr(connection, "connect", slow_connect)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(
        uvicorn.Config(create_app(), host="127.0.0.1", port=port, log_level="warning")
    )
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.1)
    assert srv.started, "the test server did not start"
    SlowCommitConnection.slow = True  # after startup, so only request commits are slow
    try:
        yield httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=30)
    finally:
        SlowCommitConnection.slow = False
        srv.should_exit = True
        thread.join(timeout=10)


def test_every_write_is_visible_to_the_very_next_request(server):
    tc = missed_punch()
    r = server.post("/api/analysis/run", json={"timecards": [tc.model_dump(mode="json")]})
    assert r.status_code == 201, r.text
    case_id = r.json()["cases"][0]["exception"]["id"]

    # analysis/run: the case and the timecard can be read straight away
    assert server.get(f"/api/cases/{case_id}").status_code == 200
    assert server.get("/api/timecards", params={"employee_id": tc.employee_id}).json()

    # propose: the proposal can be read straight away
    assert server.post(f"/api/cases/{case_id}/propose").status_code == 200
    assert server.get(f"/api/cases/{case_id}").json()["proposal"] is not None

    # approve: the case is already RESOLVED and the punch already added
    assert server.post(f"/api/cases/{case_id}/approve", json=MANAGER).status_code == 200
    detail = server.get(f"/api/cases/{case_id}").json()
    assert detail["exception"]["status"] == "RESOLVED"
    assert [p["type"] for p in detail["timecard"]["punches"]] == ["IN", "OUT"]


def test_reject_is_visible_to_the_very_next_request(server):
    tc = missed_punch()
    r = server.post("/api/analysis/run", json={"timecards": [tc.model_dump(mode="json")]})
    assert r.status_code == 201, r.text
    case_id = r.json()["cases"][0]["exception"]["id"]
    server.post(f"/api/cases/{case_id}/propose")
    body = {**MANAGER, "comment": "not right"}
    assert server.post(f"/api/cases/{case_id}/reject", json=body).status_code == 200
    assert server.get(f"/api/cases/{case_id}").json()["decision"]["decision"] == "REJECT"
