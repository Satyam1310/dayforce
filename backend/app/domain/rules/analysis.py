"""Plain-code helpers shared by the detectors and the validator. No I/O, no AI.

Conventions (documented here because the detectors and the validator both rely on them):

- Timestamps should be timezone-aware (the contracts store UTC). A naive timestamp is read
  as UTC.
- The day a timecard belongs to is `Timecard.work_date`, which the data source assigns in the
  company timezone (Asia/Kolkata). Nothing here derives a date from a timestamp, so a shift
  that crosses midnight (IST or UTC) stays on the timecard it was recorded on.
- Punches are paired IN -> OUT in time order into work blocks. BREAK_START -> BREAK_END pairs
  are logged breaks. A second IN before an OUT leaves the first IN unmatched. An OUT with no
  IN before it is an orphan OUT. An unmatched BREAK_START or BREAK_END is ignored.
- The free-text `notes` field is never read here. It is untrusted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from app.contracts import Punch, PunchType, Shift, Timecard

# Rule names, used in DetectedException.evidence["rule"].
RULE_MISSED_OUT = "MISSED_CLOCK_OUT"
RULE_MISSING_IN = "MISSING_CLOCK_IN"
RULE_DAILY_OT = "DAILY_OT"
RULE_WEEKLY_OT = "WEEKLY_OT"
RULE_OVERLAP = "OVERLAPPING_SHIFTS"
RULE_MEAL_BREAK = "MEAL_BREAK"
RULE_DAILY_MAX = "DAILY_MAX"
RULE_MIN_REST = "MIN_REST"

_TIE_ORDER = {
    PunchType.IN: 0,
    PunchType.BREAK_START: 1,
    PunchType.BREAK_END: 2,
    PunchType.OUT: 3,
}


def aware(dt: datetime) -> datetime:
    """Timezone-aware version of dt (a naive value is read as UTC)."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def iso_utc(dt: datetime) -> str:
    """ISO-8601 in UTC, the same style the contract fixtures use ('...+00:00')."""
    return aware(dt).astimezone(UTC).isoformat()


def hours(delta: timedelta) -> float:
    return round(delta.total_seconds() / 3600, 2)


def hours_td(value: float) -> timedelta:
    """A policy number of hours as an exact timedelta, so comparisons have no float error."""
    return timedelta(hours=value)


@dataclass(frozen=True)
class WorkBlock:
    """One stretch of work: a clock-in/clock-out pair, or a scheduled shift on a day that has
    no punches at all."""

    timecard_id: str
    work_date: date
    start: datetime
    end: datetime
    key: str  # id of the IN punch (worked) or of the shift (scheduled)
    break_minutes: float = 0.0
    scheduled: bool = False

    @property
    def duration(self) -> timedelta:
        """Gross time from clock-in to clock-out (used for shift length and rest)."""
        return self.end - self.start

    @property
    def paid(self) -> timedelta:
        """Time that counts as worked: gross time minus logged unpaid breaks."""
        return self.duration - timedelta(minutes=self.break_minutes)


@dataclass
class DayFacts:
    blocks: list[WorkBlock] = field(default_factory=list)
    unmatched_in: list[Punch] = field(default_factory=list)
    orphan_out: list[Punch] = field(default_factory=list)
    breaks: list[tuple[datetime, datetime]] = field(default_factory=list)  # logged breaks

    @property
    def worked(self) -> timedelta:
        """Paid hours for the day (breaks taken out). This is what overtime rules count."""
        return sum((b.paid for b in self.blocks), timedelta())

    @property
    def complete(self) -> bool:
        """True when every clock-in has a clock-out (so the day's hours can be trusted)."""
        return not self.unmatched_in and not self.orphan_out


def _overlap_minutes(
    a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime
) -> float:
    latest_start = max(a_start, b_start)
    earliest_end = min(a_end, b_end)
    return max(0.0, (earliest_end - latest_start).total_seconds() / 60)


def analyze_punches(tc: Timecard) -> DayFacts:
    punches = sorted(tc.punches, key=lambda p: (aware(p.timestamp), _TIE_ORDER[p.type]))
    pairs: list[tuple[Punch, Punch]] = []
    facts = DayFacts()
    breaks: list[tuple[datetime, datetime]] = []
    open_in: Punch | None = None
    open_break: datetime | None = None
    for p in punches:
        t = aware(p.timestamp)
        if p.type is PunchType.IN:
            if open_in is not None:
                facts.unmatched_in.append(open_in)
            open_in = p
        elif p.type is PunchType.OUT:
            if open_in is None:
                facts.orphan_out.append(p)
            else:
                pairs.append((open_in, p))
                open_in = None
        elif p.type is PunchType.BREAK_START:
            open_break = t
        elif p.type is PunchType.BREAK_END and open_break is not None:
            breaks.append((open_break, t))
            open_break = None
    if open_in is not None:
        facts.unmatched_in.append(open_in)
    facts.breaks = breaks
    for p_in, p_out in pairs:
        start, end = aware(p_in.timestamp), aware(p_out.timestamp)
        logged = sum(_overlap_minutes(start, end, b0, b1) for b0, b1 in breaks)
        facts.blocks.append(
            WorkBlock(tc.id, tc.work_date, start, end, p_in.id, break_minutes=logged)
        )
    return facts


def scheduled_shift_for(tc: Timecard, clock_in: datetime) -> Shift | None:
    """The scheduled shift a clock-in belongs to: the one starting closest to it among shifts
    that have not ended yet. None when the timecard has no usable schedule."""
    candidates = [s for s in tc.shifts if aware(s.end) > clock_in]
    if not candidates:
        return None
    return min(candidates, key=lambda s: (abs(aware(s.start) - clock_in), s.id))


def employee_blocks(timecards: list[Timecard], facts: dict[str, DayFacts]) -> list[WorkBlock]:
    """Work blocks for one employee, in time order. A day with punches contributes its
    completed pairs. A day with no punches contributes its scheduled shifts."""
    blocks: list[WorkBlock] = []
    for tc in timecards:
        f = facts[tc.id]
        if tc.punches:
            blocks.extend(f.blocks)
        else:
            blocks.extend(
                WorkBlock(tc.id, tc.work_date, aware(s.start), aware(s.end), s.id, scheduled=True)
                for s in tc.shifts
            )
    seen: set[tuple[datetime, datetime]] = set()
    unique = []
    for b in sorted(blocks, key=lambda b: (b.start, b.end, b.key)):
        if (b.start, b.end) not in seen:
            seen.add((b.start, b.end))
            unique.append(b)
    return unique


def week_start(d: date, week_starts: str = "MONDAY") -> date:
    first = {
        "MONDAY": 0,
        "TUESDAY": 1,
        "WEDNESDAY": 2,
        "THURSDAY": 3,
        "FRIDAY": 4,
        "SATURDAY": 5,
        "SUNDAY": 6,
    }[week_starts.upper()]
    return d - timedelta(days=(d.weekday() - first) % 7)
