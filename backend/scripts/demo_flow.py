"""Readable, step-by-step run of the AI flow for the demo video.

    python -m scripts.demo_flow                       # fixture MISSED_PUNCH, provider from env
    python -m scripts.demo_flow --case injection      # notes try to hijack the model
    python -m scripts.demo_flow --mock --mock-mode invalid_json    # show the fallback path
    python -m scripts.demo_flow --mock --mock-mode low_confidence  # show escalation
    python -m scripts.demo_flow --delay 1.5           # pause between steps while recording

Cases: fixture | overtime | conflict | breach | injection (see scripts/smoke_llm.py).
Provider/model/key come from env or backend/.env. The API key is never printed.
Nothing is written to a database: the flow stops at a PENDING_REVIEW proposal.
"""

from __future__ import annotations

import argparse
import dataclasses
import time
from collections.abc import Callable

from app.ai.llm import LLM, build_llm
from app.ai.prompts import sanitize_notes
from app.ai.retrieval import Retriever, build_query, get_retriever
from app.ai.settings import AISettings
from app.ai.workflow import Validator, always_pass_validator, run_workflow
from app.contracts import AuditEvent, PolicyConfig

from .smoke_llm import CASE_BUILDERS, load_env

RULE = "-" * 72


def describe_event(e: AuditEvent) -> str:
    """One plain-English line for an audit event."""
    p = e.payload
    t = e.event_type
    if t == "WORKFLOW_STARTED":
        return f"workflow started for a {p.get('exception_type')} exception"
    if t == "POLICY_RETRIEVED":
        ids = ", ".join(f"{c['clause_id']} ({c['score']})" for c in p["clauses"]) or "none"
        return f"retrieved policy clauses: {ids}; minimum score {p['min_score']}"
    if t == "LLM_CALLED":
        return f"LLM replied (attempt {p['attempt']}, model {p['model']})"
    if t == "LLM_ERROR":
        return f"LLM call failed ({p['error']}) on attempt {p['attempt']}: falling back"
    if t == "LLM_INVALID_OUTPUT":
        return f"LLM reply was not valid JSON for the schema (attempt {p['attempt']})"
    if t == "CITATION_REJECTED":
        return (
            f"LLM citations rejected (not among retrieved clauses): {p['invented'] or p['cited']}"
        )
    if t == "VALIDATION_RESULT":
        what = "rule-based fix" if p.get("target") == "baseline_fix" else "LLM fix"
        verdict = "PASSED" if p["passed"] else f"FAILED {p.get('violations', '')}"
        return f"validator checked the {what}: {verdict}"
    if t == "DEADLINE_EXCEEDED":
        return f"time limit of {p['deadline']} s reached before attempt {p['attempt']}"
    if t == "RETRY_BUDGET_EXHAUSTED":
        return f"LLM call budget ({p['max_llm_calls']}) used up"
    if t == "FALLBACK_USED":
        return f"using the rule-based baseline fix. Reason: {p['reason']}"
    if t == "ESCALATED":
        return f"ESCALATED to a manager with no AI fix. Reason: {p['reason']}"
    if t == "PROPOSAL_CREATED":
        return f"proposal created: status {p['status']}, source {p['source']}"
    return f"{t} {p}"


def run_demo(
    case_key: str,
    *,
    settings: AISettings,
    llm: LLM,
    retriever: Retriever,
    validator: Validator = always_pass_validator,
    out: Callable[[str], None] = print,
    delay: float = 0.0,
) -> str:
    """Run one case and narrate it. Returns the final proposal status."""

    def step(title: str) -> None:
        if delay:
            time.sleep(delay)
        out("")
        out(RULE)
        out(title)
        out(RULE)

    exc, tc = CASE_BUILDERS[case_key]()

    step("STEP 1  Exception found by the deterministic rules (no AI involved)")
    out(f"type: {exc.type.value}   severity: {exc.severity.value}   policy refs: {exc.policy_refs}")
    out(f"employee: {exc.employee_id}   date: {exc.work_date}")
    out(f"evidence: {exc.evidence}")
    base = exc.baseline_fix
    out(f"rule-based baseline fix: {base.action.value if base else 'none'}")
    out(f"timecard notes (UNTRUSTED, shown to the model only as data): {tc.notes!r}")
    out(f"  as embedded in the prompt: {sanitize_notes(tc.notes)}")

    step("STEP 2  Retrieve policy clauses")
    query = build_query(exc)
    found = retriever.retrieve(query, k=3)
    out(f"query: {query}")
    for c in found:
        keep = "kept" if c.score >= settings.retrieval_min_score else "below minimum"
        out(f"  {c.clause_id}  score {c.score:.3f}  ({keep})")
    out(f"retriever: {settings.retriever}   minimum score: {settings.retrieval_min_score}")

    step(
        "STEP 3  Propose, check, fall back or escalate "
        f"(LLM: {settings.llm_provider} / {llm.model_name})"
    )
    t0 = time.perf_counter()
    proposal, events = run_workflow(
        exc,
        tc,
        retriever=retriever,
        llm=llm,
        validator=validator,
        cfg=PolicyConfig(),
        settings=settings,
    )
    out(f"workflow finished in {(time.perf_counter() - t0) * 1000:.0f} ms")
    for i, e in enumerate(events, 1):
        out(f"  {i:>2}. [{e.actor:<6}] {describe_event(e)}")

    step("STEP 4  Audit events (what would be stored)")
    for e in events:
        out(f"  {e.ts.strftime('%H:%M:%S.%f')[:-3]}  {e.actor:<6}  {e.event_type}")

    step("STEP 5  Result for the manager")
    out(
        f"status: {proposal.status}   source: {proposal.source.value}   "
        f"confidence: {proposal.confidence:.2f}"
    )
    out(f"fix: {proposal.fix.action.value}")
    for ch in proposal.fix.changes:
        out(f"  change {ch.target_id} . {ch.field}: {ch.old!r} -> {ch.new!r}")
    out(f"rationale: {proposal.rationale}")
    for c in proposal.citations:
        out(f"cites {c.clause_id} (score {c.score:.2f}): {c.quote[:110]}...")
    if proposal.status == "ESCALATED":
        out("AI could not safely propose a fix. A manager has to handle this one.")
    else:
        out("Nothing is changed yet. A manager must approve or reject this proposal.")
    out("TODO(Raman): decision endpoint and audit storage are not wired into this script.")
    return proposal.status


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--case", choices=sorted(CASE_BUILDERS), default="fixture")
    ap.add_argument("--mock", action="store_true", help="force the deterministic mock LLM")
    ap.add_argument(
        "--mock-mode", default=None, help="mock only: ok | invalid_json | timeout | ..."
    )
    ap.add_argument("--delay", type=float, default=0.0, help="seconds to pause between steps")
    args = ap.parse_args(argv)

    load_env()
    settings = AISettings.from_env()
    if args.mock:
        settings = dataclasses.replace(settings, llm_provider="mock")
    if args.mock_mode:
        settings = dataclasses.replace(settings, llm_mock_mode=args.mock_mode)
    run_demo(
        args.case,
        settings=settings,
        llm=build_llm(settings),
        retriever=get_retriever(settings),
        delay=args.delay,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
