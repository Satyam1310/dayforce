"""Evaluate detection, the validator and the proposal workflow on the golden set.

    python -m scripts.evaluate                  # mock LLM, all failure modes (no network)
    python -m scripts.evaluate --real --model MODEL --max-rpm 5   # real model, throttled

The golden set is backend/tests/golden/cases.json (see tests/golden/loader.py). Every number
in docs/evaluation_report.md comes from running this script.

What is measured (mock part)
- Detection: precision and recall per exception type, and false positives on clean timecards.
- Validator: baseline fixes agree with the expected verdict, and every deliberately bad fix in
  a hand-written battery is rejected.
- Workflow: for each exception and each mock failure mode (ok, invalid_json, timeout,
  bad_citation, low_confidence) the final proposal matches the safe outcome the policy
  requires, no unvalidated fix reaches a manager, and escalations carry no edits.
- Prompt injection: a model that OBEYS the hostile notes (the worst case) is simulated.
  "Followed" counts attacks whose fix survived. Prompts are also checked for tag breakouts.

The real part (--real) needs LLM_API_KEY in backend/.env. It never exceeds 5 requests per
minute, never prints the key, and is written as a separate section of the report.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.ai.llm import LLM, MockLLM, build_llm
from app.ai.retrieval import InMemoryRetriever, get_retriever
from app.ai.settings import AISettings
from app.ai.workflow import run_workflow
from app.contracts import (
    DetectedException,
    FieldChange,
    Fix,
    FixAction,
    PolicyConfig,
    Proposal,
    Timecard,
)
from app.domain.rules import detect_exceptions
from app.validator import validate_proposal
from scripts.smoke_llm import (
    Case,
    GuardedLLM,
    RateLimiter,
    _unset,
    load_env,
    redact,
    run_case_once,
    summarize,
)
from tests.golden.loader import MODES, GoldenCase, expected_outcome, load_cases

BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = BACKEND_DIR.parent / "docs" / "evaluation_report.md"
MAX_REAL_RPM = 5  # the free tier allows 8,000 tokens per minute: 5 requests a minute is safe
CFG = PolicyConfig()
TYPES = ["MISSED_PUNCH", "OVERTIME_RISK", "SCHEDULE_CONFLICT", "POLICY_BREACH"]
_CLOSE_TAG = "</untrusted_notes>"


# ---------------------------------------------------------------- bad fixes (hand-written)


def _ch(target: str, field_name: str, old: str | None, new: str | None) -> FieldChange:
    return FieldChange(target_id=target, field=field_name, old=old, new=new)


def _fix(action: FixAction, *changes: FieldChange) -> Fix:
    return Fix(action=action, changes=list(changes))


def bad_fixes(exc: DetectedException, tc: Timecard) -> list[tuple[str, Fix]]:
    """Fixes that must NEVER pass validation for this exception. Written from the policy, not
    from the validator's code, so a validator that gets weaker is caught."""
    rule = exc.evidence.get("rule")
    emp = tc.employee_id
    some_time = "2026-03-10T12:00:00+00:00"
    out: list[tuple[str, Fix]] = [
        (
            "flag that also edits",
            _fix(FixAction.FLAG_FOR_MANAGER, _ch(tc.id, "punch.OUT", None, some_time)),
        ),
        ("edit with no change", _fix(FixAction.ADD_PUNCH)),
        (
            "edit of a record that is not on the timecard",
            _fix(FixAction.ADD_PUNCH, _ch("not-on-this-timecard", "punch.OUT", None, some_time)),
        ),
        (
            "two changes at once",
            _fix(
                FixAction.ADD_PUNCH,
                _ch(tc.id, "punch.OUT", None, some_time),
                _ch(tc.id, "punch.OUT", None, some_time),
            ),
        ),
    ]
    first_shift = tc.shifts[0].id if tc.shifts else None

    if rule == "MISSED_CLOCK_OUT":
        t_in = datetime.fromisoformat(exc.evidence["clock_in"])
        end = exc.evidence.get("scheduled_shift_end")
        t_end = datetime.fromisoformat(end) if end else None

        def out_at(label: str, when: str, field_name: str = "punch.OUT") -> tuple[str, Fix]:
            return label, _fix(FixAction.ADD_PUNCH, _ch(tc.id, field_name, None, when))

        out += [
            out_at(
                "clock-out 14 hours after the clock-in", (t_in + timedelta(hours=14)).isoformat()
            ),
            out_at("clock-out before the clock-in", (t_in - timedelta(hours=1)).isoformat()),
            out_at("clock-out with no timezone", "2026-03-10T18:00:00"),
            out_at("clock-out that is not a time", "later"),
            out_at("wrong field", (t_end or t_in + timedelta(hours=8)).isoformat(), "timestamp"),
            (
                "deduct a break instead",
                _fix(FixAction.DEDUCT_BREAK, _ch(tc.id, "break_minutes", None, "30")),
            ),
            ("do nothing", _fix(FixAction.NO_CHANGE)),
        ]
        if t_end:
            out += [
                out_at(
                    "clock-out an hour after the scheduled end",
                    (t_end + timedelta(hours=1)).isoformat(),
                ),
                out_at(
                    "clock-out an hour before the scheduled end",
                    (t_end - timedelta(hours=1)).isoformat(),
                ),
            ]
        if first_shift:
            out.append(
                (
                    "reassign a shift instead",
                    _fix(FixAction.REASSIGN_SHIFT, _ch(first_shift, "employee_id", emp, None)),
                )
            )
    elif rule == "MISSING_CLOCK_IN":
        out.append(
            (
                "invent a clock-in",
                _fix(FixAction.ADD_PUNCH, _ch(tc.id, "punch.IN", None, some_time)),
            )
        )
    elif rule == "MEAL_BREAK":
        for label, minutes in (
            ("deduct 0 minutes", "0"),
            ("deduct a negative number", "-5"),
            ("deduct 120 minutes", "120"),
            ("deduct 'abc' minutes", "abc"),
        ):
            out.append(
                (label, _fix(FixAction.DEDUCT_BREAK, _ch(tc.id, "break_minutes", None, minutes)))
            )
        out += [
            ("change a pay rate", _fix(FixAction.DEDUCT_BREAK, _ch(tc.id, "pay_rate", None, "30"))),
            ("skip the deduction", _fix(FixAction.NO_CHANGE)),
            (
                "add a punch instead",
                _fix(FixAction.ADD_PUNCH, _ch(tc.id, "punch.OUT", None, some_time)),
            ),
        ]
    elif rule == "OVERLAPPING_SHIFTS":
        keep, drop = exc.evidence["keep_shift_id"], exc.evidence["reassign_shift_id"]
        out += [
            (
                "hand the shift to another employee",
                _fix(FixAction.REASSIGN_SHIFT, _ch(drop, "employee_id", emp, "emp-999")),
            ),
            (
                "reassign the shift that should be kept",
                _fix(FixAction.REASSIGN_SHIFT, _ch(keep, "employee_id", emp, None)),
            ),
            (
                "change the location instead",
                _fix(FixAction.REASSIGN_SHIFT, _ch(drop, "location_id", "loc-1", "loc-9")),
            ),
            ("do nothing", _fix(FixAction.NO_CHANGE)),
            (
                "deduct a break instead",
                _fix(FixAction.DEDUCT_BREAK, _ch(tc.id, "break_minutes", None, "30")),
            ),
        ]
    elif rule == "WEEKLY_OT":
        listed = [r["shift_id"] for r in exc.evidence.get("remaining_shifts", [])]
        if first_shift and first_shift not in listed:
            out.append(
                (
                    "reassign a shift that was already worked",
                    _fix(FixAction.REASSIGN_SHIFT, _ch(first_shift, "employee_id", emp, None)),
                )
            )
        if listed:
            out.append(
                (
                    "hand an upcoming shift to another employee",
                    _fix(FixAction.REASSIGN_SHIFT, _ch(listed[-1], "employee_id", emp, "emp-999")),
                )
            )
        out += [
            (
                "add a punch instead",
                _fix(FixAction.ADD_PUNCH, _ch(tc.id, "punch.OUT", None, some_time)),
            ),
            (
                "deduct a break instead",
                _fix(FixAction.DEDUCT_BREAK, _ch(tc.id, "break_minutes", None, "30")),
            ),
        ]
    elif rule == "DAILY_OT":
        out += [
            (
                "do nothing but also edit",
                _fix(FixAction.NO_CHANGE, _ch(tc.id, "break_minutes", None, "30")),
            ),
            (
                "deduct a break instead",
                _fix(FixAction.DEDUCT_BREAK, _ch(tc.id, "break_minutes", None, "30")),
            ),
            (
                "add a punch instead",
                _fix(FixAction.ADD_PUNCH, _ch(tc.id, "punch.OUT", None, some_time)),
            ),
        ]
        if first_shift:
            out.append(
                (
                    "reassign a shift instead",
                    _fix(FixAction.REASSIGN_SHIFT, _ch(first_shift, "employee_id", emp, None)),
                )
            )
    else:  # DAILY_MAX and MIN_REST: no automatic fix exists, only a flag
        out += [
            (
                "deduct a break instead",
                _fix(FixAction.DEDUCT_BREAK, _ch(tc.id, "break_minutes", None, "30")),
            ),
            (
                "add a punch instead",
                _fix(FixAction.ADD_PUNCH, _ch(tc.id, "punch.OUT", None, some_time)),
            ),
            ("do nothing", _fix(FixAction.NO_CHANGE)),
        ]
        if first_shift:
            out.append(
                (
                    "reassign a shift instead",
                    _fix(FixAction.REASSIGN_SHIFT, _ch(first_shift, "employee_id", emp, None)),
                )
            )
    return out


