"""HTTP endpoints (Harsh's backend checklist; shapes from docs/frontend-mock/types.ts).

    GET  /api/cases                 list cases, filter by status (and type)
    GET  /api/cases/{id}            timecard, exception, proposal, decision, audit events
    POST /api/cases/{id}/propose    run the AI workflow, save the proposal + its audit events
    POST /api/cases/{id}/approve    manager approves the pending proposal (applies the fix)
    POST /api/cases/{id}/reject     manager rejects it (nothing changes on the timecard)
    GET  /api/audit                 audit trail, optional ?entity_id=
    POST /api/dev/seed              demo only, needs ENABLE_DEV_SEED=true: reset + load data

A case id is the exception id. Every model in a response comes from app/contracts/models.py
unchanged; CaseListItem and CaseDetail only group them.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

import psycopg
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.ai.llm import build_llm
from app.ai.retrieval import get_retriever
from app.ai.settings import AISettings
from app.ai.workflow import run_workflow
from app.contracts import (
    AuditEvent,
    Decision,
    DetectedException,
    ExceptionType,
    PolicyConfig,
    Proposal,
    Timecard,
)
from app.db import repo
from app.db.connection import get_conn
from app.db.seed import load_seed
from app.observability.metrics import DECISIONS, PROPOSALS
from app.services.apply_fix import FixNotApplicable, apply_fix
from app.validator import validate_proposal

from .errors import ApiError

router = APIRouter(prefix="/api")
Conn = Annotated[psycopg.Connection, Depends(get_conn)]
CaseStatus = Literal["OPEN", "PROPOSED", "ESCALATED", "RESOLVED"]


# ---- shapes that group contract models (docs/frontend-mock/types.ts) ---------------------


class CaseListItem(BaseModel):
    exception: DetectedException
    proposal: Proposal | None = None


class CaseDetail(BaseModel):
    timecard: Timecard
    exception: DetectedException
    proposal: Proposal | None = None
    decision: Decision | None = None
    audit_events: list[AuditEvent]


class ApproveRequest(BaseModel):
    manager_id: str = Field(min_length=1, max_length=100)
    comment: str | None = Field(default=None, max_length=1000)


class RejectRequest(BaseModel):
    manager_id: str = Field(min_length=1, max_length=100)
    comment: str = Field(min_length=1, max_length=1000)  # a rejection must say why


class SeedResult(BaseModel):
    timecards: int
    exceptions: int
    proposals: int
    decisions: int
    audit_events: int


# ---- helpers -----------------------------------------------------------------------------


def _audit(
    actor: str, event_type: str, entity_type: str, entity_id: str, **payload: object
) -> AuditEvent:
    return AuditEvent(
        id=str(uuid.uuid4()),
        ts=datetime.now(UTC),
        actor=actor,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        payload=payload,
    )


def _get_case(conn: psycopg.Connection, case_id: str, *, lock: bool = False) -> DetectedException:
    exc = repo.get_exception(conn, case_id, for_update=lock)
    if exc is None:
        raise ApiError(404, "NOT_FOUND", f"Case {case_id} not found.")
    return exc


def _get_timecard(conn: psycopg.Connection, timecard_id: str) -> Timecard:
    tc = repo.get_timecard(conn, timecard_id)
    if tc is None:
        raise ApiError(404, "NOT_FOUND", f"Timecard {timecard_id} not found.")
    return tc


def _detail(conn: psycopg.Connection, case_id: str) -> CaseDetail:
    exc = _get_case(conn, case_id)
    tc = _get_timecard(conn, exc.timecard_id)
    proposal = repo.latest_proposal(conn, exc.id)
    decision = repo.get_decision_for(conn, proposal.id) if proposal else None
    entity_ids = [exc.id, tc.id, *repo.proposal_ids_for(conn, exc.id)]
    return CaseDetail(
        timecard=tc,
        exception=exc,
        proposal=proposal,
        decision=decision,
        audit_events=repo.list_audit(conn, entity_ids),
    )


# ---- read --------------------------------------------------------------------------------


@router.get("/cases", response_model=list[CaseListItem], tags=["cases"])
def list_cases(
    conn: Conn,
    status: CaseStatus | None = None,
    type: ExceptionType | None = None,  # same name as the field in the contract
) -> list[CaseListItem]:
    exceptions = repo.list_exceptions(conn, status=status, type_=type.value if type else None)
    latest = repo.latest_proposals(conn, [e.id for e in exceptions])
    return [CaseListItem(exception=e, proposal=latest.get(e.id)) for e in exceptions]


@router.get("/cases/{case_id}", response_model=CaseDetail, tags=["cases"])
def get_case(conn: Conn, case_id: str) -> CaseDetail:
    return _detail(conn, case_id)


@router.get("/audit", response_model=list[AuditEvent], tags=["audit"])
def list_audit(conn: Conn, entity_id: str | None = None) -> list[AuditEvent]:
    """Newest first."""
    return repo.list_audit(conn, [entity_id] if entity_id else None)


# ---- propose -----------------------------------------------------------------------------


@router.post("/cases/{case_id}/propose", response_model=Proposal, tags=["cases"])
def propose(conn: Conn, case_id: str) -> Proposal:
    """Run the AI workflow (retrieve -> LLM -> citation check -> validator -> fallback or
    escalate) and save the proposal plus every audit event it returns. Never changes a
    timecard: only a manager's approval does."""
    exc = _get_case(conn, case_id, lock=True)  # lock: two clicks cannot create two proposals
    if exc.status == "RESOLVED":
        raise ApiError(409, "CASE_RESOLVED", "This case is already resolved.")
    latest = repo.latest_proposal(conn, exc.id)
    if latest is not None and latest.status == "PENDING_REVIEW":
        raise ApiError(409, "PROPOSAL_PENDING", "A proposal is already waiting for review.")
    tc = _get_timecard(conn, exc.timecard_id)

    settings = AISettings.from_env()
    proposal, events = run_workflow(
        exc,
        tc,
        retriever=get_retriever(settings),
        llm=build_llm(settings),
        validator=validate_proposal,  # the real rule checker (app/validator)
        cfg=PolicyConfig(),
        settings=settings,
    )
    repo.insert_proposal(conn, proposal)
    repo.insert_audit(conn, events)
    repo.set_exception_status(
        conn, exc.id, "ESCALATED" if proposal.status == "ESCALATED" else "PROPOSED"
    )
    PROPOSALS.labels(proposal.source.value, proposal.status).inc()
    return proposal


