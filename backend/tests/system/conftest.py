"""System tests: the real API (uvicorn, a separate process) over HTTP, against a real
Postgres with pgvector. Nothing is mocked except the LLM (LLM_PROVIDER=mock) and never the
network call to a real model.

The database:
  * DATABASE_URL is set  -> that database is used (CI service container, or your own).
  * otherwise            -> a pgvector/pgvector:pg16 container is started for the run and
                            removed afterwards. Needs Docker.
These tests are NOT skipped when no database is available: they fail, loudly. A system test
that quietly skips proves nothing.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import psycopg
import pytest

BACKEND = Path(__file__).resolve().parents[2]
IMAGE = "pgvector/pgvector:pg16"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_db(dsn: str, seconds: float = 60) -> None:
    deadline = time.time() + seconds
    while True:
        try:
            with psycopg.connect(dsn, connect_timeout=2) as conn:
                conn.execute("SELECT 1")
            return
        except psycopg.OperationalError:
            if time.time() > deadline:
                pytest.fail(f"the database at {dsn.split('@')[-1]} did not become ready")
            time.sleep(0.5)


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    url = os.environ.get("DATABASE_URL")
    if url:
        dsn = url.replace("postgresql+psycopg://", "postgresql://")
        _wait_for_db(dsn)
        yield dsn
        return

    name = f"dayforce-system-{uuid.uuid4().hex[:8]}"
    port = _free_port()
    run = subprocess.run(
        [
            *("docker", "run", "-d", "--rm", "--name", name),
            *(
                "-e",
                "POSTGRES_USER=app",
                "-e",
                "POSTGRES_PASSWORD=app",
                "-e",
                "POSTGRES_DB=timecards",
            ),
            *("-p", f"127.0.0.1:{port}:5432", IMAGE),
        ],
        capture_output=True,
        text=True,
    )
    if run.returncode != 0:
        pytest.fail(
            "System tests need a database: set DATABASE_URL to a Postgres with pgvector, or "
            f"start Docker so a {IMAGE} container can run.\n{run.stderr.strip()}"
        )
    dsn = f"postgresql://app:app@127.0.0.1:{port}/timecards"
    try:
        _wait_for_db(dsn)
        yield dsn
    finally:
        subprocess.run(["docker", "stop", name], capture_output=True)


class Api:
    """A running API process and an HTTP client for it."""

    def __init__(self, base_url: str, database_url: str, proc: subprocess.Popen):
        self.base_url = base_url
        self.database_url = database_url
        self.proc = proc
        self.http = httpx.Client(base_url=base_url, timeout=30)

    def metric(self, name: str, **labels: str) -> float:
        """One sample from /metrics (0.0 when the series does not exist yet)."""
        want = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
        for line in self.http.get("/metrics").text.splitlines():
            if line.startswith("#"):
                continue
            series, _, value = line.rpartition(" ")
            base, _, rest = series.partition("{")
            if base != name:
                continue
            got = ",".join(sorted(rest.rstrip("}").split(","))) if rest else ""
            if got == want:
                return float(value)
        return 0.0

    def analyse(self, *timecards) -> dict:
        r = self.http.post(
            "/api/analysis/run", json={"timecards": [t.model_dump(mode="json") for t in timecards]}
        )
        assert r.status_code == 201, r.text
        return r.json()

    def propose(self, case_id: str) -> dict:
        r = self.http.post(f"/api/cases/{case_id}/propose")
        assert r.status_code == 200, r.text
        return r.json()

    def case(self, case_id: str) -> dict:
        r = self.http.get(f"/api/cases/{case_id}")
        assert r.status_code == 200, r.text
        return r.json()

    def timecard(self, employee_id: str) -> dict:
        r = self.http.get("/api/timecards", params={"employee_id": employee_id})
        assert r.status_code == 200, r.text
        return r.json()[0]


@pytest.fixture(scope="session")
def start_api(database_url: str) -> Iterator[Callable[..., Api]]:
    """start_api(llm_mode="ok", dev_seed=False) -> a running Api. Servers are cached by
    their settings and stopped at the end of the run."""
    started: dict[tuple, Api] = {}

    def start(llm_mode: str = "ok", dev_seed: bool = False) -> Api:
        key = (llm_mode, dev_seed)
        if key in started:
            return started[key]
        port = _free_port()
        env = {
            **os.environ,
            "DATABASE_URL": database_url,
            "LLM_PROVIDER": "mock",  # never a real model in system tests
            "LLM_MOCK_MODE": llm_mode,
            "LLM_API_KEY": "",  # an empty value beats anything backend/.env would load
            "LLM_BASE_URL": "",
            "LLM_MODEL": "",
            "RETRIEVER": "memory",
            "LOG_LEVEL": "WARNING",
        }
        env.pop("ENABLE_DEV_SEED", None)
        if dev_seed:
            env["ENABLE_DEV_SEED"] = "true"
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "app.main:app",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            cwd=BACKEND,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        api = Api(f"http://127.0.0.1:{port}", database_url, proc)
        deadline = time.time() + 40
        while True:
            try:
                if api.http.get("/health").status_code == 200:
                    break
            except httpx.TransportError:
                pass
            if proc.poll() is not None or time.time() > deadline:
                proc.kill()
                pytest.fail(f"the API did not start (llm_mode={llm_mode}, dev_seed={dev_seed})")
            time.sleep(0.3)
        started[key] = api
        return api

    yield start
    for api in started.values():
        api.http.close()
        api.proc.terminate()
    for api in started.values():
        try:
            api.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            api.proc.kill()
