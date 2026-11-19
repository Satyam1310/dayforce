"""Compliance validator (CLAUDE.md sections 2, 8 and 10). Pure: no I/O, no AI, no clock.

    validate_proposal(timecard, exception, fix, cfg) -> ValidationResult

It does not trust the fix, whoever wrote it (the model or the rules). It checks the fix is the
right kind for the exception, only touches data that belongs to the exception, then applies it
to a COPY of the timecard and re-runs the real detectors on the copy. The input is never
changed. `Timecard.notes` is never read.

A fix fails when it: is the wrong action for the exception; targets or changes data outside the
exception; would make a shift longer than 12 hours; breaks the 10 hour rest rule; deducts the
wrong amount of break; leaves the original problem in place; or creates a new violation.

Limits, stated plainly:
- Only one timecard is visible here. Rest against neighbouring days and the shifts that remain
  later in a week are checked from facts the detector stored in `exception.evidence`
  (`next_work_start`, `remaining_shifts`). If a fix cites a shift the evidence does not list,
  it fails as unverifiable instead of being guessed at.
- A fix may only reassign a shift to nobody (`new` is null, leaving it open for the manager).
  Reassigning to another employee fails, because that employee's hours cannot be checked here.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from app.contracts import (
    DetectedException,
    ExceptionType,
    Fix,
    FixAction,
    PolicyConfig,
    Punch,
    PunchType,
    Timecard,
    ValidationResult,
    Violation,
)
from app.domain.rules import (
    RULE_DAILY_MAX,
    RULE_MEAL_BREAK,
    RULE_MIN_REST,
    RULE_MISSED_OUT,
    RULE_OVERLAP,
    RULE_WEEKLY_OT,
    detect_exceptions,
)
from app.domain.rules.analysis import analyze_punches, aware, hours, hours_td, scheduled_shift_for

_ALLOWED_ACTIONS = {
    ExceptionType.MISSED_PUNCH: {FixAction.ADD_PUNCH, FixAction.FLAG_FOR_MANAGER},
    ExceptionType.OVERTIME_RISK: {
        FixAction.REASSIGN_SHIFT,
        FixAction.NO_CHANGE,
        FixAction.FLAG_FOR_MANAGER,
    },
    ExceptionType.SCHEDULE_CONFLICT: {FixAction.REASSIGN_SHIFT, FixAction.FLAG_FOR_MANAGER},
    ExceptionType.POLICY_BREACH: {FixAction.DEDUCT_BREAK, FixAction.FLAG_FOR_MANAGER},
}
_DEFAULT_RULE = {
    ExceptionType.MISSED_PUNCH: RULE_MISSED_OUT,
    ExceptionType.SCHEDULE_CONFLICT: RULE_OVERLAP,
}
# A meal break cannot be judged on a day that was still missing a punch, so finishing the day
# may reveal one without having "created" it: it is reported as its own exception afterwards.
# The 12 hour daily limit is different: a fix that pushes the day over it must fail.
_NEEDS_COMPLETE_DAY = {RULE_MEAL_BREAK}
_VIOLATION_TYPES = {
    ExceptionType.POLICY_BREACH,
    ExceptionType.SCHEDULE_CONFLICT,
    ExceptionType.MISSED_PUNCH,
}


def requires_manager_approval(fix: Fix) -> bool:
    """POL-TA-07: no edit is final until a manager approves it. Every fix that changes data
    is only a proposal (Proposal.status is PENDING_REVIEW until a manager decides)."""
    return fix.action not in (FixAction.FLAG_FOR_MANAGER, FixAction.NO_CHANGE)


def _rule_of(exc: DetectedException) -> str | None:
    return exc.evidence.get("rule") or _DEFAULT_RULE.get(exc.type)


def _keys(excs: list[DetectedException]) -> set[tuple]:
    out = set()
    for e in excs:
        if e.type in _VIOLATION_TYPES:
            out.add(
                (
                    e.type,
                    e.evidence.get("rule"),
                    e.evidence.get("shift_a"),
                    e.evidence.get("shift_b"),
                )
            )
    return out


class _Findings:
    """Collects violations. Calling it records one; truthiness says whether any exist."""

    def __init__(self, default_clause: str | None):
        self.items: list[Violation] = []
        self._default = default_clause

    def __call__(self, code: str, message: str, clause: str | None = None) -> None:
        self.items.append(Violation(code=code, message=message, clause_id=clause or self._default))

    def __bool__(self) -> bool:
        return bool(self.items)


def validate_proposal(
    timecard: Timecard,
    exception: DetectedException,
    fix: Fix,
    cfg: PolicyConfig | None = None,
) -> ValidationResult:
    cfg = cfg or PolicyConfig()
    first_ref = exception.policy_refs[0] if exception.policy_refs else None
    bad = _Findings(first_ref)

    def done() -> ValidationResult:
        return ValidationResult(passed=not bad, violations=bad.items)

    # 1. Is this kind of fix allowed for this kind of exception?
    if fix.action not in _ALLOWED_ACTIONS[exception.type]:
        bad(
            "ACTION_NOT_ALLOWED",
            f"{fix.action.value} is not an allowed fix for a {exception.type.value} exception.",
        )
        return done()

    # 2. Flag and no-change fixes must not carry edits.
    if fix.action in (FixAction.FLAG_FOR_MANAGER, FixAction.NO_CHANGE):
        if fix.changes:
            bad("CHANGES_NOT_ALLOWED", f"{fix.action.value} must not contain any changes.")
        return done()

    if not fix.changes:
        bad("EMPTY_FIX", f"{fix.action.value} needs a change to apply, but has none.")
        return done()
    if len(fix.changes) != 1:
        bad("TOO_MANY_CHANGES", f"{fix.action.value} must make exactly one change.")
        return done()

    # 3. Does it only touch data that belongs to this exception?
    known = {timecard.id, *(p.id for p in timecard.punches), *(s.id for s in timecard.shifts)}
    ev = exception.evidence
    known |= {
        ev[k] for k in ("shift_a", "shift_b", "keep_shift_id", "reassign_shift_id") if k in ev
    }
    known |= {r["shift_id"] for r in ev.get("remaining_shifts", [])}
    change = fix.changes[0]
    if change.target_id not in known:
        bad(
            "UNRELATED_TARGET",
            f"The change targets {change.target_id}, which is not part of this exception.",
        )
        return done()

    if fix.action is FixAction.ADD_PUNCH:
        _check_add_punch(timecard, exception, change, cfg, bad)
    elif fix.action is FixAction.DEDUCT_BREAK:
        _check_deduct_break(timecard, exception, change, cfg, bad)
    else:
        _check_reassign(timecard, exception, change, cfg, bad)
    return done()


# ---------------------------------------------------------------- ADD_PUNCH (POL-TA-01)


def _check_add_punch(tc, exc, change, cfg, bad) -> None:
    if _rule_of(exc) != RULE_MISSED_OUT:
        bad("ACTION_NOT_ALLOWED", "ADD_PUNCH only fixes a missing clock-out.", "POL-TA-01")
        return
    if change.field != "punch.OUT" or change.target_id != tc.id:
        bad(
            "FIELD_NOT_ALLOWED",
            "A missed punch fix may only add 'punch.OUT' on this timecard, "
            f"not '{change.field}' on {change.target_id}.",
            "POL-TA-01",
        )
        return
    if change.old is not None:
        bad(
            "OLD_VALUE_MISMATCH", "A clock-out is being added, so 'old' must be empty.", "POL-TA-01"
        )
        return
    facts = analyze_punches(tc)
    if len(facts.unmatched_in) != 1:
        bad(
            "NO_MISSING_PUNCH",
            "The timecard does not have exactly one clock-in without a clock-out.",
            "POL-TA-01",
        )
        return
    clock_in = aware(facts.unmatched_in[0].timestamp)
    try:
        out_time = datetime.fromisoformat(change.new or "")
    except ValueError:
        bad("BAD_TIMESTAMP", f"'{change.new}' is not an ISO-8601 time.", "POL-TA-01")
        return
    if out_time.tzinfo is None:
        bad("BAD_TIMESTAMP", "The clock-out time has no timezone.", "POL-TA-01")
        return
    if out_time <= clock_in:
        bad("OUT_BEFORE_IN", "The clock-out is not after the clock-in.", "POL-TA-01")
        return
    length = out_time - clock_in
    if length > hours_td(cfg.missed_punch_max_shift_hours):
        bad(
            "SHIFT_OVER_MAX_HOURS",
            f"The corrected shift would be {hours(length)} hours, over the "
            f"{cfg.missed_punch_max_shift_hours:g} hour limit.",
            "POL-TA-01",
        )
    shift = scheduled_shift_for(tc, clock_in)
    if shift is None:
        bad("NO_SCHEDULE", "There is no scheduled shift to correct the clock-out to.", "POL-TA-01")
    elif out_time != aware(shift.end):
        bad(
            "NOT_SCHEDULED_END",
            "The clock-out may only be corrected to the scheduled shift end "
            f"({aware(shift.end).isoformat()}).",
            "POL-TA-01",
        )
    next_start = exc.evidence.get("next_work_start")
    if next_start:
        rest = aware(datetime.fromisoformat(next_start)) - out_time
        if rest < hours_td(cfg.min_rest_hours):
            bad(
                "MIN_REST_VIOLATION",
                f"Only {hours(rest)} hours of rest before the next shift; "
                f"at least {cfg.min_rest_hours:g} are required.",
                "POL-TA-06",
            )
    if bad:
        return
    copy = tc.model_copy(deep=True)
    copy.punches.append(
        Punch(
            id=f"{tc.id}:added-out",
            employee_id=tc.employee_id,
            type=PunchType.OUT,
            timestamp=out_time,
        )
    )
    _rerun(tc, copy, exc, cfg, bad)


# ---------------------------------------------------------------- DEDUCT_BREAK (POL-TA-02)


def _check_deduct_break(tc, exc, change, cfg, bad) -> None:
    if _rule_of(exc) != RULE_MEAL_BREAK:
        bad("ACTION_NOT_ALLOWED", "DEDUCT_BREAK only fixes a missing meal break.", "POL-TA-02")
        return
    if change.field != "break_minutes" or change.target_id != tc.id:
        bad(
            "FIELD_NOT_ALLOWED",
            f"A meal break fix may only change 'break_minutes' on this timecard, not "
            f"'{change.field}' on {change.target_id}.",
            "POL-TA-02",
        )
        return
    problem = next(
        (e for e in detect_exceptions([tc], cfg) if e.evidence.get("rule") == RULE_MEAL_BREAK),
        None,
    )
    if problem is None:
        bad("NO_MEAL_BREAK_PROBLEM", "This timecard has no meal break problem to fix.", "POL-TA-02")
        return
    logged = problem.evidence["break_minutes_logged"]
    needed = math.ceil(cfg.min_break_minutes - logged)
    try:
        amount = int(change.new or "")
    except ValueError:
        bad("BAD_VALUE", f"'{change.new}' is not a whole number of minutes.", "POL-TA-02")
        return
    if amount > needed:
        bad(
            "OVER_DEDUCTION",
            f"Deducting {amount} minutes takes more than policy requires: {needed} minutes "
            f"are missing ({logged:g} logged, {cfg.min_break_minutes} required).",
            "POL-TA-02",
        )
    elif amount < needed:
        bad(
            "DEDUCTION_TOO_SMALL",
            f"Deducting {amount} minutes does not cover the missing break: "
            f"{needed} minutes are needed.",
            "POL-TA-02",
        )
    if bad:
        return
    facts = analyze_punches(tc)
    long_enough = hours_td(cfg.break_required_after_hours)
    block = next(b for b in facts.blocks if b.duration > long_enough)
    start, end = _free_slot(block.start, block.end, facts.breaks, timedelta(minutes=amount))
    copy = tc.model_copy(deep=True)
    copy.punches += [
        Punch(
            id=f"{tc.id}:added-break-start",
            employee_id=tc.employee_id,
            type=PunchType.BREAK_START,
            timestamp=start,
        ),
        Punch(
            id=f"{tc.id}:added-break-end",
            employee_id=tc.employee_id,
            type=PunchType.BREAK_END,
            timestamp=end,
        ),
    ]
    _rerun(tc, copy, exc, cfg, bad)


def _free_slot(
    start: datetime, end: datetime, breaks: list[tuple[datetime, datetime]], length: timedelta
) -> tuple[datetime, datetime]:
    """Where to put a deducted break so it does not overlap one already logged: the middle of
    the longest stretch of the shift that has no break in it. A shift that needs a deduction
    is over 6 hours with under 30 minutes of break, so a stretch that fits always exists."""
    inside = sorted((max(b0, start), min(b1, end)) for b0, b1 in breaks if b1 > start and b0 < end)
    gaps, cursor = [], start
    for b0, b1 in inside:
        if b0 > cursor:
            gaps.append((cursor, b0))
        cursor = max(cursor, b1)
    if end > cursor:
        gaps.append((cursor, end))
    gap_start, gap_end = max(gaps, key=lambda g: g[1] - g[0])
    middle = gap_start + (gap_end - gap_start) / 2
    return middle - length / 2, middle + length / 2


# ---------------------------------------------------------------- REASSIGN_SHIFT


def _check_reassign(tc, exc, change, cfg, bad) -> None:
    clause = "POL-TA-05" if exc.type is ExceptionType.SCHEDULE_CONFLICT else "POL-TA-04"
    if change.field != "employee_id":
        bad(
            "FIELD_NOT_ALLOWED",
            f"A reassignment may only change 'employee_id', not '{change.field}'.",
            clause,
        )
        return
    if change.new is not None:
        bad(
            "REASSIGN_TARGET_UNVERIFIED",
            "A shift may only be taken off this employee and left open (new = null). Giving it "
            "to another employee cannot be checked against that employee's hours.",
            clause,
        )
        return
    if change.old is not None and change.old != tc.employee_id:
        bad(
            "OLD_VALUE_MISMATCH",
            f"The shift belongs to {tc.employee_id}, not {change.old}.",
            clause,
        )
        return

    if exc.type is ExceptionType.SCHEDULE_CONFLICT:
        wanted = exc.evidence.get("reassign_shift_id")
        pair = {exc.evidence.get("shift_a"), exc.evidence.get("shift_b")} - {None}
        if wanted is not None and change.target_id != wanted:
            bad(
                "KEEPS_WRONG_SHIFT",
                "Policy keeps the earlier shift and reassigns the other one "
                f"({wanted}), not {change.target_id}.",
                clause,
            )
            return
        if wanted is None and change.target_id not in pair:
            bad("UNRELATED_TARGET", "The shift is not one of the two that overlap.", clause)
            return
        if change.target_id not in {s.id for s in tc.shifts}:
            bad(
                "UNVERIFIABLE_TARGET",
                "The shift is not on this timecard, so the fix cannot be checked.",
                clause,
            )
            return
        copy = tc.model_copy(deep=True)
        copy.shifts = [s for s in copy.shifts if s.id != change.target_id]
        _rerun(tc, copy, exc, cfg, bad)
        return

    # OVERTIME_RISK: the shift must be an upcoming one the detector listed.
    listed = {r["shift_id"]: r["hours"] for r in exc.evidence.get("remaining_shifts", [])}
    if _rule_of(exc) != RULE_WEEKLY_OT or change.target_id not in listed:
        bad(
            "UNVERIFIABLE_TARGET",
            "Only an upcoming shift listed in the overtime evidence can be reassigned.",
            clause,
        )
        return
    if listed[change.target_id] <= 0:
        bad("NO_REDUCTION", "Reassigning this shift would not reduce the overtime.", clause)


# ---------------------------------------------------------------- shared re-check


def _rerun(original: Timecard, copy: Timecard, exc: DetectedException, cfg, bad) -> None:
    """Re-run the real detectors on the changed copy: the problem must be gone and no new
    violation may appear."""
    before = detect_exceptions([original], cfg)
    after = detect_exceptions([copy], cfg)
    before_keys, after_keys = _keys(before), _keys(after)

    rule = _rule_of(exc)
    still = [k for k in after_keys if k[1] == rule and k in before_keys]
    if still:
        bad("PROBLEM_NOT_RESOLVED", f"After the fix the {rule} problem is still there.")

    had_open_punch = not analyze_punches(original).complete
    for key in sorted(after_keys - before_keys, key=str):
        if had_open_punch and key[1] in _NEEDS_COMPLETE_DAY:
            continue  # only visible now that the day is complete: reported on its own later
        bad(
            "NEW_VIOLATION",
            f"The fix creates a new violation: {key[1]}.",
            _clause_for(key[1]),
        )


def _clause_for(rule: str | None) -> str | None:
    return {
        RULE_MEAL_BREAK: "POL-TA-02",
        RULE_DAILY_MAX: "POL-TA-03",
        RULE_MIN_REST: "POL-TA-06",
        RULE_OVERLAP: "POL-TA-05",
        RULE_MISSED_OUT: "POL-TA-01",
    }.get(rule or "")
