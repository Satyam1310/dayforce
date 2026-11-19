"""Loader for the golden set (tests/golden/cases.json) and the safe-outcome rule.

The golden set is plain data: each case lists timecards and what a correct system must do.
Times are written compactly so the file stays readable:

    "09:00"                      a time on the timecard's day, Asia/Kolkata
    "06:00+1"                    the same, on the next day
    "2026-03-10T09:00:00"        a full ISO time; with no zone it stays zone-less on purpose
    punches   "IN 09:00", "OUT 17:30", "BS 13:00" (break start), "BE 13:30" (break end)
    shifts    "s1 09:00 to 18:00"  (shift id, start, end)
    "days"    a list of dates: the timecard is repeated on each (shift ids get the date added)
    notes     a string, or {"repeat": [text, n]} for very long notes

`expected_outcome` is the oracle for what the proposal workflow must end with. It is written
from the policy, not from the code under test, so a regression in the code shows up as a
difference.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app.contracts import Punch, PunchType, Shift, Timecard

CASES_PATH = Path(__file__).with_name("cases.json")
IST = timezone(timedelta(hours=5, minutes=30))
MODES = ("ok", "invalid_json", "timeout", "bad_citation", "low_confidence")
_KIND = {
    "IN": PunchType.IN,
    "OUT": PunchType.OUT,
    "BS": PunchType.BREAK_START,
    "BE": PunchType.BREAK_END,
}


@dataclass(frozen=True)
class ExpectedException:
    type: str
    rule: str
    severity: str | None = None
    baseline: str | None = None  # action of the rule-based fix, or None when there is none
    baseline_valid: bool | None = None  # should that fix pass the validator? (None: no fix)
    count: int = 1


@dataclass(frozen=True)
class Attack:
    """What a hostile note is trying to make the model propose."""

    rule: str  # which exception the attack is aimed at
    fix: dict  # a Fix as a dict; target "@timecard" means the exception's own timecard


@dataclass(frozen=True)
class GoldenCase:
    id: str
    title: str
    tags: tuple[str, ...]
    timecards: list[Timecard]
    expected: list[ExpectedException]
    attack: Attack | None = None
    notes_length: int = 0  # length of the longest notes text, for the long-notes checks


def parse_time(day: date, token: str) -> datetime:
    if "T" in token:
        return datetime.fromisoformat(token.replace("Z", "+00:00"))  # zone-less stays zone-less
    m = re.fullmatch(r"(\d\d):(\d\d)(?:\+(\d))?", token)
    if not m:
        raise ValueError(f"bad time {token!r}")
    d = day + timedelta(days=int(m.group(3) or 0))
    return datetime(d.year, d.month, d.day, int(m.group(1)), int(m.group(2)), tzinfo=IST)


def _notes(value) -> str | None:
    if isinstance(value, dict):
        text, times = value["repeat"]
        return text * times
    return value


def _timecard(case_id: str, index: int, day: date, spec: dict, suffix: str = "") -> Timecard:
    emp = spec.get("employee", "emp-1")
    tc_id = f"{case_id}:{index}{suffix}"
    punches = []
    for n, token in enumerate(spec.get("punches", [])):
        kind, when = token.split(" ", 1)
        punches.append(
            Punch(
                id=f"{tc_id}:p{n}",
                employee_id=emp,
                type=_KIND[kind],
                timestamp=parse_time(day, when),
            )
        )
    shifts = []
    for token in spec.get("shifts", []):
        sid, rest = token.split(" ", 1)
        start, end = rest.split(" to ")
        shifts.append(
            Shift(
                id=f"{sid}{suffix}",
                employee_id=emp,
                start=parse_time(day, start),
                end=parse_time(day, end),
                location_id="loc-1",
            )
        )
    return Timecard(
        id=tc_id,
        employee_id=emp,
        work_date=day,
        punches=punches,
        shifts=shifts,
        notes=_notes(spec.get("notes")),
    )


def load_cases(path: Path = CASES_PATH) -> list[GoldenCase]:
    cases = []
    for raw in json.loads(path.read_text()):
        timecards: list[Timecard] = []
        longest = 0
        for i, spec in enumerate(raw["timecards"]):
            if "days" in spec:
                timecards += [
                    _timecard(raw["id"], i, date.fromisoformat(d), spec, f"-{d}")
                    for d in spec["days"]
                ]
            else:
                timecards.append(_timecard(raw["id"], i, date.fromisoformat(spec["day"]), spec))
        for tc in timecards:
            longest = max(longest, len(tc.notes or ""))
        expected = [ExpectedException(**e) for e in raw["expect"]]
        attack = Attack(**raw["attack"]) if raw.get("attack") else None
        cases.append(
            GoldenCase(
                raw["id"],
                raw["title"],
                tuple(raw.get("tags", [])),
                timecards,
                expected,
                attack,
                longest,
            )
        )
    return cases


def expected_outcome(
    mode: str, baseline: str | None, baseline_valid: bool | None
) -> tuple[str, str, str]:
    """(proposal status, source, fix action) the workflow must end with when the mock model
    behaves as `mode`. The mock model proposes the rule-based fix, or a plain flag when there
    is none.

      ok            the model's fix is validated. A flag or a valid fix reaches the manager.
                    A rule-based fix that fails validation cannot be rescued: escalate.
      invalid_json / timeout / bad_citation
                    the model is not usable: fall back to the rule-based fix if it exists
                    and passes the validator, otherwise escalate.
      low_confidence
                    a validated answer with low confidence is escalated.
    """
    flag = "FLAG_FOR_MANAGER"
    has_fix = baseline is not None
    usable = has_fix and bool(baseline_valid)
    if mode == "ok":
        if not has_fix:
            return "PENDING_REVIEW", "LLM", flag
        if usable:
            return "PENDING_REVIEW", "LLM", baseline
        return "ESCALATED", "RULES_FALLBACK", flag
    if mode == "low_confidence":
        if not has_fix:
            return "ESCALATED", "LLM", flag
        if usable:
            return "ESCALATED", "LLM", flag
        return "ESCALATED", "RULES_FALLBACK", flag
    if usable:
        return "PENDING_REVIEW", "RULES_FALLBACK", baseline
    return "ESCALATED", "RULES_FALLBACK", flag
