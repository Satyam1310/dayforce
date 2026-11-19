"""Plain-SQL query functions. Every function takes an open connection and does not commit:
the caller (one request = one transaction, see connection.get_conn) decides."""

from __future__ import annotations

from datetime import date, timedelta

import psycopg
from psycopg.types.json import Jsonb
from pydantic import BaseModel

from app.contracts import AuditEvent, Decision, DetectedException, Proposal, Timecard


def _doc(model: BaseModel) -> Jsonb:
    return Jsonb(model.model_dump(mode="json"))


# ---- timecards ---------------------------------------------------------------------------


def insert_timecard(conn: psycopg.Connection, tc: Timecard) -> None:
    conn.execute(
        "INSERT INTO timecards (id, employee_id, work_date, doc) VALUES (%s, %s, %s, %s)",
        (tc.id, tc.employee_id, tc.work_date, _doc(tc)),
    )


def update_timecard(conn: psycopg.Connection, tc: Timecard) -> None:
    conn.execute("UPDATE timecards SET doc = %s WHERE id = %s", (_doc(tc), tc.id))


def get_timecard(conn: psycopg.Connection, timecard_id: str) -> Timecard | None:
    row = conn.execute("SELECT doc FROM timecards WHERE id = %s", (timecard_id,)).fetchone()
    return Timecard.model_validate(row["doc"]) if row else None


def list_timecards(
    conn: psycopg.Connection, employee_id: str | None = None, week_start: date | None = None
) -> list[Timecard]:
    sql = "SELECT doc FROM timecards WHERE TRUE"
    params: list[object] = []
    if employee_id:
        sql += " AND employee_id = %s"
        params.append(employee_id)
    if week_start:
        sql += " AND work_date BETWEEN %s AND %s"
        params += [week_start, week_start + timedelta(days=6)]
    sql += " ORDER BY work_date, employee_id"
    return [Timecard.model_validate(r["doc"]) for r in conn.execute(sql, params).fetchall()]


# ---- exceptions --------------------------------------------------------------------------


def insert_exception(conn: psycopg.Connection, exc: DetectedException) -> None:
    conn.execute(
        "INSERT INTO exceptions (id, timecard_id, type, status, work_date, doc)"
        " VALUES (%s, %s, %s, %s, %s, %s)",
        (exc.id, exc.timecard_id, exc.type.value, exc.status, exc.work_date, _doc(exc)),
    )


def get_exception(
    conn: psycopg.Connection, exception_id: str, *, for_update: bool = False
) -> DetectedException | None:
    sql = "SELECT doc FROM exceptions WHERE id = %s" + (" FOR UPDATE" if for_update else "")
    row = conn.execute(sql, (exception_id,)).fetchone()
    return DetectedException.model_validate(row["doc"]) if row else None


def list_exceptions(
    conn: psycopg.Connection, status: str | None = None, type_: str | None = None
) -> list[DetectedException]:
    sql = "SELECT doc FROM exceptions WHERE TRUE"
    params: list[object] = []
    if status:
        sql += " AND status = %s"
        params.append(status)
    if type_:
        sql += " AND type = %s"
        params.append(type_)
    sql += " ORDER BY work_date DESC, id"
    return [
        DetectedException.model_validate(r["doc"]) for r in conn.execute(sql, params).fetchall()
    ]


def exception_exists(conn: psycopg.Connection, timecard_id: str, type_: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM exceptions WHERE timecard_id = %s AND type = %s LIMIT 1",
        (timecard_id, type_),
    ).fetchone()
    return row is not None


def set_exception_status(conn: psycopg.Connection, exception_id: str, status: str) -> None:
    conn.execute(
        "UPDATE exceptions SET status = %s, doc = jsonb_set(doc, '{status}', to_jsonb(%s::text))"
        " WHERE id = %s",
        (status, status, exception_id),
    )


# ---- proposals ---------------------------------------------------------------------------


def insert_proposal(conn: psycopg.Connection, p: Proposal) -> None:
    conn.execute(
        "INSERT INTO proposals (id, exception_id, status, doc) VALUES (%s, %s, %s, %s)",
        (p.id, p.exception_id, p.status, _doc(p)),
    )


def get_proposal(
    conn: psycopg.Connection, proposal_id: str, *, for_update: bool = False
) -> Proposal | None:
    sql = "SELECT doc FROM proposals WHERE id = %s" + (" FOR UPDATE" if for_update else "")
    row = conn.execute(sql, (proposal_id,)).fetchone()
    return Proposal.model_validate(row["doc"]) if row else None


def latest_proposal(conn: psycopg.Connection, exception_id: str) -> Proposal | None:
    row = conn.execute(
        "SELECT doc FROM proposals WHERE exception_id = %s ORDER BY created_at DESC LIMIT 1",
        (exception_id,),
    ).fetchone()
    return Proposal.model_validate(row["doc"]) if row else None


def proposal_ids_for(conn: psycopg.Connection, exception_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT id FROM proposals WHERE exception_id = %s", (exception_id,)
    ).fetchall()
    return [r["id"] for r in rows]


def set_proposal_status(conn: psycopg.Connection, proposal_id: str, status: str) -> None:
    conn.execute(
        "UPDATE proposals SET status = %s, doc = jsonb_set(doc, '{status}', to_jsonb(%s::text))"
        " WHERE id = %s",
        (status, status, proposal_id),
    )


# ---- decisions ---------------------------------------------------------------------------


def insert_decision(conn: psycopg.Connection, d: Decision) -> None:
    conn.execute(
        "INSERT INTO decisions (id, proposal_id, doc) VALUES (%s, %s, %s)",
        (d.id, d.proposal_id, _doc(d)),
    )


def get_decision_for(conn: psycopg.Connection, proposal_id: str) -> Decision | None:
    row = conn.execute(
        "SELECT doc FROM decisions WHERE proposal_id = %s", (proposal_id,)
    ).fetchone()
    return Decision.model_validate(row["doc"]) if row else None


# ---- audit -------------------------------------------------------------------------------


def insert_audit(conn: psycopg.Connection, events: list[AuditEvent]) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO audit_events (id, ts, entity_id, doc) VALUES (%s, %s, %s, %s)",
            [(e.id, e.ts, e.entity_id, _doc(e)) for e in events],
        )


def list_audit(
    conn: psycopg.Connection, entity_ids: list[str] | None = None, limit: int = 500
) -> list[AuditEvent]:
    """Newest first. With entity_ids, only events about those entities."""
    if entity_ids:
        rows = conn.execute(
            "SELECT doc FROM audit_events WHERE entity_id = ANY(%s) ORDER BY ts DESC, id"
            " LIMIT %s",
            (entity_ids, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT doc FROM audit_events ORDER BY ts DESC, id LIMIT %s", (limit,)
        ).fetchall()
    return [AuditEvent.model_validate(r["doc"]) for r in rows]


# ---- maintenance -------------------------------------------------------------------------


def clear_all(conn: psycopg.Connection) -> None:
    """Wipe all app data (seed/demo only). Policy chunks are not touched."""
    conn.execute("TRUNCATE audit_events, decisions, proposals, exceptions, timecards")
