"""Proposal workflow (CLAUDE.md section 10) as a plain-Python state machine.

Stores nothing: returns the Proposal plus AuditEvents describing every step.
TODO(Harsh): LangGraph version of the same flow.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from app.contracts import (
    AuditEvent,
    DetectedException,
    Fix,
    FixAction,
    PolicyCitation,
    PolicyConfig,
    Proposal,
    ProposalSource,
    Timecard,
    ValidationResult,
    Violation,
)

from .llm import LLM, LLMError, build_llm
from .prompts import PROMPT_VERSION, SYSTEM_PROMPT, build_user_prompt, parse_llm_output
from .retrieval import Retriever, build_query, get_retriever
from .settings import FALLBACK_CONFIDENCE, AISettings

Validator = Callable[[Timecard, DetectedException, Fix, PolicyConfig], ValidationResult]

TOP_K = 3


def always_pass_validator(
    timecard: Timecard, exc: DetectedException, fix: Fix, cfg: PolicyConfig
) -> ValidationResult:
    """Placeholder until Ankit's validate_proposal lands. Passes everything."""
    return ValidationResult(passed=True, violations=[])


def _flag_fix() -> Fix:
    return Fix(action=FixAction.FLAG_FOR_MANAGER, changes=[])


def run_workflow(
    exc: DetectedException,
    timecard: Timecard,
    *,
    retriever: Retriever,
    llm: LLM,
    validator: Validator,
    cfg: PolicyConfig,
    settings: AISettings | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[Proposal, list[AuditEvent]]:
    s = settings or AISettings.from_env()
    events: list[AuditEvent] = []

    def audit(event_type: str, actor: str = "system", **payload: Any) -> None:
        events.append(
            AuditEvent(
                id=str(uuid.uuid4()),
                ts=datetime.now(UTC),
                actor=actor,
                event_type=event_type,
                entity_type="exception",
                entity_id=exc.id,
                payload=payload,
            )
        )

    def make(
        *,
        fix: Fix,
        rationale: str,
        citations: list[PolicyCitation],
        confidence: float,
        source: ProposalSource,
        model: str | None,
        validation: ValidationResult | None,
        status: str,
    ) -> Proposal:
        p = Proposal(
            id=str(uuid.uuid4()),
            exception_id=exc.id,
            fix=fix,
            rationale=rationale[:400],
            citations=citations,
            confidence=confidence,
            source=source,
            model=model,
            prompt_version=PROMPT_VERSION,
            validation=validation,
            status=status,  # type: ignore[arg-type]
        )
        audit("PROPOSAL_CREATED", proposal_id=p.id, status=status, source=source.value)
        return p

    def escalate(reason: str, detail: str, **kw: Any) -> tuple[Proposal, list[AuditEvent]]:
        audit("ESCALATED", reason=reason)
        p = make(
            fix=_flag_fix(),
            rationale=f"AI could not safely propose a fix ({detail}). Manager review needed.",
            citations=kw.get("citations", []),
            confidence=kw.get("confidence", 0.0),
            source=kw.get("source", ProposalSource.RULES_FALLBACK),
            model=kw.get("model"),
            validation=None,
            status="ESCALATED",
        )
        return p, events

    audit("WORKFLOW_STARTED", exception_type=exc.type.value)

    # 1. retrieve policy
    retrieved = retriever.retrieve(build_query(exc), k=TOP_K)
    relevant = [c for c in retrieved if c.score >= s.retrieval_min_score]
    audit(
        "POLICY_RETRIEVED",
        clauses=[{"clause_id": c.clause_id, "score": c.score} for c in retrieved],
        min_score=s.retrieval_min_score,
    )
    if not relevant:
        return escalate("NO_POLICY_MATCH", "no policy clause matched well enough")
    allowed = {c.clause_id: c for c in relevant}

    def fallback(reason: str) -> tuple[Proposal, list[AuditEvent]]:
        audit("FALLBACK_USED", reason=reason)
        if exc.baseline_fix is None:
            return escalate("NO_BASELINE_FIX", f"{reason}; no rule-based fix exists")
        validation = validator(timecard, exc, exc.baseline_fix, cfg)
        audit("VALIDATION_RESULT", passed=validation.passed, target="baseline_fix")
        if not validation.passed:
            return escalate("BASELINE_FIX_INVALID", f"{reason}; rule-based fix failed validation")
        cites = [allowed[r] for r in exc.policy_refs if r in allowed]
        return _finish(
            exc.baseline_fix,
            f"Rule-based fix used ({reason}). Follows {', '.join(exc.policy_refs)}.",
            cites,
            FALLBACK_CONFIDENCE,
            ProposalSource.RULES_FALLBACK,
            None,
            validation,
        )

    def _finish(fix, rationale, cites, confidence, source, model, validation):
        if confidence < s.confidence_escalate_below:
            return escalate(
                "LOW_CONFIDENCE",
                f"confidence {confidence:.2f} is below {s.confidence_escalate_below}",
                citations=cites,
                confidence=confidence,
                source=source,
                model=model,
            )
        p = make(
            fix=fix,
            rationale=rationale,
            citations=cites,
            confidence=confidence,
            source=source,
            model=model,
            validation=validation,
            status="PENDING_REVIEW",
        )
        return p, events

    # 2-4. LLM propose -> parse -> citation check -> validate.
    # ONE shared budget: at most s.max_llm_calls calls and s.total_deadline_seconds overall,
    # whatever the failure cause. When it runs out we fall back to the baseline fix.
    start = clock()
    violations: list[Violation] | None = None
    feedback: str | None = None
    failure = "no attempt made"
    for attempt in range(1, s.max_llm_calls + 1):
        remaining = s.total_deadline_seconds - (clock() - start)
        if remaining <= 0:
            audit("DEADLINE_EXCEEDED", attempt=attempt, deadline=s.total_deadline_seconds)
            return fallback("time limit reached")
        prompt = build_user_prompt(exc, timecard, relevant, violations, feedback)
        violations, feedback = None, None
        try:
            raw = llm.complete(SYSTEM_PROMPT, prompt, timeout=remaining)
        except LLMError as e:
            audit("LLM_ERROR", actor="llm", attempt=attempt, error=type(e).__name__)
            return fallback("LLM unavailable")
        audit("LLM_CALLED", actor="llm", attempt=attempt, model=llm.model_name)

        try:
            out = parse_llm_output(raw)
        except (ValidationError, ValueError) as e:
            audit("LLM_INVALID_OUTPUT", actor="llm", attempt=attempt, error=type(e).__name__)
            failure = "LLM output invalid"
            feedback = (
                "Your previous reply was not valid JSON for the schema. Reply with JSON only."
            )
            continue

        invented = [c for c in out.cited_clause_ids if c not in allowed]
        if invented or not out.cited_clause_ids:
            audit(
                "CITATION_REJECTED",
                actor="llm",
                attempt=attempt,
                invented=invented,
                cited=out.cited_clause_ids,
            )
            failure = "LLM citations not grounded in retrieved policy"
            feedback = (
                "Cite at least one clause id, and only ids from <policy_clauses>: "
                + ", ".join(allowed)
            )
            continue

        validation = validator(timecard, exc, out.fix, cfg)
        audit(
            "VALIDATION_RESULT",
            attempt=attempt,
            passed=validation.passed,
            violations=[v.code for v in validation.violations],
        )
        if not validation.passed:
            failure = "LLM fix failed validation"
            violations = validation.violations
            continue

        cites = [allowed[c] for c in dict.fromkeys(out.cited_clause_ids)]
        return _finish(
            out.fix,
            out.rationale,
            cites,
            out.confidence,
            ProposalSource.LLM,
            llm.model_name,
            validation,
        )

    audit("RETRY_BUDGET_EXHAUSTED", max_llm_calls=s.max_llm_calls)
    return fallback(failure)


def generate_proposal(exc: DetectedException, timecard: Timecard) -> Proposal:
    """Public entry point (CLAUDE.md section 8), wired to defaults from env."""
    s = AISettings.from_env()
    proposal, _events = run_workflow(
        exc,
        timecard,
        retriever=get_retriever(s),
        llm=build_llm(s),
        validator=always_pass_validator,  # TODO: Ankit's validate_proposal
        cfg=PolicyConfig(),
        settings=s,
    )
    return proposal
