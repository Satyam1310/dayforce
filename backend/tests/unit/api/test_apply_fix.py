from datetime import UTC, datetime

import pytest

from app.contracts import FieldChange, Fix, FixAction, Punch, PunchType, Shift, Timecard
from app.services.apply_fix import FixNotApplicable, apply_fix

EMP = "emp-1"


def ts(hhmm: str) -> datetime:
    h, m = hhmm.split(":")
    return datetime(2026, 3, 10, int(h), int(m), tzinfo=UTC)


def timecard(punches=(("IN", "03:30"),), shifts=()) -> Timecard:
    return Timecard(
        id="tc-1",
        employee_id=EMP,
        work_date="2026-03-10",
        punches=[
            Punch(id=f"p{i}", employee_id=EMP, type=PunchType(t), timestamp=ts(at))
            for i, (t, at) in enumerate(punches)
        ],
        shifts=[
            Shift(id=sid, employee_id=EMP, start=ts(a), end=ts(b), location_id="loc")
            for sid, a, b in shifts
        ],
        notes=None,
    )


def fix(action: FixAction, *changes: FieldChange) -> Fix:
    return Fix(action=action, changes=list(changes))


def test_add_punch_adds_the_missing_clock_out():
    tc = timecard()
    out = apply_fix(
        tc,
        fix(
            FixAction.ADD_PUNCH,
            FieldChange(target_id="tc-1", field="punch.OUT", old=None, new="2026-03-10T12:30:00Z"),
        ),
    )
    assert [p.type for p in out.punches] == [PunchType.IN, PunchType.OUT]
    assert out.punches[1].timestamp == ts("12:30")
    assert len(tc.punches) == 1  # the input is not modified


def test_edit_punch_changes_only_that_punch():
    tc = timecard(punches=(("IN", "03:30"), ("OUT", "16:00")))
    out = apply_fix(
        tc,
        fix(
            FixAction.EDIT_PUNCH,
            FieldChange(target_id="p1", field="punch.timestamp", old=None,
                        new="2026-03-10T12:30:00+00:00"),
        ),
    )
    assert out.punches[0].timestamp == ts("03:30")
    assert out.punches[1].timestamp == ts("12:30")


def test_deduct_break_adds_a_30_minute_break_in_the_middle():
    tc = timecard(punches=(("IN", "03:30"), ("OUT", "11:30")))
    out = apply_fix(
        tc, fix(FixAction.DEDUCT_BREAK, FieldChange(target_id="tc-1", field="break_minutes",
                                                     old="0", new="30"))
    )
    kinds = [p.type for p in out.punches]
    assert kinds == [PunchType.IN, PunchType.BREAK_START, PunchType.BREAK_END, PunchType.OUT]
    assert out.punches[1].timestamp == ts("07:15")
    assert out.punches[2].timestamp == ts("07:45")


def test_reassign_shift_removes_it_from_this_timecard():
    tc = timecard(shifts=(("s1", "03:30", "12:30"), ("s2", "10:30", "15:30")))
    out = apply_fix(
        tc,
        fix(FixAction.REASSIGN_SHIFT, FieldChange(target_id="s2", field="shift.employee_id",
                                                   old=EMP, new="UNASSIGNED")),
    )
    assert [s.id for s in out.shifts] == ["s1"]


def test_flag_for_manager_changes_nothing():
    tc = timecard()
    assert apply_fix(tc, fix(FixAction.FLAG_FOR_MANAGER)) == tc


@pytest.mark.parametrize(
    "the_fix",
    [
        fix(FixAction.ADD_PUNCH, FieldChange(target_id="x", field="salary", new="1")),
        fix(FixAction.ADD_PUNCH, FieldChange(target_id="x", field="punch.OUT", new="not a date")),
        fix(FixAction.ADD_PUNCH, FieldChange(target_id="x", field="punch.OUT",
                                             new="2026-03-10T12:30:00")),  # no timezone
        fix(FixAction.EDIT_PUNCH, FieldChange(target_id="nope", field="punch.timestamp",
                                              new="2026-03-10T12:30:00Z")),
        fix(FixAction.DEDUCT_BREAK, FieldChange(target_id="x", field="break_minutes",
                                                old="0", new="30")),  # no OUT punch
    ],
)
def test_bad_fixes_are_refused(the_fix):
    with pytest.raises(FixNotApplicable):
        apply_fix(timecard(), the_fix)
