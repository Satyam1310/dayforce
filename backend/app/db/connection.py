"""Connections and schema setup."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from app.ai.retrieval import to_psycopg_dsn

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
DEFAULT_DATABASE_URL = "postgresql://app:app@localhost:5432/timecards"


def database_dsn() -> str:
    """DATABASE_URL from env (SQLAlchemy-style URLs are accepted), else local default."""
    return to_psycopg_dsn(os.environ.get("DATABASE_URL") or DEFAULT_DATABASE_URL)


def connect() -> psycopg.Connection:
    return psycopg.connect(database_dsn(), row_factory=dict_row, connect_timeout=5)


def init_schema(conn: psycopg.Connection) -> None:
    """Create the tables if they do not exist (idempotent)."""
    sql = SCHEMA_PATH.read_text()
    for statement in sql.split(";"):
        lines = [ln for ln in statement.splitlines() if not ln.strip().startswith("--")]
        if "".join(lines).strip():
            conn.execute("\n".join(lines))
    conn.commit()


def get_conn() -> Iterator[psycopg.Connection]:
    """FastAPI dependency: one connection and ONE transaction per request.

    Committed when the handler returns, rolled back if it raises, so a decision and the
    timecard change it causes are saved together or not at all.
    """
    conn = connect()
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