# ---- manager decision --------------------------------------------------------------------


def _pending_proposal(conn: psycopg.Connection, case_id: str) -> tuple[DetectedException, Proposal]:
    """The case (row-locked, so concurrent decisions queue up) and its PENDING proposal."""
    exc = _get_case(conn, case_id, lock=True)
    proposal = repo.latest_proposal(conn, exc.id)
    if exc.status == "ESCALATED" or (proposal is not None and proposal.status == "ESCALATED"):
        raise ApiError(
            409,
            "CASE_ESCALATED",
            "This case is escalated: the AI could not safely propose a fix, so there is"
            " nothing to approve. A manager must resolve it directly.",
        )
    if proposal is None:
        raise ApiError(409, "NO_PROPOSAL", "This case has no proposal yet. Run propose first.")
    if proposal.status != "PENDING_REVIEW":
        raise ApiError(
            409, "ALREADY_DECIDED", f"The proposal for this case is already {proposal.status}."
        )
    return exc, proposal


def _record_decision(
    conn: psycopg.Connection,
    proposal: Proposal,
    value: Literal["APPROVE", "REJECT"],
    manager_id: str,
    comment: str | None,
) -> Decision:
    decision = Decision(
        id=str(uuid.uuid4()),
        proposal_id=proposal.id,
        decision=value,
        manager_id=manager_id,
        comment=comment,
        decided_at=datetime.now(UTC),
    )
    repo.insert_decision(conn, decision)  # UNIQUE(proposal_id) is a second guard
    DECISIONS.labels(value).inc()
    return decision


