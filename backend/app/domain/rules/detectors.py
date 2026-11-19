"""The four exception detectors (CLAUDE.md section 7). Pure functions: no I/O, no AI, no clock.

    detect_exceptions(timecards, cfg) -> list[DetectedException]

Every detector reads only punches, shifts and PolicyConfig. `Timecard.notes` is untrusted
text and is never read. The output is deterministic: ids come from the content (uuid5), and
the list is sorted, so running the analysis twice gives identical results.

Clause mapping:
    MISSED_PUNCH       POL-TA-01
    OVERTIME_RISK      POL-TA-03 (daily), POL-TA-04 (weekly)
    SCHEDULE_CONFLICT  POL-TA-05
    POLICY_BREACH      POL-TA-02 (meal break), POL-TA-03 (over 12 h), POL-TA-06 (rest)

Where the policy gives no automatic fix, `baseline_fix` is None and the case is escalated.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from app.contracts import (
    DetectedException,
    ExceptionType,
    FieldChange,
    Fix,
    FixAction,
    PolicyConfig,
    Severity,
    Shift,
    Timecard,
)

from .analysis import (
    RULE_DAILY_MAX,
    RULE_DAILY_OT,
    RULE_MEAL_BREAK,
    RULE_MIN_REST,
    RULE_MISSED_OUT,
    RULE_MISSING_IN,
    RULE_OVERLAP,
    RULE_WEEKLY_OT,
    DayFacts,
    WorkBlock,
    analyze_punches,
    aware,
    employee_blocks,
    hours,
    hours_td,
    iso_utc,
    scheduled_shift_for,
    week_start,
)

_NAMESPACE = uuid.UUID("0c1f7a52-7d7e-4c55-9a36-4a1a5f6b7e01")


def _make(
    kind: ExceptionType,
    severity: Severity,
    tc: Timecard,
    evidence: dict[str, Any],
    refs: list[str],
    baseline: Fix | None,
    *id_parts: str,
) -> DetectedException:
    return DetectedException(
        id=str(uuid.uuid5(_NAMESPACE, "|".join([kind.value, evidence["rule"], tc.id, *id_parts]))),
        type=kind,
        severity=severity,
        employee_id=tc.employee_id,
        timecard_id=tc.id,
        work_date=tc.work_date,
        evidence=evidence,
        policy_refs=refs,
        baseline_fix=baseline,
        status="OPEN",
    )


def _unassign(shift: Shift) -> Fix:
    """Reassigning a shift: it is taken off this employee and left open for the manager."""
    return Fix(
        action=FixAction.REASSIGN_SHIFT,
        changes=[
            FieldChange(target_id=shift.id, field="employee_id", old=shift.employee_id, new=None)
        ],
    )


# ---------------------------------------------------------------- POL-TA-01 missed punch


def _missed_punch(
    tc: Timecard, facts: DayFacts, blocks: list[WorkBlock], cfg: PolicyConfig
) -> list[DetectedException]:
    out = []
    max_shift = hours_td(cfg.missed_punch_max_shift_hours)
    for p in facts.unmatched_in:
        t_in = aware(p.timestamp)
        shift = scheduled_shift_for(tc, t_in)
        prev_end = max((b.end for b in blocks if b.end <= t_in and b.key != p.id), default=None)
        next_start = min((b.start for b in blocks if b.start > t_in), default=None)
        evidence: dict[str, Any] = {
            "rule": RULE_MISSED_OUT,
            "clock_in": iso_utc(t_in),
            "clock_out": None,
            "scheduled_shift_start": iso_utc(shift.start) if shift else None,
            "scheduled_shift_end": iso_utc(shift.end) if shift else None,
            "resulting_shift_hours": None,
            "max_shift_hours": cfg.missed_punch_max_shift_hours,
            "previous_work_end": iso_utc(prev_end) if prev_end else None,
            "next_work_start": iso_utc(next_start) if next_start else None,
        }
        baseline: Fix | None = None
        if shift is None:
            evidence["reason_no_fix"] = "no scheduled shift on this timecard"
        else:
            length = aware(shift.end) - t_in
            evidence["resulting_shift_hours"] = hours(length)
            if length > max_shift:
                evidence["reason_no_fix"] = (
                    f"scheduled end would make the shift {hours(length)} h, "
                    f"over the {cfg.missed_punch_max_shift_hours:g} h limit"
                )
            else:
                baseline = Fix(
                    action=FixAction.ADD_PUNCH,
                    changes=[
                        FieldChange(
                            target_id=tc.id, field="punch.OUT", old=None, new=iso_utc(shift.end)
                        )
                    ],
                )
        out.append(
            _make(
                ExceptionType.MISSED_PUNCH,
                Severity.MEDIUM if baseline else Severity.HIGH,
                tc,
                evidence,
                ["POL-TA-01"],
                baseline,
                p.id,
            )
        )
    for p in facts.orphan_out:
        evidence = {
            "rule": RULE_MISSING_IN,
            "clock_in": None,
            "clock_out": iso_utc(p.timestamp),
            "reason_no_fix": "clock-out with no clock-in: the start time is unknown",
        }
        out.append(
            _make(
                ExceptionType.MISSED_PUNCH, Severity.HIGH, tc, evidence, ["POL-TA-01"], None, p.id
            )
        )
    return out


# ---------------------------------------------------------------- POL-TA-02/03 one day


def _daily(tc: Timecard, facts: DayFacts, cfg: PolicyConfig) -> list[DetectedException]:
    # An unfinished day (missing punch) has unknown hours, so no hour-based rule is judged.
    if not facts.complete or not facts.blocks:
        return []
    out = []
    worked = facts.worked
    if worked > hours_td(cfg.max_daily_hours):
        evidence = {
            "rule": RULE_DAILY_MAX,
            "worked_hours": hours(worked),
            "max_daily_hours": cfg.max_daily_hours,
            "reason_no_fix": "a day over the maximum needs manager review",
        }
        out.append(
            _make(ExceptionType.POLICY_BREACH, Severity.HIGH, tc, evidence, ["POL-TA-03"], None)
        )
    elif worked > hours_td(cfg.daily_ot_hours):
        evidence = {
            "rule": RULE_DAILY_OT,
            "worked_hours": hours(worked),
            "daily_ot_hours": cfg.daily_ot_hours,
            "overtime_hours": hours(worked - hours_td(cfg.daily_ot_hours)),
        }
        out.append(
            _make(
                ExceptionType.OVERTIME_RISK,
                Severity.LOW,
                tc,
                evidence,
                ["POL-TA-03"],
                Fix(action=FixAction.NO_CHANGE, changes=[]),
            )
        )
    for block in facts.blocks:
        if block.duration > hours_td(cfg.break_required_after_hours) and (
            block.break_minutes < cfg.min_break_minutes
        ):
            evidence = {
                "rule": RULE_MEAL_BREAK,
                "shift_hours": hours(block.duration),
                "break_required_after_hours": cfg.break_required_after_hours,
                "break_minutes_logged": round(block.break_minutes, 1),
                "min_break_minutes": cfg.min_break_minutes,
            }
            baseline: Fix | None = None
            if block.break_minutes == 0:
                baseline = Fix(
                    action=FixAction.DEDUCT_BREAK,
                    changes=[
                        FieldChange(
                            target_id=tc.id,
                            field="break_minutes",
                            old=None,
                            new=str(cfg.min_break_minutes),
                        )
                    ],
                )
            else:
                evidence["reason_no_fix"] = "a short break is logged: the manager decides"
            out.append(
                _make(
                    ExceptionType.POLICY_BREACH,
                    Severity.LOW,
                    tc,
                    evidence,
                    ["POL-TA-02"],
                    baseline,
                    block.key,
                )
            )
            break  # one meal-break exception per timecard
    return out


# ---------------------------------------------------------------- POL-TA-05 conflicts


def _schedule_conflicts(timecards: list[Timecard]) -> list[DetectedException]:
    shifts: dict[str, Shift] = {}
    owner: dict[str, Timecard] = {}
    for tc in timecards:
        for s in tc.shifts:
            if s.id not in shifts:
                shifts[s.id] = s
                owner[s.id] = tc
    ordered = sorted(shifts.values(), key=lambda s: (aware(s.start), aware(s.end), s.id))
    out = []
    for i, a in enumerate(ordered):
        for b in ordered[i + 1 :]:
            if aware(b.start) >= aware(a.end):
                break  # sorted by start: nothing later can overlap a
            overlap = min(aware(a.end), aware(b.end)) - aware(b.start)
            # POL-TA-05 says keep the earliest-assigned shift. The contract has no assignment
            # time, so the shift that starts first is kept. TODO(Ankit): confirm this stand-in.
            evidence = {
                "rule": RULE_OVERLAP,
                "shift_a": a.id,
                "shift_b": b.id,
                "keep_shift_id": a.id,
                "reassign_shift_id": b.id,
                "overlap_hours": hours(overlap),
            }
            tc = owner[b.id]
            out.append(
                _make(
                    ExceptionType.SCHEDULE_CONFLICT,
                    Severity.HIGH,
                    tc,
                    evidence,
                    ["POL-TA-05"],
                    _unassign(b),
                    a.id,
                    b.id,
                )
            )
    return out


# ---------------------------------------------------------------- POL-TA-06 rest


def _min_rest(
    blocks: list[WorkBlock], cfg: PolicyConfig, by_id: dict[str, Timecard]
) -> list[DetectedException]:
    out = []
    minimum = hours_td(cfg.min_rest_hours)
    for prev, nxt in zip(blocks, blocks[1:], strict=False):
        gap = nxt.start - prev.end
        if gap < hours_td(0):
            continue  # overlapping blocks are reported as a schedule conflict instead
        if gap < minimum:
            evidence = {
                "rule": RULE_MIN_REST,
                "previous_end": iso_utc(prev.end),
                "next_start": iso_utc(nxt.start),
                "rest_hours": hours(gap),
                "min_rest_hours": cfg.min_rest_hours,
                "reason_no_fix": "a rest breach needs manager review",
            }
            out.append(
                _make(
                    ExceptionType.POLICY_BREACH,
                    Severity.MEDIUM,
                    by_id[nxt.timecard_id],
                    evidence,
                    ["POL-TA-06"],
                    None,
                    nxt.key,
                )
            )
    return out


# ---------------------------------------------------------------- POL-TA-04 weekly


def _weekly_overtime(
    tcs: list[Timecard], facts: dict[str, DayFacts], cfg: PolicyConfig
) -> list[DetectedException]:
    weeks: dict[date, list[Timecard]] = {}
    for tc in tcs:
        weeks.setdefault(week_start(tc.work_date, cfg.week_starts), []).append(tc)
    out = []
    for start, week in sorted(weeks.items()):
        worked = hours_td(0)
        remaining = hours_td(0)
        remaining_shifts: list[tuple[Shift, Timecard]] = []
        missing_days = 0
        for tc in week:
            f = facts[tc.id]
            if tc.punches:
                worked += f.worked
                missing_days += 0 if f.complete else 1
            else:
                for s in tc.shifts:
                    remaining += aware(s.end) - aware(s.start)
                    remaining_shifts.append((s, tc))
        projected = worked + remaining
        if projected < hours_td(cfg.overtime_risk_threshold):
            continue
        # Attach to the latest day that has punches (the "as of" day), else the last day.
        with_punches = [tc for tc in week if tc.punches]
        anchor = (with_punches or week)[-1]
        remaining_shifts.sort(key=lambda pair: (aware(pair[0].start), pair[0].id))
        evidence = {
            "rule": RULE_WEEKLY_OT,
            "week_start": start.isoformat(),
            "worked_hours": hours(worked),
            "remaining_scheduled_hours": hours(remaining),
            "projected_hours": hours(projected),
            "overtime_risk_threshold": cfg.overtime_risk_threshold,
            "weekly_ot_hours": cfg.weekly_ot_hours,
            "days_with_missing_punches": missing_days,
            "remaining_shifts": [
                {
                    "shift_id": s.id,
                    "start": iso_utc(s.start),
                    "hours": hours(aware(s.end) - aware(s.start)),
                }
                for s, _ in remaining_shifts
            ],
        }
        baseline = _unassign(remaining_shifts[-1][0]) if remaining_shifts else None
        if baseline is None:
            evidence["reason_no_fix"] = "no upcoming shift is left to reduce or reassign"
        out.append(
            _make(
                ExceptionType.OVERTIME_RISK,
                Severity.HIGH if projected > hours_td(cfg.weekly_ot_hours) else Severity.MEDIUM,
                anchor,
                evidence,
                ["POL-TA-04"],
                baseline,
                start.isoformat(),
            )
        )
    return out


# ---------------------------------------------------------------- entry point


def detect_exceptions(
    timecards: list[Timecard], cfg: PolicyConfig | None = None
) -> list[DetectedException]:
    """Find every exception in these timecards. Pass all the timecards you have for a week
    (several employees and days are fine): weekly overtime and rest need the neighbouring days."""
    cfg = cfg or PolicyConfig()
    by_employee: dict[str, list[Timecard]] = {}
    for tc in timecards:
        by_employee.setdefault(tc.employee_id, []).append(tc)

    found: list[DetectedException] = []
    for emp in sorted(by_employee):
        tcs = sorted(by_employee[emp], key=lambda t: (t.work_date, t.id))
        facts = {tc.id: analyze_punches(tc) for tc in tcs}
        blocks = employee_blocks(tcs, facts)
        by_id = {tc.id: tc for tc in tcs}
        for tc in tcs:
            found += _missed_punch(tc, facts[tc.id], blocks, cfg)
            found += _daily(tc, facts[tc.id], cfg)
        found += _schedule_conflicts(tcs)
        found += _min_rest(blocks, cfg, by_id)
        found += _weekly_overtime(tcs, facts, cfg)
    found.sort(key=lambda e: (e.work_date, e.employee_id, e.type.value, e.id))
    return found
