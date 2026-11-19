"""All /api/v1 endpoints (CLAUDE.md section 9)."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Literal

import psycopg
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.ai.llm import build_llm
from app.ai.retrieval import get_retriever
from app.ai.settings import AISettings
from app.ai.workflow import Validator, always_pass_validator, run_workflow
from app.contracts import (
    AuditEvent,
    Decision,
    DetectedException,
    ExceptionType,
    PolicyCitation,
    PolicyConfig,
    Proposal,
    Timecard,
)
from app.db import repo
from app.db.connection import get_conn
from app.db.seed import load_seed
from app.observability.metrics import DECISIONS, PROPOSALS
from app.services.apply_fix import FixNotApplicable, apply_fix

from .errors import ApiError

router = APIRouter(prefix="/api/v1")
Conn = Annotated[psycopg.Connection, Depends(get_conn)]

ExceptionStatus = Literal["OPEN", "PROPOSED", "ESCALATED", "RESOLVED"]


# ---- response / request shapes not in models.py -------------------------------------------


class ExceptionDetail(BaseModel):
    """GET /exceptions/{id}. Same fields as the frontend's ExceptionDetail and Harsh's draft
    CaseDetail (docs/frontend-mock/types.ts)."""

    exception: DetectedException
    timecard: Timecard
    proposal: Proposal | None
    decision: Decision | None
    audit_events: list[AuditEvent]


class DecisionRequest(BaseModel):
    decision: Literal["APPROVE", "REJECT"]
    manager_id: str = Field(min_length=1, max_length=100)
    comment: str | None = Field(default=None, max_length=1000)


class SeedResult(BaseModel):
    timecards: int
    exceptions: int
    proposals: int
    decisions: int
    audit_events: int


class AnalysisResult(BaseModel):
    detected: int
    created: int


# ---- helpers -----------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _audit(
    actor: str, event_type: str, entity_type: str, entity_id: str, **payload: object
) -> AuditEvent:
    return AuditEvent(
        id=str(uuid.uuid4()),
        ts=_now(),
        actor=actor,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        payload=payload,
    )


def _validator() -> Validator:
    """Ankit's validate_proposal when it exists, else the AI module's pass-through."""
    try:
        from app.validator import validate_proposal  # type: ignore[attr-defined]
    except ImportError:
        return always_pass_validator
    return validate_proposal


def _must_get_exception(conn: psycopg.Connection, exception_id: str, **kw) -> DetectedException:
    exc = repo.get_exception(conn, exception_id, **kw)
    if exc is None:
        raise ApiError(404, "NOT_FOUND", f"Exception {exception_id} not found.")
    return exc


def _must_get_timecard(conn: psycopg.Connection, timecard_id: str) -> Timecard:
    tc = repo.get_timecard(conn, timecard_id)
    if tc is None:
        raise ApiError(404, "NOT_FOUND", f"Timecard {timecard_id} not found.")
    return tc


# ---- seed & analysis ---------------------------------------------------------------------


@router.post("/seed/load", response_model=SeedResult, tags=["demo"])
def seed_load(
    conn: Conn,
    with_history: Annotated[
        bool,
        Query(description="true: include past proposals/decisions; false: all cases OPEN"),
    ] = True,
) -> SeedResult:
    """Demo/dev only: wipe the app tables and load the 8 demo cases."""
    return SeedResult(**load_seed(conn, with_history=with_history))


@router.post("/analysis/run", response_model=AnalysisResult, tags=["analysis"])
def analysis_run(
    conn: Conn,
    week: Annotated[date | None, Query(description="Monday of the week, YYYY-MM-DD")] = None,
) -> AnalysisResult:
    """Run the rule detectors over a week's timecards and store new exceptions."""
    try:
        from app.domain.rules import detect_exceptions  # type: ignore[attr-defined]
    except ImportError as e:
        raise ApiError(
            501, "DETECTORS_NOT_AVAILABLE", "The rule detectors are not installed yet."
        ) from e

    timecards = repo.list_timecards(conn, week_start=week)
    found = detect_exceptions(timecards, PolicyConfig())
    created = 0
    events = []
    for exc in found:
        if repo.exception_exists(conn, exc.timecard_id, exc.type.value):
            continue
        repo.insert_exception(conn, exc)
        created += 1
        events.append(
            _audit("system", "EXCEPTION_DETECTED", "exception", exc.id,
                   type=exc.type.value, timecard_id=exc.timecard_id)
        )
    repo.insert_audit(conn, events)
    return AnalysisResult(detected=len(found), created=created)


# ---- read endpoints ----------------------------------------------------------------------


@router.get("/timecards", response_model=list[Timecard], tags=["timecards"])
def list_timecards(
    conn: Conn,
    employee_id: str | None = None,
    week: Annotated[date | None, Query(description="Monday of the week, YYYY-MM-DD")] = None,
) -> list[Timecard]:
    return repo.list_timecards(conn, employee_id=employee_id, week_start=week)


@router.get("/exceptions", response_model=list[DetectedException], tags=["exceptions"])
def list_exceptions(
    conn: Conn,
    status: ExceptionStatus | None = None,
    type: ExceptionType | None = None,  # query param name from CLAUDE.md
) -> list[DetectedException]:
    return repo.list_exceptions(conn, status=status, type_=type.value if type else None)


