"""Small builders so the rule and validator tests read like the policy they check."""

from datetime import UTC, date, datetime, timedelta, timezone

from app.contracts import Punch, PunchType, Shift, Timecard

IST = timezone(timedelta(hours=5, minutes=30))
EMP = "emp-1"


def at(day: int, hour: int = 0, minute: int = 0, month: int = 3, tz=IST) -> datetime:
    """A wall-clock time in March 2026 (default Asia/Kolkata). 2026-03-09 is a Monday."""
    return datetime(2026, month, day, hour, minute, tzinfo=tz)


def punch(pid: str, kind: PunchType, when: datetime, emp: str = EMP) -> Punch:
    return Punch(id=pid, employee_id=emp, type=kind, timestamp=when)


def shift(sid: str, start: datetime, end: datetime, emp: str = EMP) -> Shift:
    return Shift(id=sid, employee_id=emp, start=start, end=end, location_id="loc-1")


def timecard(
    day: int,
    punches=(),
    shifts=(),
    emp: str = EMP,
    notes: str | None = None,
    tcid: str | None = None,
    month: int = 3,
) -> Timecard:
    return Timecard(
        id=tcid or f"tc-{emp}-{day}",
        employee_id=emp,
        work_date=date(2026, month, day),
        punches=list(punches),
        shifts=list(shifts),
        notes=notes,
    )


def worked_day(
    day: int,
    start=(9, 0),
    hours: float = 8.5,
    break_minutes: int = 30,
    emp: str = EMP,
    shifted: bool = True,
    notes: str | None = None,
) -> Timecard:
    """A finished day: clock in, an optional break in the middle, clock out `hours` later
    (gross). With the default 8.5 hours and a 30 minute break, 8 hours are paid."""
    t_in = at(day, *start)
    t_out = t_in + timedelta(hours=hours)
    ps = [punch(f"in{day}{emp}", PunchType.IN, t_in, emp)]
    if break_minutes:
        mid = t_in + (t_out - t_in) / 2
        ps += [
            punch(f"bs{day}{emp}", PunchType.BREAK_START, mid, emp),
            punch(
                f"be{day}{emp}", PunchType.BREAK_END, mid + timedelta(minutes=break_minutes), emp
            ),
        ]
    ps.append(punch(f"out{day}{emp}", PunchType.OUT, t_out, emp))
    shifts = [shift(f"s{day}{emp}", t_in, t_out, emp)] if shifted else []
    return timecard(day, ps, shifts, emp, notes)


def open_day(day: int, start=(9, 0), shift_hours: float | None = 9, emp: str = EMP) -> Timecard:
    """A clock-in with no clock-out, optionally with a scheduled shift of `shift_hours`."""
    t_in = at(day, *start)
    shifts = (
        [shift(f"s{day}{emp}", t_in, t_in + timedelta(hours=shift_hours), emp)]
        if shift_hours
        else []
    )
    return timecard(day, [punch(f"in{day}{emp}", PunchType.IN, t_in, emp)], shifts, emp)


def future_day(day: int, hours: float, emp: str = EMP) -> Timecard:
    """A day with a scheduled shift and no punches (not worked yet)."""
    t0 = at(day, 9)
    return timecard(day, [], [shift(f"f{day}{emp}", t0, t0 + timedelta(hours=hours), emp)], emp)


def week_with_friday(friday_hours: float, emp: str = EMP) -> list[Timecard]:
    """Mon 9 to Thu 12 March worked at 8 paid hours each (32 h), plus a Friday shift."""
    return [worked_day(d, emp=emp) for d in (9, 10, 11, 12)] + [future_day(13, friday_hours, emp)]


def utc(dt: datetime) -> datetime:
    return dt.astimezone(UTC)
