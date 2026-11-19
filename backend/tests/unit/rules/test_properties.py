"""Property-style tests: the same promises, checked on many random timecards.

hypothesis writes the timecards (random punches, breaks, shifts, time zones, notes, several
employees) and each test states something that must hold for ALL of them. `derandomize=True`
makes every run use the same examples, so a CI failure can always be reproduced. To explore
new random examples instead, run:

    PROPERTY_EXPLORE=1 PROPERTY_EXAMPLES=2000 python -m pytest tests/unit/rules/test_properties.py
"""

import json
import os
from datetime import UTC, datetime, timedelta

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.contracts import (
    DetectedException,
    FieldChange,
    Fix,
    FixAction,
    PolicyConfig,
    Punch,
    PunchType,
    Shift,
    Timecard,
    ValidationResult,
)
from app.domain.rules import RULE_MISSED_OUT, detect_exceptions
from app.domain.rules.analysis import analyze_punches, aware
from app.validator import validate_proposal
from tests.unit.rules.builders import IST

CFG = PolicyConfig()
MONDAY = datetime(2026, 3, 9, tzinfo=IST)
PROPS = settings(
    max_examples=int(os.environ.get("PROPERTY_EXAMPLES", "150")),
    deadline=None,
    derandomize=os.environ.get("PROPERTY_EXPLORE") != "1",
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
)


def stamp(day: int, minutes: int, zone: str) -> datetime:
    """`minutes` after midnight Kolkata time on a day of the week of 9 March 2026, written
    in Kolkata time, in UTC, or as a zone-less UTC time."""
    t = MONDAY + timedelta(days=day, minutes=minutes)
    if zone == "ist":
        return t
    t = t.astimezone(UTC)
    return t.replace(tzinfo=None) if zone == "naive" else t


@st.composite
def timecards(draw, emp: str = "emp-1", day: int | None = None, open_ok: bool = True) -> Timecard:
    day = draw(st.integers(0, 4)) if day is None else day
    zone = draw(st.sampled_from(["ist", "utc", "naive"]))
    punches, n = [], 0

    def add(kind, minutes):
        nonlocal n
        punches.append(
            Punch(
                id=f"{emp}-{day}-p{n}",
                employee_id=emp,
                type=kind,
                timestamp=stamp(day, minutes, zone),
            )
        )
        n += 1

    cursor = draw(st.integers(0, 600))
    pairs = draw(st.integers(0, 3))
    for i in range(pairs):
        start = cursor + draw(st.integers(0, 300))
        length = draw(st.integers(1, 900))
        unfinished = open_ok and i == pairs - 1 and draw(st.booleans())
        add(PunchType.IN, start)
        if not unfinished and length > 60 and draw(st.booleans()):
            b0 = start + draw(st.integers(10, length - 40))
            add(PunchType.BREAK_START, b0)
            add(PunchType.BREAK_END, b0 + draw(st.integers(1, 40)))
        if not unfinished:
            add(PunchType.OUT, start + length)
        cursor = start + length
    if draw(st.booleans()):  # stray punches the data source might produce
        add(
            draw(st.sampled_from([PunchType.OUT, PunchType.BREAK_START, PunchType.BREAK_END])),
            draw(st.integers(0, 1400)),
        )
    shifts = []
    for k in range(draw(st.integers(0, 2))):
        s0 = draw(st.integers(0, 900))
        s1 = s0 + draw(st.integers(30, 900))
        shifts.append(
            Shift(
                id=f"{emp}-{day}-s{k}",
                employee_id=emp,
                start=stamp(day, s0, zone),
                end=stamp(day, s1, zone),
                location_id="loc-1",
            )
        )
    notes = draw(st.one_of(st.none(), st.text(max_size=60)))
    return Timecard(
        id=f"{emp}-{day}",
        employee_id=emp,
        work_date=MONDAY.date() + timedelta(days=day),
        punches=punches,
        shifts=shifts,
        notes=notes,
    )


