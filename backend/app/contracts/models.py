"""Shared Pydantic v2 contracts (CLAUDE.md section 8).

Changes go through a `contract-change` GitHub issue, never a silent edit.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class PunchType(str, Enum):
    IN = "IN"
    OUT = "OUT"
    BREAK_START = "BREAK_START"
    BREAK_END = "BREAK_END"


class Punch(BaseModel):
    id: str
    employee_id: str
    type: PunchType
    timestamp: datetime


class Shift(BaseModel):
    id: str
    employee_id: str
    start: datetime
    end: datetime
    location_id: str


class Timecard(BaseModel):
    id: str
    employee_id: str
    work_date: date
    punches: list[Punch]
    shifts: list[Shift]
    notes: str | None = None  # UNTRUSTED text: data, never instructions


class ExceptionType(str, Enum):
    MISSED_PUNCH = "MISSED_PUNCH"
    OVERTIME_RISK = "OVERTIME_RISK"
    SCHEDULE_CONFLICT = "SCHEDULE_CONFLICT"
    POLICY_BREACH = "POLICY_BREACH"


class Severity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class FixAction(str, Enum):
    ADD_PUNCH = "ADD_PUNCH"
    EDIT_PUNCH = "EDIT_PUNCH"
    DEDUCT_BREAK = "DEDUCT_BREAK"
    REASSIGN_SHIFT = "REASSIGN_SHIFT"
    FLAG_FOR_MANAGER = "FLAG_FOR_MANAGER"
    NO_CHANGE = "NO_CHANGE"


class FieldChange(BaseModel):
    target_id: str
    field: str
    old: str | None = None
    new: str | None = None


class Fix(BaseModel):
    action: FixAction
    changes: list[FieldChange]


class DetectedException(BaseModel):
    id: str
    type: ExceptionType
    severity: Severity
    employee_id: str
    timecard_id: str
    work_date: date
    evidence: dict[str, Any]  # facts the detector found
    policy_refs: list[str]  # clause IDs, e.g. ["POL-TA-01"]
    baseline_fix: Fix | None = None  # rule-based fix, used as the fallback
    status: Literal["OPEN", "PROPOSED", "ESCALATED", "RESOLVED"]


class PolicyCitation(BaseModel):
    clause_id: str
    quote: str
    score: float


class Violation(BaseModel):
    code: str
    message: str
    clause_id: str | None = None


class ValidationResult(BaseModel):
    passed: bool
    violations: list[Violation]


class ProposalSource(str, Enum):
    LLM = "LLM"
    RULES_FALLBACK = "RULES_FALLBACK"


class Proposal(BaseModel):
    id: str
    exception_id: str
    fix: Fix
    rationale: str = Field(max_length=400)  # plain language
    citations: list[PolicyCitation]
    confidence: float = Field(ge=0, le=1)
    source: ProposalSource
    model: str | None = None
    prompt_version: str
    validation: ValidationResult | None = None
    status: Literal["PENDING_REVIEW", "APPROVED", "REJECTED", "ESCALATED"]


class Decision(BaseModel):
    id: str
    proposal_id: str
    decision: Literal["APPROVE", "REJECT"]
    manager_id: str
    comment: str | None = None
    decided_at: datetime


class AuditEvent(BaseModel):
    id: str
    ts: datetime
    actor: str  # "system" | "llm" | manager id
    event_type: str
    entity_type: str
    entity_id: str
    payload: dict[str, Any]
