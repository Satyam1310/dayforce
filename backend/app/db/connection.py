"""Connections and schema setup. Settings come only from env vars (DATABASE_URL)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from app.ai.retrieval import to_psycopg_dsn

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class DatabaseNotConfigured(RuntimeError):
    """DATABASE_URL is not set."""


def database_dsn() -> str:
    """DATABASE_URL from env. SQLAlchemy-style 'postgresql+psycopg://' URLs are accepted."""
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise DatabaseNotConfigured("DATABASE_URL is not set")
    return to_psycopg_dsn(url)


def connect() -> psycopg.Connection:
    return psycopg.connect(database_dsn(), row_factory=dict_row, connect_timeout=5)


def init_schema(conn: psycopg.Connection) -> None:
    """Create extension, tables and the audit trigger if missing (idempotent).

    Several app copies may start at once (Kubernetes), so a transaction-level advisory lock
    makes them run the schema one after another instead of racing.
    """
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(727001)")
        conn.execute(SCHEMA_PATH.read_text())  # no parameters, so several statements are fine
    conn.commit()


def get_conn() -> Iterator[psycopg.Connection]:
    """FastAPI dependency: one connection and ONE transaction per request.

    Committed when the handler returns (before the response is sent, see `Conn` in api/routes.py),
    rolled back if it raises, so a decision and the
    timecard change it causes are saved together or not at all. Nothing is kept in memory
    between requests, so any number of app copies can run side by side.
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