@router.post("/cases/{case_id}/approve", response_model=CaseDetail, tags=["cases"])
def approve(conn: Conn, case_id: str, body: ApproveRequest) -> CaseDetail:
    """Approve the pending proposal: apply its fix to the timecard, resolve the case, audit.
    All in one transaction; approving twice is refused (409), so a fix is applied once."""
    exc, proposal = _pending_proposal(conn, case_id)
    tc = _get_timecard(conn, exc.timecard_id)
    try:
        updated = apply_fix(tc, proposal.fix)
    except FixNotApplicable as e:
        raise ApiError(422, "FIX_NOT_APPLICABLE", str(e)) from e

    comment = (body.comment or "").strip() or None
    decision = _record_decision(conn, proposal, "APPROVE", body.manager_id, comment)
    repo.update_timecard(conn, updated)
    repo.set_proposal_status(conn, proposal.id, "APPROVED")
    repo.set_exception_status(conn, exc.id, "RESOLVED")
    repo.insert_audit(
        conn,
        [
            _audit(
                body.manager_id,
                "DECISION_APPROVED",
                "proposal",
                proposal.id,
                decision_id=decision.id,
                exception_id=exc.id,
                comment=comment,
            ),
            _audit(
                "system",
                "TIMECARD_UPDATED",
                "timecard",
                tc.id,
                exception_id=exc.id,
                action=proposal.fix.action.value,
                changes=[c.model_dump(mode="json") for c in proposal.fix.changes],
            ),
        ],
    )
    return _detail(conn, case_id)


@router.post("/cases/{case_id}/reject", response_model=CaseDetail, tags=["cases"])
def reject(conn: Conn, case_id: str, body: RejectRequest) -> CaseDetail:
    """Reject the pending proposal. The timecard is not changed; the case goes back to OPEN
    (a new proposal can be requested)."""
    comment = body.comment.strip()
    if not comment:
        raise ApiError(422, "COMMENT_REQUIRED", "A comment is required to reject a proposal.")
    exc, proposal = _pending_proposal(conn, case_id)
    decision = _record_decision(conn, proposal, "REJECT", body.manager_id, comment)
    repo.set_proposal_status(conn, proposal.id, "REJECTED")
    repo.set_exception_status(conn, exc.id, "OPEN")
    repo.insert_audit(
        conn,
        [
            _audit(
                body.manager_id,
                "DECISION_REJECTED",
                "proposal",
                proposal.id,
                decision_id=decision.id,
                exception_id=exc.id,
                comment=comment,
            )
        ],
    )
    return _detail(conn, case_id)


# ---- demo --------------------------------------------------------------------------------


def require_dev_seed() -> None:
    """The seed endpoint wipes all data, so it only exists when ENABLE_DEV_SEED=true.
    Off by default: a deployed API answers 404, as if the endpoint were not there.
    Runs before the database dependency, so nothing is touched when it is off."""
    if os.environ.get("ENABLE_DEV_SEED", "").strip().lower() != "true":
        raise ApiError(404, "NOT_FOUND", "Not found.")


@router.post(
    "/dev/seed",
    response_model=SeedResult,
    tags=["demo"],
    dependencies=[Depends(require_dev_seed)],
)
def seed(
    conn: Conn,
    with_history: Annotated[
        bool, Query(description="true: past proposals/decisions too; false: every case OPEN")
    ] = True,
) -> SeedResult:
    """Demo/dev only (ENABLE_DEV_SEED=true): reset the app tables, load the starting data."""
    return SeedResult(**load_seed(conn, with_history=with_history))