@router.get("/exceptions/{exception_id}", response_model=ExceptionDetail, tags=["exceptions"])
def get_exception(conn: Conn, exception_id: str) -> ExceptionDetail:
    exc = _must_get_exception(conn, exception_id)
    tc = _must_get_timecard(conn, exc.timecard_id)
    proposal = repo.latest_proposal(conn, exc.id)
    decision = repo.get_decision_for(conn, proposal.id) if proposal else None
    entity_ids = [exc.id, tc.id, *repo.proposal_ids_for(conn, exc.id)]
    return ExceptionDetail(
        exception=exc,
        timecard=tc,
        proposal=proposal,
        decision=decision,
        audit_events=repo.list_audit(conn, entity_ids),
    )


@router.get("/proposals/{proposal_id}", response_model=Proposal, tags=["proposals"])
def get_proposal(conn: Conn, proposal_id: str) -> Proposal:
    p = repo.get_proposal(conn, proposal_id)
    if p is None:
        raise ApiError(404, "NOT_FOUND", f"Proposal {proposal_id} not found.")
    return p


@router.get("/audit", response_model=list[AuditEvent], tags=["audit"])
def list_audit(conn: Conn, entity_id: str | None = None) -> list[AuditEvent]:
    return repo.list_audit(conn, [entity_id] if entity_id else None)


@router.get("/policies/search", response_model=list[PolicyCitation], tags=["debug"])
def search_policies(q: Annotated[str, Query(min_length=1)], k: int = 3) -> list[PolicyCitation]:
    """Debug retrieval: what the AI would see for this query."""
    return get_retriever(AISettings.from_env()).retrieve(q, k=max(1, min(k, 7)))


# ---- AI proposal -------------------------------------------------------------------------


@router.post("/exceptions/{exception_id}/propose", response_model=Proposal, tags=["proposals"])
def propose(conn: Conn, exception_id: str) -> Proposal:
    """Run the AI workflow (retrieve -> LLM -> citation check -> validator -> fallback /
    escalate) and store the proposal as PENDING_REVIEW or ESCALATED. Never changes a timecard."""
    exc = _must_get_exception(conn, exception_id, for_update=True)
    if exc.status == "RESOLVED":
        raise ApiError(409, "EXCEPTION_RESOLVED", "This exception is already resolved.")
    latest = repo.latest_proposal(conn, exc.id)
    if latest is not None and latest.status == "PENDING_REVIEW":
        raise ApiError(
            409, "PROPOSAL_PENDING", f"Proposal {latest.id} is already waiting for review."
        )
    tc = _must_get_timecard(conn, exc.timecard_id)

    settings = AISettings.from_env()
    proposal, events = run_workflow(
        exc,
        tc,
        retriever=get_retriever(settings),
        llm=build_llm(settings),
        validator=_validator(),
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


@router.post("/proposals/{proposal_id}/decision", response_model=Decision, tags=["proposals"])
def decide(conn: Conn, proposal_id: str, body: DecisionRequest) -> Decision:
    """Approve or reject. Only APPROVE changes the timecard; both are audited.
    Runs in one transaction (see db.connection.get_conn)."""
    proposal = repo.get_proposal(conn, proposal_id, for_update=True)  # blocks double-clicks
    if proposal is None:
        raise ApiError(404, "NOT_FOUND", f"Proposal {proposal_id} not found.")
    if proposal.status != "PENDING_REVIEW":
        raise ApiError(409, "PROPOSAL_NOT_PENDING", f"Proposal is already {proposal.status}.")
    comment = (body.comment or "").strip() or None
    if body.decision == "REJECT" and not comment:
        raise ApiError(422, "COMMENT_REQUIRED", "A comment is required to reject a proposal.")

    exc = _must_get_exception(conn, proposal.exception_id, for_update=True)
    decision = Decision(
        id=str(uuid.uuid4()),
        proposal_id=proposal.id,
        decision=body.decision,
        manager_id=body.manager_id,
        comment=comment,
        decided_at=_now(),
    )
    repo.insert_decision(conn, decision)
    events: list[AuditEvent] = []

    if body.decision == "APPROVE":
        tc = _must_get_timecard(conn, exc.timecard_id)
        try:
            updated = apply_fix(tc, proposal.fix)
        except FixNotApplicable as e:
            raise ApiError(422, "FIX_NOT_APPLICABLE", str(e)) from e
        repo.update_timecard(conn, updated)
        repo.set_proposal_status(conn, proposal.id, "APPROVED")
        repo.set_exception_status(conn, exc.id, "RESOLVED")
        events.append(
            _audit(body.manager_id, "DECISION_APPROVED", "proposal", proposal.id,
                   decision_id=decision.id, exception_id=exc.id, comment=comment)
        )
        events.append(
            _audit("system", "TIMECARD_UPDATED", "timecard", tc.id,
                   exception_id=exc.id, action=proposal.fix.action.value,
                   changes=[c.model_dump(mode="json") for c in proposal.fix.changes])
        )
    else:
        repo.set_proposal_status(conn, proposal.id, "REJECTED")
        repo.set_exception_status(conn, exc.id, "OPEN")  # same convention as docs/frontend-mock
        events.append(
            _audit(body.manager_id, "DECISION_REJECTED", "proposal", proposal.id,
                   decision_id=decision.id, exception_id=exc.id, comment=comment)
        )

    repo.insert_audit(conn, events)
    DECISIONS.labels(body.decision).inc()
    return decision