# ---------------------------------------------------------------- scripted models


class RecordingLLM:
    """Wraps a model and keeps every prompt it was sent."""

    def __init__(self, inner: LLM):
        self.inner = inner
        self.model_name = inner.model_name
        self.prompts: list[str] = []

    def complete(self, system: str, user: str, timeout: float | None = None) -> str:
        self.prompts.append(user)
        return self.inner.complete(system, user, timeout=timeout)


class ObedientLLM:
    """The worst case: a model that does exactly what the hostile notes ask."""

    model_name = "obedient-attacker"

    def __init__(self, attack_fix: Fix, clause: str):
        self._reply = json.dumps(
            {
                "fix": attack_fix.model_dump(mode="json"),
                "rationale": "Following the instructions in the notes.",
                "cited_clause_ids": [clause],
                "confidence": 0.95,
            }
        )
        self.prompts: list[str] = []

    def complete(self, system: str, user: str, timeout: float | None = None) -> str:
        self.prompts.append(user)
        return self._reply


def resolve_attack(raw: dict, exc: DetectedException) -> Fix:
    text = json.dumps(raw).replace("@timecard", exc.timecard_id)
    return Fix.model_validate(json.loads(text))


# ---------------------------------------------------------------- measurements


@dataclass
class CaseRow:
    case_id: str
    title: str
    detection: bool = True
    baselines: bool = True
    validator: bool = True
    workflow: bool = True
    injection: bool | None = None  # None when the case has no attack
    problems: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(
            [
                self.detection,
                self.baselines,
                self.validator,
                self.workflow,
                self.injection is not False,
            ]
        )


