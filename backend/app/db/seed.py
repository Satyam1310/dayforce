"""Starting data: 8 demo cases. Case 1 is exactly the contract fixtures
(backend/app/contracts/fixtures/: timecard, detected_exception, proposal_pending_review);
the others add one case per exception type, an escalation, a rule-based fallback, an approved
and a rejected case. Same data as the frontend mock (frontend/src/mocks/data.ts).
The large evaluation set (>= 60 timecards + ground truth) is separate: data/seed/ (Ankit).

Load it:  python -m app.db.seed               (with past proposals and decisions)
          python -m app.db.seed --open        (every case OPEN, to run the AI live)
"""

from __future__ import annotations

import argparse
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import psycopg

from app.contracts import AuditEvent, Decision, DetectedException, Proposal, Timecard

from . import repo
from .connection import connect, init_schema

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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Reset the app tables and load the demo cases.")
    ap.add_argument("--open", action="store_true", help="all cases OPEN, no past proposals")
    args = ap.parse_args(argv)
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)  # backend/.env
    with connect() as conn:
        init_schema(conn)
        counts = load_seed(conn, with_history=not args.open)
        conn.commit()
    print(json.dumps(counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