@st.composite
def timecard_sets(draw) -> list[Timecard]:
    out = []
    for emp in ("emp-1", "emp-2")[: draw(st.integers(1, 2))]:
        days = draw(st.lists(st.integers(0, 4), min_size=1, max_size=4, unique=True))
        out += [draw(timecards(emp=emp, day=d)) for d in days]
    return out


# ---------------------------------------------------------------- the detectors


@PROPS
@given(timecard_sets())
def test_detectors_never_crash_and_return_valid_exceptions(tcs):
    found = detect_exceptions(tcs, CFG)
    assert len({e.id for e in found}) == len(found)
    for e in found:
        DetectedException.model_validate(e.model_dump(mode="json"))
        json.dumps(e.evidence)  # plain JSON only
        assert e.status == "OPEN" and e.policy_refs
        assert e.timecard_id in {t.id for t in tcs}


@PROPS
@given(timecard_sets(), st.randoms(use_true_random=False))
def test_detectors_are_deterministic_and_ignore_input_order(tcs, rng):
    first = detect_exceptions(tcs, CFG)
    shuffled = tcs[:]
    rng.shuffle(shuffled)
    assert detect_exceptions(shuffled, CFG) == first
    assert detect_exceptions(tcs, CFG) == first


@PROPS
@given(timecard_sets(), st.text(max_size=200))
def test_notes_never_change_what_is_detected(tcs, hostile):
    stripped = [t.model_copy(update={"notes": None}) for t in tcs]
    hostile_notes = [t.model_copy(update={"notes": hostile}) for t in tcs]
    assert detect_exceptions(hostile_notes, CFG) == detect_exceptions(stripped, CFG)


@PROPS
@given(timecard_sets())
def test_a_day_with_a_clock_in_and_no_clock_out_is_always_reported(tcs):
    reported = {
        (e.timecard_id)
        for e in detect_exceptions(tcs, CFG)
        if e.evidence["rule"] == RULE_MISSED_OUT
    }
    for tc in tcs:
        if analyze_punches(tc).unmatched_in:
            assert tc.id in reported


# ---------------------------------------------------------------- the validator


@st.composite
def missed_punch_scenarios(draw):
    day = draw(st.integers(0, 3))
    zone = draw(st.sampled_from(["ist", "utc"]))
    clock_in = draw(st.integers(0, 900))
    shift_minutes = draw(st.one_of(st.none(), st.integers(30, 1100)))
    shifts = []
    if shift_minutes is not None:
        shifts = [
            Shift(
                id="s0",
                employee_id="emp-1",
                start=stamp(day, clock_in, zone),
                end=stamp(day, clock_in + shift_minutes, zone),
                location_id="loc-1",
            )
        ]
    tc = Timecard(
        id="tc-0",
        employee_id="emp-1",
        work_date=MONDAY.date() + timedelta(days=day),
        punches=[
            Punch(
                id="in",
                employee_id="emp-1",
                type=PunchType.IN,
                timestamp=stamp(day, clock_in, zone),
            )
        ],
        shifts=shifts,
        notes=None,
    )
    others = []
    if draw(st.booleans()):  # a later shift the clock-out must leave enough rest before
        later = clock_in + draw(st.integers(0, 1900))
        others = [
            Timecard(
                id="tc-1",
                employee_id="emp-1",
                work_date=tc.work_date + timedelta(days=1),
                punches=[],
                shifts=[
                    Shift(
                        id="s1",
                        employee_id="emp-1",
                        start=stamp(day, later, zone),
                        end=stamp(day, later + 480, zone),
                        location_id="loc-1",
                    )
                ],
                notes=None,
            )
        ]
    offset = draw(st.integers(-120, 1900))  # proposed clock-out, minutes after the clock-in
    use_scheduled_end = bool(shifts) and draw(st.booleans())
    return tc, others, clock_in, offset, use_scheduled_end, zone


def missed_exception(tc, others):
    return next(
        (
            e
            for e in detect_exceptions([tc, *others], CFG)
            if e.evidence["rule"] == RULE_MISSED_OUT and e.timecard_id == tc.id
        ),
        None,
    )