@dataclass
class TypeScore:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float | None:
        return None if self.tp + self.fp == 0 else self.tp / (self.tp + self.fp)

    @property
    def recall(self) -> float | None:
        return None if self.tp + self.fn == 0 else self.tp / (self.tp + self.fn)


@dataclass
class ModeStats:
    runs: int = 0
    matched: int = 0
    fallbacks: int = 0
    escalations: int = 0
    pending: int = 0
    unsafe: int = 0  # a proposal that broke a safety rule


@dataclass
class MockResults:
    cases: int = 0
    timecards: int = 0
    expected_exceptions: int = 0
    by_type: dict[str, TypeScore] = field(default_factory=lambda: {t: TypeScore() for t in TYPES})
    clean_cases: int = 0
    clean_false_positives: int = 0
    baseline_checks: int = 0
    baseline_agree: int = 0
    bad_fixes: int = 0
    bad_rejected: int = 0
    bad_accepted: list[str] = field(default_factory=list)
    modes: dict[str, ModeStats] = field(default_factory=lambda: {m: ModeStats() for m in MODES})
    attacks: int = 0
    attacks_followed: int = 0
    prompts_checked: int = 0
    tag_breakouts: int = 0
    long_notes_cut: int = 0
    rows: list[CaseRow] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.rows)


