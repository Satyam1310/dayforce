"""Real Postgres + real fastembed model. Runs only when DATABASE_URL is set, e.g.

  docker run -d --name pgv -e POSTGRES_USER=app -e POSTGRES_PASSWORD=app \
    -e POSTGRES_DB=timecards -p 54329:5432 pgvector/pgvector:pg16
  DATABASE_URL=postgresql://app:app@localhost:54329/timecards pytest -m integration
"""

import os

import pytest

from app.ai.embeddings import Embedder
from app.ai.ingest import ingest
from app.ai.retrieval import PgVectorRetriever, to_psycopg_dsn

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL not set"),
]


@pytest.fixture(scope="module")
def embedder():
    return Embedder()


@pytest.fixture(scope="module")
def dsn(embedder):
    import psycopg

    url = to_psycopg_dsn(os.environ["DATABASE_URL"])
    with psycopg.connect(url) as conn:
        ingest(conn, embedder)
    return url


def test_missing_clock_out_retrieves_missed_punch(dsn, embedder):
    r = PgVectorRetriever(dsn, embedder, min_score=0.0)
    out = r.retrieve("missing clock out", k=3)
    print([(c.clause_id, c.score) for c in out])
    assert out[0].clause_id == "POL-TA-01"
    assert all(0 <= c.score <= 1 for c in out)


def test_overlapping_shifts_retrieves_schedule_conflict(dsn, embedder):
    r = PgVectorRetriever(dsn, embedder, min_score=0.0)
    out = r.retrieve("overlapping shifts", k=3)
    print([(c.clause_id, c.score) for c in out])
    assert out[0].clause_id == "POL-TA-05"


def test_ingest_is_idempotent(dsn, embedder):
    import psycopg

    with psycopg.connect(dsn) as conn:
        ingest(conn, embedder)
        ingest(conn, embedder)
        n = conn.execute("SELECT count(*) FROM policy_chunks").fetchone()[0]
        dims = conn.execute("SELECT vector_dims(embedding) FROM policy_chunks LIMIT 1").fetchone()[
            0
        ]
    assert n == 7 and dims == 384


def test_min_score_filters_results(dsn, embedder):
    assert PgVectorRetriever(dsn, embedder, min_score=0.999).retrieve("missing clock out") == []


def test_unrelated_query_scores(dsn, embedder):
    """Informational: shows how bge-small scores off-topic text, to tune RETRIEVAL_MIN_SCORE."""
    out = PgVectorRetriever(dsn, embedder, min_score=0.0).retrieve("best pizza toppings", k=3)
    print("unrelated:", [(c.clause_id, c.score) for c in out])
    assert len(out) == 3