def assert_limits_hold(tc, exc, out_time):
    """What a passing clock-out must satisfy, stated independently of the validator."""
    t_in = aware(tc.punches[0].timestamp)
    assert t_in < out_time <= t_in + timedelta(hours=12)
    shift = tc.shifts[0]
    assert out_time == aware(shift.end)  # only the scheduled end is allowed
    next_start = exc.evidence.get("next_work_start")
    if next_start:
        assert aware(datetime.fromisoformat(next_start)) - out_time >= timedelta(hours=10)
    fixed = tc.model_copy(deep=True)
    fixed.punches.append(
        Punch(id="new", employee_id="emp-1", type=PunchType.OUT, timestamp=out_time)
    )
    longest = max(b.duration for b in analyze_punches(fixed).blocks)
    assert longest <= timedelta(hours=12)


@PROPS
@given(missed_punch_scenarios())
def test_validator_never_accepts_a_clock_out_over_12_hours_or_with_under_10_hours_rest(case):
    tc, others, clock_in, offset, use_end, zone = case
    exc = missed_exception(tc, others)
    assert exc is not None
    if use_end:
        out_time = aware(tc.shifts[0].end)
    else:
        out_time = aware(tc.punches[0].timestamp) + timedelta(minutes=offset)
    fix = Fix(
        action=FixAction.ADD_PUNCH,
        changes=[
            FieldChange(target_id=tc.id, field="punch.OUT", old=None, new=out_time.isoformat())
        ],
    )
    result = validate_proposal(tc, exc, fix, CFG)
    assert isinstance(result, ValidationResult) and result.passed == (not result.violations)
    if result.passed:
        assert_limits_hold(tc, exc, out_time)


@PROPS
@given(missed_punch_scenarios())
def test_a_baseline_fix_that_passes_also_keeps_to_the_limits(case):
    tc, others, *_ = case
    exc = missed_exception(tc, others)
    if exc is None or exc.baseline_fix is None:
        return
    result = validate_proposal(tc, exc, exc.baseline_fix, CFG)
    if result.passed:
        assert_limits_hold(tc, exc, datetime.fromisoformat(exc.baseline_fix.changes[0].new))


@st.composite
def random_fixes(draw, tc: Timecard) -> Fix:
    targets = [tc.id, "junk", *(s.id for s in tc.shifts), *(p.id for p in tc.punches)]
    changes = [
        FieldChange(
            target_id=draw(st.sampled_from(targets)),
            field=draw(
                st.sampled_from(
                    ["punch.OUT", "punch.IN", "break_minutes", "employee_id", "timestamp", "x"]
                )
            ),
            old=draw(st.one_of(st.none(), st.just("emp-1"), st.text(max_size=8))),
            new=draw(
                st.one_of(
                    st.none(),
                    st.just("30"),
                    st.just("emp-2"),
                    st.text(max_size=12),
                    st.just("2026-03-10T12:00:00+00:00"),
                )
            ),
        )
        for _ in range(draw(st.integers(0, 3)))
    ]
    return Fix(action=draw(st.sampled_from(list(FixAction))), changes=changes)


@PROPS
@given(st.data(), timecard_sets())
def test_any_fix_gets_a_clear_verdict_and_nothing_is_changed(data, tcs):
    found = detect_exceptions(tcs, CFG)
    if not found:
        return
    exc = data.draw(st.sampled_from(found))
    tc = next(t for t in tcs if t.id == exc.timecard_id)
    fix = data.draw(random_fixes(tc))
    before = (tc.model_dump(), exc.model_dump(), fix.model_dump())
    result = validate_proposal(tc, exc, fix, CFG)
    assert (tc.model_dump(), exc.model_dump(), fix.model_dump()) == before
    assert result.passed == (not result.violations)
    for v in result.violations:
        assert v.code and v.message
    if fix.action in (FixAction.FLAG_FOR_MANAGER, FixAction.NO_CHANGE) and not fix.changes:
        assert result.passed or fix.action is FixAction.NO_CHANGE  # a flag always passes