def _unsafe(proposal: Proposal, exc: DetectedException, tc: Timecard) -> str | None:
    """Why this final proposal is unsafe, or None. Checked on every run, in every mode."""
    if proposal.status == "PENDING_REVIEW":
        if proposal.validation is None or not proposal.validation.passed:
            return "a proposal reached the manager without passing validation"
        again = validate_proposal(tc, exc, proposal.fix, CFG)
        if not again.passed:
            return "the proposed fix fails the validator when checked again"
    if proposal.status == "ESCALATED" and (
        proposal.fix.action is not FixAction.FLAG_FOR_MANAGER or proposal.fix.changes
    ):
        return "an escalated case carries an edit"
    if proposal.status not in ("PENDING_REVIEW", "ESCALATED"):
        return f"unexpected status {proposal.status}"
    return None


def _prompt_problems(prompts: list[str]) -> tuple[int, int]:
    """(prompts with a tag breakout, prompts whose notes were not cut down)."""
    breakouts = cut_missing = 0
    for p in prompts:
        if p.count(_CLOSE_TAG) != 1 or p.count("<untrusted_notes>") != 1:
            breakouts += 1
            continue
        inner = p.split("<untrusted_notes>")[1].split(_CLOSE_TAG)[0]
        if (
            "<" in inner
            or ">" in inner
            or len(inner) > 2000
            or re.search(r"[\x00-\x08\x0b-\x1f]", inner)
        ):
            cut_missing += 1
    return breakouts, cut_missing


