"""Demo seed data: the same 8 cases the frontend mock uses (frontend/src/mocks/data.ts).

BACKUP ONLY: the real seed set (>= 60 timecards + ground truth) is Ankit's job in data/seed/.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from app.contracts import AuditEvent, Decision, DetectedException, Proposal, Timecard

from . import repo

SEED_PATH = Path(__file__).with_name("seed_data.json")


def load_seed(conn: psycopg.Connection, *, with_history: bool = True) -> dict[str, int]:
    """Replace all app data with the seed set.

    with_history=True  -> timecards, exceptions, proposals, decisions and audit, as in the
                          frontend mock (some cases already proposed / approved / rejected).
    with_history=False -> only timecards and exceptions, all OPEN, so every case can be run
                          through the real AI workflow live (good for the demo).
    """
    data = json.loads(SEED_PATH.read_text())
    repo.clear_all(conn)

    timecards = [Timecard.model_validate(t) for t in data["timecards"]]
    for tc in timecards:
        repo.insert_timecard(conn, tc)

    exceptions = [DetectedException.model_validate(e) for e in data["exceptions"]]
    if with_history:
        for exc in exceptions:
            repo.insert_exception(conn, exc)
        proposals = [Proposal.model_validate(p) for p in data["proposals"]]
        for p in proposals:
            repo.insert_proposal(conn, p)
        decisions = [Decision.model_validate(d) for d in data["decisions"]]
        for d in decisions:
            repo.insert_decision(conn, d)
        events = [AuditEvent.model_validate(a) for a in data["audit_events"]]
        repo.insert_audit(conn, events)
        return {
            "timecards": len(timecards),
            "exceptions": len(exceptions),
            "proposals": len(proposals),
            "decisions": len(decisions),
            "audit_events": len(events),
        }

    now = datetime.now(UTC)
    events = []
    loaded = 0
    for exc in exceptions:
        if exc.status == "RESOLVED":
            continue  # its timecard already carries the approved fix
        exc = exc.model_copy(update={"status": "OPEN"})
        repo.insert_exception(conn, exc)
        loaded += 1
        events.append(
            AuditEvent(
                id=str(uuid.uuid4()),
                ts=now,
                actor="system",
                event_type="EXCEPTION_DETECTED",
                entity_type="exception",
                entity_id=exc.id,
                payload={"type": exc.type.value, "timecard_id": exc.timecard_id},
            )
        )
    repo.insert_audit(conn, events)
    return {"timecards": len(timecards), "exceptions": loaded, "proposals": 0, "decisions": 0,
            "audit_events": len(events)}
