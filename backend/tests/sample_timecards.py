"""Timecards with a known problem, for the integration and system tests.

Every call makes fresh ids, so tests can share one database without touching each other.
Dates are in March 2026 (Mon 2026-03-09), times in Asia/Kolkata like the policy.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from app.contracts import Punch, PunchType, Shift, Timecard

IST = timezone(timedelta(hours=5, minutes=30))


def _id() -> str:
    return str(uuid.uuid4())


def at(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 3, day, hour, minute, tzinfo=IST)


def _shift(emp: str, start: datetime, end: datetime) -> Shift:
    return Shift(id=_id(), employee_id=emp, start=start, end=end, location_id="loc-pune-01")


def _punch(emp: str, kind: PunchType, when: datetime) -> Punch:
    return Punch(id=_id(), employee_id=emp, type=kind, timestamp=when)


def new_employee() -> str:
    return f"emp-{uuid.uuid4().hex[:8]}"


def _card(emp: str, day: int, punches: list[Punch], shifts: list[Shift], notes=None) -> Timecard:
    return Timecard(
        id=_id(),
        employee_id=emp,
        work_date=date(2026, 3, day),
        punches=punches,
        shifts=shifts,
        notes=notes,
    )


def clean_day(emp: str | None = None, day: int = 9) -> Timecard:
    """9:00-17:30 with a 30 minute break: nothing to report."""
    emp = emp or new_employee()
    return _card(
        emp,
        day,
        [
            _punch(emp, PunchType.IN, at(day, 9)),
            _punch(emp, PunchType.BREAK_START, at(day, 13)),
            _punch(emp, PunchType.BREAK_END, at(day, 13, 30)),
            _punch(emp, PunchType.OUT, at(day, 17, 30)),
        ],
        [_shift(emp, at(day, 9), at(day, 17, 30))],
    )


def missed_punch(emp: str | None = None, day: int = 9, notes: str | None = None) -> Timecard:
    """Clocked in at 9:00, shift ends 17:30, never clocked out (POL-TA-01)."""
    emp = emp or new_employee()
    return _card(
        emp,
        day,
        [_punch(emp, PunchType.IN, at(day, 9))],
        [_shift(emp, at(day, 9), at(day, 17, 30))],
        notes,
    )


def schedule_conflict(emp: str | None = None, day: int = 10) -> Timecard:
    """Two overlapping shifts on one day (POL-TA-05)."""
    emp = emp or new_employee()
    return _card(
        emp,
        day,
        [],
        [_shift(emp, at(day, 9), at(day, 17)), _shift(emp, at(day, 15), at(day, 21))],
    )


def long_day(emp: str | None = None, day: int = 11) -> Timecard:
    """A 13 hour day with a break (POL-TA-03: over 12 hours is a breach)."""
    emp = emp or new_employee()
    return _card(
        emp,
        day,
        [
            _punch(emp, PunchType.IN, at(day, 6)),
            _punch(emp, PunchType.BREAK_START, at(day, 12)),
            _punch(emp, PunchType.BREAK_END, at(day, 12, 30)),
            _punch(emp, PunchType.OUT, at(day, 19, 30)),
        ],
        [_shift(emp, at(day, 6), at(day, 19, 30))],
    )


def overtime_risk_week(emp: str | None = None) -> list[Timecard]:
    """Mon-Thu 8 paid hours each, plus an 8 hour shift still to work on Friday:
    40 projected hours, over the 38 hour warning level (POL-TA-04)."""
    emp = emp or new_employee()
    days = [clean_day(emp, d) for d in (9, 10, 11, 12)]
    friday = _card(emp, 13, [], [_shift(emp, at(13, 9), at(13, 17))])
    return [*days, friday]