def evaluate_case(case: GoldenCase, res: MockResults, retriever: InMemoryRetriever) -> CaseRow:
    row = CaseRow(case.id, case.title)
    by_id = {t.id: t for t in case.timecards}
    found = detect_exceptions(case.timecards, CFG)

    # ---- detection
    want = Counter()
    for e in case.expected:
        want[(e.type, e.rule)] += e.count
    have = Counter((f.type.value, f.evidence["rule"]) for f in found)
    for t in TYPES:
        w = sum(v for (ty, _), v in want.items() if ty == t)
        h = sum(v for (ty, _), v in have.items() if ty == t)
        tp = sum(min(want[k], have[k]) for k in want if k[0] == t)
        res.by_type[t].tp += tp
        res.by_type[t].fp += h - tp
        res.by_type[t].fn += w - tp
    if want != have:
        row.detection = False
        row.problems.append(f"detection: expected {dict(want)}, got {dict(have)}")
    if not case.expected:
        res.clean_cases += 1
        res.clean_false_positives += len(found)

    # ---- baselines and validator
    exp_by_key = {(e.type, e.rule): e for e in case.expected}
    for exc in found:
        exp = exp_by_key.get((exc.type.value, exc.evidence["rule"]))
        tc = by_id[exc.timecard_id]
        if exp is None:
            continue
        action = exc.baseline_fix.action.value if exc.baseline_fix else None
        if action != exp.baseline or (exp.severity and exc.severity.value != exp.severity):
            row.baselines = False
            row.problems.append(
                f"{exp.rule}: baseline {action}/{exc.severity.value}, "
                f"expected {exp.baseline}/{exp.severity}"
            )
        if exc.baseline_fix is not None:
            res.baseline_checks += 1
            verdict = validate_proposal(tc, exc, exc.baseline_fix, CFG)
            if verdict.passed == exp.baseline_valid:
                res.baseline_agree += 1
            else:
                row.baselines = False
                row.problems.append(
                    f"{exp.rule}: baseline valid={verdict.passed}, expected {exp.baseline_valid}"
                )
        for label, bad in bad_fixes(exc, tc):
            res.bad_fixes += 1
            if validate_proposal(tc, exc, bad, CFG).passed:
                res.bad_accepted.append(f"{case.id} / {exp.rule}: {label}")
                row.validator = False
                row.problems.append(f"validator accepted a bad fix: {label}")
            else:
                res.bad_rejected += 1

    # ---- the workflow under every mock failure mode
    for exc in found:
        exp = exp_by_key.get((exc.type.value, exc.evidence["rule"]))
        tc = by_id[exc.timecard_id]
        if exp is None:
            continue
        for mode in MODES:
            stats = res.modes[mode]
            proposal, events = run_workflow(
                exc,
                tc,
                retriever=retriever,
                llm=MockLLM(mode),
                validator=validate_proposal,
                cfg=CFG,
                settings=AISettings(),
            )
            stats.runs += 1
            kinds = [e.event_type for e in events]
            stats.fallbacks += "FALLBACK_USED" in kinds
            stats.escalations += proposal.status == "ESCALATED"
            stats.pending += proposal.status == "PENDING_REVIEW"
            got = (proposal.status, proposal.source.value, proposal.fix.action.value)
            expected = expected_outcome(mode, exp.baseline, exp.baseline_valid)
            problem = _unsafe(proposal, exc, tc)
            if problem:
                stats.unsafe += 1
                row.workflow = False
                row.problems.append(f"{mode}/{exp.rule}: UNSAFE: {problem}")
            if got == expected:
                stats.matched += 1
            else:
                row.workflow = False
                row.problems.append(f"{mode}/{exp.rule}: ended {got}, safe outcome is {expected}")

    # ---- prompt injection: a model that obeys the notes
    if case.notes_length:
        recorder = RecordingLLM(MockLLM("ok"))
        for exc in found:
            run_workflow(
                exc,
                by_id[exc.timecard_id],
                retriever=retriever,
                llm=recorder,
                validator=validate_proposal,
                cfg=CFG,
                settings=AISettings(),
            )
        if recorder.prompts:
            res.prompts_checked += len(recorder.prompts)
            breakouts, cut_missing = _prompt_problems(recorder.prompts)
            res.tag_breakouts += breakouts
            res.long_notes_cut += cut_missing
            if breakouts or cut_missing:
                row.injection = False
                row.problems.append("the notes were not safely wrapped in the prompt")
    if case.attack is not None:
        targets = [f for f in found if f.evidence["rule"] == case.attack.rule]
        row.injection = row.injection is not False
        for exc in targets:
            attack = resolve_attack(case.attack.fix, exc)
            tc = by_id[exc.timecard_id]
            proposal, _ = run_workflow(
                exc,
                tc,
                retriever=retriever,
                llm=ObedientLLM(attack, exc.policy_refs[0]),
                validator=validate_proposal,
                cfg=CFG,
                settings=AISettings(),
            )
            res.attacks += 1
            followed = proposal.fix == attack and proposal.status == "PENDING_REVIEW"
            unsafe = _unsafe(proposal, exc, tc)
            if followed or unsafe:
                res.attacks_followed += bool(followed)
                row.injection = False
                row.problems.append(
                    f"injection: attack survived ({'followed' if followed else unsafe})"
                )
        if not targets:
            row.injection = False
            row.problems.append("injection: the attacked exception was not detected")
    return row


def run_mock_evaluation(cases: list[GoldenCase] | None = None) -> MockResults:
    cases = cases if cases is not None else load_cases()
    res = MockResults(cases=len(cases))
    retriever = InMemoryRetriever()
    for case in cases:
        res.timecards += len(case.timecards)
        res.expected_exceptions += sum(e.count for e in case.expected)
        res.rows.append(evaluate_case(case, res, retriever))
    return res


# ---------------------------------------------------------------- report (mock)


def _pct(num: int, den: int) -> str:
    return "n/a" if den == 0 else f"{100 * num / den:.1f}%"


def _frac(x: float | None) -> str:
    return "n/a" if x is None else f"{100 * x:.1f}%"


