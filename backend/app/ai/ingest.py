"""Ingest the policy markdown into the pgvector `policy_chunks` table.

Run:  python -m app.ai.ingest [--policy path/to/policy.md]   (needs DATABASE_URL)
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from .embeddings import Embedder
from .retrieval import DEFAULT_POLICY_PATH, parse_policy_chunks, to_psycopg_dsn, vector_literal

# Agreed table. Raman puts the same DDL in backend/app/db/schema.sql; keep the two identical.
# 384 = embedding size of BAAI/bge-small-en-v1.5 (fastembed).
POLICY_CHUNKS_DDL = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS policy_chunks (
  clause_id TEXT PRIMARY KEY, title TEXT NOT NULL, text TEXT NOT NULL,
  embedding vector(384) NOT NULL);
"""

UPSERT_SQL = """
INSERT INTO policy_chunks (clause_id, title, text, embedding)
VALUES (%s, %s, %s, %s::vector)
ON CONFLICT (clause_id) DO UPDATE
SET title = EXCLUDED.title, text = EXCLUDED.text, embedding = EXCLUDED.embedding
"""


def ingest(conn: Any, embedder: Any, policy_path: Path | str | None = None) -> list[str]:
    """Embed each clause and upsert it. Idempotent. Returns the clause ids written."""
    chunks = parse_policy_chunks(Path(policy_path or DEFAULT_POLICY_PATH).read_text())
    if not chunks:
        raise ValueError("no '## POL-TA-' clauses found in the policy file")
    vectors = embedder.embed([c.full_text for c in chunks])
    conn.execute(POLICY_CHUNKS_DDL)
    for c, v in zip(chunks, vectors, strict=True):
        conn.execute(UPSERT_SQL, (c.clause_id, c.title, c.text, vector_literal(v)))
    conn.commit()
    return [c.clause_id for c in chunks]


def main(argv: list[str] | None = None) -> int:
    import psycopg

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--policy", default=None)
    args = ap.parse_args(argv)
    url = os.environ.get("DATABASE_URL")
    if not url:
        ap.error("DATABASE_URL is not set")
    with psycopg.connect(to_psycopg_dsn(url)) as conn:
        ids = ingest(conn, Embedder(), args.policy)
    print(f"ingested {len(ids)} clauses: {', '.join(ids)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
