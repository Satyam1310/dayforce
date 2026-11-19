"""HTTP endpoints (Harsh's backend checklist; shapes from docs/frontend-mock/types.ts).

    GET  /api/cases                 list cases, filter by status (and type)
    GET  /api/cases/{id}            timecard, exception, proposal, decision, audit events
    POST /api/analysis/run          timecards in: run the detectors, save timecards + cases
    GET  /api/timecards             saved timecards, filter by employee_id and week
    GET  /api/policies/search       debug: which policy clauses a query retrieves (?q=&k=)
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
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal

import psycopg
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.ai.guard import guard_llm
from app.ai.llm import build_llm
from app.ai.retrieval import get_retriever
from app.ai.settings import AISettings
from app.ai.workflow import run_workflow
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
from app.domain.rules import detect_exceptions
from app.observability.metrics import (
    ANALYSIS_RUNS,
    DECISIONS,
    ESCALATIONS,
    EXCEPTIONS_DETECTED,
    PROPOSALS,
    VALIDATOR_REJECTIONS,
)
from app.services.apply_fix import FixNotApplicable, apply_fix
from app.validator import validate_proposal

from .errors import ApiError

router = APIRouter(prefix="/api")
# scope="function": the commit (the code after `yield` in get_conn) runs when the handler
# returns, BEFORE the response is sent. With the default scope it runs after the response, and
# a client could read "201 Created" and then get a 404 for what it just wrote.
# (tests/integration/test_commit_before_response.py). Needs FastAPI 0.121 or newer.
Conn = Annotated[psycopg.Connection, Depends(get_conn, scope="function")]
CaseStatus = Literal["OPEN", "PROPOSED", "ESCALATED", "RESOLVED"]
MAX_ANALYSIS_TIMECARDS = 200  # one request analyses at most this many timecards


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


class AnalysisRequest(BaseModel):
    """Timecards for one analysis. Send every timecard of the people and week you want checked
    together: weekly overtime and minimum rest need the neighbouring days."""

    timecards: list[Timecard] = Field(min_length=1, max_length=MAX_ANALYSIS_TIMECARDS)


class AnalysisResult(BaseModel):
    timecards_saved: int
    cases: list[CaseListItem]  # the new cases only; clean timecards produce none


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


# ---- analysis: timecards in, cases out ---------------------------------------------------


def _check_timecards(timecards: list[Timecard]) -> None:
    """Cheap sanity checks before anything is saved. Free text (notes) is not inspected:
    it is stored as data and never used as an instruction."""
    ids = [t.id for t in timecards]
    if len(set(ids)) != len(ids):
        raise ApiError(422, "DUPLICATE_TIMECARD_IN_REQUEST", "Timecard ids must be unique.")
    for t in timecards:
        owners = {p.employee_id for p in t.punches} | {s.employee_id for s in t.shifts}
        if owners - {t.employee_id}:
            raise ApiError(
                422,
                "EMPLOYEE_MISMATCH",
                f"Timecard {t.id}: every punch and shift must belong to employee {t.employee_id}.",
            )


@router.post("/analysis/run", response_model=AnalysisResult, status_code=201, tags=["analysis"])
def run_analysis(conn: Conn, body: AnalysisRequest) -> AnalysisResult:
    """Run the rule detectors (plain code, no AI) over the timecards, save the timecards and
    every exception found as new OPEN cases, and write audit events. Timecard ids that already
    exist are refused (409) so a repeat call cannot create duplicate cases."""
    _check_timecards(body.timecards)
    taken = repo.existing_timecard_ids(conn, [t.id for t in body.timecards])
    if taken:
        raise ApiError(
            409, "TIMECARD_EXISTS", f"Timecard already saved: {sorted(taken)[0]} (and maybe more)."
        )

    found = detect_exceptions(body.timecards, PolicyConfig())
    events: list[AuditEvent] = []
    try:
        for tc in body.timecards:
            repo.insert_timecard(conn, tc)
            events.append(
                _audit("system", "TIMECARD_IMPORTED", "timecard", tc.id, employee_id=tc.employee_id)
            )
        for exc in found:
            repo.insert_exception(conn, exc)
            events.append(
                _audit(
                    "system",
                    "EXCEPTION_DETECTED",
                    "exception",
                    exc.id,
                    type=exc.type.value,
                    timecard_id=exc.timecard_id,
                    rule=exc.evidence.get("rule"),
                )
            )
    except psycopg.errors.UniqueViolation as e:  # two identical calls racing
        raise ApiError(
            409, "TIMECARD_EXISTS", "Those timecards were saved by another request."
        ) from e
    repo.insert_audit(conn, events)

    ANALYSIS_RUNS.inc()
    for exc in found:
        EXCEPTIONS_DETECTED.labels(exc.type.value).inc()
    return AnalysisResult(
        timecards_saved=len(body.timecards),
        cases=[CaseListItem(exception=e) for e in found],
    )


@router.get("/timecards", response_model=list[Timecard], tags=["timecards"])
def list_timecards(
    conn: Conn,
    employee_id: str | None = None,
    week: Annotated[
        date | None, Query(description="any date; returns that Monday-to-Sunday week")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[Timecard]:
    week_start = week - timedelta(days=week.weekday()) if week else None  # weeks start Monday
    return repo.list_timecards(conn, employee_id=employee_id, week_start=week_start, limit=limit)


@router.get("/policies/search", response_model=list[PolicyCitation], tags=["policies"])
def search_policies(
    q: Annotated[str, Query(min_length=1, max_length=300)],
    k: Annotated[int, Query(ge=1, le=10)] = 3,
) -> list[PolicyCitation]:
    """Debug view of retrieval: the clauses (with scores) the AI would be shown for this text.
    Scores are not filtered by the minimum-relevance threshold."""
    return get_retriever(AISettings.from_env()).retrieve(q, k=k)


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
        llm=guard_llm(build_llm(settings), settings),  # metrics, and the LLM_MAX_RPM limit
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
    for e in events:
        if e.event_type == "VALIDATION_RESULT" and e.payload.get("passed") is False:
            VALIDATOR_REJECTIONS.inc()
        elif e.event_type == "ESCALATED":
            ESCALATIONS.labels(str(e.payload.get("reason", "UNKNOWN"))).inc()
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