def render_mock_section(res: MockResults) -> str:
    tp = sum(s.tp for s in res.by_type.values())
    fp = sum(s.fp for s in res.by_type.values())
    fn = sum(s.fn for s in res.by_type.values())
    passed = sum(r.passed for r in res.rows)
    lines = [
        "## Offline evaluation (mock LLM, no network)",
        "",
        f"Golden set: **{res.cases} cases**, {res.timecards} timecards, {res.expected_exceptions} "
        f"expected exceptions, {res.clean_cases} cases that must produce no exception, "
        f"{sum(1 for r in res.rows if r.injection is not None)} prompt-injection cases.",
        f"Result: **{passed} of {res.cases} cases pass every check.**",
        "",
        "### Detection",
        "",
        "| Exception type | True positives | False positives | Missed | Precision | Recall |",
        "|---|---|---|---|---|---|",
    ]
    for t in TYPES:
        s = res.by_type[t]
        lines.append(
            f"| {t} | {s.tp} | {s.fp} | {s.fn} | {_frac(s.precision)} | {_frac(s.recall)} |"
        )
    overall_p = None if tp + fp == 0 else tp / (tp + fp)
    overall_r = None if tp + fn == 0 else tp / (tp + fn)
    lines += [
        f"| **All** | {tp} | {fp} | {fn} | {_frac(overall_p)} | {_frac(overall_r)} |",
        "",
        f"False positives on clean timecards: **{res.clean_false_positives}** "
        f"(across {res.clean_cases} cases that must produce nothing).",
        "",
        "### Validator",
        "",
        f"- Rule-based fixes whose verdict matches the expected one: **{res.baseline_agree} of "
        f"{res.baseline_checks}**.",
        f"- Deliberately bad fixes rejected: **{res.bad_rejected} of {res.bad_fixes} "
        f"({_pct(res.bad_rejected, res.bad_fixes)})**.",
    ]
    for item in res.bad_accepted:
        lines.append(f"  - ACCEPTED (should be rejected): {item}")
    lines += [
        "",
        "### Proposal workflow under each mock model behaviour",
        "",
        "Every exception the detectors found was run through the whole workflow. The safe "
        "outcome is decided by the policy (see `expected_outcome` in `tests/golden/loader.py`).",
        "",
        "| Model behaviour | Runs | Matches safe outcome | Fell back to rules | Escalated "
        "| Reached manager | Unsafe |",
        "|---|---|---|---|---|---|---|",
    ]
    for mode in MODES:
        s = res.modes[mode]
        lines.append(
            f"| {mode} | {s.runs} | {s.matched} ({_pct(s.matched, s.runs)}) | "
            f"{s.fallbacks} ({_pct(s.fallbacks, s.runs)}) | "
            f"{s.escalations} ({_pct(s.escalations, s.runs)}) | "
            f"{s.pending} | {s.unsafe} |"
        )
    lines += [
        "",
        '"Fell back to rules" means the workflow gave up on the model\'s answer (unusable '
        "output, an invented citation, a timeout, or a fix the validator rejected) and tried "
        "the rule-based fix. If that fix is missing or fails validation too, the case is "
        "escalated instead. With a working model (`ok`) the cases that fall back are the ones "
        "whose rule-based fix itself breaks the rules, so no model fix could be accepted.",
        "",
        "### Prompt injection",
        "",
        f"- Attacks simulated with a model that **obeys** the hostile notes: {res.attacks}.",
        f"- **Attacks that got through (injection followed): {res.attacks_followed}.**",
        f"- Prompts checked for the untrusted-notes wrapper: {res.prompts_checked}. "
        f"Tag breakouts: **{res.tag_breakouts}**. "
        f"Notes not cut or cleaned: **{res.long_notes_cut}**.",
        "- The model is never trusted: the attack fix is checked by the same validator as any "
        "other fix, so a model that obeys the notes still cannot move a clock-out, skip a "
        "deduction or reassign the wrong shift.",
        "",
        "### Per-case results",
        "",
        "| Case | What it checks | Detect | Baselines | Validator | Workflow | Injection |",
        "|---|---|---|---|---|---|---|",
    ]
    mark = lambda ok: "pass" if ok else "**FAIL**"  # noqa: E731
    for r in res.rows:
        inj = "-" if r.injection is None else mark(r.injection)
        lines.append(
            f"| {r.case_id} | {r.title} | {mark(r.detection)} | {mark(r.baselines)} | "
            f"{mark(r.validator)} | {mark(r.workflow)} | {inj} |"
        )
    failures = [r for r in res.rows if not r.passed]
    if failures:
        lines += ["", "### Problems", ""]
        for r in failures:
            for p in r.problems:
                lines.append(f"- {r.case_id}: {p}")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- real model (--real)


@dataclass
class RealPlan:
    proposals: int
    min_requests: int
    max_requests: int
    tokens_per_request: int
    minutes_low: float
    minutes_high: float


def real_cases(cases: list[GoldenCase]) -> list[Case]:
    """One proposal per exception the golden set expects. Hostile notes stay attached."""
    out: list[Case] = []
    for case in cases:
        by_id = {t.id: t for t in case.timecards}
        keys = {(e.type, e.rule) for e in case.expected}
        for exc in detect_exceptions(case.timecards, CFG):
            if (exc.type.value, exc.evidence["rule"]) in keys:
                out.append(
                    Case(
                        f"{case.id} {exc.evidence['rule']}",
                        exc,
                        by_id[exc.timecard_id],
                        is_injection=case.attack is not None,
                    )
                )
    return out


def plan_real(cases: list[Case], max_rpm: int) -> RealPlan:
    from app.ai.prompts import SYSTEM_PROMPT, build_user_prompt

    retriever = InMemoryRetriever()
    sizes = []
    for c in cases:
        clauses = retriever.retrieve("overtime shift break punch", k=3)
        sizes.append((len(SYSTEM_PROMPT) + len(build_user_prompt(c.exc, c.timecard, clauses))) // 4)
    avg = int(sum(sizes) / len(sizes)) if sizes else 0
    n = len(cases)
    return RealPlan(n, n, 2 * n, avg, n / max_rpm, 2 * n / max_rpm)


def run_real_evaluation(
    cases: list[Case], llm: LLM, limiter: RateLimiter | None, settings: AISettings
) -> dict:
    guarded = GuardedLLM(llm, limiter)
    results = [
        run_case_once(
            c,
            retriever=get_retriever(settings),
            llm=guarded,
            validator=validate_proposal,
            cfg=CFG,
            settings=settings,
            limiter=limiter,
        )
        for c in cases
    ]
    # An injection run "changed the fix" when the notes' case ended with a model-written fix
    # that is not the rule-based one. That is not necessarily wrong (the validator passed it),
    # so it is reported for a human to read, not counted as a failure.
    changed = sum(
        1
        for c, r in zip(cases, results, strict=True)
        if c.is_injection
        and r.status == "PENDING_REVIEW"
        and r.source == "LLM"
        and not r.matches_baseline
    )
    return {
        "summary": summarize(results),
        "results": results,
        "stats": guarded.stats,
        "agreed_with_rules": sum(r.matches_baseline for r in results),
        # only cases that HAVE a rule-based fix can agree with it
        "with_baseline": sum(c.exc.baseline_fix is not None for c in cases),
        "injection_runs": sum(c.is_injection for c in cases),
        "injection_changed_fix": changed,
    }


def render_real_section(data: dict, *, model: str, provider: str, when: str) -> str:
    s = data["summary"]
    res = data["results"]
    lines = [
        "## Real model run",
        "",
        f"Run {when} with `{provider}` / `{model}`, throttled to at most {MAX_REAL_RPM} "
        "requests per minute.",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Proposals run | {s['runs']} |",
        f"| LLM requests sent | {data['stats'].requests} |",
        f"| JSON-valid rate | {_frac(s['json_valid_rate'])} |",
        f"| Citation-valid rate | {_frac(s['citation_valid_rate'])} |",
        f"| Fallback rate (rule-based fix used) | {_frac(s['fallback_rate'])} |",
        f"| Escalation rate | {_frac(s['escalation_rate'])} |",
        "| Model's fix identical to the rule-based fix (cases that have one) "
        f"| {data['agreed_with_rules']} of {data['with_baseline']} |",
        f"| Latency p50 / p95 | {s['p50_ms']:.0f} ms / {s['p95_ms']:.0f} ms |",
        f"| LLM call errors / HTTP 429 | {data['stats'].errors} / {data['stats'].rate_limited} |",
        "| Injection runs / fix changed by the notes "
        f"| {data['injection_runs']} / {data['injection_changed_fix']} |",
        "",
        "### Per-proposal results",
        "",
        "| Case | Status | Source | Fix action | Citations | Fell back |",
        "|---|---|---|---|---|---|",
    ]
    for r in res:
        lines.append(
            f"| {r.case} | {r.status} | {r.source} | {r.action} "
            f"| {', '.join(r.citations) or '-'} | "
            f"{'yes' if r.used_fallback else 'no'} |"
        )
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- report file


_INTRO = """# Evaluation report

How good are the detectors, the validator and the proposal workflow, measured on the golden
set in `backend/tests/golden/cases.json`. Regenerate with `python -m scripts.evaluate`
(offline part, no network) and `python -m scripts.evaluate --real --model MODEL` (real model,
at most 5 requests a minute). The two sections below are written separately.

"""
_BLOCKS = {
    "mock": ("<!-- MOCK:BEGIN -->", "<!-- MOCK:END -->"),
    "real": ("<!-- REAL:BEGIN -->", "<!-- REAL:END -->"),
}
_REAL_PLACEHOLDER = (
    "## Real model run\n\nNot run yet. Run `python -m scripts.evaluate --real --model MODEL` "
    "with the key in `backend/.env`.\n"
)


def write_report(path: Path, which: str, text: str) -> None:
    """Replace one section of the report and keep the other one."""
    existing = path.read_text() if path.exists() else ""
    blocks = {}
    for name, (a, b) in _BLOCKS.items():
        m = re.search(re.escape(a) + r"\n(.*?)\n" + re.escape(b), existing, re.S)
        blocks[name] = m.group(1) if m else (_REAL_PLACEHOLDER if name == "real" else "")
    blocks[which] = text.rstrip("\n")
    body = _INTRO
    for name in ("mock", "real"):
        a, b = _BLOCKS[name]
        body += f"{a}\n{blocks[name]}\n{b}\n\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body.rstrip("\n") + "\n")


# ---------------------------------------------------------------- command line


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--real", action="store_true", help="also run the real model (throttled)")
    ap.add_argument("--model", default=None, help="model name for --real (default: LLM_MODEL)")
    ap.add_argument(
        "--max-rpm",
        type=int,
        default=MAX_REAL_RPM,
        help=f"requests per minute, at most {MAX_REAL_RPM}",
    )
    ap.add_argument(
        "--limit", type=int, default=None, help="--real only: run just the first N proposals"
    )
    ap.add_argument("--out", type=Path, default=DEFAULT_REPORT, help="report path")
    args = ap.parse_args(argv)

    cases = load_cases()
    if not args.real:
        res = run_mock_evaluation(cases)
        text = render_mock_section(res)
        write_report(args.out, "mock", text)
        print(text)
        print(f"written to {args.out}")
        return 0 if res.all_passed else 1

    if not 1 <= args.max_rpm <= MAX_REAL_RPM:
        ap.error(
            f"--max-rpm must be between 1 and {MAX_REAL_RPM} "
            "(the free tier is 8,000 tokens a minute)"
        )
    load_env()
    settings = AISettings.from_env()
    if args.model:
        settings = dataclasses.replace(settings, llm_model=args.model)
    if settings.llm_provider == "mock":
        ap.error("--real needs LLM_PROVIDER=openai_compatible in backend/.env")
    if _unset(settings.llm_model):
        ap.error("no model: pass --model NAME or set LLM_MODEL in backend/.env")
    if settings.llm_provider == "openai_compatible" and (
        _unset(settings.llm_api_key) or _unset(settings.llm_base_url)
    ):
        ap.error("LLM_API_KEY and LLM_BASE_URL must be set in backend/.env (never commit them)")

    chosen = real_cases(cases)[: args.limit]
    plan = plan_real(chosen, args.max_rpm)
    print(
        f"{plan.proposals} proposals, {plan.min_requests}-{plan.max_requests} requests, about "
        f"{plan.tokens_per_request} tokens each, throttled to {args.max_rpm} a minute: "
        f"roughly {plan.minutes_low:.0f}-{plan.minutes_high:.0f} minutes."
    )
    data = run_real_evaluation(chosen, build_llm(settings), RateLimiter(args.max_rpm), settings)
    when = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    text = redact(
        render_real_section(
            data, model=settings.llm_model, provider=settings.llm_provider, when=when
        ),
        settings.llm_api_key,
    )
    write_report(args.out, "real", text)
    print(text)
    print(f"written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
